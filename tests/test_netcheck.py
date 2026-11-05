"""feature-network-check §8.1：启动前网络检查的回归。

本地起假服务代替真实网络：TLS 假 trace（chatgpt.com/cdn-cgi/trace）、假 CONNECT 代理、假 SOCKS5 代理、
假 mihomo 外部控制接口。CLI 仍以子进程运行（helpers.CliTestCase），假服务在本进程的线程里，
子进程经 MULTI_CODEX_TEST_TRACE / MULTI_CODEX_TEST_CA 连到它们。三平台都跑（CI Windows 行单独一步）。
"""

import http.server
import json
import os
import socket
import socketserver
import ssl
import subprocess
import sys
import threading
import unittest

from helpers import IS_WINDOWS, ROOT, SRC, CliTestCase

sys.path.insert(0, SRC)

from multi_codex import launcher, netcheck  # noqa: E402
from multi_codex.config import ConfigError, parse_config  # noqa: E402

DATA = os.path.join(ROOT, "tests", "data")
CERT = os.path.join(DATA, "netcheck-cert.pem")
KEY = os.path.join(DATA, "netcheck-key.pem")
POSIX_ONLY = unittest.skipIf(IS_WINDOWS, "POSIX only")
SKIP = netcheck.SKIP_ENV


# ---------------------------------------------------------------- 假服务

class _Quiet(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, *args):  # 不往测试输出里写访问日志；unix 套接字下 address_string 也会出错
        pass

    def reply(self, status, body):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class _TraceHandler(_Quiet):
    def do_GET(self):
        server = self.server
        with server.lock:
            server.count += 1
        if self.path != "/cdn-cgi/trace":
            self.reply(404, "not found")
        elif server.status != 200:
            self.reply(server.status, "error")
        else:
            lines = ["fl=1", "h=chatgpt.com", "ip=203.0.113.9"]
            if server.loc is not None:
                lines.append("loc=" + server.loc)
            self.reply(200, "\n".join(lines) + "\n")


class _ThreadingHTTP(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


class TraceServer(object):
    """本地 TLS 假 trace 服务；loc / status 可随时改，count 记录收到的请求数。"""

    def __init__(self):
        self.httpd = _ThreadingHTTP(("127.0.0.1", 0), _TraceHandler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(CERT, KEY)
        self.httpd.socket = context.wrap_socket(self.httpd.socket, server_side=True)
        self.httpd.lock = threading.Lock()
        self.httpd.count = 0
        self.httpd.loc = "US"
        self.httpd.status = 200
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    @property
    def count(self):
        return self.httpd.count

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def _relay(left, right):
    def pump(src, dst):
        try:
            while True:
                data = src.recv(65536)
                if not data:
                    break
                dst.sendall(data)
        except OSError:
            pass
        finally:
            try:
                dst.shutdown(socket.SHUT_WR)
            except OSError:
                pass
    thread = threading.Thread(target=pump, args=(right, left), daemon=True)
    thread.start()
    pump(left, right)
    thread.join(10)
    left.close()
    right.close()


class _ConnectHandler(socketserver.BaseRequestHandler):
    def handle(self):
        sock = self.request
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = sock.recv(4096)
            if not chunk:
                return  # 端口检查：连上就关
            head += chunk
        line = head.split(b"\r\n", 1)[0].decode("ascii")
        method, target, _ = line.split(" ", 2)
        self.server.targets.append(target)
        if method != "CONNECT" or self.server.refuse:
            sock.sendall(b"HTTP/1.1 403 Forbidden\r\n\r\n")
            return
        host, _, port = target.rpartition(":")
        upstream = socket.create_connection((host, int(port)), 5)
        sock.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
        _relay(sock, upstream)


class _Socks5Handler(socketserver.BaseRequestHandler):
    def recv_exact(self, size):
        data = b""
        while len(data) < size:
            chunk = self.request.recv(size - len(data))
            if not chunk:
                raise EOFError
            data += chunk
        return data

    def handle(self):
        sock = self.request
        try:
            greeting = self.recv_exact(2)
            self.recv_exact(greeting[1])
            sock.sendall(b"\x05\x00")
            head = self.recv_exact(4)
            if head[3] != 3:
                self.server.targets.append("atyp={}".format(head[3]))
                sock.sendall(b"\x05\x08\x00\x01" + b"\x00" * 6)
                return
            host = self.recv_exact(self.recv_exact(1)[0]).decode("ascii")
            port = int.from_bytes(self.recv_exact(2), "big")
        except EOFError:
            return  # 端口检查：连上就关
        self.server.targets.append("{}:{}".format(host, port))
        if self.server.rep:
            sock.sendall(bytes([5, self.server.rep, 0, 1]) + b"\x00" * 6)
            return
        upstream = socket.create_connection((host, port), 5)
        sock.sendall(b"\x05\x00\x00\x01" + b"\x00" * 6)
        _relay(sock, upstream)


class _ThreadingTCP(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


class ProxyServer(object):
    """假代理：kind 为 "http"（CONNECT）或 "socks5"。refuse / rep 控制拒绝行为，targets 记录请求目标。"""

    def __init__(self, kind):
        handler = _ConnectHandler if kind == "http" else _Socks5Handler
        self.server = _ThreadingTCP(("127.0.0.1", 0), handler)
        self.server.targets = []
        self.server.refuse = False
        self.server.rep = 0
        self.port = self.server.server_address[1]
        self.url = "{}://127.0.0.1:{}".format(kind, self.port)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


class _MihomoHandler(_Quiet):
    def do_GET(self):
        server = self.server
        if server.secret and self.headers.get("Authorization") != "Bearer " + server.secret:
            self.reply(401, '{"message":"Unauthorized"}')
            return
        if self.path != "/configs":
            self.reply(404, "{}")
            return
        self.reply(200, json.dumps(server.payload))


class MihomoServer(object):
    """假 mihomo 外部控制接口（GET /configs）；unix_path 不为空时监听 unix 套接字。"""

    def __init__(self, unix_path=None):
        if unix_path:
            class _UnixHTTP(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
                daemon_threads = True
            self.server = _UnixHTTP(unix_path, _MihomoHandler)
            self.address = "unix:" + unix_path
        else:
            self.server = _ThreadingHTTP(("127.0.0.1", 0), _MihomoHandler)
            self.address = "127.0.0.1:{}".format(self.server.server_address[1])
        self.server.secret = ""
        self.server.payload = {"mode": "global", "tun": {"enable": True, "stack": "mixed"}, "port": 7890}
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


def closed_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


# ---------------------------------------------------------------- 进程内：检查项本身

class ChecksTest(unittest.TestCase):
    """§8.1-3～6：直接调用 netcheck.run_checks，环境变量在本进程里设置。"""

    def setUp(self):
        self.trace = TraceServer()
        self.addCleanup(self.trace.stop)
        self.saved = {key: os.environ.get(key) for key in (netcheck.TEST_TRACE_ENV, netcheck.TEST_CA_ENV)}
        os.environ[netcheck.TEST_TRACE_ENV] = "127.0.0.1:{}".format(self.trace.port)
        os.environ[netcheck.TEST_CA_ENV] = CERT
        self.addCleanup(self.restore_env)

    def restore_env(self):
        for key, value in self.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def config(self, proxy="inherit", level="off", country="US", clash=None, clash_api=None, clash_tun=None,
               clash_global=None):
        """clash 写旧字段（两项一起），clash_tun / clash_global 写新字段；都不给时不写 Clash 设置。"""
        net_check = {"level": level, "country": country}
        if clash is not None:
            net_check["clash"] = clash
        if clash_tun is not None:
            net_check["clash_tun"] = clash_tun
        if clash_global is not None:
            net_check["clash_global"] = clash_global
        data = {"accounts": {"work": {"proxy": proxy, "net_check": net_check}}}
        if clash_api is not None:
            data["clash_api"] = clash_api
        config = parse_config(json.dumps(data), "test")
        return config, config.find("work")

    def results(self, *args, **kwargs):
        config, account = self.config(*args, **kwargs)
        return netcheck.run_checks(config, account, stop_on_failure=False)

    def by_item(self, results):
        return {result.item: result for result in results}

    # §8.1-3 端口

    def test_port_open_and_refused(self):
        proxy = ProxyServer("http")
        self.addCleanup(proxy.stop)
        (result,) = self.results(proxy.url, "basic")
        self.assertTrue(result.ok, result)
        self.assertEqual(result.item, "proxy-port")
        (result,) = self.results("http://127.0.0.1:{}".format(closed_port()), "basic")
        self.assertFalse(result.ok)
        self.assertIn("refused", result.detail)

    def test_port_ipv6(self):
        try:
            server = socket.socket(socket.AF_INET6)
            server.bind(("::1", 0))
        except OSError:
            self.skipTest("IPv6 loopback not available")
        server.listen(1)
        self.addCleanup(server.close)
        (result,) = self.results("http://[::1]:{}".format(server.getsockname()[1]), "basic")
        self.assertTrue(result.ok, result)

    def test_inherit_proxy_fails(self):
        (result,) = self.results("inherit", "basic")
        self.assertFalse(result.ok)
        self.assertIn("proxy is inherit", result.detail)

    # §8.1-4 直连出口国家

    def test_direct_country(self):
        proxy = ProxyServer("http")
        self.addCleanup(proxy.stop)
        found = self.by_item(self.results(proxy.url, "strict"))
        self.assertTrue(found["direct-country"].ok, found)
        self.assertIn("loc=US", found["direct-country"].detail)
        self.trace.httpd.loc = "JP"
        found = self.by_item(self.results(proxy.url, "strict"))
        self.assertFalse(found["direct-country"].ok)
        self.assertIn("loc is JP, expected US", found["direct-country"].detail)
        found = self.by_item(self.results(proxy.url, "strict", country="jp"))
        self.assertTrue(found["direct-country"].ok, found)

    def test_trace_without_loc_or_error_status(self):
        proxy = ProxyServer("http")
        self.addCleanup(proxy.stop)
        self.trace.httpd.loc = None
        found = self.by_item(self.results(proxy.url, "strict"))
        self.assertIn("no loc= line", found["direct-country"].detail)
        self.trace.httpd.status = 500
        found = self.by_item(self.results(proxy.url, "strict"))
        self.assertIn("HTTP 500", found["direct-country"].detail)

    def test_certificate_not_trusted(self):
        proxy = ProxyServer("http")
        self.addCleanup(proxy.stop)
        del os.environ[netcheck.TEST_CA_ENV]
        found = self.by_item(self.results(proxy.url, "strict"))
        self.assertFalse(found["direct-country"].ok)
        self.assertIn("TLS certificate verification failed", found["direct-country"].detail)

    # §8.1-5 经代理出口国家

    def test_via_http_proxy(self):
        proxy = ProxyServer("http")
        self.addCleanup(proxy.stop)
        found = self.by_item(self.results(proxy.url, "strict"))
        self.assertTrue(found["proxy-country"].ok, found)
        self.assertIn("via " + proxy.url, found["proxy-country"].detail)
        self.assertEqual(proxy.server.targets, ["127.0.0.1:{}".format(self.trace.port)])
        proxy.server.refuse = True
        found = self.by_item(self.results(proxy.url, "strict"))
        self.assertFalse(found["proxy-country"].ok)
        self.assertIn("proxy refused CONNECT", found["proxy-country"].detail)

    def test_via_socks5_proxy(self):
        for scheme in ("socks5", "socks5h"):
            proxy = ProxyServer("socks5")
            self.addCleanup(proxy.stop)
            url = proxy.url.replace("socks5", scheme)
            found = self.by_item(self.results(url, "strict"))
            self.assertTrue(found["proxy-country"].ok, found)
            self.assertEqual(proxy.server.targets[-1], "127.0.0.1:{}".format(self.trace.port))
            proxy.server.rep = 5
            found = self.by_item(self.results(url, "strict"))
            self.assertIn("REP=5", found["proxy-country"].detail)

    def test_https_proxy_rejected(self):
        found = self.by_item(self.results("https://127.0.0.1:{}".format(closed_port()), "strict"))
        self.assertIn("not supported", found["proxy-country"].detail)

    # §8.1-6 Clash

    def clash_result(self, mihomo, secret=""):
        api = {"address": mihomo.address}
        if secret:
            api["secret"] = secret
        (result,) = self.results(clash=True, clash_api=api)
        self.assertEqual(result.item, "clash")
        return result

    def test_clash_states(self):
        mihomo = MihomoServer()
        self.addCleanup(mihomo.stop)
        self.assertTrue(self.clash_result(mihomo).ok)
        mihomo.server.payload = {"mode": "rule", "tun": {"enable": True}}
        self.assertIn("mode is rule", self.clash_result(mihomo).detail)
        mihomo.server.payload = {"mode": "Global", "tun": {"enable": False}}
        self.assertIn("TUN is off", self.clash_result(mihomo).detail)
        mihomo.server.payload = {"hello": 1}
        self.assertIn("unexpected response", self.clash_result(mihomo).detail)

    def test_clash_secret(self):
        mihomo = MihomoServer()
        self.addCleanup(mihomo.stop)
        mihomo.server.secret = "s3cret"
        self.assertTrue(self.clash_result(mihomo, "s3cret").ok)
        result = self.clash_result(mihomo, "badvalue")
        self.assertIn("401", result.detail)
        self.assertNotIn("badvalue", result.detail)
        self.assertIn("secret", self.clash_result(mihomo).detail)

    def test_clash_not_set_or_down(self):
        (result,) = self.results(clash=True)
        self.assertIn("multi-codex clash-api ADDRESS", result.detail)
        self.assertIn("multi-codex clash-api auto", result.detail)
        result = self.clash_result(type("Down", (), {"address": "127.0.0.1:{}".format(closed_port())})())
        self.assertIn("refused", result.detail)

    @POSIX_ONLY
    def test_clash_unix_socket(self):
        import tempfile
        directory = tempfile.mkdtemp(prefix="mcx-")
        self.addCleanup(lambda: os.path.isdir(directory) and __import__("shutil").rmtree(directory))
        mihomo = MihomoServer(os.path.join(directory, "m.sock"))
        self.addCleanup(mihomo.stop)
        # mihomo 的 unix 套接字不鉴权，配置里的 secret 不影响结果
        self.assertTrue(self.clash_result(mihomo, "ignored").ok)

    def test_stop_on_failure_and_order(self):
        mihomo = MihomoServer()
        self.addCleanup(mihomo.stop)
        mihomo.server.payload = {"mode": "rule", "tun": {"enable": True}}
        config, account = self.config("http://127.0.0.1:{}".format(closed_port()), "strict", clash=True,
                                      clash_api={"address": mihomo.address})
        all_items = [result.item for result in netcheck.run_checks(config, account, stop_on_failure=False)]
        self.assertEqual(all_items, ["clash", "proxy-port", "direct-country", "proxy-country"])
        stopped = netcheck.run_checks(config, account, stop_on_failure=True)
        self.assertEqual([result.item for result in stopped], ["clash"])

    def test_scope_text(self):
        cases = [(("basic", False, False), "basic"), (("strict", False, False), "strict, US"),
                 (("strict", True, True), "strict, US, clash"), (("off", True, True), "clash"),
                 (("off", True, False), "clash tun"), (("off", False, True), "clash global"),
                 (("strict", True, False), "strict, US, clash tun"),
                 (("basic", False, True), "basic, clash global")]
        for (level, tun, global_), expected in cases:
            _, account = self.config("http://127.0.0.1:1", level, clash_tun=tun, clash_global=global_)
            self.assertEqual(netcheck.scope_text(account), expected)

    # feature-clash-split §8-2：两项分别判定

    def split_result(self, mihomo, mode, tun, need_tun, need_global):
        mihomo.server.payload = {"mode": mode, "tun": {"enable": tun}}
        (result,) = self.results(clash_tun=need_tun, clash_global=need_global,
                                 clash_api={"address": mihomo.address})
        return result

    def test_clash_split_states(self):
        mihomo = MihomoServer()
        self.addCleanup(mihomo.stop)
        cases = [  # (mode, tun 开, 要 TUN, 要全局) → 是否通过
            ("rule", True, True, False, True), ("global", True, True, False, True),
            ("direct", True, True, False, False), ("rule", False, True, False, False),
            ("global", False, False, True, True), ("rule", False, False, True, False),
            ("global", True, True, True, True), ("rule", True, True, True, False),
            ("global", False, True, True, False)]
        for mode, tun, need_tun, need_global, ok in cases:
            with self.subTest(mode=mode, tun=tun, need_tun=need_tun, need_global=need_global):
                result = self.split_result(mihomo, mode, tun, need_tun, need_global)
                self.assertEqual(result.ok, ok, result.detail)
        self.assertEqual(self.split_result(mihomo, "rule", True, True, False).detail, "mode=rule, tun on")
        self.assertEqual(self.split_result(mihomo, "global", False, False, True).detail, "mode=global, tun off")
        self.assertIn("direct", self.split_result(mihomo, "direct", True, True, False).detail)

    def test_failure_text_single_item(self):
        for tun, global_ in ((True, False), (False, True)):
            _, account = self.config("http://127.0.0.1:1", "off", clash_tun=tun, clash_global=global_)
            failed = netcheck.CheckResult("clash", False, "Clash TUN is off", 1)
            text = netcheck.failure_text(account, [failed], "codex-work")
            self.assertIn("multi-codex netcheck work off --clash off", text)

    def test_parse_loc(self):
        self.assertEqual(netcheck.parse_loc(b"ip=1\nloc=SG\nwarp=off\n"), "SG")
        self.assertIsNone(netcheck.parse_loc(b"ip=1\nlocation=SG\n"))


class ConfigTest(unittest.TestCase):
    """§8.1-1 配置往返与校验。"""

    def test_round_trip(self):
        raw = {"accounts": {"a": {"proxy": "http://127.0.0.1:7901",
                                  "net_check": {"level": "strict", "country": "jp", "clash": True}},
                            "b": {"proxy": "http://127.0.0.1:7902"}},
               "clash_api": {"address": "127.0.0.1:9097", "secret": "x"}}
        config = parse_config(json.dumps(raw), "t")
        data = config.to_dict()
        self.assertEqual(data["accounts"]["a"]["net_check"],
                         {"level": "strict", "country": "JP", "clash_tun": True, "clash_global": True})
        self.assertNotIn("net_check", data["accounts"]["b"])
        self.assertEqual(data["clash_api"], {"address": "http://127.0.0.1:9097", "secret": "x"})
        self.assertEqual(parse_config(json.dumps(data), "t").to_dict(), data)
        copied = config.find("a").copy()
        copied.net_check.level = "off"
        self.assertEqual(config.find("a").net_check.level, "strict")

    def test_invalid_values(self):
        for net_check in ({"level": "max"}, {"country": "USA"}, {"clash": "yes"}, {"clash_tun": "yes"},
                          {"clash_global": 1}, "strict"):
            with self.subTest(net_check=net_check):
                with self.assertRaises(ConfigError):
                    parse_config(json.dumps({"accounts": {"a": {"net_check": net_check}}}), "t")
        for api in ({"address": "https://127.0.0.1:9097"}, {"address": ""}, {"address": "127.0.0.1"},
                    {"address": "127.0.0.1:9097", "secret": "a\nb"}, "127.0.0.1:9097"):
            with self.subTest(api=api):
                with self.assertRaises(ConfigError):
                    parse_config(json.dumps({"clash_api": api}), "t")

    def test_yaml_scalar(self):
        from multi_codex.clashfind import _yaml_scalar
        self.assertEqual(_yaml_scalar(" 'it''s' # c"), "it's")
        self.assertEqual(_yaml_scalar(' "a\\"b"'), 'a"b')
        self.assertEqual(_yaml_scalar(" 127.0.0.1:9097  # c"), "127.0.0.1:9097")

    def test_split_fields(self):
        """feature-clash-split §8-1：新旧字段同时出现以新字段为准；只给一个新字段时另一项为关。"""
        def parsed(net_check):
            config = parse_config(json.dumps({"accounts": {"a": {"net_check": net_check}}}), "t")
            check = config.find("a").net_check
            return check.clash_tun, check.clash_global
        self.assertEqual(parsed({"clash": True}), (True, True))
        self.assertEqual(parsed({"clash": True, "clash_tun": True}), (True, False))
        self.assertEqual(parsed({"clash": False, "clash_global": True}), (False, True))
        config = parse_config(json.dumps({"accounts": {"a": {"net_check": {"clash_tun": True}}}}), "t")
        self.assertNotIn("clash", config.to_dict()["accounts"]["a"]["net_check"])

    def test_clash_port_message(self):
        with self.assertRaises(ConfigError) as ctx:
            parse_config(json.dumps({"clash_api": {"address": "127.0.0.1:70000"}}), "t")
        self.assertIn("Clash controller", str(ctx.exception))
        self.assertNotIn("proxy port", str(ctx.exception))

    def test_parse_raw_response(self):
        """命名管道路径读全后经 http.client 解析：Content-Length 与分块编码两种回包。"""
        body = b'{"mode":"global","tun":{"enable":true}}'
        plain = b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: " + \
            str(len(body)).encode() + b"\r\n\r\n" + body
        chunked = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n" + \
            "{:x}".format(10).encode() + b"\r\n" + body[:10] + b"\r\n" + \
            "{:x}".format(len(body) - 10).encode() + b"\r\n" + body[10:] + b"\r\n0\r\n\r\n"
        for raw in (plain, chunked):
            status, data = netcheck._parse_raw_response(raw)
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(data.decode()), {"mode": "global", "tun": {"enable": True}})
        status, _ = netcheck._parse_raw_response(b"HTTP/1.0 401 Unauthorized\r\nContent-Length: 0\r\n\r\n")
        self.assertEqual(status, 401)

    def test_render_default_unchanged(self):
        base = launcher.render("work", "/x/work", "http://127.0.0.1:7901", {"K": "v"})
        self.assertEqual(launcher.render("work", "/x/work", "http://127.0.0.1:7901", {"K": "v"}, net_check=False),
                         base)
        checked = launcher.render("work", "/x/work", "http://127.0.0.1:7901", {"K": "v"}, net_check=True)
        self.assertIn("(cd / && exec multi-codex netgate work) || exit $?\nunset " + SKIP + "\n", checked)
        self.assertEqual(launcher.render("work", "/x/work", "inherit", None, True, net_check=True),
                         launcher.render("work", "/x/work", "inherit", None, True))


# ---------------------------------------------------------------- CLI 子进程

class NetCheckCliBase(CliTestCase):
    def setUp(self):
        super().setUp()
        self.trace = TraceServer()
        self.addCleanup(self.trace.stop)
        self.proxy = ProxyServer("http")
        self.addCleanup(self.proxy.stop)
        self.env[netcheck.TEST_TRACE_ENV] = "127.0.0.1:{}".format(self.trace.port)
        self.env[netcheck.TEST_CA_ENV] = CERT
        if not IS_WINDOWS:
            # 检查版启动命令调用 PATH 上的 multi-codex（做法同 tests/test_ergonomics.py 的 CompletionTest）。
            self.write(os.path.join(self.fakebin, "multi-codex"),
                       '#!/bin/sh\nexec "$FAKE_PYTHON" -m multi_codex "$@"\n', 0o755)
        self.ok("add", "work", "--proxy", self.proxy.url)
        self.launcher_path = os.path.join(self.bin, "codex-work") + (".cmd" if IS_WINDOWS else "")

    def read(self, path):
        with open(path) as handle:
            return handle.read()

    def dead_proxy(self):
        self.ok("proxy", "work", "http://127.0.0.1:{}".format(closed_port()))

    def assert_refused(self, code, err, called):
        self.assertEqual(code, 5, err)
        self.assertIn("multi-codex: network check failed for account work (basic): proxy-port:", err)
        self.assertIn("skip once", err)
        self.assertIn("multi-codex netcheck work off", err)
        self.assertFalse(called, "codex must not run")


class NetCheckCommandTest(NetCheckCliBase):
    """§8.1-2 netcheck / clash-api 命令。"""

    def test_enable_rewrites_launcher_and_disable_restores(self):
        base = self.read(self.launcher_path)
        result = self.ok("netcheck", "work", "basic")
        self.assertIn("network check of work: basic", result.out)
        checked = self.read(self.launcher_path)
        if IS_WINDOWS:
            self.assertEqual(checked, base)
        else:
            expected = launcher.render("work", os.path.join(self.root, "work"), self.proxy.url, None,
                                       net_check=True)
            self.assertEqual(checked, expected)
        self.ok("netcheck", "work", "strict", "--country", "jp")
        self.assertEqual(self.read(self.launcher_path), checked)
        self.ok("netcheck", "work", "--country", "SG")
        with open(os.path.join(self.state, "config.json")) as handle:
            saved = json.load(handle)["accounts"]["work"]["net_check"]
        self.assertEqual(saved, {"level": "strict", "country": "SG", "clash_tun": False, "clash_global": False})
        self.ok("netcheck", "work", "off")
        self.assertEqual(self.read(self.launcher_path), base)

    def test_validation(self):
        self.ok("proxy", "work", "off")
        result = self.run_cli("netcheck", "work", "basic")
        self.assertEqual(result.code, 2, result)
        self.assertIn("needs a proxy", result.err)
        self.ok("proxy", "work", "https://127.0.0.1:8443")
        self.ok("netcheck", "work", "basic")
        self.assertEqual(self.run_cli("netcheck", "work", "strict").code, 2)
        self.assertEqual(self.run_cli("netcheck", "work", "--country", "USA").code, 2)
        self.assertEqual(self.run_cli("netcheck", "ghost", "basic").code, 1)
        result = self.ok("netcheck", "work", "--clash", "on")
        self.assertIn("Clash controller is not set", result.err)
        self.assertIn("clash-api auto", result.err)

    def test_country_on_off_account_warns(self):
        result = self.ok("netcheck", "work", "--country", "JP")
        self.assertIn("--country is not saved", result.err)
        self.assertIn("network check of work: off", result.out)

    def test_apply_ignores_foreign_clash_api(self):
        """apply -f 文件里的 clash_api 即使是另一个平台的写法也不让整个 apply 失败，只提示并沿用本机值。"""
        self.ok("clash-api", "127.0.0.1:9097")
        foreign = "unix:/tmp/mihomo.sock" if IS_WINDOWS else "\\\\.\\pipe\\mihomo"
        for api in ({"address": foreign}, {"address": "nonsense"}):
            with self.subTest(api=api):
                path = os.path.join(self.root, "apply.json")
                with open(path, "w") as handle:
                    json.dump({"accounts": {"work": {"proxy": self.proxy.url}}, "clash_api": api}, handle)
                result = self.run_cli("apply", "-f", path)
                self.assertEqual(result.code, 0, result)
                self.assertIn("'clash_api' in", result.err)
                with open(os.path.join(self.state, "config.json")) as handle:
                    self.assertEqual(json.load(handle)["clash_api"]["address"], "http://127.0.0.1:9097")

    def test_list_and_doctor_with_env(self):
        self.ok("env", "work", "SECRET=s3cret")
        self.ok("netcheck", "work", "basic")
        data = json.loads(self.ok("list", "--json").out)
        self.assertEqual(data["accounts"][0]["launcher"], "ok")
        self.assertNotIn("change(s) pending", self.run_cli("doctor").out)
        if not IS_WINDOWS:
            self.assertEqual(os.stat(self.launcher_path).st_mode & 0o777, 0o700)
            # 手改配置关掉检查：启动命令与配置不一致
            path = os.path.join(self.state, "config.json")
            with open(path) as handle:
                data = json.load(handle)
            del data["accounts"]["work"]["net_check"]
            with open(path, "w") as handle:
                json.dump(data, handle)
            self.assertEqual(json.loads(self.ok("list", "--json").out)["accounts"][0]["launcher"], "stale")
            self.assertIn("change(s) pending", self.run_cli("doctor").out)

    def test_show_and_clash_api(self):
        self.assertIn("network check is off for work", self.ok("netcheck", "work").out)
        self.ok("netcheck", "work", "strict")
        result = self.ok("netcheck", "work")
        self.assertIn("ok   proxy-port", result.out)
        self.assertIn("ok   proxy-country loc=US", result.out)
        self.trace.httpd.loc = "JP"
        result = self.run_cli("netcheck", "work")
        self.assertEqual(result.code, 5, result)
        self.assertIn("fail direct-country loc is JP", result.out)
        self.assertIn("fail proxy-country", result.out)
        self.assertIn("Clash controller is not set", self.ok("clash-api").out)
        mihomo = MihomoServer()
        self.addCleanup(mihomo.stop)
        mihomo.server.secret = "topsecret"
        self.run_cli_input(["clash-api", mihomo.address, "--secret-stdin"], "topsecret\n")
        result = self.ok("clash-api")
        self.assertIn("secret set", result.out)
        self.assertNotIn("topsecret", result.out)
        self.trace.httpd.loc = "US"
        self.ok("netcheck", "work", "off", "--clash", "on")
        self.assertIn("ok   clash mode=global, tun on", self.ok("netcheck", "work").out)
        self.ok("clash-api", "off")
        self.assertEqual(self.run_cli("netcheck", "work").code, 5)
        self.assertEqual(self.run_cli("clash-api", "https://x:1").code, 2)

    def run_cli_input(self, args, text):
        full_env = dict(self.env)
        proc = subprocess.run([sys.executable, "-m", "multi_codex"] + args, env=full_env, input=text,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                              cwd=self.tmp, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_rename_and_remove(self):
        self.ok("netcheck", "work", "basic")
        self.ok("rename", "work", "client")
        self.assertFalse(os.path.exists(self.launcher_path))
        new_path = os.path.join(self.bin, "codex-client") + (".cmd" if IS_WINDOWS else "")
        if not IS_WINDOWS:
            self.assertIn("netgate client", self.read(new_path))
        self.assertIn("network check of client: basic", self.ok("netcheck", "client").out)
        self.ok("remove", "client")
        self.assertFalse(os.path.exists(new_path))


class GateTest(NetCheckCliBase):
    """§8.1-7～9 各入口的放行与拒绝。"""

    def setUp(self):
        super().setUp()
        self.ok("netcheck", "work", "basic")

    def launch(self, *args, env=None):
        code, received, seen, err = self.run_launcher("work", *args, env=env)
        return code, received, seen, err

    def test_refused_everywhere(self):
        self.dead_proxy()
        code, received, _, err = self.launch("--version")
        self.assert_refused(code, err, received)
        for args in (("run", "work"), ("login", "work")):
            result = self.run_cli(*args)
            self.assert_refused(result.code, result.err, os.path.exists(self.fake_out + ".args"))
            self.assertIn("multi-codex {} work".format(args[0]), result.err)

    def test_skip_once(self):
        self.dead_proxy()
        code, received, seen, err = self.launch("--x", env={SKIP: "1"})
        self.assertEqual(code, 0, err)
        self.assertEqual(received, ["--x"])
        self.assertNotIn(SKIP, seen)
        self.assertIn("network check skipped for this run", err)
        result = self.run_cli("run", "work", env={SKIP: "1 "})
        self.assertEqual(result.code, 0, result)
        self.assertNotIn(SKIP, self.read(self.fake_out + ".env"))

    def test_passed_line_and_same_environment(self):
        code, received, seen, err = self.launch("a", "b c")
        self.assertEqual(code, 0, err)
        self.assertEqual(err.count("network check passed for account work (basic;"), 1, err)
        self.assertEqual(received, ["a", "b c"])
        self.ok("netcheck", "work", "off")
        code, received_off, seen_off, err_off = self.launch("a", "b c")
        self.assertNotIn("network check", err_off)
        ignore = {"PWD", "OLDPWD", "SHLVL", "_"}
        self.assertEqual({k: v for k, v in seen.items() if k not in ignore},
                         {k: v for k, v in seen_off.items() if k not in ignore})
        self.assertEqual(received, received_off)

    def test_run_and_login_pass(self):
        for args in (("run", "work"), ("login", "work")):
            result = self.run_cli(*args)
            self.assertEqual(result.code, 0, result)
            self.assertEqual(result.err.count("network check passed for account work"), 1, result)
        self.assertEqual(self.read(self.fake_out + ".args").split("\n")[:1], ["login"])

    def test_missing_dir_reports_dir_error_not_netcheck(self):
        self.dead_proxy()
        import shutil
        shutil.rmtree(os.path.join(self.root, "work"))
        code, _, _, err = self.launch()
        self.assertEqual(code, 1, err)
        self.assertNotIn("network check", err)
        result = self.run_cli("run", "work")
        self.assertEqual(result.code, 1, result)
        self.assertNotIn("network check", result.err)

    @POSIX_ONLY
    def test_code_refused_and_passed(self):
        tool = self.write(os.path.join(self.tmp, "fake-code"),
                          '#!/bin/sh\nprintf "%s\\n" "$@" > "$FAKE_CODEX_OUT.args"\n', 0o755)
        self.assertEqual(self.run_cli("code", "work", "--bin", tool).code, 0)
        self.dead_proxy()
        os.unlink(self.fake_out + ".args")
        result = self.run_cli("code", "work", "--bin", tool)
        self.assert_refused(result.code, result.err, os.path.exists(self.fake_out + ".args"))
        self.assertIn("multi-codex code work", result.err)

    @POSIX_ONLY
    def test_codex_auto_checked(self):
        project = os.path.join(self.tmp, "proj")
        os.makedirs(project)
        self.ok("bind", "work", project)
        auto = os.path.join(self.bin, "codex-auto")
        env = dict(self.env)
        proc = subprocess.run([auto, "x"], env=env, cwd=project, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, universal_newlines=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr.count("network check passed for account work"), 1, proc.stderr)
        self.dead_proxy()
        proc = subprocess.run([auto, "x"], env=env, cwd=project, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, universal_newlines=True)
        self.assertEqual(proc.returncode, 5, proc.stderr)

    @POSIX_ONLY
    def test_malicious_module_in_cwd_not_loaded(self):
        project = os.path.join(self.tmp, "evil")
        os.makedirs(project)
        marker = os.path.join(self.tmp, "pwned")
        for module in ("json", "ssl", "socket"):
            self.write(os.path.join(project, module + ".py"),
                       "open({!r}, 'w').close()\nraise SystemExit(99)\n".format(marker))
        proc = subprocess.run([self.launcher_path, "--v"], env=self.env, cwd=project, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, universal_newlines=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse(os.path.exists(marker))
        with open(self.fake_out + ".env") as handle:
            self.assertIn("PWD=" + project, handle.read())

    @POSIX_ONLY
    def test_missing_multi_codex_or_codex(self):
        os.unlink(os.path.join(self.fakebin, "multi-codex"))
        code, received, _, err = self.launch()
        self.assertEqual(code, 127, err)
        self.assertIn("multi-codex not found in PATH", err)
        self.assertFalse(received)
        # 有 multi-codex、没有 codex：报错与未开启检查时相同
        self.write(os.path.join(self.tmp, "only", "multi-codex"),
                   '#!/bin/sh\nexec "$FAKE_PYTHON" -m multi_codex "$@"\n', 0o755)
        env = {"PATH": os.pathsep.join([os.path.join(self.tmp, "only"), "/usr/bin", "/bin"])}
        code, _, _, err = self.launch(env=env)
        self.assertEqual(code, 127, err)
        self.assertTrue(err.endswith("multi-codex: codex not found in PATH\n"), err)


class LiveUsageNetCheckTest(NetCheckCliBase):
    """§8.1-10 usage --live。"""

    def setUp(self):
        super().setUp()
        self.ok("netcheck", "work", "strict")

    def live(self, env=None):
        full = {"FAKE_APP_SERVER_MODE": "ok"}
        full.update(env or {})
        return self.run_cli("usage", "--live", "--json", env=full)

    def test_checked_once_and_quiet(self):
        result = self.live()
        self.assertEqual(result.code, 0, result)
        self.assertEqual(self.trace.count, 2)
        self.assertNotIn("passed", result.out + result.err)
        data = json.loads(result.out)
        self.assertTrue(data["accounts"][0]["ok"], data)

    def test_failure_does_not_start_app_server(self):
        self.trace.httpd.loc = "JP"
        log = os.path.join(self.tmp, "server.log")
        result = self.run_cli("usage", "--live", env={"FAKE_APP_SERVER_MODE": "ok", "FAKE_APP_SERVER_LOG": log})
        self.assertEqual(result.code, 1, result)
        self.assertIn("network check failed: direct-country: loc is JP", result.out + result.err)
        self.assertFalse(os.path.exists(log))

    def test_skip_for_all_accounts(self):
        self.ok("add", "two", "--proxy", self.proxy.url)
        self.ok("netcheck", "two", "strict")
        self.trace.httpd.loc = "JP"
        result = self.live({SKIP: "1"})
        self.assertEqual(result.code, 0, result)
        self.assertEqual(self.trace.count, 0)
        self.assertNotIn("skipped", result.out + result.err)

    def test_missing_dir_reports_dir_error(self):
        import shutil
        shutil.rmtree(os.path.join(self.root, "work"))
        self.trace.httpd.loc = "JP"
        result = self.live()
        self.assertEqual(result.code, 1, result)
        self.assertNotIn("network check failed", result.out + result.err)
        self.assertEqual(self.trace.count, 0)


# ---------------------------------------------------------------- feature-clash-split：拆分开关与自动查找


def command_line(argv):
    """把参数列表拼成一行命令，格式与本平台的拆分方式（platform._split_command）对应。"""
    if IS_WINDOWS:
        return subprocess.list2cmdline(argv)
    import shlex
    return " ".join(shlex.quote(arg) for arg in argv)


class ClashSplitCliTest(NetCheckCliBase):
    """feature-clash-split §8-1、§8-4：命令行的两个开关与旧字段兼容。"""

    def saved_check(self):
        with open(os.path.join(self.state, "config.json")) as handle:
            return json.load(handle)["accounts"]["work"].get("net_check")

    def test_flags(self):
        self.ok("netcheck", "work", "--clash", "on")
        check = self.saved_check()
        self.assertTrue(check["clash_tun"] and check["clash_global"])
        self.ok("netcheck", "work", "--clash", "off")
        self.ok("netcheck", "work", "--clash-tun", "on")
        check = self.saved_check()
        self.assertEqual((check["clash_tun"], check["clash_global"]), (True, False))
        result = self.run_cli("netcheck", "work", "--clash", "on", "--clash-tun", "off")
        self.assertEqual(result.code, 2, result)
        result = self.ok("netcheck", "work", "--clash-tun", "off", "--clash-global", "on")
        self.assertIn("without TUN", result.err)
        path = os.path.join(self.state, "config.json")
        before = self.read(path)
        self.run_cli("netcheck", "work")  # 接口未设置，检查本身不通过（退出 5），这里只看配置没被改写
        self.assertEqual(self.read(path), before)

    def test_legacy_field_kept_until_changed(self):
        path = os.path.join(self.state, "config.json")
        with open(path) as handle:
            data = json.load(handle)
        data["accounts"]["work"]["net_check"] = {"level": "off", "clash": True}
        with open(path, "w") as handle:
            json.dump(data, handle)
        # 手改配置让启动命令过期，第一次会顺带更新启动命令；两次都不改写配置里的旧字段。
        self.ok("netcheck", "work", "--clash", "on")
        self.assertEqual(self.saved_check(), {"level": "off", "clash": True})
        result = self.ok("netcheck", "work", "--clash", "on")
        self.assertIn("already up to date", result.out + result.err)
        self.assertEqual(self.saved_check(), {"level": "off", "clash": True})
        self.ok("netcheck", "work", "--clash-tun", "off")
        check = self.saved_check()
        self.assertNotIn("clash", check)
        self.assertEqual((check["clash_tun"], check["clash_global"]), (False, True))


class ClashFindTest(NetCheckCliBase):
    """feature-clash-split §8-6：`clash-api auto` 与 netcheck 开启时的自动查找。"""

    def setUp(self):
        super().setUp()
        self.mihomo = MihomoServer()
        self.addCleanup(self.mihomo.stop)

    def processes(self, *argvs):
        with open(self.env["MULTI_CODEX_TEST_CLASH_PROCESSES"], "w") as handle:
            json.dump([{"ProcessId": 100 + index, "Name": os.path.basename(argv[0]),
                        "CommandLine": command_line(argv)} for index, argv in enumerate(argvs)], handle)

    def saved_api(self):
        with open(os.path.join(self.state, "config.json")) as handle:
            return json.load(handle).get("clash_api")

    def temp_dir(self):
        import shutil
        import tempfile
        directory = tempfile.mkdtemp(prefix="mcf-")
        self.addCleanup(shutil.rmtree, directory, True)
        return directory

    def write_file(self, path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as handle:
            handle.write(text)

    def http(self, address):
        return "http://" + address

    def test_tcp_flag_forms(self):
        for argv in (["/opt/mihomo", "-ext-ctl", self.mihomo.address],
                     ["/opt/mihomo", "-ext-ctl=" + self.mihomo.address],
                     ["/opt/mihomo", "--ext-ctl", self.mihomo.address]):
            with self.subTest(argv=argv):
                self.ok("clash-api", "off")
                self.processes(argv)
                result = self.ok("clash-api", "auto")
                self.assertIn("found Clash controller", result.err)
                self.assertEqual(self.saved_api()["address"], self.http(self.mihomo.address))

    @POSIX_ONLY
    def test_unix_socket(self):
        directory = self.temp_dir()
        server = MihomoServer(os.path.join(directory, "m.sock"))
        self.addCleanup(server.stop)
        self.processes(["/opt/verge-mihomo", "-ext-ctl-unix", os.path.join(directory, "m.sock")])
        self.ok("clash-api", "auto")
        self.assertEqual(self.saved_api(), {"address": "unix:" + os.path.join(directory, "m.sock")})
        self.ok("clash-api", "off")
        self.processes(["/opt/verge-mihomo", "-d", directory, "-ext-ctl-unix", "m.sock"])
        self.ok("clash-api", "auto")
        self.assertEqual(self.saved_api()["address"], "unix:" + os.path.join(directory, "m.sock"))

    def test_config_file_and_secret_flag(self):
        directory = self.temp_dir()
        config = os.path.join(directory, "my config.yaml")
        self.write_file(config, "mixed-port: 7890\nexternal-controller: {}\nsecret: other\n".format(
            self.mihomo.address))
        self.processes(["/opt/clash-meta", "-f", config])
        self.ok("clash-api", "auto")
        self.assertEqual(self.saved_api()["address"], self.http(self.mihomo.address))
        # -secret 覆盖文件里的 secret；-v 输出里不得出现 secret 原文
        self.ok("clash-api", "off")
        self.mihomo.server.secret = "s3cretflag"
        self.processes(["/opt/clash-meta", "-f", config, "-secret", "s3cretflag"])
        result = self.ok("clash-api", "auto", "-v")
        self.assertNotIn("s3cretflag", result.out + result.err)
        self.assertEqual(self.saved_api()["secret"], "s3cretflag")
        self.assertIn("secret set", self.ok("clash-api").out)

    def test_first_dead_second_ok(self):
        dead = "127.0.0.1:{}".format(closed_port())
        self.processes(["/opt/mihomo", "-ext-ctl", dead], ["/opt/mihomo2", "-ext-ctl", self.mihomo.address])
        result = self.ok("clash-api", "auto", "-v")
        self.assertIn(dead, result.err)
        self.assertEqual(self.saved_api()["address"], self.http(self.mihomo.address))

    def test_not_found_and_dry_run(self):
        result = self.run_cli("clash-api", "auto")
        self.assertEqual(result.code, 1, result)
        self.assertIn("no working Clash", result.err)
        self.assertIsNone(self.saved_api())
        self.processes(["/opt/mihomo", "-ext-ctl", self.mihomo.address])
        result = self.ok("clash-api", "auto", "--dry-run")
        self.assertIn("found Clash controller", result.err)
        self.assertIsNone(self.saved_api())
        result = self.run_cli("clash-api", "auto", "--secret-stdin")
        self.assertEqual(result.code, 2, result)

    def test_ignored_candidates(self):
        directory = self.temp_dir()
        config = os.path.join(directory, "c.yaml")
        self.write_file(config, "external-controller: {}\n".format(self.mihomo.address))
        self.processes(["/usr/bin/tail", "-f", config],
                       ["/opt/mihomo", "-ext-ctl", "10.0.0.1:9090"],
                       ["/opt/mihomo", "-f", "rel.yaml", "-ext-ctl-unix", "rel.sock"])
        result = self.run_cli("clash-api", "auto", "-v")
        self.assertEqual(result.code, 1, result)
        self.assertIn("10.0.0.1:9090 (process mihomo): not a local address, skipped", result.err)
        self.assertNotIn("rel.yaml", result.err)  # 相对 -f 按进程当前目录解析，拿不到，不读
        if not IS_WINDOWS:
            self.assertIn("relative path and the home directory is unknown", result.err)
        self.assertNotIn(self.mihomo.address, result.err)

    @unittest.skipUnless(IS_WINDOWS or sys.platform == "darwin", "no known Clash Verge config path here")
    def test_known_config_file(self):
        if IS_WINDOWS:
            base = os.path.join(self.env["APPDATA"], "io.github.clash-verge-rev.clash-verge-rev")
        else:
            base = os.path.join(self.home, "Library", "Application Support", "io.github.clash-verge-rev.clash-verge-rev")
        self.mihomo.server.secret = "filesecret"
        lines = ['external-controller: "{}"  # 注释'.format(self.mihomo.address),
                 "profile:", "  external-controller: 127.0.0.1:{}".format(closed_port()),
                 "secret: filesecret"]
        if not IS_WINDOWS:
            lines.append("external-controller-unix: rel.sock")
        self.write_file(os.path.join(base, "config.yaml"), "\n".join(lines) + "\n")
        result = self.ok("clash-api", "auto", "-v")
        self.assertEqual(self.saved_api(), {"address": self.http(self.mihomo.address), "secret": "filesecret"})
        self.assertNotIn("filesecret", result.out + result.err)
        if not IS_WINDOWS:
            self.assertIn("unix:" + os.path.join(base, "rel.sock"), result.err)

    def test_secret_not_sendable(self):
        """secret 含中文时 http.client 编不进请求头：该候选记为失败，继续试下一个，不出 traceback。"""
        self.processes(["/opt/mihomo", "-ext-ctl", "127.0.0.1:{}".format(closed_port()), "-secret", "密码"],
                       ["/opt/mihomo2", "-ext-ctl", self.mihomo.address])
        result = self.ok("clash-api", "auto", "-v")
        self.assertIn("cannot be sent in an HTTP header", result.err)
        self.assertNotIn("Traceback", result.err)
        self.assertNotIn("密码", result.out + result.err)
        self.assertEqual(self.saved_api()["address"], self.http(self.mihomo.address))

    def test_netcheck_finds_when_unset(self):
        self.processes(["/opt/mihomo", "-ext-ctl", self.mihomo.address])
        self.ok("netcheck", "work", "basic")
        self.ok("proxy", "work", self.proxy.url)
        self.assertIsNone(self.saved_api())
        result = self.ok("netcheck", "work", "--clash-tun", "on")
        self.assertIn("found Clash controller", result.err)
        self.assertEqual(self.saved_api()["address"], self.http(self.mihomo.address))


class ProcessListTest(unittest.TestCase):
    """feature-clash-split §8-7、7a：ps 输出拆分与真实进程枚举。"""

    def test_split_ps_command(self):
        from multi_codex import platform
        line = ("/Library/Application Support/clash-verge-service/cores/verge-mihomo -d "
                "/Users/u/Library/Application Support/io.github.clash-verge-rev.clash-verge-rev -f "
                "/Users/u/Library/Application Support/io.github.clash-verge-rev.clash-verge-rev/clash-verge.yaml "
                "-ext-ctl-unix /var/run/clash-verge-service/users/501/verge-mihomo.sock")
        self.assertEqual(platform.split_ps_command("/a b/mihomo -d  /x  y -f /z"),
                         ["/a b/mihomo", "-d", "/x  y", "-f", "/z"])
        argv = platform.split_ps_command(line)
        self.assertEqual(argv, [
            "/Library/Application Support/clash-verge-service/cores/verge-mihomo",
            "-d", "/Users/u/Library/Application Support/io.github.clash-verge-rev.clash-verge-rev",
            "-f", "/Users/u/Library/Application Support/io.github.clash-verge-rev.clash-verge-rev/clash-verge.yaml",
            "-ext-ctl-unix", "/var/run/clash-verge-service/users/501/verge-mihomo.sock"])

    def test_real_process_list(self):
        import uuid
        from multi_codex import platform
        saved = os.environ.pop(platform.TEST_CLASH_PROCESSES_ENV, None)
        if saved is not None:
            self.addCleanup(os.environ.__setitem__, platform.TEST_CLASH_PROCESSES_ENV, saved)
        marker = "mc-marker-" + uuid.uuid4().hex
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", marker])
        self.addCleanup(child.wait)
        self.addCleanup(child.kill)
        result = platform.list_process_argv()
        self.assertTrue(any(marker in " ".join(argv) for argv in result))


if __name__ == "__main__":
    unittest.main()
