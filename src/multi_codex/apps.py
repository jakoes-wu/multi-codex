"""按账号打开 VS Code 与 Codex 桌面端（实验功能，feature-app-launch）。

两者都依赖未公开的行为（核实过程见方案 §1.1）：
- VS Code：不同的 --user-data-dir 一定开出新实例；从命令行启动时不读登录 shell 的环境，
  OpenAI 扩展把 process.env 原样传给它启动的 codex，所以 CODEX_HOME 能一路传到 codex app-server。
- 桌面端：启动后会把登录 shell 的环境合并进来，只有设置了 CODEX_ELECTRON_USER_DATA_PATH 时
  才把 CODEX_HOME 恢复成启动时的值，用户数据目录与单实例锁也由这个变量决定。

每个账号的 GUI 数据放在 <root>/.apps/<名>/：账号名以字母或数字开头，`.apps` 不会与账号目录重名。
"""

import os
import plistlib
import shutil
from typing import Optional

from .config import Config
from .fsutil import expand

VERIFIED_WITH = "VS Code 1.139.1 / extension 26.928.31416 / Codex desktop 26.831.11858"
DEFAULT_DESKTOP_APP = "/Applications/ChatGPT.app"
# 只允许 Codex 桌面端；同一目录下的 ChatGPT Classic（com.openai.chat）是另一个应用。
DESKTOP_BUNDLE_ID = "com.openai.codex"
# 不从 PATH 查找 open，避免同名命令被误用；测试用这个环境变量换成假命令。
OPEN_OVERRIDE_ENV = "MULTI_CODEX_TEST_OPEN"
# macOS 的 unix 套接字路径上限是 104 字节，VS Code 的主套接字放在用户数据目录下（<版本>-main.sock）。
SOCKET_DIR_WARN_LENGTH = 80


def _private_dir(path: str) -> str:
    os.makedirs(path, mode=0o700, exist_ok=True)
    os.chmod(path, 0o700)  # makedirs 的 mode 受 umask 影响，这里显式收紧
    return path


def gui_data_dir(config: Config, dir_name: str, kind: str) -> str:
    """返回并创建 <root>/.apps/<账号目录名>/<kind>（kind 为 vscode 或 desktop），各级目录都只允许本人访问。

    按目录名而不是账号名存放：rename 后 VS Code 与桌面端的登录状态、窗口与扩展数据都还在原处。
    """
    apps_root = _private_dir(os.path.join(expand(config.root), ".apps"))
    account_root = _private_dir(os.path.join(apps_root, dir_name))
    return _private_dir(os.path.join(account_root, kind))


def desktop_log(config: Config, dir_name: str) -> str:
    path = os.path.join(expand(config.root), ".apps", dir_name, "desktop.log")
    fd = os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
    os.close(fd)
    os.chmod(path, 0o600)
    return path


def find_code(override: Optional[str]) -> Optional[str]:
    candidate = override or shutil.which("code")
    if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
        return candidate
    return None


def desktop_app_problem(app: str) -> Optional[str]:
    """应用包不可用时返回原因，可用时返回 None。"""
    info_path = os.path.join(app, "Contents", "Info.plist")
    try:
        with open(info_path, "rb") as handle:
            info = plistlib.load(handle)
    except (OSError, ValueError) as exc:  # plistlib 的解析错误继承自 ValueError
        return "cannot read {}: {}".format(info_path, exc)
    bundle_id = info.get("CFBundleIdentifier") if isinstance(info, dict) else None
    if bundle_id != DESKTOP_BUNDLE_ID:
        return "{} is not the Codex desktop app (bundle id {!r}, expected {!r})".format(
            app, bundle_id, DESKTOP_BUNDLE_ID)
    return None


def open_command() -> str:
    return os.environ.get(OPEN_OVERRIDE_ENV) or "/usr/bin/open"
