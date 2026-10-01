"""体检：只读地检查安装、配置、环境变量、各账号状态，以及配置与实际文件是否一致。

不修复任何东西：已有的 `apply` 就是修复手段，这里只给出建议命令。不联网，不运行 `codex login status`。
检查项与 fail / warn 条件见 feature-account-insight §5.1.3。
"""

import os
import re
import shutil
import subprocess
from typing import List, NamedTuple, Optional, Tuple

from . import accounts, identity, migrate, platform, switch
from .actions import CONFLICT, CREATE, DELETE, SKIP, UPDATE
from .config import ConfigError, load_config
from .fsutil import KIND_DIR, KIND_LINK, KIND_MISSING, entry_kind, expand

OK = "ok"
WARN = "warn"
FAIL = "fail"

# usage --live 依赖的 account/rateLimits/read 从 0.48.0 起提供。
MIN_LIVE_CODEX_VERSION = (0, 48, 0)
_VERSION_TIMEOUT_SEC = 10
_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")


class Check(NamedTuple):
    id: str
    status: str
    message: str
    hint: Optional[str] = None
    details: Tuple[str, ...] = ()


def run_checks() -> List[Check]:
    """依次执行全部检查。配置无法读取时，后面依赖配置的检查全部跳过。"""
    checks = [_check_codex()]
    try:
        config, exists = load_config()
    except ConfigError as exc:
        checks.append(Check("config", FAIL, str(exc)))
        checks.append(_check_migration())
        return checks
    if not exists:
        checks.append(Check("config", WARN, "no configuration yet", "multi-codex add NAME"))
        checks.append(_check_migration())
        checks.append(_check_env())
        return checks
    checks.append(Check("config", OK, "loaded {} account(s)".format(len(config.accounts))))
    checks.append(_check_migration())
    checks.append(_check_path(expand(config.bin_dir)))
    checks.append(_check_env())
    checks.append(_check_default_dir(config))
    checks.append(_check_drift(config))
    identities = []
    for name in config.accounts:
        check, found = _check_account(config, name)
        checks.append(check)
        if found is not None:
            identities.append((name, found))
    checks.append(_check_duplicates(identities))
    return checks


def _check_codex() -> Check:
    path = shutil.which("codex")
    if path is None:
        return Check("codex", FAIL, "codex was not found in PATH", "install Codex CLI and put it on PATH")
    try:
        proc = subprocess.run([path, "--version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              universal_newlines=True, timeout=_VERSION_TIMEOUT_SEC)
        output = proc.stdout.strip()
    except subprocess.TimeoutExpired:
        return Check("codex", WARN, "{} --version did not finish within {}s".format(path, _VERSION_TIMEOUT_SEC))
    except OSError as exc:
        return Check("codex", WARN, "cannot run {} --version: {}".format(path, exc))
    match = _VERSION_RE.search(output)
    if match is None:
        return Check("codex", WARN, "cannot parse the version of {}: {!r}".format(path, output[:100]))
    version = tuple(int(part) for part in match.groups())
    message = "{} ({})".format(output.splitlines()[-1] if output else match.group(0), path)
    if version < MIN_LIVE_CODEX_VERSION:
        return Check("codex", WARN, message + "; `usage --live` needs Codex 0.48.0 or newer", "upgrade Codex")
    return Check("codex", OK, message)


def _check_migration() -> Check:
    try:
        notice = migrate.pending_journal_notice()
    except migrate.JournalError as exc:
        return Check("migration", FAIL, str(exc),
                     "move the complete data back to the source path, then delete {}".format(migrate.journal_path()))
    if notice:
        return Check("migration", FAIL, notice, "rerun the same `multi-codex migrate-default` command")
    # restore 做到一半同样会拦下其它写命令，这里一并报告。
    try:
        restore = switch.load_restore_journal()
    except switch.RestoreJournalError as exc:
        return Check("migration", FAIL, str(exc), "check ~/.codex and the account directory, then delete {}".format(
            switch.restore_journal_path()))
    if restore:
        return Check("migration", FAIL, "unfinished restore of {}".format(restore["name"]),
                     "multi-codex restore {}".format(restore["name"]))
    return Check("migration", OK, "no unfinished migration or restore")


def _check_path(bin_dir: str) -> Check:
    entries = [os.path.realpath(expand(entry)) for entry in os.environ.get("PATH", "").split(os.pathsep) if entry]
    if os.path.realpath(bin_dir) in entries:
        return Check("path", OK, "{} is in PATH".format(bin_dir))
    return Check("path", WARN, "{} is not in PATH; codex-<name> launchers cannot be found".format(bin_dir),
                 "add {} to PATH in your shell profile".format(bin_dir))


def _check_env() -> Check:
    found = [name for name in ("CODEX_HOME",) + platform.ISOLATION_BREAKING_ENV if os.environ.get(name)]
    if not found:
        return Check("env", OK, "no variables that override per-account isolation")
    return Check("env", WARN, "set in this shell: {}; they override or bypass per-account isolation".format(
        ", ".join(found)), "unset {}".format(" ".join(found)))


def _check_default_dir(config) -> Check:
    # default_source() 返回字面量 "~/.codex"，必须先展开；输出里仍用短写法 source 方便阅读。
    source = platform.default_source()
    path = expand(source)
    kind = entry_kind(path)
    if kind == KIND_MISSING:
        return Check("default-dir", OK, "{} does not exist".format(source))
    if kind == KIND_DIR:
        return Check("default-dir", OK, "{} is a real directory (not migrated)".format(source))
    if kind == KIND_LINK:
        real = os.path.realpath(path)
        for name in config.accounts:
            if os.path.realpath(accounts.account_dir(config, name)) == real:
                return Check("default-dir", OK, "{} links to account {}".format(source, name))
        return Check("default-dir", WARN, "{} links to {}, which is not a registered account".format(source, real))
    return Check("default-dir", WARN, "{} is neither a directory nor a link".format(source))


def _check_drift(config) -> Check:
    """复用收敛计划判断配置与文件是否一致。plan 只读文件系统，副作用都在动作的 run 回调里，这里不执行。

    第二个参数必须传副本：plan_shared 会就地改写 new 的 managed_links（shared.py:92），
    传同一个对象会让 old 也被改掉，计划结果就不再反映真实差异。
    """
    actions = accounts.plan(config, config.copy(), orphan_scope=accounts.ALL_ORPHANS)
    conflicts = [action for action in actions if action.status == CONFLICT]
    pending = [action for action in actions if action.status in (CREATE, UPDATE, DELETE)]
    # skip 是持久状态（例如共享目录里本来就没有某项），apply 也消除不了，只作为说明列出。
    notes = [action for action in actions if action.status == SKIP]
    details = tuple(_describe(action) for action in conflicts + pending + notes)
    if conflicts:
        return Check("drift", FAIL, "{} conflict(s) with files multi-codex does not own".format(len(conflicts)),
                     "resolve the conflicts listed below by hand", details)
    if pending:
        return Check("drift", WARN, "{} change(s) pending".format(len(pending)), "multi-codex apply", details)
    return Check("drift", OK, "files match the configuration", None, details)


def _describe(action) -> str:
    text = "{} {} {}".format(action.status, action.kind, action.path)
    return text + (" ({})".format(action.reason) if action.reason else "")


def _check_account(config, name: str) -> Tuple[Check, Optional[identity.Identity]]:
    check_id = "account:" + name
    directory = accounts.account_dir(config, name)
    if not os.path.isdir(directory):
        return Check(check_id, FAIL, "account directory {} does not exist".format(directory),
                     "multi-codex apply"), None
    found = identity.read_identity(directory)
    problems = []
    hint = None
    if found.login == identity.LOGIN_LOGGED_OUT:
        problems.append("not logged in")
        hint = "codex-{} login".format(name)
    elif found.login == identity.LOGIN_UNREADABLE:
        problems.append("auth.json cannot be read")
        hint = "codex-{} login".format(name)
    elif found.login == identity.LOGIN_KEYRING:
        problems.append("credentials are in the system keyring (cli_auth_credentials_store = {!r}); "
                        "the login cannot be read from files".format(found.store))
    if os.path.islink(os.path.join(directory, "sessions")):
        problems.append("sessions is a link; local usage data may belong to another account")
    if problems:
        return Check(check_id, WARN, "; ".join(problems), hint), found
    return Check(check_id, OK, _identity_summary(found)), found


def _identity_summary(found: identity.Identity) -> str:
    if found.login == identity.LOGIN_CHATGPT:
        return "logged in as {}{}".format(found.email or "(no email)",
                                          " ({})".format(found.plan) if found.plan else "")
    if found.login == identity.LOGIN_APIKEY:
        return "logged in with an API key"
    return "login type: {}".format(found.login)


def _check_duplicates(identities) -> Check:
    groups = identity.duplicate_groups(identities)
    if not groups:
        return Check("duplicates", OK, "no two accounts share one ChatGPT login")
    return Check("duplicates", WARN, "; ".join("{} are logged in as the same ChatGPT account".format(
        ", ".join(group)) for group in groups), "log one of them in with another ChatGPT account")


def to_json(checks: List[Check]) -> dict:
    return {
        "version": 1,
        "ok": not any(check.status == FAIL for check in checks),
        "checks": [{"id": check.id, "status": check.status, "message": check.message, "hint": check.hint,
                    "details": list(check.details)} for check in checks],
    }


def format_checks(checks: List[Check]) -> List[str]:
    width = max(len(check.id) for check in checks)
    lines = []
    for check in checks:
        lines.append("{:<4}  {}  {}".format(check.status, check.id.ljust(width), check.message))
        for detail in check.details:
            lines.append("      {}".format(detail))
        if check.hint and check.status != OK:
            lines.append("      fix: {}".format(check.hint))
    counts = {status: sum(1 for check in checks if check.status == status) for status in (OK, WARN, FAIL)}
    lines.append("{} ok, {} warn, {} fail".format(counts[OK], counts[WARN], counts[FAIL]))
    return lines
