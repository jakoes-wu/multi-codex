"""额度读取：默认读本地会话记录里最近一次的快照，`--live` 时经官方 codex app-server 联网读取。

两条通道的权威性不同：
- 本地快照是 Codex 上次运行时记下的，不联网，可能已经过时（窗口可能已经重置）；
- 实时结果由官方客户端按它自己的协议向后端查询，我们不接触令牌，也不直接调用后端接口。
所以 `--live` 失败时如实报错，不悄悄退回本地快照（feature-account-insight §6）。

数据格式以 openai/codex 源码为准（基线 6b4daafd），出处见 feature-account-insight §1。
"""

import calendar
import glob
import json
import os
import queue
import signal
import subprocess
import tempfile
import threading
import time
from datetime import datetime
from typing import Any, Dict, Iterator, List, NamedTuple, Optional, Tuple

from . import __version__

SOURCE_LOCAL = "local"
SOURCE_LIVE = "live"

# 本地扫描上限：按修改时间从新到旧最多看这么多个会话文件，读到第一个含快照的文件就停。
MAX_SCAN_FILES = 20
# 反向分块读取的块大小，单位字节。
CHUNK_BYTES = 64 * 1024
# serde 输出的紧凑 JSON 中，非 null 的额度字段一定以这个片段出现；先按字节过滤，避免逐行解析整个文件。
_RATE_LIMITS_MARK = b'"rate_limits":{'
DEFAULT_LIMIT_ID = "codex"

DEFAULT_LIVE_TIMEOUT_SEC = 30.0
# 正常拿到响应后，关闭 stdin 等 app-server 自行退出的时间，单位秒。
_GRACEFUL_EXIT_SEC = 5.0
# 对进程组发 SIGTERM 后，等多久再发 SIGKILL，单位秒。
_TERM_GRACE_SEC = 2.0
_STDERR_TAIL_LINES = 20
_MESSAGE_LIMIT = 300

# 未知方法在旧版本里是 -32601；当前版本反序列化失败统一返回 -32600，信息里带 serde 的 unknown variant。
_METHOD_NOT_FOUND = -32601
_INVALID_REQUEST = -32600


class Window(NamedTuple):
    kind: str                       # primary / secondary
    used_percent: float             # 0-100
    window_minutes: Optional[int]   # 窗口长度，单位分钟
    resets_at: Optional[int]        # 窗口重置时刻，epoch 秒


class Limit(NamedTuple):
    limit_id: str
    plan_type: Optional[str]
    windows: List[Window]
    credits: Optional[dict]


class UsageResult(NamedTuple):
    name: str
    source: str
    ok: bool
    error: Optional[str]
    snapshot_time: Optional[float]  # 本地：快照所在行的时间；实时：查询时刻。epoch 秒
    sessions_shared: bool
    limits: List[Limit]


class LiveError(Exception):
    """实时查询失败；信息会直接显示给用户，不含令牌（app-server 的响应与 stderr 都不含令牌）。"""


# ---- 快照解析（本地与实时共用） ----

def _pick(data: dict, *keys: str) -> Any:
    for key in keys:
        if key in data:
            return data[key]
    return None


def _parse_window(kind: str, raw: object) -> Optional[Window]:
    if not isinstance(raw, dict):
        return None
    # 本地记录与 0.48.0 的 app-server 用蛇形命名，当前 app-server 用驼峰命名，两种都接受。
    used = _pick(raw, "used_percent", "usedPercent")
    if isinstance(used, bool) or not isinstance(used, (int, float)):
        return None
    minutes = _pick(raw, "window_minutes", "windowDurationMins")
    resets = _pick(raw, "resets_at", "resetsAt")
    return Window(kind, float(used),
                  minutes if isinstance(minutes, int) and not isinstance(minutes, bool) else None,
                  resets if isinstance(resets, int) and not isinstance(resets, bool) else None)


def parse_limit(raw: dict, limit_id: Optional[str] = None) -> Limit:
    windows = [window for window in (_parse_window("primary", raw.get("primary")),
                                     _parse_window("secondary", raw.get("secondary"))) if window]
    plan = _pick(raw, "plan_type", "planType")
    credits = raw.get("credits")
    return Limit(limit_id or _pick(raw, "limit_id", "limitId") or DEFAULT_LIMIT_ID,
                 plan if isinstance(plan, str) else None, windows,
                 credits if isinstance(credits, dict) else None)


# ---- 本地快照 ----

def _candidate_files(account_dir: str) -> List[str]:
    patterns = [os.path.join(account_dir, "sessions", "*", "*", "*", "rollout-*.jsonl"),
                os.path.join(account_dir, "archived_sessions", "rollout-*.jsonl")]
    stamped = []
    for pattern in patterns:
        for path in glob.glob(pattern):
            try:
                stamped.append((os.stat(path).st_mtime, path))
            except OSError:
                continue
    stamped.sort(reverse=True)
    return [path for _, path in stamped[:MAX_SCAN_FILES]]


def _reverse_lines(path: str) -> Iterator[bytes]:
    """从文件末尾向前逐行产出（不含行尾的 \n 和 \r）。会话文件可能很大，不整个读进内存。"""
    with open(path, "rb") as handle:
        handle.seek(0, os.SEEK_END)
        position = handle.tell()
        pending = b""
        while position > 0:
            step = min(CHUNK_BYTES, position)
            position -= step
            handle.seek(position)
            pending = handle.read(step) + pending
            lines = pending.split(b"\n")
            # 第一段可能是被块边界截断的半行，留到下一轮与更前面的内容拼起来。
            pending = lines[0]
            for line in reversed(lines[1:]):
                line = line.rstrip(b"\r")
                if line:
                    yield line
        pending = pending.rstrip(b"\r")
        if pending:
            yield pending


def _parse_timestamp(value: object) -> Optional[float]:
    # 记录格式为 YYYY-MM-DDTHH:MM:SS.mmmZ（UTC）；Python 3.8 的 fromisoformat 不接受末尾的 Z。
    if not isinstance(value, str):
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return float(calendar.timegm(time.strptime(value, fmt)))
        except ValueError:
            continue
    return None


def _snapshot_in_file(path: str) -> Tuple[Optional[float], List[Limit]]:
    """在一个文件里按 limit_id 各取最新的一条快照；返回 (最新一条的时间, 快照列表)。"""
    found: Dict[str, Limit] = {}
    newest: Optional[float] = None
    try:
        for line in _reverse_lines(path):
            if _RATE_LIMITS_MARK not in line:
                continue
            try:
                record = json.loads(line.decode("utf-8"))
            except (ValueError, UnicodeError):
                continue
            if not isinstance(record, dict):
                continue
            payload = record.get("payload")
            if record.get("type") != "event_msg" or not isinstance(payload, dict) \
                    or payload.get("type") != "token_count" or not isinstance(payload.get("rate_limits"), dict):
                continue
            limit = parse_limit(payload["rate_limits"])
            if limit.limit_id in found:
                continue
            found[limit.limit_id] = limit
            if newest is None:
                newest = _parse_timestamp(record.get("timestamp"))
                if newest is None:
                    newest = os.stat(path).st_mtime
    except OSError:
        return None, []
    return newest, [found[key] for key in sorted(found)]


def local_snapshot(name: str, account_dir: str) -> UsageResult:
    """读一个账号最近一次的本地额度快照。没有任何快照时 ok 仍为 True、limits 为空（“暂无数据”）。"""
    shared = os.path.islink(os.path.join(account_dir, "sessions"))
    for path in _candidate_files(account_dir):
        snapshot_time, limits = _snapshot_in_file(path)
        if limits:
            return UsageResult(name, SOURCE_LOCAL, True, None, snapshot_time, shared, limits)
    return UsageResult(name, SOURCE_LOCAL, True, None, None, shared, [])


def last_used(account_dir: str) -> Optional[float]:
    """账号最近一次使用的时间（epoch 秒）：最新会话文件与 history.jsonl 的最大 mtime；都没有时为 None。

    只 stat、不读内容（feature-last-used §5.1.1）。history.jsonl 是软链时不计入：它可能被多个账号共享，
    软链目标的 mtime 反映的是任意一个账号的使用。sessions 是软链时会话文件同样可能来自别的账号，
    由调用方按 sessions 是否为软链加标记（与额度快照的 sessions_shared 判定相同）。
    """
    times = []
    # 已按 mtime 从新到旧排序：取第一个还能 stat 的。枚举之后最新的文件被删时，退到下一个，而不是直接放弃。
    for path in _candidate_files(account_dir):
        try:
            times.append(os.stat(path).st_mtime)
            break
        except OSError:
            continue
    history = os.path.join(account_dir, "history.jsonl")
    if not os.path.islink(history):
        try:
            times.append(os.stat(history).st_mtime)
        except OSError:
            pass
    return max(times) if times else None


# ---- 实时额度 ----

class _AppServer(object):
    """一次性的 app-server 会话：启动、按行收发 JSON、在任何路径上回收整个进程组。

    必须用新的会话（进程组）启动：npm 安装的 codex 是 node 包装器，再拉起真正的程序，
    包装器只转发 SIGINT / SIGTERM / SIGHUP（上游 codex-cli/bin/codex.js:241,259-271）。
    只对包装器发 SIGKILL，真正的 app-server 会变成孤儿，只能等它自己 45 秒的看门狗退出。
    """

    def __init__(self, launcher: str, timeout_sec: float) -> None:
        self.deadline = time.monotonic() + timeout_sec
        self.timeout_sec = timeout_sec
        self.stderr_file = tempfile.TemporaryFile()
        try:
            self.proc = subprocess.Popen([launcher, "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=self.stderr_file, start_new_session=True)
        except OSError:
            # 构造失败时调用方拿不到对象、不会调用 close()，临时文件在这里关掉。
            self.stderr_file.close()
            raise
        self.lines: "queue.Queue[Optional[bytes]]" = queue.Queue()
        self.reader = threading.Thread(target=self._read_stdout, daemon=True)
        self.reader.start()

    def _read_stdout(self) -> None:
        try:
            for raw in self.proc.stdout:
                self.lines.put(raw)
        except (OSError, ValueError):
            pass
        self.lines.put(None)

    def send(self, message: dict) -> None:
        # 协议不是真正的 JSON-RPC 2.0：不带 "jsonrpc" 字段（上游 app-server-protocol/src/rpc.rs:1-2）。
        try:
            self.proc.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError):
            raise LiveError(self._exited_message())

    def response(self, request_id: int) -> dict:
        """等待指定 id 的响应；通知和服务端发来的请求都跳过。"""
        while True:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise LiveError("no response from app-server within {:g}s".format(self.timeout_sec))
            try:
                raw = self.lines.get(timeout=remaining)
            except queue.Empty:
                raise LiveError("no response from app-server within {:g}s".format(self.timeout_sec))
            if raw is None:
                raise LiveError(self._exited_message())
            try:
                message = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeError):
                raise LiveError("invalid response from app-server: {}".format(
                    raw.decode("utf-8", "replace").strip()[:_MESSAGE_LIMIT]))
            # 服务端发给客户端的请求也带 id，只有带 result / error 且不带 method 的才是响应。
            if isinstance(message, dict) and message.get("id") == request_id and "method" not in message \
                    and ("result" in message or "error" in message):
                return message

    def _exited_message(self) -> str:
        code = self.proc.poll()
        text = "app-server exited before responding" + (" (exit code {})".format(code) if code is not None else "")
        tail = self.stderr_tail()
        return text + (": " + tail if tail else "")

    def stderr_tail(self) -> str:
        try:
            self.stderr_file.seek(0)
            lines = self.stderr_file.read().decode("utf-8", "replace").strip().splitlines()
        except OSError:
            return ""
        return " | ".join(line.strip() for line in lines[-_STDERR_TAIL_LINES:] if line.strip())[:_MESSAGE_LIMIT * 2]

    def close(self, graceful: bool) -> None:
        """回收子进程。正常结束时先关 stdin 让它自行退出；超时或出错时直接结束整个进程组。"""
        try:
            if graceful:
                self._close_stdin()
                try:
                    self.proc.wait(timeout=_GRACEFUL_EXIT_SEC)
                except subprocess.TimeoutExpired:
                    graceful = False
            if not graceful:
                self._signal_group(signal.SIGTERM)
                self._close_stdin()
                give_up_at = time.monotonic() + _TERM_GRACE_SEC
                while time.monotonic() < give_up_at and self._group_alive():
                    time.sleep(0.05)
            # 包装器退出不代表它拉起的子进程也退出了；无论如何对整个进程组补一次 SIGKILL。
            self._signal_group(signal.SIGKILL)
            try:
                self.proc.wait(timeout=_GRACEFUL_EXIT_SEC)
            except subprocess.TimeoutExpired:
                # 只有进程组成员换了 uid、SIGKILL 被拒时才会走到这里；不能让 usage 因此挂住。
                pass
        finally:
            for stream in (self.proc.stdout, self.stderr_file):
                try:
                    stream.close()
                except OSError:
                    pass
            self.reader.join(timeout=1)

    def _close_stdin(self) -> None:
        try:
            self.proc.stdin.close()
        except OSError:
            pass

    def _signal_group(self, sig: int) -> None:
        try:
            os.killpg(self.proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            pass

    def _group_alive(self) -> bool:
        self.proc.poll()  # 回收已退出的组长，否则僵尸进程会让进程组看起来仍然存在
        try:
            os.killpg(self.proc.pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True


def _rpc_error(message: dict, what: str) -> Optional[LiveError]:
    error = message.get("error")
    if error is None:
        return None
    code = error.get("code") if isinstance(error, dict) else None
    text = str(error.get("message", "")) if isinstance(error, dict) else str(error)
    if code == _METHOD_NOT_FOUND or (code == _INVALID_REQUEST and "unknown variant" in text):
        return LiveError("{} is not supported by this Codex version; Codex 0.48.0 or newer is required".format(what))
    return LiveError("app-server error {}: {}".format(code, text[:_MESSAGE_LIMIT]))


def live_snapshot(name: str, launcher: str, timeout_sec: float) -> UsageResult:
    """通过该账号的启动命令运行 `codex app-server`，调用 account/rateLimits/read。

    走启动命令是为了沿用该账号的 CODEX_HOME 和代理设置。查询会联网；Codex 可能顺带刷新令牌并写回
    该账号自己的凭据存储，这与用户自己启动一次 Codex 相同，不会写到其它账号。
    """
    server = None
    graceful = False
    try:
        try:
            server = _AppServer(launcher, timeout_sec)
        except OSError as exc:
            raise LiveError("cannot start {} app-server: {}".format(launcher, exc))
        server.send({"id": 1, "method": "initialize",
                     "params": {"clientInfo": {"name": "multi-codex", "title": "multi-codex",
                                               "version": __version__}}})
        failure = _rpc_error(server.response(1), "initialize")
        if failure:
            raise failure
        server.send({"method": "initialized"})
        server.send({"id": 2, "method": "account/rateLimits/read"})
        reply = server.response(2)
        failure = _rpc_error(reply, "account/rateLimits/read")
        if failure:
            raise failure
        result = reply.get("result")
        limits = _limits_from_result(result if isinstance(result, dict) else {})
        graceful = True
        return UsageResult(name, SOURCE_LIVE, True, None, time.time(), False, limits)
    except LiveError as exc:
        return UsageResult(name, SOURCE_LIVE, False, str(exc), None, False, [])
    finally:
        if server is not None:
            server.close(graceful)


def _limits_from_result(result: dict) -> List[Limit]:
    by_id = result.get("rateLimitsByLimitId")
    if isinstance(by_id, dict) and by_id:
        # HashMap 序列化后的顺序不稳定，按名字排序保证输出可比较。
        return [parse_limit(by_id[key], key) for key in sorted(by_id) if isinstance(by_id[key], dict)]
    snapshot = result.get("rateLimits")
    if not isinstance(snapshot, dict):
        raise LiveError("unexpected response from account/rateLimits/read: no rateLimits field")
    return [parse_limit(snapshot)]


# ---- 输出 ----

def _window_label(minutes: Optional[int]) -> str:
    if minutes is None:
        return "?"
    if minutes < 1440:
        return "{:g}h".format(minutes / 60.0)
    return "{:g}d".format(round(minutes / 1440.0, 1))


def _human_duration(seconds: float) -> str:
    seconds = int(max(0, seconds))
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return "{}d{}h".format(days, hours) if hours and days < 3 else "{}d".format(days)
    if hours:
        return "{}h{}m".format(hours, minutes) if minutes else "{}h".format(hours)
    return "{}m".format(minutes)


def _local_time(epoch: float, now: float) -> str:
    moment = datetime.fromtimestamp(epoch)
    if moment.date() == datetime.fromtimestamp(now).date():
        return moment.strftime("%H:%M")
    return moment.strftime("%m-%d %H:%M")


def format_result(result: UsageResult, now: float) -> List[str]:
    """给人看的输出；格式不承诺稳定，脚本请用 --json。"""
    if not result.ok:
        return ["{}  error: {}".format(result.name, result.error)]
    parts = []
    plan = next((limit.plan_type for limit in result.limits if limit.plan_type), None)
    if plan:
        parts.append(plan)
    if result.source == SOURCE_LIVE:
        parts.append("live")
    elif result.snapshot_time is not None:
        parts.append("snapshot {}, {} ago".format(datetime.fromtimestamp(result.snapshot_time).strftime(
            "%Y-%m-%d %H:%M"), _human_duration(now - result.snapshot_time)))
    lines = ["{}  ({})".format(result.name, ", ".join(parts)) if parts else result.name]
    if result.sessions_shared:
        lines.append("  note: sessions is a link shared with other accounts; this usage may belong to another account")
    if not result.limits:
        lines.append("  no usage data in local session logs")
        return lines
    width = max(len(limit.limit_id) for limit in result.limits)
    for limit in result.limits:
        if not limit.windows:
            lines.append("  {}  (no window data)".format(limit.limit_id.ljust(width)))
        for index, window in enumerate(limit.windows):
            label = limit.limit_id if index == 0 else ""
            head = "  {}  {:>4}".format(label.ljust(width), _window_label(window.window_minutes))
            if window.resets_at is not None and window.resets_at <= now and result.source == SOURCE_LOCAL:
                # 快照之后窗口已经重置，旧百分比不再代表当前用量。
                lines.append("{}  reset since snapshot".format(head))
                continue
            line = "{}  {:>3.0f}% used".format(head, window.used_percent)
            if window.resets_at is not None:
                line += "  resets {} (in {})".format(_local_time(window.resets_at, now),
                                                      _human_duration(window.resets_at - now))
            lines.append(line)
    return lines


def to_json(result: UsageResult) -> dict:
    snapshot_time = None
    if result.snapshot_time is not None:
        snapshot_time = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(result.snapshot_time))
    return {
        "name": result.name,
        "source": result.source,
        "ok": result.ok,
        "error": result.error,
        "snapshot_time": snapshot_time,
        "sessions_shared": result.sessions_shared,
        "limits": [{
            "limit_id": limit.limit_id,
            "plan_type": limit.plan_type,
            "windows": [window._asdict() for window in limit.windows],
            "credits": limit.credits,
        } for limit in result.limits],
    }
