"""会话列表：只读找出各账号目录下的 Codex 会话（方案 feature-session-list）。

会话文件在 <账号目录>/sessions/<年>/<月>/<日>/rollout-*.jsonl 与 <账号目录>/archived_sessions/rollout-*.jsonl，
会话名称另存在 <账号目录>/session_index.jsonl。记录格式未写入官方文档，本模块对任何一行都按“可能缺字段、
类型不对、解析失败”处理，取不到的字段按 SessionInfo 的说明兜底，绝不因为一份坏文件让整条命令失败。

单个会话文件可达上千 MB：只读第一行、尾部一块，没有名称时再从头读到首条提问为止（有字节上限），不通读文件。
本模块不写任何文件，也不读凭据与 SQLite 数据库。
"""

import json
import os
import re
import unicodedata
from typing import Dict, List, NamedTuple, Optional, Tuple

from .usage import _parse_timestamp

# 文件名里的时间是本地时间、精确到秒，只用来认文件；会话 ID 取文件名里的 UUID，它只含十六进制和 -，
# 不以 - 开头，可以直接拼进续聊命令。fullmatch：match 加 $ 会放过结尾带换行的文件名。
ROLLOUT_NAME = re.compile(r"rollout-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-"
                          r"([0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12})\.jsonl")
# 尾部只为取最后的时间戳：本机 640 个会话里，尾部 64 KB 的最后一个时间戳与通读全文件的结果全部一致
TAIL_BYTES = 64 * 1024
# 从文件开头最多读这么多字节找首条提问；本机首条提问 99% 在 431 KB 以内，更靠后的显示为没有标题
HEAD_BUDGET = 1024 * 1024
SESSION_INDEX = "session_index.jsonl"

TITLE_NAME = "name"
TITLE_PROMPT = "prompt"
TITLE_NONE = "none"

# Codex 自动插在用户消息里的项目说明，不是用户敲的提问
_AGENTS_PREFIX = "# AGENTS.md instructions"


class SessionInfo(NamedTuple):
    session_id: str
    # 会话启动时的工作目录（续聊要先 cd 到这里）；取不到可信的值时为 None
    directory: Optional[str]
    # 未清洗、未截断的原文；来自会话内容，输出前必须过 clean_title
    title: str
    # name、prompt 或 none
    title_source: str
    # 会话开始时间（UTC 秒）；取不到时为 None。不用文件创建时间：复制过的文件是复制时间
    created: Optional[float]
    # 最后一行带时间戳的记录的时间（UTC 秒）；取不到时退到文件修改时间
    updated: float
    # 在 archived_sessions/ 下，即在 Codex 里归档过
    archived: bool
    path: str


def list_sessions(account_dir: str) -> Tuple[List[SessionInfo], int]:
    """只读列出账号目录下的会话（不含子代理会话），返回 (会话列表, 读不了而跳过的文件数)。

    目录不存在或读不了视为没有会话；只有“看得见但打不开”的会话文件才计入跳过数。
    """
    names = read_names(account_dir)
    found: List[SessionInfo] = []
    skipped = 0
    for path, archived in _rollout_files(account_dir):
        try:
            session = read_session(path, names, archived)
        except OSError:
            skipped += 1
            continue
        if session is not None:
            found.append(session)
    return found, skipped


def _rollout_files(account_dir: str) -> List[Tuple[str, bool]]:
    """sessions/<年>/<月>/<日>/ 与 archived_sessions/ 下文件名合规的常规文件；更深或更浅的一律不看。"""
    files: List[Tuple[str, bool]] = []
    day_dirs = [os.path.join(account_dir, "sessions", year, month, day)
                for year in _listdir(os.path.join(account_dir, "sessions"))
                for month in _listdir(os.path.join(account_dir, "sessions", year))
                for day in _listdir(os.path.join(account_dir, "sessions", year, month))]
    for directory, archived in [(day, False) for day in day_dirs] + \
            [(os.path.join(account_dir, "archived_sessions"), True)]:
        for name in _listdir(directory):
            path = os.path.join(directory, name)
            if ROLLOUT_NAME.fullmatch(name) and os.path.isfile(path):
                files.append((path, archived))
    return files


def _listdir(directory: str) -> List[str]:
    try:
        return sorted(os.listdir(directory))
    except OSError:
        # 不存在、不是目录或读不了：里面有没有会话无从得知，按没有处理
        return []


def read_names(account_dir: str) -> Dict[str, str]:
    """session_index.jsonl 里的会话名称 {会话ID: 名称}；同一 ID 改过名时后写的生效。读不了时返回空表。"""
    names: Dict[str, str] = {}
    try:
        with open(os.path.join(account_dir, SESSION_INDEX), "rb") as handle:
            for raw in handle:
                record = _record(raw)
                if record is not None and isinstance(record.get("id"), str) \
                        and isinstance(record.get("thread_name"), str):
                    names[record["id"]] = record["thread_name"]
    except OSError:
        return {}
    return names


def read_session(path: str, names: Dict[str, str], archived: bool) -> Optional[SessionInfo]:
    """从一个会话文件取列表要用的字段；子代理会话返回 None。读不了时抛 OSError。"""
    match = ROLLOUT_NAME.fullmatch(os.path.basename(path))
    if match is None:
        return None
    session_id = match.group(1)
    with open(path, "rb") as handle:
        stat = os.fstat(handle.fileno())
        first = handle.readline(HEAD_BUDGET + 1)
        meta = _record(first)
        payload = meta.get("payload") if meta is not None and meta.get("type") == "session_meta" else None
        directory = None
        created = None
        if isinstance(payload, dict):
            # 子代理会话的 source 是对象（{"subagent": …}）；用户发起的会话是 cli、vscode、exec 等字符串
            if "source" in payload and not isinstance(payload["source"], str):
                return None
            directory = payload.get("cwd") if _usable_cwd(payload.get("cwd")) else None
            created = _parse_timestamp(payload.get("timestamp"))
            if created is None:
                created = _parse_timestamp(meta.get("timestamp"))
        title, source = names.get(session_id, ""), TITLE_NAME
        if not clean_title(title, len(title)):
            # 名称只有空白或控制字符时打印出来是空的，不能挡住首条提问
            title, source = "", TITLE_NONE
            if first.endswith(b"\n"):
                prompt = _first_prompt(handle, HEAD_BUDGET - len(first))
                if prompt:
                    title, source = prompt, TITLE_PROMPT
        handle.seek(max(0, stat.st_size - TAIL_BYTES))
        updated = None
        for raw in handle.read(TAIL_BYTES).split(b"\n"):
            record = _record(raw)
            if record is not None:
                stamp = _parse_timestamp(record.get("timestamp"))
                if stamp is not None:
                    updated = stamp
    if updated is None:
        updated = stat.st_mtime
    return SessionInfo(session_id, directory, title, source, created, updated, archived, path)


def _first_prompt(handle, budget: int) -> str:
    """接着当前位置逐行读，返回第一条用户自己敲的提问；最多再读 budget 字节，没有时返回空串。"""
    used = 0
    while used < budget:
        # 带上限读：一行比剩余额度还长时只读到额度为止，不把整行（可能几十 MB 的工具输出）读进内存
        raw = handle.readline(budget - used + 1)
        if not raw:
            break
        used += len(raw)
        if not raw.endswith(b"\n") and used > budget:
            break
        record = _record(raw)
        if record is not None:
            prompt = _plain_prompt(record)
            if prompt:
                return prompt
    return ""


def _plain_prompt(record: dict) -> str:
    """用户消息行里第一段用户自己敲的文字；其它行返回空串。

    Codex 会把环境说明（<environment_context> 等，以 < 开头）和 AGENTS.md 内容也作为用户消息写入，跳过它们。
    旧版本的会话文件用 event_msg / user_message 记录提问，同样认。
    """
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return ""
    texts: List[object] = []
    if record.get("type") == "response_item" and payload.get("type") == "message" and payload.get("role") == "user":
        content = payload.get("content")
        if isinstance(content, list):
            texts = [block.get("text") for block in content
                     if isinstance(block, dict) and block.get("type") == "input_text"]
    elif record.get("type") == "event_msg" and payload.get("type") == "user_message":
        texts = [payload.get("message")]
    for text in texts:
        if isinstance(text, str):
            stripped = text.strip()
            # 按清洗后的结果判断非空：只有控制字符的提问打印出来是空的，不能挡住后面的提问
            if not stripped.startswith("<") and not stripped.startswith(_AGENTS_PREFIX) \
                    and clean_title(text, len(text)):
                return text
    return ""


def _record(raw: bytes) -> Optional[dict]:
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except (ValueError, RecursionError):
        # RecursionError：嵌套上千层的 JSON 行。它不是 ValueError，漏掉它一个坏文件就让整条命令带着调用栈退出
        return None
    return data if isinstance(data, dict) else None


# 不能原样打印的字符：控制字符（Cc，含换行与终端转义的 ESC）、格式字符（Cf，如能把后面的文字
# 倒过来显示的 U+202E）、行分隔符与段分隔符（Zl、Zp），以及孤立代理字符（Cs：JSON 里只有半个表情的
# \ud83d 会被解成它，print 时抛 UnicodeEncodeError，整条命令带着调用栈退出）
_UNPRINTABLE = frozenset(("Cc", "Cf", "Zl", "Zp", "Cs"))
# 零宽非连接符与零宽连接符也属于 Cf，但它们是正常文字的一部分（波斯语等文字的拼写、组合表情），
# 也不会改变别的字符的显示：删掉会拆开表情，目录名带着它的会话还会丢掉 cd
KEPT_FORMAT_CHARS = frozenset(("\u200c", "\u200d"))


def _unprintable(char: str) -> bool:
    return unicodedata.category(char) in _UNPRINTABLE and char not in KEPT_FORMAT_CHARS


def _usable_cwd(value: object) -> bool:
    # 目录要原样拼进续聊命令；shlex.quote 不去换行与控制字符，含这些字符的目录会破坏输出，也可能改写终端显示。
    # 必须是绝对路径：相对路径换个地方 cd 就不是原来的目录，`-` 还会被 cd 当成“上一个目录”
    return isinstance(value, str) and os.path.isabs(value) and not any(_unprintable(char) for char in value)


def clean_title(text: str, limit: int) -> str:
    """把来自会话内容的文字变成能安全打印的一行：空白压成一个空格，删控制与格式字符，超长截断加 …。

    名称与提问是会话里的任意文字，可能含换行和终端转义序列，原样打印会破坏表格，转义序列还能改写终端显示。
    """
    flat = " ".join(text.split())
    flat = "".join(char for char in flat if not _unprintable(char)).strip()
    return flat if len(flat) <= limit else flat[:limit].rstrip() + "…"
