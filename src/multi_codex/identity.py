"""账号的凭据存储模式与登录身份：只读本地文件，从不联网，也不输出任何令牌。

两类调用方：
- 迁移预检（fix-keyring-migration）：凭据存在系统钥匙串时，迁移会改变目录真实路径，
  Codex 按新路径算出的钥匙串键找不到旧条目，登录就丢了，所以要先知道存储模式；
- list / usage / doctor（feature-account-insight）：显示每个账号登录的邮箱与套餐，检测重复登录。

字段与取值都以 openai/codex 源码为准（基线 6b4daafd），出处见两份方案文档 §1 / §3。
"""

import base64
import json
import os
import re
from typing import Dict, Iterable, List, NamedTuple, Optional, Tuple

STORE_FILE = "file"
STORE_KEYRING = "keyring"
STORE_AUTO = "auto"
STORE_EPHEMERAL = "ephemeral"

# 未设置时 Codex 的默认值（上游 config/defaults.toml:6）。
DEFAULT_STORE = STORE_FILE

# 系统级配置层，优先级低于账号目录里的 config.toml（上游 config/src/loader/mod.rs:79）。
SYSTEM_CONFIG = "/etc/codex/config.toml"
# 测试专用：把系统级配置换成临时文件，避免开发机上真实的 /etc/codex/config.toml 影响结果。
SYSTEM_CONFIG_ENV = "MULTI_CODEX_TEST_SYSTEM_CONFIG"

LOGIN_CHATGPT = "chatgpt"
LOGIN_APIKEY = "apikey"
LOGIN_LOGGED_OUT = "logged-out"
LOGIN_KEYRING = "keyring"
LOGIN_UNREADABLE = "unreadable"
LOGIN_OTHER = "other"

_AUTH_CLAIMS = "https://api.openai.com/auth"
_PROFILE_CLAIMS = "https://api.openai.com/profile"

# 只认顶层的 `cli_auth_credentials_store = "..."`，值可用单引号或双引号，允许行尾注释。
_STORE_LINE = re.compile(r"""^\s*cli_auth_credentials_store\s*=\s*(?:"([^"]*)"|'([^']*)')\s*(?:#.*)?$""")


class Identity(NamedTuple):
    """一个账号目录的登录状态。

    user_id、workspace_id 只用于比较重复登录，不显示、不写进 JSON。
    plan 是 id_token 签发时的套餐，下次刷新令牌后才会更新。
    """
    login: str
    email: Optional[str]
    plan: Optional[str]
    user_id: Optional[str]
    workspace_id: Optional[str]
    store: str


def _system_config_path() -> str:
    return os.environ.get(SYSTEM_CONFIG_ENV) or SYSTEM_CONFIG


def _read_store_key(path: str) -> Optional[str]:
    """在第一个表头之前找 cli_auth_credentials_store；找不到或读不了时返回 None。

    Python 3.8 没有 tomllib，这里只做最小解析。已知局限：多行字符串中以 `[` 开头的行会被当成表头，
    带引号的键名识别不了。两种情况都按“没有这个键”处理，最坏是漏报，与没有这项检查时一样。
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return None
    for line in lines:
        if line.strip().startswith("["):
            # 这个键属于顶层 ConfigToml，写在任何表（如 [profiles.x]）里都不生效。
            return None
        match = _STORE_LINE.match(line)
        if match:
            return match.group(1) if match.group(1) is not None else match.group(2)
    return None


def credentials_store(account_dir: str) -> Tuple[str, Optional[str]]:
    """返回 (存储模式, 取值来源文件)。两层都没有设置时返回 (file, None)。

    按 Codex 的配置层优先级：账号目录的 config.toml（User 层）高于系统级配置（System 层）。
    托管配置、MDM、项目目录 .codex/ 中的设置不在考虑范围内（方案 §2 非目标）。
    """
    for path in (os.path.join(account_dir, "config.toml"), _system_config_path()):
        value = _read_store_key(path)
        if value is not None:
            return value, path
    return DEFAULT_STORE, None


def _decode_jwt_payload(token: str) -> Optional[dict]:
    """只做 base64url 解码，不验签，与 Codex 自己读 id_token 的方式一致（上游 token_data.rs:129-140）。"""
    parts = token.split(".")
    if len(parts) < 2:
        return None
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")).decode("utf-8"))
    except (ValueError, UnicodeError):
        return None
    return claims if isinstance(claims, dict) else None


def _text(value: object) -> Optional[str]:
    return value if isinstance(value, str) and value else None


def read_identity(account_dir: str) -> Identity:
    """读取一个账号目录的登录身份（判定顺序见 feature-account-insight §5.1.1）。

    令牌只在本函数内部解码，不放进返回值，也不出现在任何异常信息里。
    """
    store, _ = credentials_store(account_dir)
    if store == STORE_KEYRING:
        # keyring 模式只读钥匙串、不回落读文件（上游 storage.rs:310-313），
        # 从 file 模式切过来时残留的 auth.json 不代表当前登录。
        return Identity(LOGIN_KEYRING, None, None, None, None, store)
    path = os.path.join(account_dir, "auth.json")
    if not os.path.lexists(path):
        login = LOGIN_KEYRING if store == STORE_AUTO else LOGIN_LOGGED_OUT
        return Identity(login, None, None, None, None, store)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return Identity(LOGIN_UNREADABLE, None, None, None, None, store)
    if not isinstance(data, dict):
        return Identity(LOGIN_UNREADABLE, None, None, None, None, store)

    tokens = data.get("tokens")
    id_token = tokens.get("id_token") if isinstance(tokens, dict) else None
    if isinstance(id_token, str) and id_token:
        claims = _decode_jwt_payload(id_token)
        if claims is None:
            return Identity(LOGIN_UNREADABLE, None, None, None, None, store)
        auth = claims.get(_AUTH_CLAIMS)
        auth = auth if isinstance(auth, dict) else {}
        profile = claims.get(_PROFILE_CLAIMS)
        profile = profile if isinstance(profile, dict) else {}
        email = _text(claims.get("email")) or _text(profile.get("email"))
        user_id = _text(auth.get("chatgpt_user_id")) or _text(auth.get("user_id"))
        workspace_id = _text(auth.get("chatgpt_account_id")) or _text(tokens.get("account_id"))
        return Identity(LOGIN_CHATGPT, email, _text(auth.get("chatgpt_plan_type")), user_id, workspace_id, store)
    if data.get("auth_mode") == "apikey" or _text(data.get("OPENAI_API_KEY")):
        return Identity(LOGIN_APIKEY, None, None, None, None, store)
    return Identity(LOGIN_OTHER, None, None, None, None, store)


def duplicate_groups(identities: Iterable[Tuple[str, Identity]]) -> List[List[str]]:
    """找出登录了同一个 ChatGPT 账号和同一个工作区的账号组（每组至少两个）。

    同一个邮箱在不同工作区登录不算重复：它们的额度互相独立。
    任一 ID 缺失的账号不参与比较，否则两个都缺 ID 的账号会被误判为同一个。
    """
    groups: Dict[Tuple[str, str], List[str]] = {}
    for name, identity in identities:
        if identity.login == LOGIN_CHATGPT and identity.user_id and identity.workspace_id:
            groups.setdefault((identity.user_id, identity.workspace_id), []).append(name)
    return [names for names in groups.values() if len(names) > 1]


def display_login(identity: Identity) -> str:
    """list 表格中 LOGIN 列的取值。"""
    if identity.login == LOGIN_CHATGPT:
        return identity.email or "chatgpt"
    return {LOGIN_APIKEY: "api-key", LOGIN_LOGGED_OUT: "-"}.get(identity.login, identity.login)
