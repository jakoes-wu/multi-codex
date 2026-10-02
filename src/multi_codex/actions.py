"""动作列表：计划阶段产出、执行阶段逐个执行的最小单元，以及统一的输出格式。

所有写命令都先生成完整的动作列表，只要其中有一个 conflict 就什么都不写（方案 §5.1.1），
所以“计划”与“执行”必须分开：计划阶段只读文件系统，副作用全部放在 run 回调里。
"""

import sys
from typing import Callable, List, Optional

from .fsutil import display_path

CREATE = "create"
UPDATE = "update"
DELETE = "delete"
UNCHANGED = "unchanged"
CONFLICT = "conflict"
# 不需要改动、但值得告诉用户的情况，例如共享目录里没有某个条目。
SKIP = "skip"

LOG_PREFIX = "[multi-codex]"


class Action(object):
    def __init__(self, status: str, kind: str, path: str, reason: str = "",
                 run: Optional[Callable[[], None]] = None) -> None:
        self.status = status
        # 对象类型：config、account-dir、launcher、shared-link、backup 等
        self.kind = kind
        self.path = path
        self.reason = reason
        self.run = run

    @property
    def changes(self) -> bool:
        return self.status in (CREATE, UPDATE, DELETE)


def has_conflict(actions: List[Action]) -> bool:
    return any(action.status == CONFLICT for action in actions)


def print_action(action: Action, dry_run: bool = False) -> None:
    status = action.status + (" (dry-run)" if dry_run and action.changes else "")
    # 路径缩写成 ~/…，只为好读；脚本应读 --json，这里的文本格式不保证稳定。
    line = "{} {} {} {}".format(LOG_PREFIX, status, action.kind, display_path(action.path))
    if action.reason:
        line += " ({})".format(action.reason)
    stream = sys.stderr if action.status == CONFLICT else sys.stdout
    print(line, file=stream)


def info(message: str) -> None:
    print("{} {}".format(LOG_PREFIX, message))


def hint(message: str) -> None:
    """给新用户的下一步提示。写 stderr：stdout 可能被脚本逐行读取（例如 `use` 不带参数时输出账号名）。

    先 flush stdout：输出接到管道时 stdout 有缓冲，不 flush 的话提示会排在它所针对的动作行前面。
    """
    sys.stdout.flush()
    print("{} hint: {}".format(LOG_PREFIX, message), file=sys.stderr)


def warn(message: str) -> None:
    print("{} warning: {}".format(LOG_PREFIX, message), file=sys.stderr)


def error(message: str, phase: Optional[str] = None, path: Optional[str] = None) -> None:
    """错误输出：带阶段和路径，让用户不必翻代码就能知道失败在哪一步、哪个文件。"""
    parts = [LOG_PREFIX, "error"]
    if phase:
        parts.append("phase={}".format(phase))
    if path:
        parts.append("path={}".format(path))
    print("{}: {}".format(" ".join(parts), message), file=sys.stderr)
