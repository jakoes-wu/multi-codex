"""平台适配：默认路径、创建与识别链接、占用检查。

支持 macOS、Linux 与原生 Windows（方案 docs/feature/feature-windows-support.md）。平台差异集中在
本模块和启动命令模板里，其它模块通过 IS_WINDOWS 与这里的函数分支，不各自判断操作系统；
POSIX 分支保持原有行为。
"""

import errno
import json
import os
import shutil
import stat
import subprocess
import sys
import time
from typing import Callable, Dict, List, NamedTuple, Optional, Tuple, TypeVar

from .fsutil import expand, is_under

# 这些变量在全局设置时会破坏账号隔离，见方案 §3。启动命令会清除它们（launcher.render），
# 所以只有直接运行的 codex 仍会用到；账号需要时用 `multi-codex env` 单独设置。
ISOLATION_BREAKING_ENV = ("CODEX_SQLITE_HOME", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN")


IS_WINDOWS = sys.platform == "win32"


def is_supported() -> bool:
    return sys.platform == "darwin" or sys.platform.startswith("linux") or IS_WINDOWS


def default_source() -> str:
    return "~/.codex"


def default_root() -> str:
    return "~/.cx"


def default_bin_dir() -> str:
    return "~/.local/bin"


def dir_on_path(directory: str) -> bool:
    """directory 是否在当前 PATH 中。两边都解析软链后比较，`~` 写法与软链写法都算命中。

    doctor 的 path 检查与 add / migrate-default 之后的提示共用它，两处结论必须一致。
    """
    entries = [os.path.realpath(expand(entry)) for entry in os.environ.get("PATH", "").split(os.pathsep) if entry]
    return os.path.realpath(directory) in entries


def state_dir() -> str:
    """工具状态目录：config.json、lock、migrate-journal.json 所在位置。

    Windows 放 %APPDATA%\\multi-codex（漫游配置目录的惯例位置）；APPDATA 没设时按它的默认位置推算。
    """
    if IS_WINDOWS:
        appdata = os.environ.get("APPDATA")
        base = appdata if appdata and os.path.isabs(appdata) else os.path.join(
            os.path.expanduser("~"), "AppData", "Roaming")
        return os.path.join(base, "multi-codex")
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = xdg if xdg and os.path.isabs(xdg) else os.path.expanduser("~/.config")
    return os.path.join(base, "multi-codex")


def create_link(target: str, link_path: str) -> None:
    """在 link_path 创建指向 target 的软链；target 使用绝对路径，避免随工作目录变化。

    Windows 上目录建 junction：普通用户即可创建，不需要管理员或开发者模式；文件只能建符号链接，
    没有权限时抛 LinkPermissionError。不退而复制文件：复制后各账号各改各的，共享就失效了。
    """
    if not IS_WINDOWS:
        os.symlink(target, link_path)
        return
    if os.path.isdir(target):
        _create_junction(target, link_path)
        return
    _create_file_symlink(target, link_path)


# ---- Windows 链接（方案 §5.1.1） ----

# junction 的 reparse tag；Python 3.12 前 os.path 没有 isjunction，靠它识别
_IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003
# 没有“创建符号链接”特权：未开开发者模式的普通用户建文件符号链接会得到这个错误
_ERROR_PRIVILEGE_NOT_HELD = 1314
LINK_PERMISSION_HINT = ("creating a file link needs Developer Mode or an administrator shell: "
                        "Settings > System > For developers > Developer Mode")


class LinkPermissionError(OSError):
    """Windows 上没有权限建文件符号链接。调用方据此提示开启开发者模式或排除该共享文件。"""


def _create_file_symlink(target: str, link_path: str, directory: bool = False) -> None:
    try:
        os.symlink(target, link_path, target_is_directory=directory)
    except OSError as exc:
        if getattr(exc, "winerror", None) == _ERROR_PRIVILEGE_NOT_HELD:
            raise LinkPermissionError(errno.EPERM, LINK_PERMISSION_HINT, link_path)
        raise


def _create_junction(target: str, link_path: str) -> None:
    # junction 只能指向本机磁盘上的绝对路径；网络路径只能用符号链接，而那需要开发者模式。
    if target.startswith("\\\\") or target.startswith("//"):
        raise OSError(errno.EINVAL, "a directory link cannot point to a network path on Windows", target)
    target = os.path.abspath(target)
    try:
        import _winapi
        create = getattr(_winapi, "CreateJunction", None)
    except ImportError:
        create = None
    if create is not None:
        create(target, link_path)
        return
    from .actions import info
    info("(fallback) mklink /J {} {}".format(link_path, target))
    proc = subprocess.run([system_program("cmd.exe"), "/c", "mklink", "/J", link_path, target],
                          stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                          universal_newlines=True, creationflags=_CREATE_NO_WINDOW)
    if proc.returncode != 0:
        raise OSError(errno.EIO, "mklink /J failed (exit {}): {}".format(proc.returncode, proc.stderr.strip()),
                      link_path)


def copy_link(source: str, link_path: str) -> None:
    """把链接 source 原样复制到 link_path（复制目录树时用）。

    POSIX 与基线 fsutil.copy_tree 相同：按原样复制链接目标文本。Windows 上目标原文照抄（不按当前目录
    解释相对路径、不检查目标是否存在）：源是 junction 就建 junction；否则建符号链接并保留源的目录 /
    文件类型。不能改用 create_link：它按目标是否为目录选类型，会把相对或悬空链接复制成错的类型。
    """
    if not IS_WINDOWS:
        os.symlink(os.readlink(source), link_path)
        return
    target = read_link(source)
    if is_junction(source):
        _create_junction(target, link_path)
    else:
        _create_file_symlink(target, link_path, directory=_is_directory_entry(source))


def is_junction(path: str) -> bool:
    """path 本身是不是 junction（不跟随）；非 Windows 恒为 False。"""
    if not IS_WINDOWS:
        return False
    check = getattr(os.path, "isjunction", None)
    if check is not None:
        return check(path)
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return getattr(st, "st_reparse_tag", 0) == _IO_REPARSE_TAG_MOUNT_POINT


def is_link(path: str) -> bool:
    """path 本身是软链或 junction。os.path.islink 对 junction 返回 False，凡要“认出链接”的地方都用本函数。"""
    return os.path.islink(path) or is_junction(path)


def remove_link(path: str) -> None:
    """只删除链接本身，不碰它指向的内容。

    Windows 上 junction 和目录符号链接都是“目录项”，os.unlink 删不掉，要用 os.rmdir；
    rmdir 对链接只删链接，不会删目标目录里的东西。
    """
    if IS_WINDOWS and (is_junction(path) or (os.path.islink(path) and _is_directory_entry(path))):
        os.rmdir(path)
        return
    os.unlink(path)


def _is_directory_entry(path: str) -> bool:
    try:
        attributes = getattr(os.lstat(path), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_DIRECTORY", 0x10))


def read_link(path: str) -> str:
    """读链接目标。Windows 上 junction 的目标带 `\\\\?\\` 前缀，去掉后与写入时的路径一致。"""
    target = os.readlink(path)
    if IS_WINDOWS and target.startswith("\\\\?\\") and not target.upper().startswith("\\\\?\\UNC\\"):
        target = target[4:]
    return target


# ---- 找可执行文件、系统程序与共享冲突重试 ----

# 后台子进程不弹控制台窗口；非 Windows 上 subprocess 要求 creationflags 为 0
_CREATE_NO_WINDOW = 0x08000000 if IS_WINDOWS else 0

# Windows 自带程序所在的子目录（相对 SystemRoot\System32）。PowerShell 不在 System32 根下，
# 直接拼 System32\powershell.exe 会找不到文件、占用检查每次失败。
_SYSTEM_PROGRAMS = {"cmd.exe": "", "powershell.exe": os.path.join("WindowsPowerShell", "v1.0"), "taskkill.exe": ""}


def system_program(name: str) -> str:
    """Windows 自带的 cmd.exe / powershell.exe / taskkill.exe 的绝对路径。

    不能只写程序名：CreateProcess 找程序时先查当前目录，用户在放了同名程序的项目目录里运行就会执行到它。
    """
    root = os.environ.get("SystemRoot") or os.environ.get("SYSTEMROOT") or r"C:\Windows"
    return os.path.join(root, "System32", _SYSTEM_PROGRAMS[name], name)


def which(name: str, path: Optional[str] = None) -> Optional[str]:
    """按 PATH（path 为 None 时取本进程的 PATH）找可执行文件的完整路径。

    POSIX 上就是 shutil.which。Windows 上不用 shutil.which：Python 3.8 的实现会先查当前目录，
    在某个项目目录里运行时可能执行到目录里放的同名程序。这里只查 PATH 中的绝对路径目录，扩展名按 PATHEXT。
    """
    if not IS_WINDOWS:
        return shutil.which(name, path=path)
    extensions = [ext for ext in os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(";") if ext]
    if os.path.dirname(name):
        for candidate in [name] + [name + ext for ext in extensions]:
            if os.path.isfile(candidate):
                return candidate
        return None
    if os.path.splitext(name)[1].upper() in (ext.upper() for ext in extensions):
        candidates = [name]
    else:
        candidates = [name + ext for ext in extensions]
    search = os.environ.get("PATH", "") if path is None else path
    for directory in search.split(os.pathsep):
        directory = directory.strip().strip('"')
        if not directory or not os.path.isabs(directory):
            continue
        for candidate in candidates:
            full = os.path.join(directory, candidate)
            if os.path.isfile(full):
                return full
    return None


def test_hook_point(point: str) -> bool:
    """测试注入：MULTI_CODEX_TEST_FAIL_AT 等于 point 时为真，由调用方决定怎样模拟失败。

    与 migrate._test_hook 读同一个环境变量；本模块不能导入 migrate（migrate 导入了本模块，会循环导入）。
    生产环境不设该变量，恒为假。
    """
    return os.environ.get("MULTI_CODEX_TEST_FAIL_AT") == point


_T = TypeVar("_T")
# 杀毒软件、索引服务、Codex 本身短暂打开文件时，Windows 上替换 / 改名会失败（拒绝访问 5、共享冲突 32）
_SHARING_ERRORS = (5, 32)
_SHARING_RETRY_DELAYS = (0.05, 0.1, 0.2, 0.4, 0.8)
# 测试注入 MULTI_CODEX_TEST_SHARING_AT 的已注入次数，按注入点计；只在本进程内有效
_injected_sharing: Dict[str, int] = {}


def is_sharing_violation(exc: BaseException) -> bool:
    """Windows 上文件被其它程序占用导致的失败。POSIX 恒为假：那里的 EACCES 是真的权限问题，不能当占用重试。"""
    return IS_WINDOWS and isinstance(exc, PermissionError) and getattr(exc, "winerror", None) in _SHARING_ERRORS


def _injected_sharing_violation(point: str, path: str) -> None:
    """MULTI_CODEX_TEST_SHARING_AT=<点>:<次数>：该点前 <次数> 次调用直接抛共享冲突，不执行真实操作。

    只在 Windows 上生效。必须带第 4 个参数 32：Windows 上它成为 winerror，is_sharing_violation 才认得；
    写成 PermissionError("msg") 时 winerror 为 None，重试逻辑会把它当普通错误直接抛出。
    """
    spec = os.environ.get("MULTI_CODEX_TEST_SHARING_AT", "")
    if not IS_WINDOWS or ":" not in spec:
        return
    name, _, times = spec.rpartition(":")
    if name != point or not times.isdigit():
        return
    done = _injected_sharing.get(point, 0)
    if done < int(times):
        _injected_sharing[point] = done + 1
        raise PermissionError(errno.EACCES, "injected sharing violation", path, 32)


def retry_on_sharing_violation(point: str, func: Callable[..., _T], *args: str) -> _T:
    """Windows 上遇共享冲突按 50/100/200/400/800 ms 退避重试，最后一次的错误原样抛出；POSIX 直接调用一次。

    point 是测试注入点名（MULTI_CODEX_TEST_SHARING_AT），各调用点取值见方案 §5.1.1；
    args 的第一个是被操作的路径，注入的异常带上它。只用于改名 / 替换这类失败不留半截状态的操作。
    """
    if not IS_WINDOWS:
        return func(*args)
    path = args[0] if args else ""

    def attempt() -> _T:
        _injected_sharing_violation(point, path)
        return func(*args)

    for delay in _SHARING_RETRY_DELAYS:
        try:
            return attempt()
        except PermissionError as exc:
            if not is_sharing_violation(exc):
                raise
        time.sleep(delay)
    return attempt()


def can_create_file_link() -> bool:
    """当前用户能否建文件符号链接。POSIX 恒为真；Windows 上在临时目录里试建一个再删掉。"""
    if not IS_WINDOWS:
        return True
    if test_hook_point("file-link-denied"):
        return False
    import tempfile
    directory = tempfile.mkdtemp(prefix="multi-codex-link-")
    try:
        target = os.path.join(directory, "target")
        with open(target, "w"):
            pass
        try:
            _create_file_symlink(target, os.path.join(directory, "link"))
        except LinkPermissionError:
            return False
        return True
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def in_use_message(path: str, exc: BaseException) -> str:
    """改名遇占用时给用户的提示；只在 is_sharing_violation 为真时使用。"""
    return ("{} is in use by another program (winerror {}); close all Codex sessions, IDEs and terminals "
            "opened in it, then retry".format(path, getattr(exc, "winerror", None)))


class BusyProcess(NamedTuple):
    pid: int
    command: str
    # 占用方式（给人看的）：cwd（工作目录）、executable（可执行文件）、mapped（内存映射）或 fd N（打开的文件）
    usage: str
    # 落在源目录下的那个路径，让用户一眼看出是哪个文件被占用
    path: str


def describe_usage(fd: str) -> str:
    """把 lsof 的 fd 字段或 /proc 的条目名翻译成好懂的占用方式；未知值原样返回。"""
    if fd == "txt":
        return "executable"
    if fd == "mem":
        return "mapped"
    digits = fd.rstrip("rwuRWU")
    if digits.isdigit():
        return "fd " + digits
    return fd


class BusyCheckError(Exception):
    """占用检查本身无法完成（找不到检查工具，或 lsof / PowerShell 没有正常输出）。"""


def find_busy_processes(path: str, exclude_pids: Optional[List[int]] = None) -> List[BusyProcess]:
    """列出工作目录、可执行文件或打开的文件落在 path 之下的进程。

    只检查顶层数据库文件会漏掉浏览器扩展宿主这类只把工作目录设在其中的进程，
    所以这里对全部进程逐个判断。结果按“进程名含 codex 的在前”排序。
    """
    excluded = set(exclude_pids or [])
    excluded.add(os.getpid())
    if IS_WINDOWS:
        found = _find_busy_windows(excluded)
        found.sort(key=lambda item: item.pid)
        return found
    root = os.path.realpath(path)
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
                found[pid] = BusyProcess(pid, command, describe_usage(fd), value)
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
        hit = _proc_usage(base, root)
        if hit:
            found.append(BusyProcess(pid, _proc_command(base), describe_usage(hit[0]), hit[1]))
    return found


def _proc_usage(base: str, root: str) -> Optional[Tuple[str, str]]:
    """返回 (占用方式, 路径)；与 lsof 的 fd 字段取值保持一致，交给 describe_usage 统一翻译。"""
    for name, usage in (("cwd", "cwd"), ("exe", "txt")):
        target = _readlink_quiet(os.path.join(base, name))
        if target and is_under(target, root):
            return usage, target
    fd_dir = os.path.join(base, "fd")
    try:
        fds = os.listdir(fd_dir)
    except OSError:
        return None
    for fd in fds:
        target = _readlink_quiet(os.path.join(fd_dir, fd))
        if target and target.startswith("/") and is_under(target, root):
            return fd, target
    return None


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


# ---- Windows 占用检查（方案 §5.1.1）：按进程判断，不按打开的文件 ----

_WIN32_PROCESS_QUERY = ("try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}; "
                        "Get-CimInstance Win32_Process | Select-Object ProcessId,Name,CommandLine,ExecutablePath "
                        "| ConvertTo-Json -Compress")
# WMI 冷启动可能很慢（CI 上实测可超过 30 秒），给足时间
_WIN32_PROCESS_TIMEOUT = 90


def _find_busy_windows(excluded: set) -> List[BusyProcess]:
    """列出正在运行的 Codex 进程：codex.exe，或命令行里带 @openai\\codex 的 node.exe。

    Windows 上枚举进程打开的文件要做句柄枚举，代价与权限都不可控；用户已定“有任何 Codex 进程在运行就拒绝”，
    所以按进程名判断，不看它在用哪个目录。失败或超时抛 BusyCheckError，与 POSIX 找不到 lsof 走同一通道。
    """
    if test_hook_point("busy-check-powershell"):
        raise BusyCheckError("powershell exited with code 1 (injected)")
    listing = os.environ.get("MULTI_CODEX_TEST_PROCESS_LIST")
    if listing:
        with open(listing, "rb") as handle:
            raw = handle.read()
    else:
        raw = _run_process_query()
    found = []
    for item in _parse_win32_processes(raw.decode("utf-8", "replace")):
        pid = item.get("ProcessId")
        if not isinstance(pid, int) or isinstance(pid, bool) or pid in excluded:
            continue
        name = item.get("Name") if isinstance(item.get("Name"), str) else ""
        exe = item.get("ExecutablePath") if isinstance(item.get("ExecutablePath"), str) else ""
        command = item.get("CommandLine") if isinstance(item.get("CommandLine"), str) else ""
        if _is_codex_process(name, command):
            found.append(BusyProcess(pid, name, "running", exe))
    return found


def _run_process_query() -> bytes:
    try:
        proc = subprocess.Popen([system_program("powershell.exe"), "-NoProfile", "-NonInteractive", "-Command",
                                 _WIN32_PROCESS_QUERY],
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                creationflags=_CREATE_NO_WINDOW)
    except OSError as exc:
        raise BusyCheckError("cannot run powershell: {}".format(exc))
    try:
        stdout, _ = proc.communicate(timeout=_WIN32_PROCESS_TIMEOUT)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise BusyCheckError("powershell did not list processes within {} seconds".format(_WIN32_PROCESS_TIMEOUT))
    if proc.returncode != 0:
        raise BusyCheckError("powershell exited with code {}".format(proc.returncode))
    return stdout


def _parse_win32_processes(text: str) -> List[dict]:
    """解析 ConvertTo-Json 的输出：多个进程是数组，只有一个时是单个对象。输出不是合法 JSON 时抛 BusyCheckError。"""
    text = text.strip().lstrip("\ufeff")
    try:
        data = json.loads(text) if text else []
    except ValueError:
        raise BusyCheckError("cannot parse the process list from powershell")
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise BusyCheckError("unexpected process list from powershell")
    return [item for item in data if isinstance(item, dict)]


def _is_codex_process(name: str, command: str) -> bool:
    lowered = name.lower()
    if lowered == "codex.exe":
        return True
    if lowered != "node.exe" or not command:
        return False
    from . import winapi
    argv = winapi.split_command_line(command) if IS_WINDOWS else command.split()
    return any("@openai\\codex" in arg.lower().replace("/", "\\") for arg in argv)


def _pid_alive(pid: int) -> bool:
    """进程是否仍在。Windows 上绝不能用 os.kill(pid, 0)：那里它会调用 TerminateProcess，直接结束目标进程。"""
    if IS_WINDOWS:
        from . import winapi
        return winapi.pid_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True
