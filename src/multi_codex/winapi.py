"""Windows 系统接口的 ctypes 封装：判活、拆命令行、取 8.3 短名、Job Object。

只在 Windows 上调用（调用方先判断 platform.IS_WINDOWS）；本模块在其它平台上可以导入，
但不会加载任何 DLL——所有 WinDLL 都在函数体内按需打开。不依赖本包其它模块，避免循环导入。
"""

import ctypes
from typing import List, Optional

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_ERROR_INVALID_PARAMETER = 87
_STILL_ACTIVE = 259


def _kernel32():  # type: ignore[no-untyped-def]
    return ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]


def pid_alive(pid: int) -> bool:
    """只查询、不碰目标进程：OpenProcess 打开查询句柄，看退出码是否仍是 STILL_ACTIVE。

    打不开且错误是“参数无效”说明没有这个 pid；其它错误（如拒绝访问）说明进程在但属于别人，按存活处理，
    与 POSIX 上 os.kill 得到 PermissionError 时一致。绝不能改用 os.kill：Windows 上它会直接结束目标进程。
    """
    kernel32 = _kernel32()
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ctypes.get_last_error() != _ERROR_INVALID_PARAMETER  # type: ignore[attr-defined]
    try:
        code = ctypes.c_uint32(0)
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return True
        return code.value == _STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def split_command_line(command: str) -> List[str]:
    """按 Windows 自己的规则（CommandLineToArgvW）把命令行拆成 argv；拆分失败时按空白拆。"""
    if not command:
        return []
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)  # type: ignore[attr-defined]
    kernel32 = _kernel32()
    shell32.CommandLineToArgvW.restype = ctypes.POINTER(ctypes.c_wchar_p)
    shell32.CommandLineToArgvW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    count = ctypes.c_int(0)
    argv = shell32.CommandLineToArgvW(command, ctypes.byref(count))
    if not argv:
        return command.split()
    try:
        return [argv[index] for index in range(count.value)]
    finally:
        kernel32.LocalFree(ctypes.cast(argv, ctypes.c_void_p))


def short_path(path: str) -> str:
    """取路径的 8.3 短名（全 ASCII）；取不到（卷上关了短名、路径不存在）时原样返回。

    .cmd 文件由 cmd.exe 按控制台代码页读取，不按 UTF-8：路径里有中文时，写进启动命令的路径会被读成乱码。
    """
    kernel32 = _kernel32()
    kernel32.GetShortPathNameW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
    kernel32.GetShortPathNameW.restype = ctypes.c_uint32
    size = kernel32.GetShortPathNameW(path, None, 0)
    if not size:
        return path
    buffer = ctypes.create_unicode_buffer(size)
    if not kernel32.GetShortPathNameW(path, buffer, size):
        return path
    return buffer.value


# ---- Job Object：usage --live 结束 app-server 进程树的兜底（方案 §5.1.7） ----

_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class _BasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class _ExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class JobError(OSError):
    """建 Job Object 或把进程放进去失败；winerror 是 GetLastError 的值。"""


def create_kill_on_close_job(process_handle: int) -> int:
    """建一个“句柄关闭即结束其中全部进程”的 Job Object，并把 process_handle 放进去；返回 job 句柄。

    之后由该进程起的全部后代自动进入同一 job。失败时关掉已建的 job 并抛 JobError，调用方降级为只用 taskkill。
    """
    kernel32 = _kernel32()
    kernel32.CreateJobObjectW.restype = ctypes.c_void_p
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
    kernel32.SetInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    kernel32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        error = ctypes.get_last_error()  # type: ignore[attr-defined]
        raise JobError(error, "CreateJobObjectW failed", None, error)
    info = _ExtendedLimitInformation()
    info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    ok = kernel32.SetInformationJobObject(job, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
                                          ctypes.byref(info), ctypes.sizeof(info))
    if ok:
        ok = kernel32.AssignProcessToJobObject(job, ctypes.c_void_p(int(process_handle)))
    if not ok:
        error = ctypes.get_last_error()  # type: ignore[attr-defined]
        kernel32.CloseHandle(job)
        raise JobError(error, "cannot put the process into a job object", None, error)
    return int(job)


def close_handle(handle: Optional[int]) -> None:
    """关闭句柄；对 kill-on-close 的 job，这一步会结束 job 里仍在的全部进程。"""
    if not handle:
        return
    kernel32 = _kernel32()
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle(ctypes.c_void_p(handle))
