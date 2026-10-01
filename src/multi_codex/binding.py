"""目录绑定账号（feature-dir-binding）：`run` 省略账号名时，按当前目录向上找最近一个绑定。

绑定的键是规范路径 normalize_dir：
- 先 realpath 解析软链（macOS 上 /tmp 会变成 /private/tmp）；
- 再逐级修正大小写：macOS 默认文件系统不区分大小写，realpath("casedir") 会保留输入的大小写，
  而 os.getcwd() 返回磁盘上的真实大小写，不修正的话键永远匹配不上。
"""

import os
from typing import Optional, Tuple

from .config import Config


def _true_name(parent: str, name: str) -> str:
    """parent 下与 name 对应的磁盘上的名字。

    原名精确存在时保留原名：区分大小写的文件系统上 Foo 与 foo 可以并存，不能挑错。
    精确匹配不到时才按 casefold 找，且只在恰好一个时采用；listdir 失败（例如只有执行权限）时保留原名。
    """
    try:
        entries = os.listdir(parent)
    except OSError:
        return name
    if name in entries:
        return name
    folded = name.casefold()
    matches = [entry for entry in entries if entry.casefold() == folded]
    return matches[0] if len(matches) == 1 else name


def normalize_dir(path: str) -> str:
    real = os.path.realpath(os.path.expanduser(path))
    parts = []
    current = real
    while True:
        parent, name = os.path.split(current)
        if not name:
            root = current
            break
        parts.append((parent, name))
        current = parent
    result = root
    for parent, name in reversed(parts):
        # 父目录已按真实大小写拼好，用它去 listdir；这一级不存在时 _true_name 原样返回。
        result = os.path.join(result, _true_name(result, name))
    return result


def resolve(config: Config, cwd: str) -> Optional[Tuple[str, str]]:
    """从 cwd 开始逐级向上，返回最近一个绑定的 (目录, 账号名)；没有时返回 None。"""
    current = normalize_dir(cwd)
    while True:
        if current in config.bindings:
            return current, config.bindings[current]
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def drop_account(config: Config, name: str) -> None:
    """账号被注销时（remove、restore、apply -f）删除指向它的绑定，按 casefold 匹配账号名。"""
    folded = name.casefold()
    for path in [path for path, bound in config.bindings.items() if bound.casefold() == folded]:
        del config.bindings[path]
