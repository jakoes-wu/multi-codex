"""平台适配：默认路径、创建与识别链接、占用检查。

一期只支持 macOS 与 Linux。二期加 Windows 时，平台差异都在本模块和启动命令模板里扩展，
其它模块不直接判断操作系统。
"""

import os
import shutil
import subprocess
import sys
from typing import List, NamedTuple, Optional

from .fsutil import is_under

# 这些变量在全局设置时会破坏账号隔离，见方案 §3。
ISOLATION_BREAKING_ENV = ("CODEX_SQLITE_HOME", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN")


def is_supported() -> bool:
    return sys.platform == "darwin" or sys.platform.startswith("linux")


def default_source() -> str:
    return "~/.codex"


def default_root() -> str:
    return "~/.cx"


def default_bin_dir() -> str:
    return "~/.local/bin"


def state_dir() -> str:
    """工具状态目录：config.json、lock、migrate-journal.json 所在位置。"""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = xdg if xdg and os.path.isabs(xdg) else os.path.expanduser("~/.config")
    return os.path.join(base, "multi-codex")


def create_link(target: str, link_path: str) -> None:
    """在 link_path 创建指向 target 的软链；target 使用绝对路径，避免随工作目录变化。"""
    os.symlink(target, link_path)


class BusyProcess(NamedTuple):
    pid: int
    command: str
    # 占用方式：cwd（工作目录）、txt（可执行文件）或其它文件描述符
    usage: str


class BusyCheckError(Exception):
    """占用检查本身无法完成（找不到检查工具，或 lsof 没有正常输出）。"""


def find_busy_processes(path: str, exclude_pids: Optional[List[int]] = None) -> List[BusyProcess]:
    """列出工作目录、可执行文件或打开的文件落在 path 之下的进程。

    只检查顶层数据库文件会漏掉浏览器扩展宿主这类只把工作目录设在其中的进程，
    所以这里对全部进程逐个判断。结果按“进程名含 codex 的在前”排序。
    """
    root = os.path.realpath(path)
    excluded = set(exclude_pids or [])
    excluded.add(os.getpid())
    if shutil.which("lsof"):
        found = _scan_with_lsof(root, excluded)
    elif sys.platform.startswith("linux") and os.path.isdir("/proc"):
        found = _scan_proc(root, excluded)
    else:
        raise BusyCheckError("neither lsof nor /proc is available")
    found.sort(key=lambda item: ("codex" not in item.command.lower(), item.pid))
    return found


def _scan_with_lsof(root: str, excluded: set) -> List[BusyProcess]:
    # -F pcfn：按字段输出 pid、命令、fd、文件名；-n -P 关闭 DNS 与端口名解析，避免卡顿。
    proc = subprocess.Popen(
        ["lsof", "-n", "-P", "-F", "pcfn"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
        errors="replace",
    )
    stdout, _stderr = proc.communicate()
    excluded = excluded | {proc.pid}
    has_process_record = False
    found = {}
    pid = None
    command = ""
    fd = ""
    for line in stdout.splitlines():
        if not line:
            continue
        field, value = line[0], line[1:]
        if field == "p":
            has_process_record = True
            pid = int(value) if value.isdigit() else None
            command = ""
            fd = ""
        elif field == "c":
            command = value
        elif field == "f":
            fd = value
        elif field == "n" and pid is not None and pid not in excluded:
            if value.startswith("/") and is_under(value, root) and pid not in found:
                found[pid] = BusyProcess(pid, command, fd)
    # Linux 普通用户运行 lsof 时，常因无权读取其它用户的进程而返回非 0 并输出告警；
    # 只有一个进程记录都没有时才视为检查失败，否则会把正常情况误判为失败。
    if proc.returncode != 0 and not has_process_record:
        raise BusyCheckError("lsof exited with code {} and produced no output".format(proc.returncode))
    return list(found.values())


def _scan_proc(root: str, excluded: set) -> List[BusyProcess]:
    found = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        pid = int(entry)
        if pid in excluded:
            continue
        base = os.path.join("/proc", entry)
        usage = _proc_usage(base, root)
        if usage:
            found.append(BusyProcess(pid, _proc_command(base), usage))
    return found


def _proc_usage(base: str, root: str) -> str:
    for name, usage in (("cwd", "cwd"), ("exe", "txt")):
        target = _readlink_quiet(os.path.join(base, name))
        if target and is_under(target, root):
            return usage
    fd_dir = os.path.join(base, "fd")
    try:
        fds = os.listdir(fd_dir)
    except OSError:
        return ""
    for fd in fds:
        target = _readlink_quiet(os.path.join(fd_dir, fd))
        if target and target.startswith("/") and is_under(target, root):
            return fd
    return ""


def _proc_command(base: str) -> str:
    try:
        with open(os.path.join(base, "comm"), "r", encoding="utf-8", errors="replace") as handle:
            return handle.read().strip()
    except OSError:
        return "?"


def _readlink_quiet(path: str) -> str:
    try:
        return os.readlink(path)
    except OSError:
        return ""
