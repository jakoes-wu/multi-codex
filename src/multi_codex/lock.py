"""写操作互斥锁。

POSIX 用 fcntl.flock，Windows 用 msvcrt.locking：两者都在进程退出（包括被强制结束）时由系统自动释放，
不会出现“持锁进程已死但锁还在”的情况，也不受 PID 复用影响。
锁文件里的 PID 只用于报错时告诉用户是谁持有锁，不参与判断。
"""

import errno
import os
from typing import Optional

from . import platform

if platform.IS_WINDOWS:
    import msvcrt
else:
    import fcntl

# Windows 的字节锁是强制锁：锁住的区域别的进程读不了。锁在远超文件长度的位置，
# 锁文件里的 PID 照常可读；Windows 允许锁定文件末尾之后的区域，截断文件也不影响它。
LOCK_OFFSET = 0x7FFFFFFF


def lock_fd(fd: int) -> None:
    """非阻塞地给 fd 加排它锁；拿不到锁抛 BlockingIOError（与 flock 的行为一致）。

    Windows 上 msvcrt.locking 锁的是“当前文件位置起的 N 字节”，所以先移到 LOCK_OFFSET；
    不论成败，返回时文件位置都在 0：调用方加锁失败后会从头读持锁进程号。
    """
    if not platform.IS_WINDOWS:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return
    try:
        os.lseek(fd, LOCK_OFFSET, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            # 只有“被别人锁着”才算忙；其它错误（坏描述符等）原样抛出
            if exc.errno in (errno.EACCES, getattr(errno, "EDEADLOCK", errno.EDEADLK)):
                raise BlockingIOError(errno.EAGAIN, "file is locked by another process")
            raise
    finally:
        os.lseek(fd, 0, os.SEEK_SET)


def unlock_fd(fd: int) -> None:
    if not platform.IS_WINDOWS:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return
    try:
        os.lseek(fd, LOCK_OFFSET, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    finally:
        os.lseek(fd, 0, os.SEEK_SET)


class LockBusyError(Exception):
    def __init__(self, holder_pid: Optional[str]) -> None:
        super().__init__(holder_pid)
        self.holder_pid = holder_pid


class WriteLock(object):
    def __init__(self) -> None:
        self.path = os.path.join(platform.state_dir(), "lock")
        self._fd: Optional[int] = None

    def __enter__(self) -> "WriteLock":
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        # 不能带 O_TRUNC 打开：此时还没拿到锁，截断会清掉持锁进程写的 PID。
        # 锁文件也不能用 atomic_write 写或被删除：换了 inode，两个进程会各自锁住不同的文件。
        # Windows 上加 O_BINARY：文本模式会改写换行，并在读到 Ctrl+Z 时提前结束。
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o600)
        try:
            lock_fd(fd)
        except (BlockingIOError, PermissionError):
            # 显式回到开头再读：Windows 的 lock_fd 已把位置恢复到 0，这里防将来改动；POSIX 上无副作用
            os.lseek(fd, 0, os.SEEK_SET)
            holder = os.read(fd, 64).decode("ascii", "replace").strip() or None
            os.close(fd)
            raise LockBusyError(holder)
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, str(os.getpid()).encode("ascii"))
        self._fd = fd
        return self

    def __exit__(self, *_exc) -> None:
        if self._fd is not None:
            unlock_fd(self._fd)
            os.close(self._fd)
            self._fd = None
