"""配置模型：读写 config.json，校验账号名与代理值。

config.json 是唯一的权威源，启动命令、共享链接都是从它推导出来的产物（方案 §5.1.1）。
本模块只负责数据本身，不创建或删除任何账号文件。
"""

import difflib
import json
import os
import re
import urllib.parse
from typing import Dict, List, Optional, Tuple

from . import platform
from .fsutil import atomic_write

CONFIG_VERSION = 1
DEFAULT_SHARED_ITEMS = ["AGENTS.md", "skills", "rules", "agents"]
# `add/set NAME --shared` 不带目录、且 shared.dir 尚未设置时使用的共享目录；按原样写入配置，用到时再展开 `~`。
DEFAULT_SHARED_DIR = "~/.codex-shared"

# 以字母或数字开头：不会被当成命令行选项，也不会以 `.` 开头与 `.migration` 等目录混淆。
NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@+-]{0,63}$")

PROXY_INHERIT = "inherit"
PROXY_OFF = "off"
PROXY_SCHEMES = ("http", "https", "socks5", "socks5h")

# 启动命令的代理逻辑管理的 8 个变量（launcher.py 也用这份列表）。账号环境变量不得设置它们：
# 代理一律由 `multi-codex proxy` 管理，两处都能设置时，谁覆盖谁取决于行的顺序，用户很难看懂。
PROXY_ENV_VARS = ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "NO_PROXY",
                  "https_proxy", "http_proxy", "all_proxy", "no_proxy")
# CODEX_HOME 由启动命令设为账号目录，被覆盖就失去了按账号隔离的意义。
RESERVED_ENV_KEYS = ("CODEX_HOME",) + PROXY_ENV_VARS
ENV_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ConfigError(Exception):
    """配置文件内容不合法，对应退出码 1。"""


class Account(object):
    """一个已登记的账号。

    managed_links 是工具内部状态：记录本工具在该账号目录里建立过的共享链接，
    关闭共享时只删除这些链接，用户自己建的同名链接不受影响。

    env 是写进启动命令的额外环境变量，值按字面写入、不经 shell 求值。
    """

    def __init__(self, name: str, proxy: str = PROXY_INHERIT, shared: bool = False,
                 managed_links: Optional[List[str]] = None, env: Optional[Dict[str, str]] = None) -> None:
        self.name = name
        self.proxy = proxy
        self.shared = shared
        self.managed_links = list(managed_links or [])
        self.env = dict(env or {})

    def copy(self) -> "Account":
        return Account(self.name, self.proxy, self.shared, list(self.managed_links), dict(self.env))

    def to_dict(self) -> dict:
        data = {"proxy": self.proxy, "shared": self.shared, "managed_links": list(self.managed_links)}
        # 没有环境变量时不写这个字段：0.3.0 的配置序列化结果保持不变，升级后的第一条写命令
        # 才不会因为“配置文本变了”而把每个账号都判为 update（accounts.plan 按序列化文本比较）。
        # 按键名排序：apply -f 文件里键的顺序不同，不应被当成改动。
        if self.env:
            data["env"] = {key: self.env[key] for key in sorted(self.env)}
        return data


class Config(object):
    def __init__(self, root: str, bin_dir: str, shared_dir: Optional[str],
                 shared_items: List[str], accounts: Dict[str, Account],
                 bindings: Optional[Dict[str, str]] = None) -> None:
        self.root = root
        self.bin_dir = bin_dir
        self.shared_dir = shared_dir
        self.shared_items = list(shared_items)
        # 键保持账号名的原始大小写；查找一律经 find() 做大小写不敏感匹配。
        self.accounts = accounts
        # 目录绑定：规范路径（binding.normalize_dir）-> 账号名。属于本机状态，apply -f 时沿用当前值。
        self.bindings = dict(bindings or {})

    def find(self, name: str) -> Optional[Account]:
        """按大小写不敏感匹配查找账号。

        macOS 默认文件系统不区分大小写，`Work` 与 `work` 实际指向同一个目录和同一个启动命令，
        所以必须视为同一个账号。
        """
        folded = name.casefold()
        for account in self.accounts.values():
            if account.name.casefold() == folded:
                return account
        return None

    def not_registered(self, name: str) -> str:
        """账号不存在时的报错文本，附带下一步：相近的账号名、已登记的账号，或先 add。

        前半句 `account '<名>' is not registered` 保持旧版原文，按它 grep 的脚本和日志检索不受影响。
        相近匹配用 casefold 比较，与 find() 的大小写不敏感一致；给出的名字用登记时的原始大小写。
        """
        message = "account {!r} is not registered".format(name)
        names = list(self.accounts)
        folded = {account.casefold(): account for account in names}
        close = difflib.get_close_matches(name.casefold(), list(folded), n=1, cutoff=0.6)
        if close:
            return "{}; did you mean {!r}?".format(message, folded[close[0]])
        if not names:
            return "{}; run `multi-codex add NAME` first".format(message)
        # 账号多时整列打出来反而难读，只在不超过 5 个时列出。
        if len(names) <= 5:
            return "{}; registered: {}".format(message, ", ".join(names))
        return message

    def copy(self) -> "Config":
        return Config(self.root, self.bin_dir, self.shared_dir, self.shared_items,
                      {key: value.copy() for key, value in self.accounts.items()}, dict(self.bindings))

    def to_dict(self) -> dict:
        data = {
            "version": CONFIG_VERSION,
            "root": self.root,
            "bin_dir": self.bin_dir,
            "shared": {"dir": self.shared_dir, "items": list(self.shared_items)},
            "accounts": {name: account.to_dict() for name, account in self.accounts.items()},
        }
        # 没有绑定时不写这个字段，0.4.0 的配置序列化结果保持不变（原因同 Account.to_dict 的 env）。
        if self.bindings:
            data["bindings"] = {path: self.bindings[path] for path in sorted(self.bindings)}
        return data


def default_config() -> Config:
    return Config(platform.default_root(), platform.default_bin_dir(), None,
                  DEFAULT_SHARED_ITEMS, {})


def config_path() -> str:
    return os.path.join(platform.state_dir(), "config.json")


def dump_config(config: Config) -> str:
    return json.dumps(config.to_dict(), indent=2, ensure_ascii=False) + "\n"


def load_config() -> Tuple[Config, bool]:
    """读取 config.json，返回 (配置, 文件是否存在)。

    文件不存在时返回默认配置，第一次写命令会把它写出来（方案 §5.1.1 自动初始化）。
    """
    path = config_path()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = handle.read()
    except FileNotFoundError:
        return default_config(), False
    except OSError as exc:
        raise ConfigError("cannot read {}: {}".format(path, exc))
    return parse_config(raw, path), True


def save_config(config: Config) -> None:
    atomic_write(config_path(), dump_config(config), mode=0o600)


def parse_config(raw: str, source: str) -> Config:
    """解析并校验配置文本；任何不合法内容都抛出 ConfigError，并指明出错的字段。"""
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise ConfigError("{} is not valid JSON: {}".format(source, exc))
    if not isinstance(data, dict):
        raise ConfigError("{}: top level must be an object".format(source))
    if data.get("version", CONFIG_VERSION) != CONFIG_VERSION:
        raise ConfigError("{}: unsupported version {!r}".format(source, data.get("version")))

    defaults = default_config()
    root = _string_field(data, "root", defaults.root, source)
    bin_dir = _string_field(data, "bin_dir", defaults.bin_dir, source)

    shared = data.get("shared", {})
    if not isinstance(shared, dict):
        raise ConfigError("{}: 'shared' must be an object".format(source))
    shared_dir = shared.get("dir")
    if shared_dir is not None and (not isinstance(shared_dir, str) or not shared_dir):
        raise ConfigError("{}: 'shared.dir' must be a non-empty string or null".format(source))
    shared_items = shared.get("items", DEFAULT_SHARED_ITEMS)
    if not isinstance(shared_items, list) or not all(_valid_item(item) for item in shared_items):
        raise ConfigError("{}: 'shared.items' must be a list of plain file names".format(source))

    accounts_raw = data.get("accounts", {})
    if not isinstance(accounts_raw, dict):
        raise ConfigError("{}: 'accounts' must be an object".format(source))
    accounts: Dict[str, Account] = {}
    seen = {}
    for name, value in accounts_raw.items():
        if not NAME_PATTERN.match(name):
            raise ConfigError("{}: invalid account name {!r}".format(source, name))
        folded = name.casefold()
        if folded in seen:
            raise ConfigError("{}: account names {!r} and {!r} differ only in case".format(
                source, seen[folded], name))
        seen[folded] = name
        accounts[name] = _parse_account(name, value, source)

    bindings = data.get("bindings", {})
    if not isinstance(bindings, dict):
        raise ConfigError("{}: 'bindings' must be an object".format(source))
    for path, name in bindings.items():
        # 不要求账号已登记：悬空的绑定由 doctor 报告，不让整个配置无法加载。
        if not isinstance(path, str) or not os.path.isabs(path) or not isinstance(name, str) \
                or not NAME_PATTERN.match(name):
            raise ConfigError("{}: invalid binding {!r} -> {!r}".format(source, path, name))
    return Config(root, bin_dir, shared_dir, shared_items, accounts, bindings)


def _string_field(data: dict, key: str, default: str, source: str) -> str:
    value = data.get(key, default)
    if not isinstance(value, str) or not value:
        raise ConfigError("{}: '{}' must be a non-empty string".format(source, key))
    return value


def _valid_item(item: object) -> bool:
    # 共享条目只能是账号目录下的一级名称，不能带路径分隔符或指向上级目录。
    return isinstance(item, str) and item not in ("", ".", "..") and "/" not in item


def _parse_account(name: str, value: object, source: str) -> Account:
    if not isinstance(value, dict):
        raise ConfigError("{}: account {!r} must be an object".format(source, name))
    proxy_raw = value.get("proxy", PROXY_INHERIT)
    try:
        proxy = normalize_proxy(proxy_raw if proxy_raw is not None else PROXY_INHERIT)
    except ValueError as exc:
        raise ConfigError("{}: account {!r}: {}".format(source, name, exc))
    shared = value.get("shared", False)
    if not isinstance(shared, bool):
        raise ConfigError("{}: account {!r}: 'shared' must be true or false".format(source, name))
    links = value.get("managed_links", [])
    if not isinstance(links, list) or not all(_valid_item(item) for item in links):
        raise ConfigError("{}: account {!r}: 'managed_links' must be a list of names".format(source, name))
    env = value.get("env", {})
    if not isinstance(env, dict):
        raise ConfigError("{}: account {!r}: 'env' must be an object".format(source, name))
    for key, item in env.items():
        try:
            validate_env_key(key)
            validate_env_value(item)
        except ValueError as exc:
            raise ConfigError("{}: account {!r}: {}".format(source, name, exc))
    return Account(name, proxy, shared, links, env)


def validate_name(name: str) -> None:
    if not NAME_PATTERN.match(name):
        raise ValueError(
            "invalid account name {!r}: use 1-64 characters from [A-Za-z0-9._@+-], "
            "starting with a letter or digit".format(name))


def validate_env_key(key: object) -> None:
    if not isinstance(key, str) or not ENV_KEY_PATTERN.match(key):
        raise ValueError("invalid environment variable name {!r}: use letters, digits and '_', "
                         "not starting with a digit".format(key))
    if key == "CODEX_HOME":
        raise ValueError("CODEX_HOME is managed by multi-codex and cannot be set per account")
    if key in PROXY_ENV_VARS:
        raise ValueError("{} is a proxy variable; use `multi-codex proxy` for proxy settings".format(key))


def validate_env_value(value: object) -> None:
    if not isinstance(value, str):
        raise ValueError("environment variable values must be strings")
    if "\0" in value:
        # exec 用 C 字符串传递环境变量，NUL 之后的部分会被截掉。
        raise ValueError("environment variable values must not contain NUL characters")


def normalize_proxy(value: object) -> str:
    """把用户输入的代理值规范化为 `inherit`、`off` 或 `scheme://host:port`。

    只写端口号时展开为 http://127.0.0.1:<端口>。
    带用户名密码的 URL 一律拒绝：启动命令是所有人可读的明文文件，写进去就泄露了。
    """
    if not isinstance(value, str) or not value:
        raise ValueError("proxy must be a port number, a URL, 'off' or 'inherit'")
    if value in (PROXY_INHERIT, PROXY_OFF):
        return value
    if value.isdigit():
        port = int(value)
        _check_port(port)
        return "http://127.0.0.1:{}".format(port)
    # 既不是端口也不是 URL（如 `abc`、`127.0.0.1:7901`）：urlsplit 会给出空的或奇怪的 scheme，
    # 原来的“unsupported proxy scheme ''”让人看不懂，这里直接说明可以填什么。
    if "://" not in value:
        raise ValueError("invalid proxy {!r}: use a port number (e.g. 7901), a URL such as "
                         "http://127.0.0.1:7901, 'off' or 'inherit'".format(value))
    parts = urllib.parse.urlsplit(value)
    if parts.scheme not in PROXY_SCHEMES:
        raise ValueError("unsupported proxy scheme {!r}; use one of {}".format(
            parts.scheme, ", ".join(PROXY_SCHEMES)))
    if parts.username is not None or parts.password is not None:
        raise ValueError("proxy URL must not contain credentials")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise ValueError("proxy URL must not contain a path, query or fragment")
    host = parts.hostname
    if not host:
        raise ValueError("proxy URL must contain a host")
    try:
        port = parts.port
    except ValueError:
        raise ValueError("proxy URL has an invalid port")
    if port is None:
        raise ValueError("proxy URL must contain a port")
    _check_port(port)
    if ":" in host:
        host = "[{}]".format(host)
    return "{}://{}:{}".format(parts.scheme, host, port)


def _check_port(port: int) -> None:
    if not 1 <= port <= 65535:
        raise ValueError("proxy port must be between 1 and 65535, got {}".format(port))


def is_socks(proxy: str) -> bool:
    return proxy.startswith("socks5://") or proxy.startswith("socks5h://")
