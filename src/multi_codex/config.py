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

# 禁止共享清单（feature-isolation-sharing-rename §5.1.2）：这些条目保存账号自己的凭据、标识、数据库、
# 会话或运行时状态，软链成同一份会让账号互相串号或损坏数据库。与 README「What can be shared」表中
# 标 No 的行一一对应，改这里要同步改 README 两个语言版本。按大小写不敏感比较：macOS 默认文件系统
# 不区分大小写，`Auth.json` 与 `auth.json` 是同一个文件。
UNSHAREABLE_ITEMS = ("auth.json", ".credentials.json", "secrets", ".env", "installation_id", "sqlite",
                     "sessions", "archived_sessions", "session_index.jsonl", "app-server-control",
                     "app-server-daemon", "packages", "tmp", ".tmp", "log", "shell_snapshots")
# SQLite 数据库及其 WAL、共享内存文件（state_5.sqlite、logs_2.sqlite-wal 等，名字带版本号，只能按后缀判断）。
UNSHAREABLE_SUFFIXES = (".sqlite", ".sqlite-wal", ".sqlite-shm")


def is_unshareable(item: str) -> bool:
    folded = item.casefold()
    return folded in UNSHAREABLE_ITEMS or folded.endswith(UNSHAREABLE_SUFFIXES)

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

    shared_exclude 是用户配置：共享开启时不链接的条目（按名称精确匹配 shared.items）。

    dir_name 是账号目录在根目录下的名字，缺省等于账号名。rename 只改账号名、不改它：
    凭据在系统钥匙串时，钥匙串条目按 CODEX_HOME 的路径区分，目录一改就掉登录。
    它是工具内部状态，apply -f 不能修改已登记账号的这个值。
    """

    def __init__(self, name: str, proxy: str = PROXY_INHERIT, shared: bool = False,
                 managed_links: Optional[List[str]] = None, env: Optional[Dict[str, str]] = None,
                 shared_exclude: Optional[List[str]] = None, dir_name: Optional[str] = None,
                 net_check: Optional["NetCheck"] = None) -> None:
        self.name = name
        self.proxy = proxy
        self.shared = shared
        self.managed_links = list(managed_links or [])
        self.env = dict(env or {})
        self.shared_exclude = list(shared_exclude or [])
        self.dir_name = dir_name or name
        # 启动前网络检查设置（feature-network-check §5.1.1）；缺省为关闭。
        self.net_check = net_check.copy() if net_check else NetCheck()

    def copy(self) -> "Account":
        return Account(self.name, self.proxy, self.shared, list(self.managed_links), dict(self.env),
                       list(self.shared_exclude), self.dir_name, self.net_check)

    def to_dict(self) -> dict:
        data = {"proxy": self.proxy, "shared": self.shared, "managed_links": list(self.managed_links)}
        # 没有环境变量时不写这个字段：0.3.0 的配置序列化结果保持不变，升级后的第一条写命令
        # 才不会因为“配置文本变了”而把每个账号都判为 update（accounts.plan 按序列化文本比较）。
        # 按键名排序：apply -f 文件里键的顺序不同，不应被当成改动。
        if self.env:
            data["env"] = {key: self.env[key] for key in sorted(self.env)}
        # 下面两个字段同理：只在有内容时写出，0.8 的配置读入再写回逐字不变。
        if self.shared_exclude:
            data["shared_exclude"] = list(self.shared_exclude)
        if self.dir_name != self.name:
            data["dir"] = self.dir_name
        # 未开启检查时不写：未开启账号的序列化结果与旧版逐字相同（原因同上面 env 的注释）。
        if self.net_check.active:
            data["net_check"] = self.net_check.to_dict()
        return data


class Config(object):
    def __init__(self, root: str, bin_dir: str, shared_dir: Optional[str],
                 shared_items: List[str], accounts: Dict[str, Account],
                 bindings: Optional[Dict[str, str]] = None, clash_api: Optional["ClashApi"] = None) -> None:
        self.root = root
        self.bin_dir = bin_dir
        self.shared_dir = shared_dir
        self.shared_items = list(shared_items)
        # 键保持账号名的原始大小写；查找一律经 find() 做大小写不敏感匹配。
        self.accounts = accounts
        # 目录绑定：规范路径（binding.normalize_dir）-> 账号名。属于本机状态，apply -f 时沿用当前值。
        self.bindings = dict(bindings or {})
        # Clash / mihomo 外部控制接口，整台机器一个（feature-network-check §5.1.1）。属于本机状态，
        # apply -f 时沿用当前值；None 表示未设置。
        self.clash_api = clash_api

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
                      {key: value.copy() for key, value in self.accounts.items()}, dict(self.bindings),
                      self.clash_api)

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
        # 同理：未设置时不写，旧配置读入再写回逐字不变。
        if self.clash_api is not None:
            data["clash_api"] = self.clash_api.to_dict()
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
    for item in shared_items:
        if is_unshareable(item):
            # 所有命令都在读配置时失败，init --shared-items 也改不掉，所以直接告诉用户去编辑哪个文件。
            raise ConfigError("{0}: 'shared.items' must not include {1!r}: it holds account-specific state; "
                              "edit {0} and remove it".format(source, item))

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
    _check_dir_names(accounts, source)

    bindings = data.get("bindings", {})
    if not isinstance(bindings, dict):
        raise ConfigError("{}: 'bindings' must be an object".format(source))
    for path, name in bindings.items():
        # 不要求账号已登记：悬空的绑定由 doctor 报告，不让整个配置无法加载。
        if not isinstance(path, str) or not os.path.isabs(path) or not isinstance(name, str) \
                or not NAME_PATTERN.match(name):
            raise ConfigError("{}: invalid binding {!r} -> {!r}".format(source, path, name))
    clash_api = _parse_clash_api(data.get("clash_api"), source)
    return Config(root, bin_dir, shared_dir, shared_items, accounts, bindings, clash_api)


def _check_dir_names(accounts: Dict[str, Account], source: str) -> None:
    """账号目录名大小写不敏感互不相同，且账号名不等于另一个账号的目录名。

    后一条防的是：rename work job 之后，又登记一个叫 work 的账号——它的目录 <root>/work 正是 job 在用的。
    accounts.dir_conflicts 在收敛计划里按同样规则判冲突，这里兜住手写的配置文件。
    """
    for name, account in accounts.items():
        for other_name, other in accounts.items():
            if other_name == name:
                continue
            if account.dir_name.casefold() == other.dir_name.casefold() or \
                    name.casefold() == other.dir_name.casefold():
                raise ConfigError("{}: account {!r} would use the directory of account {!r}".format(
                    source, name, other_name))


def _string_field(data: dict, key: str, default: str, source: str) -> str:
    value = data.get(key, default)
    if not isinstance(value, str) or not value:
        raise ConfigError("{}: '{}' must be a non-empty string".format(source, key))
    return value


def _valid_item(item: object) -> bool:
    # 共享条目只能是账号目录下的一级名称，不能带路径分隔符或指向上级目录。
    if not (isinstance(item, str) and item not in ("", ".", "..") and "/" not in item):
        return False
    # Windows 上 \ 也是路径分隔符，: 会被解释成盘符或备用数据流，同样不能出现在条目名里。
    return not (platform.IS_WINDOWS and ("\\" in item or ":" in item))


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
    exclude = value.get("shared_exclude", [])
    if not isinstance(exclude, list) or not all(_valid_item(item) for item in exclude):
        raise ConfigError("{}: account {!r}: 'shared_exclude' must be a list of names".format(source, name))
    dir_name = value.get("dir", name)
    if not isinstance(dir_name, str) or not NAME_PATTERN.match(dir_name):
        raise ConfigError("{}: account {!r}: 'dir' must be a valid account directory name".format(source, name))
    net_check = _parse_net_check(value.get("net_check"), "{}: account {!r}".format(source, name))
    return Account(name, proxy, shared, links, env, unique_items(exclude), dir_name, net_check)


def unique_items(items: List[str]) -> List[str]:
    """去重并保持首次出现的顺序。"""
    result: List[str] = []
    for item in items:
        if item not in result:
            result.append(item)
    return result


def validate_name(name: str) -> None:
    if not NAME_PATTERN.match(name):
        raise ValueError(
            "invalid account name {!r}: use 1-64 characters from [A-Za-z0-9._@+-], "
            "starting with a letter or digit".format(name))


def validate_env_key(key: object) -> None:
    if not isinstance(key, str) or not ENV_KEY_PATTERN.match(key):
        raise ValueError("invalid environment variable name {!r}: use letters, digits and '_', "
                         "not starting with a digit".format(key))
    # Windows 的环境变量名不区分大小写，codex_home、Http_Proxy 与保留名是同一个变量；POSIX 仍区分大小写。
    folded = key.upper() if platform.IS_WINDOWS else key
    if folded == "CODEX_HOME":
        raise ValueError("CODEX_HOME is managed by multi-codex and cannot be set per account")
    if folded in (PROXY_ENV_VARS if not platform.IS_WINDOWS else [name.upper() for name in PROXY_ENV_VARS]):
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


NET_CHECK_LEVELS = ("off", "basic", "strict")
# 本次跳过网络检查的环境变量。放在 config 而不是 netcheck，是为了让生成启动命令的 launcher
# 不必导入联网模块（http.client、ssl），未开启检查的账号启动时不多付导入开销。
NETCHECK_SKIP_ENV = "MULTI_CODEX_SKIP_NETCHECK"
DEFAULT_COUNTRY = "US"
COUNTRY_PATTERN = re.compile(r"^[A-Za-z]{2}$")


class NetCheck(object):
    """账号的启动前网络检查设置（feature-network-check §5.1.1）。

    level：off 不检查代理；basic 只要求账号代理端口能连通；strict 另要求直连与经代理访问
    chatgpt.com/cdn-cgi/trace 得到的 loc 都等于 country。
    clash_tun：要求 Clash / mihomo 的 TUN 已开启且不是 direct 模式（direct 下 TUN 接住的流量照样直连）；
    clash_global：要求 Clash / mihomo 为全局模式。两项与 level 相互独立（feature-clash-split §5.1.1）。
    country 在非 strict 档位也保存，切到 strict 时生效。
    """

    def __init__(self, level: str = "off", country: str = DEFAULT_COUNTRY, clash_tun: bool = False,
                 clash_global: bool = False) -> None:
        self.level = level
        self.country = country
        self.clash_tun = clash_tun
        self.clash_global = clash_global

    @property
    def clash(self) -> bool:
        """是否开启了任一项 Clash 检查。"""
        return self.clash_tun or self.clash_global

    @property
    def active(self) -> bool:
        """是否需要在启动前检查；为假时启动命令与旧版逐字相同。"""
        return self.level != "off" or self.clash

    def copy(self) -> "NetCheck":
        return NetCheck(self.level, self.country, self.clash_tun, self.clash_global)

    def to_dict(self) -> dict:
        # 只写新字段：旧字段 clash 只读不写，配置有改动、整体重写时自然换成新写法。
        return {"level": self.level, "country": self.country, "clash_tun": self.clash_tun,
                "clash_global": self.clash_global}


class ClashApi(object):
    r"""Clash / mihomo 外部控制接口。

    address 已规范化为三种形态之一：`http://HOST:PORT`、`unix:/绝对路径`（仅 POSIX）、
    `\\.\pipe\名称`（仅 Windows）。secret 只用于 TCP 形态（mihomo 的 unix 套接字与命名管道不鉴权），
    空字符串表示不带。它和账号 env 一样存在 0600 的 config.json 里。
    """

    def __init__(self, address: str, secret: str = "") -> None:
        self.address = address
        self.secret = secret

    def to_dict(self) -> dict:
        data = {"address": self.address}
        if self.secret:
            data["secret"] = self.secret
        return data


def normalize_country(value: object) -> str:
    """两位字母国家代码，存大写。"""
    if not isinstance(value, str) or not COUNTRY_PATTERN.match(value):
        raise ValueError("invalid country {!r}: use a two-letter code such as US".format(value))
    return value.upper()


PIPE_PREFIX = "\\\\.\\pipe\\"


def normalize_clash_address(value: object) -> str:
    r"""把 Clash 接口地址规范化为 `http://HOST:PORT`、`unix:/路径` 或 `\\.\pipe\名称`。

    mihomo 的命名管道名必须以 `\\.\pipe\` 开头（hub/route/server.go 的 startPipe），这里同样要求。
    """
    if not isinstance(value, str) or not value:
        raise ValueError("Clash controller address must be a non-empty string")
    if value.startswith("pipe:"):
        value = value[len("pipe:"):]
    if value.lower().startswith(PIPE_PREFIX):
        if not platform.IS_WINDOWS:
            raise ValueError("named pipe addresses are only supported on Windows")
        if len(value) == len(PIPE_PREFIX):
            raise ValueError("named pipe address must include a pipe name")
        return value
    if value.startswith("unix:"):
        path = value[len("unix:"):]
        if platform.IS_WINDOWS:
            raise ValueError("unix socket addresses are not supported on Windows; use a TCP address "
                             "or \\\\.\\pipe\\NAME")
        if not os.path.isabs(path):
            raise ValueError("unix socket address must be an absolute path, e.g. unix:/tmp/mihomo.sock")
        return "unix:" + path
    if "://" not in value:
        value = "http://" + value
    parts = urllib.parse.urlsplit(value)
    if parts.scheme != "http":
        raise ValueError("unsupported Clash controller scheme {!r}; use HOST:PORT, http://HOST:PORT, "
                         "unix:/PATH or \\\\.\\pipe\\NAME".format(parts.scheme))
    if parts.username is not None or parts.password is not None or parts.path not in ("", "/") \
            or parts.query or parts.fragment:
        raise ValueError("Clash controller address must be HOST:PORT without a path; "
                         "pass the secret with --secret-stdin")
    host = parts.hostname
    try:
        port = parts.port
    except ValueError:
        raise ValueError("Clash controller address has an invalid port")
    if not host or port is None:
        raise ValueError("Clash controller address must contain a host and a port")
    if not 1 <= port <= 65535:
        raise ValueError("Clash controller port must be between 1 and 65535, got {}".format(port))
    if ":" in host:
        host = "[{}]".format(host)
    return "http://{}:{}".format(host, port)


def _parse_net_check(value: object, where: str) -> NetCheck:
    if value is None:
        return NetCheck()
    if not isinstance(value, dict):
        raise ConfigError("{}: 'net_check' must be an object".format(where))
    level = value.get("level", "off")
    if level not in NET_CHECK_LEVELS:
        raise ConfigError("{}: 'net_check.level' must be one of {}".format(where, ", ".join(NET_CHECK_LEVELS)))
    try:
        country = normalize_country(value.get("country", DEFAULT_COUNTRY))
    except ValueError as exc:
        raise ConfigError("{}: 'net_check.country': {}".format(where, exc))
    if "clash_tun" in value or "clash_global" in value:
        # 新字段优先；同时残留的旧字段 clash 忽略。
        flags = []
        for key in ("clash_tun", "clash_global"):
            flag = value.get(key, False)
            if not isinstance(flag, bool):
                raise ConfigError("{}: 'net_check.{}' must be true or false".format(where, key))
            flags.append(flag)
        return NetCheck(level, country, flags[0], flags[1])
    # 旧写法 "clash": true 表示 TUN 与全局都要求，与拆分前的检查行为一致。
    clash = value.get("clash", False)
    if not isinstance(clash, bool):
        raise ConfigError("{}: 'net_check.clash' must be true or false".format(where))
    return NetCheck(level, country, clash, clash)


def _parse_clash_api(value: object, source: str) -> Optional[ClashApi]:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ConfigError("{}: 'clash_api' must be an object".format(source))
    try:
        address = normalize_clash_address(value.get("address"))
    except ValueError as exc:
        raise ConfigError("{}: 'clash_api.address': {}".format(source, exc))
    secret = value.get("secret", "")
    if not isinstance(secret, str) or "\n" in secret or "\r" in secret:
        raise ConfigError("{}: 'clash_api.secret' must be a single-line string".format(source))
    return ClashApi(address, secret)
