"""按用户的 shell 给出“把启动命令目录加进 PATH”的一行命令（feature-everyday-commands §5.1.5）。

只生成提示文字，不改任何 shell 配置文件。`add` / `migrate-default` 之后的提示、`doctor` 的 path 检查
都用这里的结果；install.sh 用 shell 实现了同一张表，改这里时要同步改 install.sh。
"""

import os
import sys
from typing import Optional

# 这些字符写进单引号或双引号后会改变命令的含义（结束引号、变量展开、命令替换、转义），
# 目录里含有它们时不给可直接执行的命令，只给通用说明。
_UNSAFE_CHARS = ("'", '"', "$", "`", "\\")

_GENERIC_HINT = "add it to PATH in your shell profile"


def add_to_path_command(directory: str, shell: Optional[str], is_macos: bool) -> Optional[str]:
    """返回一行可直接执行的命令；不认识的 shell 或目录含特殊字符时返回 None。

    directory 必须是展开后的绝对路径：命令写进 rc 文件后，`~` 在双引号里不会被展开，
    传 display_path 的 `~/…` 形式会得到一个永远找不到的 PATH 条目。
    shell 取 $SHELL 的值（可为完整路径），只看文件名。
    macOS 的终端默认打开登录 shell，bash 读 ~/.bash_profile 而不读 ~/.bashrc，所以两个系统写的文件不同。
    """
    if any(char in directory for char in _UNSAFE_CHARS):
        return None
    name = os.path.basename(shell or "")
    export_line = "echo 'export PATH=\"{}:$PATH\"' >> ".format(directory)
    if name == "zsh":
        return export_line + "~/.zshrc"
    if name == "bash":
        return export_line + ("~/.bash_profile" if is_macos else "~/.bashrc")
    if name == "fish":
        return "fish_add_path '{}'".format(directory)
    return None


def path_hint(directory: str, shell: Optional[str], is_macos: bool) -> str:
    """提示的后半句：能给命令时给命令，否则给通用说明。"""
    command = add_to_path_command(directory, shell, is_macos)
    if command is None:
        return _GENERIC_HINT
    return "run: {}, then open a new terminal".format(command)


def current_path_hint(directory: str) -> str:
    """按当前进程的 $SHELL 与操作系统给提示；cli 与 doctor 共用，两处的文字因此保持一致。

    用 sys.platform 判断 macOS：本包有自己的 platform 模块，不导入标准库同名模块。
    """
    return path_hint(directory, os.environ.get("SHELL"), sys.platform == "darwin")
