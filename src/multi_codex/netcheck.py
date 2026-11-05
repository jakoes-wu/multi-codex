"""启动前网络检查（docs/feature/feature-network-check.md）。

职责：按账号设置检查代理端口、出口国家与 Clash 状态，决定是否放行这一次 Codex 启动。
只读网络状态，不改任何配置；不缓存结果，每次启动重新检查。

局限（方案 §1）：只在启动那一刻检查一次。Codex 的 system_proxy_fallback 默认开启，代理在运行中断开后
它仍可能直连；桌面版与 VS Code 插件不经过启动命令。本模块不能、也不声称能防止这些情况。

只用标准库：HTTP 用 http.client（它不读环境变量代理，直连请求因此真的不走代理），SOCKS5 握手自行实现。
"""

import http.client
import io
import json
import os
import socket
import ssl
import sys
import threading
import time
import urllib.parse
from typing import List, NamedTuple, Optional, Tuple

from . import __version__, platform
from .config import NETCHECK_SKIP_ENV, PIPE_PREFIX, Account, Config

TRACE_HOST = "chatgpt.com"
TRACE_PORT = 443
TRACE_PATH = "/cdn-cgi/trace"
# 单位：秒。端口与 Clash 都在本机，3 s 足够；trace 要出国际网络，单次连接 / 读写给 8 s。
PORT_TIMEOUT_SEC = 3
TRACE_TIMEOUT_SEC = 8
CLASH_TIMEOUT_SEC = 3
# trace 响应正常不到 1 KB；读满上限仍找不到 loc 就判失败，防止对方返回大文件。
MAX_RESPONSE_BYTES = 64 * 1024

# 跳过变量：值为 "1" 时本次跳过并提示；"quiet" 只由 `usage --live` 注入给子进程，静默跳过。
SKIP_ENV = NETCHECK_SKIP_ENV
SKIP_PRINT = "1"
SKIP_QUIET = "quiet"
# 测试注入（只在测试里设置）：trace 目标改为本地假服务器 HOST:PORT；TLS 校验改用指定的 CA 文件。
TEST_TRACE_ENV = "MULTI_CODEX_TEST_TRACE"
TEST_CA_ENV = "MULTI_CODEX_TEST_CA"

ITEM_CLASH = "clash"
ITEM_PORT = "proxy-port"
ITEM_DIRECT = "direct-country"
ITEM_PROXY = "proxy-country"


class CheckResult(NamedTuple):
    """一项检查的结果；detail 是给人看的说明（失败时含错误分类），不含 Clash secret。"""

    item: str
    ok: bool
    detail: str
    elapsed_ms: int


class CheckFailure(Exception):
    """检查项未通过；消息即对外的失败说明。"""


def skip_mode() -> Optional[str]:
    """读取跳过变量，返回 SKIP_PRINT / SKIP_QUIET / None。

    先 strip()：cmd 的 `set X=1 && …` 会把 && 前的空格算进值里。`gate` 与 `usage --live` 共用这一个口径。
    """
    value = os.environ.get(SKIP_ENV, "").strip()
    return value if value in (SKIP_PRINT, SKIP_QUIET) else None


def scope_text(account: Account) -> str:
    """提示行括号里的检查范围：basic / `strict, 国家`，开了 Clash 再加 `, clash`；只开 Clash 时写 `clash`。"""
    check = account.net_check
    parts = []
    if check.level == "basic":
        parts.append("basic")
    elif check.level == "strict":
        parts.extend(["strict", check.country])
    if check.clash:
        parts.append("clash")
    return ", ".join(parts)


def run_checks(config: Config, account: Account, stop_on_failure: bool) -> List[CheckResult]:
    """按 clash → proxy-port → direct-country → proxy-country 的顺序检查账号开启的项。

    stop_on_failure=True 用于放行判断：第一项失败就停，失败时尽快拒绝启动；
    `netcheck NAME` 诊断用 False，逐项列出全部结果。不重试：这是一次性的放行判断，重试只会拖慢启动。
    """
    check = account.net_check
    steps = []
    if check.clash:
        steps.append((ITEM_CLASH, lambda: _check_clash(config)))
    if check.level in ("basic", "strict"):
        steps.append((ITEM_PORT, lambda: _check_port(account.proxy)))
    if check.level == "strict":
        steps.append((ITEM_DIRECT, lambda: _check_country(None, check.country)))
        steps.append((ITEM_PROXY, lambda: _check_country(account.proxy, check.country)))
    results = []
    for item, step in steps:
        start = time.monotonic()
        try:
            detail = step()
            ok = True
        except CheckFailure as exc:
            detail = str(exc)
            ok = False
        results.append(CheckResult(item, ok, detail, int((time.monotonic() - start) * 1000)))
        if not ok and stop_on_failure:
            break
    return results


def gate(config: Config, account: Account, hint: str) -> Optional[str]:
    """启动前的放行判断：返回 None 表示放行，否则返回拒绝说明（多行，调用方打印到 stderr 并退出 5）。

    未开启检查 → 直接放行，不读跳过变量。跳过变量为 1 / quiet → 放行（1 时提示一行），并从 os.environ
    删掉它：在 Python 里启动 Codex 的路径（run / code / Windows 启动命令）不让 Codex 继承“跳过”。
    通过时向 stderr 打一行“passed”（用户 2026-11-04 拍板）。hint 是用户本次运行的命令，
    用于拼出“本次跳过”的写法。KeyboardInterrupt 不在这里处理，由调用方转成退出码 130。
    """
    if not account.net_check.active:
        return None
    mode = skip_mode()
    if mode is not None:
        os.environ.pop(SKIP_ENV, None)
        if mode == SKIP_PRINT:
            _print_stderr("multi-codex: network check skipped for this run ({}=1)".format(SKIP_ENV))
        return None
    start = time.monotonic()
    results = run_checks(config, account, stop_on_failure=True)
    failed = [result for result in results if not result.ok]
    if not failed:
        _print_stderr("multi-codex: network check passed for account {} ({}; {} ms)".format(
            account.name, scope_text(account), int((time.monotonic() - start) * 1000)))
        return None
    return failure_text(account, results, hint)


def failure_text(account: Account, results: List[CheckResult], hint: str) -> str:
    """拼出检查不通过时给用户看的多行说明：第一行写账号、档位和第一个失败项及原因，
    之后依次是已通过的项、本次跳过的写法（Windows 给 PowerShell 与 cmd 两种）和关闭检查的命令。
    results 里至少要有一个失败项。"""
    failed = [result for result in results if not result.ok][0]
    lines = ["multi-codex: network check failed for account {} ({}): {}: {} ({} ms)".format(
        account.name, scope_text(account), failed.item, failed.detail, failed.elapsed_ms)]
    passed = [result.item for result in results if result.ok]
    if passed:
        lines.append("  passed: " + ", ".join(passed))
    if platform.IS_WINDOWS:
        lines.append("  skip once (PowerShell): try {{ $env:{0}=1; {1} }} finally {{ Remove-Item Env:{0} }}".format(
            SKIP_ENV, hint))
        lines.append('  skip once (cmd):        cmd /c "set {}=1&& {}"'.format(SKIP_ENV, hint))
    else:
        lines.append("  skip once: {}=1 {}".format(SKIP_ENV, hint))
    turn_off = "multi-codex netcheck {} off".format(account.name)
    if account.net_check.clash:
        turn_off += " --clash off"
    lines.append("  turn off:  " + turn_off)
    return "\n".join(lines)


def _print_stderr(message: str) -> None:
    """提示写 stderr：stdout 可能被调用方当作 Codex 的输出读取（如 `codex-work exec … > out`）。"""
    sys.stderr.write(message + "\n")
    sys.stderr.flush()


# ---------------------------------------------------------------- 各检查项


def _proxy_endpoint(proxy: str) -> Tuple[str, str, int]:
    """账号代理 URL → (scheme, host, port)；host 去掉 IPv6 的方括号。代理为 inherit / off 时判失败。"""
    if "://" not in proxy:
        raise CheckFailure("account proxy is {}; set one with `multi-codex proxy NAME PORT` "
                           "or turn the check off".format(proxy))
    parts = urllib.parse.urlsplit(proxy)
    # 配置里的代理已由 normalize_proxy 校验过，hostname / port 一定存在。
    return parts.scheme, parts.hostname or "", parts.port or 0


def _check_port(proxy: str) -> str:
    _, host, port = _proxy_endpoint(proxy)
    try:
        sock = socket.create_connection((host, port), PORT_TIMEOUT_SEC)
    except OSError as exc:
        raise CheckFailure("{} not reachable: {}".format(proxy, _classify(exc)))
    sock.close()
    return "{} reachable".format(proxy)


def _trace_target() -> Tuple[str, int]:
    override = os.environ.get(TEST_TRACE_ENV)
    if override:
        host, _, port = override.rpartition(":")
        return host, int(port)
    return TRACE_HOST, TRACE_PORT


def _tls_context() -> ssl.SSLContext:
    cafile = os.environ.get(TEST_CA_ENV) or None
    return ssl.create_default_context(cafile=cafile)


def _check_country(proxy: Optional[str], country: str) -> str:
    """访问 trace 地址，要求 loc 等于 country。proxy 为 None 时直连（不读任何代理设置）。"""
    host, port = _trace_target()
    context = _tls_context()
    via = "direct"
    if proxy is None:
        conn = http.client.HTTPSConnection(host, port, timeout=TRACE_TIMEOUT_SEC, context=context)
    else:
        scheme, proxy_host, proxy_port = _proxy_endpoint(proxy)
        via = "via " + proxy
        if scheme == "http":
            # CONNECT 隧道：TLS 按隧道主机名握手，证书校验针对 chatgpt.com 而不是代理。
            conn = http.client.HTTPSConnection(proxy_host, proxy_port, timeout=TRACE_TIMEOUT_SEC, context=context)
            conn.set_tunnel(host, port)
        elif scheme in ("socks5", "socks5h"):
            conn = _Socks5HTTPSConnection(proxy_host, proxy_port, host, port, TRACE_TIMEOUT_SEC, context)
        else:
            # https:// 代理要 TLS 套 TLS，标准库做不到；设置时已拒绝，这里兜住手改的配置。
            raise CheckFailure("{} proxies are not supported by the strict check; use http:// or socks5://".format(
                scheme))
    try:
        try:
            conn.request("GET", TRACE_PATH, headers={
                "User-Agent": "multi-codex/" + __version__, "Connection": "close"})
            response = conn.getresponse()
            body = response.read(MAX_RESPONSE_BYTES)
        except (OSError, http.client.HTTPException) as exc:
            raise CheckFailure("{} ({}): {}".format(TRACE_PATH, via, _classify(exc)))
    finally:
        conn.close()
    if response.status != 200:
        raise CheckFailure("{} ({}) returned HTTP {}".format(TRACE_PATH, via, response.status))
    loc = parse_loc(body)
    if loc is None:
        raise CheckFailure("{} ({}) has no loc= line".format(TRACE_PATH, via))
    if loc.upper() != country.upper():
        raise CheckFailure("loc is {}, expected {} ({})".format(loc, country, via))
    return "loc={} ({})".format(loc, via)


def parse_loc(body: bytes) -> Optional[str]:
    """从 trace 响应（逐行 key=value）里取 loc 的值。"""
    for line in body.decode("utf-8", "replace").splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() == "loc":
            return value.strip()
    return None


class _Socks5HTTPSConnection(http.client.HTTPSConnection):
    """经 SOCKS5 代理（无认证）连到目标后再做 TLS。

    以域名形式（ATYP=3）请求，由代理解析域名：socks5 与 socks5h 都这样处理，只影响 DNS 在哪里解析，
    不影响出口 IP，loc 判定不变。
    """

    def __init__(self, proxy_host: str, proxy_port: int, host: str, port: int, timeout: float,
                 context: ssl.SSLContext) -> None:
        super().__init__(host, port, timeout=timeout, context=context)
        self._proxy_address = (proxy_host, proxy_port)
        self._tls_context = context

    def connect(self) -> None:
        sock = socket.create_connection(self._proxy_address, self.timeout)
        try:
            socks5_connect(sock, self.host, self.port)
            self.sock = self._tls_context.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise


class Socks5Error(OSError):
    """SOCKS5 握手失败（含 REP 码）。"""


def socks5_connect(sock: socket.socket, host: str, port: int) -> None:
    sock.sendall(b"\x05\x01\x00")
    reply = _recv_exact(sock, 2)
    if reply != b"\x05\x00":
        raise Socks5Error("SOCKS5 proxy refused no-auth method (reply {!r})".format(reply))
    name = host.encode("idna")
    sock.sendall(b"\x05\x01\x00\x03" + bytes([len(name)]) + name + port.to_bytes(2, "big"))
    head = _recv_exact(sock, 4)
    if head[0] != 5:
        raise Socks5Error("not a SOCKS5 reply")
    if head[1] != 0:
        raise Socks5Error("SOCKS5 CONNECT failed with REP={}".format(head[1]))
    # 读掉代理回报的绑定地址，之后才是目标的数据流。
    if head[3] == 1:
        _recv_exact(sock, 4 + 2)
    elif head[3] == 3:
        _recv_exact(sock, _recv_exact(sock, 1)[0] + 2)
    elif head[3] == 4:
        _recv_exact(sock, 16 + 2)
    else:
        raise Socks5Error("SOCKS5 reply has unknown address type {}".format(head[3]))


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    data = b""
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise Socks5Error("SOCKS5 proxy closed the connection")
        data += chunk
    return data


def _classify(exc: BaseException) -> str:
    """把底层异常转成带分类的说明（编码规范 §11.3）；子类在前，ssl / timeout 都是 OSError 的子类。"""
    if isinstance(exc, socket.gaierror):
        return "DNS lookup failed ({})".format(exc)
    if isinstance(exc, ssl.SSLCertVerificationError):
        return ("TLS certificate verification failed ({}); Python may not find the system CA "
                "certificates".format(exc.verify_message or exc))
    if isinstance(exc, ssl.SSLError):
        return "TLS error ({})".format(exc)
    if isinstance(exc, socket.timeout):
        return "timed out"
    if isinstance(exc, ConnectionRefusedError):
        return "connection refused"
    if isinstance(exc, Socks5Error):
        return str(exc)
    if isinstance(exc, OSError) and str(exc).startswith("Tunnel connection failed"):
        return "proxy refused CONNECT ({})".format(exc)
    if isinstance(exc, http.client.HTTPException):
        return "bad HTTP response ({})".format(exc.__class__.__name__)
    return "{} ({})".format(exc.__class__.__name__, exc)


# ---------------------------------------------------------------- Clash / mihomo


def _check_clash(config: Config) -> str:
    """GET /configs，要求 mode=global 且 tun.enable=true（mihomo v1.19.32 hub/route/configs.go）。

    TUN 启动失败时 mihomo 会把 tun.enable 写回 false（listener/listener.go），所以它反映的是实际状态。
    只读，不修改 Clash 的任何设置。
    """
    api = config.clash_api
    if api is None:
        raise CheckFailure("Clash controller is not set; run `multi-codex clash-api ADDRESS`")
    try:
        status, body = _clash_get(api.address, api.secret)
    except CheckFailure:
        raise
    except (OSError, http.client.HTTPException) as exc:
        raise CheckFailure("Clash controller {}: {}".format(api.address, _classify(exc)))
    if status == 401:
        raise CheckFailure("Clash controller {} rejected the request (401): secret missing or wrong; "
                           "set it with `multi-codex clash-api ADDRESS --secret-stdin`".format(api.address))
    if status != 200:
        raise CheckFailure("Clash controller {} returned HTTP {}".format(api.address, status))
    try:
        data = json.loads(body.decode("utf-8"))
    except ValueError:
        data = None
    mode = data.get("mode") if isinstance(data, dict) else None
    tun = data.get("tun") if isinstance(data, dict) else None
    if not isinstance(mode, str) or not isinstance(tun, dict) or "enable" not in tun:
        raise CheckFailure("Clash controller {} gave an unexpected response (no mode / tun.enable); "
                           "is it a Clash / mihomo controller?".format(api.address))
    if mode.lower() != "global":
        raise CheckFailure("Clash mode is {}, expected global".format(mode))
    if tun.get("enable") is not True:
        raise CheckFailure("Clash TUN is off")
    return "mode=global, tun on"


def _clash_get(address: str, secret: str) -> Tuple[int, bytes]:
    headers = {"Connection": "close", "User-Agent": "multi-codex/" + __version__}
    if address.lower().startswith(PIPE_PREFIX):  # 规范化时按不区分大小写接受，原样保存
        return _pipe_get(address, headers)
    if address.startswith("unix:"):
        conn = _UnixHTTPConnection(address[len("unix:"):], CLASH_TIMEOUT_SEC)  # type: http.client.HTTPConnection
    else:
        parts = urllib.parse.urlsplit(address)
        conn = http.client.HTTPConnection(parts.hostname or "", parts.port, timeout=CLASH_TIMEOUT_SEC)
        # mihomo 只在 TCP 接口上校验 secret；unix 套接字与命名管道不鉴权，不发送。
        if secret:
            headers["Authorization"] = "Bearer " + secret
    try:
        conn.request("GET", "/configs", headers=headers)
        response = conn.getresponse()
        return response.status, response.read(MAX_RESPONSE_BYTES)
    finally:
        conn.close()


class _UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float) -> None:
        super().__init__("localhost", timeout=timeout)
        self._path = path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect(self._path)
        except BaseException:
            sock.close()
            raise
        self.sock = sock


class _BufferSocket(object):
    """让 http.client.HTTPResponse 解析已读到内存里的响应：它只调用 makefile("rb")。"""

    def __init__(self, data: bytes) -> None:
        self._data = data

    def makefile(self, *args: object, **kwargs: object) -> io.BytesIO:
        return io.BytesIO(self._data)


def _parse_raw_response(data: bytes) -> Tuple[int, bytes]:
    """把已读全的原始 HTTP 响应字节交给 http.client 解析，返回（状态码，正文）；
    带 Content-Length 与分块编码两种回包都能处理。命名管道没有 socket 可读，所以先读全再解析。"""
    response = http.client.HTTPResponse(_BufferSocket(data))  # type: ignore
    response.begin()
    return response.status, response.read()


def _pipe_get(path: str, headers: dict) -> Tuple[int, bytes]:
    """经 Windows 命名管道发 GET /configs，读到 EOF 后再解析。

    请求带 Connection: close，mihomo 回完就关管道；读到空字节或 BrokenPipeError 都按读完处理。
    管道句柄的读写没有超时参数，所以放在守护线程里做，主线程最多等 CLASH_TIMEOUT_SEC；
    超时后线程仍可能阻塞在读上，它是守护线程，不阻止进程退出。
    """
    outcome = {}  # type: dict

    def worker() -> None:
        try:
            with open(path, "r+b", buffering=0) as handle:
                # 用 HTTP/1.0：服务端不会回分块编码，读到 EOF 即整份响应。
                request = "GET /configs HTTP/1.0\r\nHost: localhost\r\n" + "".join(
                    "{}: {}\r\n".format(key, value) for key, value in headers.items()) + "\r\n"
                handle.write(request.encode("ascii"))
                data = b""
                while len(data) < MAX_RESPONSE_BYTES:
                    try:
                        chunk = handle.read(MAX_RESPONSE_BYTES - len(data))
                    except BrokenPipeError:
                        break
                    if not chunk:
                        break
                    data += chunk
            outcome["result"] = _parse_raw_response(data)
        except BaseException as exc:  # 交回主线程分类
            outcome["error"] = exc

    thread = threading.Thread(target=worker, name="netcheck-pipe", daemon=True)
    thread.start()
    thread.join(CLASH_TIMEOUT_SEC)
    if thread.is_alive():
        raise CheckFailure("Clash controller {}: timed out".format(path))
    if "error" in outcome:
        error = outcome["error"]
        if isinstance(error, (OSError, http.client.HTTPException)):
            raise error
        raise CheckFailure("Clash controller {}: {}".format(path, error))
    return outcome["result"]
