"""切换默认账号（use）与撤销迁移（restore），见 docs/feature/feature-default-switch.md。

“默认账号”就是 `~/.codex` 软链指向的账号目录：直接运行的 codex、桌面端和 IDE 扩展都用它。

为什么切换前必须确认没有进程在用旧账号：没设置 CODEX_HOME 的 Codex 进程只记住字面路径 `~/.codex`，
之后每次读写都按这个路径重新打开文件（上游 utils/home-dir/src/lib.rs:52-61）。运行中改了软链，
它已打开的句柄还在旧账号、新打开的文件却落到新账号；刷新 token 时甚至可能把旧账号的 token
写进新账号的 auth.json（上游 login/src/auth/manager.rs:1600-1622）。

restore 的进度标记 restore-journal.json：
- 写于移动任何东西之前，第 3 步注销成功后才删除；
- 标记存在期间，cli 拦下其它写命令（见 cli._blocked_by_migration），否则 use / migrate-default
  可能在 restore 做到一半时新建或改动 ~/.codex，让它再也无法完成；
- 用来区分“restore 做到一半”和“用户手工删掉了账号目录”，两者的文件状态相同。
调用方（cli.dispatch）已持有写锁。
"""

import json
import os
from typing import Optional, Tuple

from . import accounts, binding, launcher, migrate, platform
from .actions import error, info, warn
from .config import Config
from .fsutil import KIND_DIR, KIND_LINK, KIND_MISSING, atomic_write, entry_kind, expand

STATE_A = "A"
STATE_A2 = "A'"
STATE_B = "B"
STATE_C = "C"


class RestoreJournalError(Exception):
    """restore 标记存在但读不出来；不能猜测进度，交给用户手工检查。"""


def default_link() -> str:
    # default_source() 返回字面量 "~/.codex"，必须展开后才能操作。
    return expand(platform.default_source())


def restore_journal_path() -> str:
    return os.path.join(platform.state_dir(), "restore-journal.json")


def load_restore_journal() -> Optional[dict]:
    path = restore_journal_path()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            journal = json.load(handle)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise RestoreJournalError("cannot read the restore marker {}: {}".format(path, exc))
    if not isinstance(journal, dict) or any(not isinstance(journal.get(key), str)
                                            for key in ("name", "source", "target")):
        raise RestoreJournalError("the restore marker {} is incomplete".format(path))
    return journal


def pending_restore_notice() -> Optional[str]:
    journal = load_restore_journal()
    if journal is None:
        return None
    return "an unfinished `restore {0}` exists; rerun `multi-codex restore {0}` to finish it".format(journal["name"])


def _registered_owner(config: Config, real_path: str) -> Optional[str]:
    for name in config.accounts:
        if os.path.realpath(accounts.account_dir(config, name)) == real_path:
            return name
    return None


def describe_default(config: Config) -> Tuple[Optional[str], str]:
    """返回 (默认账号名或 None, 给人看的说明)，供 `use`（不带参数）和 `list` 使用。"""
    link = default_link()
    kind = entry_kind(link)
    if kind == KIND_MISSING:
        return None, "(none)"
    if kind == KIND_DIR:
        return None, "(not migrated: ~/.codex is a real directory)"
    if kind == KIND_LINK:
        real = os.path.realpath(link)
        owner = _registered_owner(config, real)
        if owner is not None:
            return owner, owner
        return None, "(unregistered: {})".format(real)
    return None, "(~/.codex is neither a directory nor a link)"


def use_account(config: Config, name: str, skip_process_check: bool, dry_run: bool) -> int:
    account = config.find(name)
    if account is None:
        error(config.not_registered(name), phase="use")
        return accounts.EXIT_ERROR
    target = accounts.account_dir(config, account.name)
    # 上一次 use 在 Windows 上中断留下的记录（POSIX 不写，这里什么也不做）
    code = recover_use_journal(dry_run)
    if code is not None:
        return code
    # 不跟随软链：账号目录本身是软链时，~/.codex 会变成“软链指向软链”，与 restore 的前提不一致。
    if entry_kind(target) != KIND_DIR:
        error("account directory is not a real directory", phase="use", path=target)
        return accounts.EXIT_ERROR
    link = default_link()
    kind = entry_kind(link)
    current = None
    if kind == KIND_LINK:
        current = os.path.realpath(link)
        if current == os.path.realpath(target):
            info("already the default account: {}".format(account.name))
            return accounts.EXIT_OK
        if _registered_owner(config, current) is None:
            error("~/.codex links to {}, which is not a registered account; nothing was changed".format(current),
                  phase="use", path=link)
            return accounts.EXIT_CONFLICT
        if skip_process_check:
            warn("--skip-process-check: a running Codex that uses ~/.codex may mix files of the two accounts")
        else:
            # 只查旧账号目录：正在用它的进程会在切换后读写到新账号；查不出占用不代表绝对安全，见方案 §1.1。
            code = migrate._busy_check(current)
            if code:
                return code
    elif kind == KIND_DIR:
        error("~/.codex is a real directory; run `multi-codex migrate-default NAME` first", phase="use", path=link)
        return accounts.EXIT_CONFLICT
    elif kind != KIND_MISSING:
        error("~/.codex is neither a directory nor a link", phase="use", path=link)
        return accounts.EXIT_CONFLICT

    if dry_run:
        info("(dry-run) would point ~/.codex at {}".format(target))
        return accounts.EXIT_OK
    if platform.IS_WINDOWS:
        code = _use_windows(link, kind, current, target)
        if code != accounts.EXIT_OK:
            return code
        info("default account is now {} (~/.codex -> {}); restart the Codex desktop app and IDE extensions".format(
            account.name, target))
        if os.environ.get("CODEX_HOME"):
            warn("CODEX_HOME is set in this shell; running `codex` here ignores ~/.codex")
        return accounts.EXIT_OK
    temp_link = "{}.multi-codex-tmp.{}".format(link, os.getpid())
    try:
        platform.create_link(target, temp_link)
        migrate._test_hook("use-replace")
        # 用 rename 原子替换：任何时刻 ~/.codex 要么指向旧账号、要么指向新账号，不会暂时不存在。
        os.replace(temp_link, link)
    except Exception as exc:  # noqa: BLE001 —— 任何失败都要清掉临时软链，~/.codex 保持原样
        if os.path.lexists(temp_link):
            os.unlink(temp_link)
        error("{}: {}".format(type(exc).__name__, exc), phase="use", path=link)
        return accounts.EXIT_ERROR
    info("default account is now {} (~/.codex -> {}); restart the Codex desktop app and IDE extensions".format(
        account.name, target))
    if os.environ.get("CODEX_HOME"):
        warn("CODEX_HOME is set in this shell; running `codex` here ignores ~/.codex")
    return accounts.EXIT_OK


def _restore_state(config: Config, name: str, link: str, target: str,
                   journal: Optional[dict]) -> Tuple[Optional[str], Optional[int], str]:
    """判断 restore 从哪一步开始。返回 (状态, 直接结束时的退出码, 说明)。"""
    link_kind, target_kind = entry_kind(link), entry_kind(target)
    points_to_target = link_kind == KIND_LINK and os.path.realpath(link) == os.path.realpath(target)
    registered = config.find(name) is not None
    if journal is None:
        if points_to_target and target_kind == KIND_DIR and registered:
            return STATE_A, None, ""
        if not registered and target_kind == KIND_MISSING:
            return None, None, "D"
        if link_kind == KIND_LINK and registered and not points_to_target:
            return None, accounts.EXIT_CONFLICT, ("~/.codex does not point to {}; run `multi-codex use {}` "
                                                  "first".format(target, name))
        if not registered:
            return None, accounts.EXIT_ERROR, config.not_registered(name)
        if target_kind != KIND_DIR:
            return None, accounts.EXIT_CONFLICT, "{} is not a real directory".format(target)
        return None, accounts.EXIT_CONFLICT, "~/.codex is not a link to {}".format(target)
    # 有标记：只有三项都一致才续跑，避免 HOME 或根目录配置变了以后按旧标记去移动目录。
    if journal["name"].casefold() != name.casefold() or journal["source"] != link or journal["target"] != target:
        return None, accounts.EXIT_CONFLICT, ("an unfinished restore of {!r} ({} -> {}) exists; finish it or "
                                              "check it by hand".format(journal["name"], journal["target"],
                                                                        journal["source"]))
    if points_to_target and target_kind == KIND_DIR:
        return STATE_A2, None, ""
    if link_kind == KIND_MISSING and target_kind == KIND_DIR:
        return STATE_B, None, ""
    if link_kind == KIND_DIR and target_kind == KIND_MISSING:
        return STATE_C, None, ""
    return None, accounts.EXIT_CONFLICT, "unexpected state: ~/.codex is {}, {} is {}".format(
        link_kind, target, target_kind)


def restore_account(config: Config, config_exists: bool, name: str, skip_process_check: bool,
                    accept_relogin: bool, dry_run: bool) -> int:
    link = default_link()
    # Windows 上 use 中断留下的记录：~/.codex 缺失时先建回旧链接，再按现有状态判断（POSIX 不写这份记录）
    code = recover_use_journal(dry_run, for_restore=True)
    if code is not None:
        return code
    account = config.find(name)
    journal = load_restore_journal()
    # 账号已注销（状态 C 中途）时配置里查不到原名，用标记中记录的名字，用户输入的大小写不同也能续跑。
    if account is not None:
        canonical = account.name
        target = accounts.account_dir(config, canonical)
    elif journal is not None and journal["name"].casefold() == name.casefold():
        canonical = journal["name"]
        # 账号已注销时按名字拼不出改过名账号的目录（目录名与账号名不同），用标记里记下的目录，
        # 否则重跑会因三项比对不一致判冲突，用户只能手工删标记（feature-isolation-sharing-rename §5.1.6）。
        target = journal["target"]
    else:
        canonical = name
        target = accounts.account_dir(config, canonical)
    state, code, message = _restore_state(config, canonical, link, target, journal)

    if state is None and message == "D":
        # 没有标记、账号未登记、目录也不在：要么从未有过这个账号，要么上一次 restore 写完配置后、
        # 删启动命令前中断了。后一种情况要把残留的启动命令清掉。
        path = launcher.launcher_path(expand(config.bin_dir), canonical)
        owner = launcher.managed_account(path)
        if owner is None or owner.casefold() != canonical.casefold():
            error(config.not_registered(name), phase="restore")
            return accounts.EXIT_ERROR
        if dry_run:
            info("(dry-run) would delete the leftover launcher {}".format(path))
            return accounts.EXIT_OK
        code = accounts.converge(config, config.copy(), config_exists=config_exists, dry_run=False,
                                 orphan_scope=frozenset([canonical.casefold()]))
        if code == accounts.EXIT_OK:
            info("restore of {} finished".format(canonical))
        return code
    if state is None:
        error(message, phase="restore", path=link)
        return code
    if state != STATE_A:
        info("resuming restore of {} from state {}".format(canonical, state))

    relogin_needed = bool(journal.get("relogin_needed")) if journal else False
    if state in (STATE_A, STATE_A2, STATE_B):
        parent = os.path.dirname(link)
        if os.stat(target).st_dev != os.stat(parent).st_dev:
            error("{} and ~/.codex are on different file systems; restore by hand: remove the ~/.codex link, "
                  "copy {} to ~/.codex, then run `multi-codex remove {}`".format(target, target, canonical),
                  phase="restore", path=target)
            return accounts.EXIT_CONFLICT
        if state == STATE_A:
            # 移回 ~/.codex 改变了真实路径，与迁移一样会让钥匙串里的登录失效。
            code, relogin_needed = migrate._check_credentials_store(target, canonical, accept_relogin,
                                                                    relogin_command="codex login")
            if code is not None:
                return code
        if skip_process_check:
            warn("--skip-process-check: {} may still be in use".format(target))
        else:
            code = migrate._busy_check(target)
            if code:
                return code

    if state == STATE_A:
        # 先移动数据、最后才收敛注销：收敛若冲突（例如 codex-auto 与名为 auto 的账号），会停在“数据已回到
        # ~/.codex、注销失败”的半完成状态。所以在写标记、动数据之前，用同样的参数先预检一次
        # （feature-auto-launcher §5.1.4）。A2/B/C 已写标记，配置被锁住，进入前已在状态 A 通过预检。
        new = _unregistered(config, canonical)
        conflicts = [action for action in accounts.plan(config, new, config_exists=config_exists,
                                                        orphan_scope=frozenset([canonical.casefold()]))
                     if action.status == accounts.CONFLICT]
        if conflicts:
            for action in conflicts:
                error(action.reason, phase="precheck", path=action.path)
            error("unregistering {} would conflict; nothing was moved".format(canonical), phase="precheck")
            return accounts.EXIT_CONFLICT

    if dry_run:
        if state in (STATE_A, STATE_A2):
            info("(dry-run) would unlink ~/.codex")
        if state in (STATE_A, STATE_A2, STATE_B):
            info("(dry-run) would move {} to ~/.codex".format(target))
        info("(dry-run) would unregister {} and delete codex-{}".format(canonical, canonical))
        return accounts.EXIT_OK

    if state == STATE_A:
        atomic_write(restore_journal_path(), json.dumps({
            "name": canonical, "source": link, "target": target, "relogin_needed": relogin_needed,
        }, indent=2, ensure_ascii=False) + "\n", mode=0o600)
        migrate._test_hook("restore-marked")
    if state in (STATE_A, STATE_A2):
        # Windows 上 ~/.codex 是 junction，要用 rmdir 删；POSIX 上 remove_link 就是 os.unlink
        platform.remove_link(link)
        info("step=unlink {}".format(link))
        migrate._test_hook("restore-unlinked")
    if state in (STATE_A, STATE_A2, STATE_B):
        try:
            platform.retry_on_sharing_violation("restore-rename", os.rename, target, link)
        except OSError as exc:
            if platform.is_sharing_violation(exc):
                # 只在 Windows：账号目录里的文件被其它程序打开着，关掉后重跑同一命令续做
                error(platform.in_use_message(target, exc), phase="restore", path=link)
                return accounts.EXIT_ERROR
            # 第 1、2 步之间有人运行了 codex，新建了非空的 ~/.codex。
            error("cannot move {} to ~/.codex ({}); move the new ~/.codex away and rerun "
                  "`multi-codex restore {}`".format(target, exc, canonical), phase="restore", path=link)
            return accounts.EXIT_ERROR
        info("step=rename {} -> {}".format(target, link))
        migrate._test_hook("restore-renamed")

    info("step=unregister {}".format(canonical))
    new = _unregistered(config, canonical)
    code = accounts.converge(config, new, config_exists=config_exists, dry_run=False,
                             orphan_scope=frozenset([canonical.casefold()]))
    if code != accounts.EXIT_OK:
        # 保留标记：现在处于状态 C，修好问题后重跑同一命令即可。
        error("data is back at ~/.codex but unregistering failed; fix the problem above and rerun "
              "`multi-codex restore {}`".format(canonical), phase="restore")
        return code
    if os.path.exists(restore_journal_path()):
        os.unlink(restore_journal_path())
    info("restored {} to ~/.codex".format(canonical))
    if relogin_needed:
        info("log in again: codex login")
    return accounts.EXIT_OK


# ---- Windows 的 use 中断记录（方案 §5.1.8） ----
# Windows 上 os.replace 不能覆盖已存在的 junction，use 只能先删旧链接再建新链接。旧目标只在内存里，
# 删除后进程被杀就丢了，所以删之前先把它写进 use-journal.json；POSIX 走原子替换，不写这份记录。

def use_journal_path() -> str:
    return os.path.join(platform.state_dir(), "use-journal.json")


def load_use_journal() -> Optional[dict]:
    """读 use 中断记录；没有时返回 None。内容不完整按没有处理会丢掉旧目标，所以与 restore 标记一样报错。"""
    path = use_journal_path()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            journal = json.load(handle)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise RestoreJournalError("cannot read the switch record {}: {}".format(path, exc))
    if not isinstance(journal, dict) or any(not isinstance(journal.get(key), str)
                                            for key in ("link", "old_target", "new_target")):
        raise RestoreJournalError("the switch record {} is incomplete".format(path))
    return journal


def _delete_use_journal() -> None:
    try:
        os.unlink(use_journal_path())
    except FileNotFoundError:
        pass


def use_journal_account(journal: dict, config: Optional[Config] = None) -> str:
    """记录里 new_target 对应的已登记账号名，供提示用；找不到时写 NAME。"""
    if config is None:
        from .config import load_config
        try:
            config = load_config()[0]
        except Exception:  # noqa: BLE001 —— 只用于生成提示文字，配置读不出时退回占位名
            return "NAME"
    owner = _registered_owner(config, os.path.realpath(journal["new_target"]))
    return owner or "NAME"


def recover_use_journal(dry_run: bool, for_restore: bool = False) -> Optional[int]:
    """处理上一次 use 中断留下的记录；返回非 None 时调用方应以该退出码结束。只在记录存在时做事。

    有 restore 标记时以 restore 为准：~/.codex 由 restore 的状态机负责，直接删掉 use 记录。
    否则按 ~/.codex 的现状：缺失时 use 按“不存在”一路继续（成功后删记录），restore 先建回旧链接；
    是链接说明中断发生在建链之后，删记录；是真实目录时 use 报错保留记录，restore 交给它自己的状态判断。
    """
    journal = load_use_journal()
    if journal is None:
        return None
    link, old_target, new_target = journal["link"], journal["old_target"], journal["new_target"]
    if dry_run:
        info("(dry-run) would recover interrupted switch: ~/.codex -> {} (was being replaced by {})".format(
            old_target, new_target))
        return None
    if load_restore_journal() is not None:
        _delete_use_journal()
        return None
    kind = entry_kind(link)
    if kind == KIND_LINK:
        _delete_use_journal()
        info("recovered interrupted switch: {} -> {}".format(old_target, new_target))
        return None
    if kind == KIND_MISSING:
        if not for_restore:
            # use 本次按 KIND_MISSING 直接建新链接，成功后再删记录
            return None
        try:
            platform.create_link(old_target, link)
        except OSError as exc:
            error("cannot restore ~/.codex -> {} ({}); fix it, then rerun".format(old_target, exc),
                  phase="use restore-old-link failed", path=link)
            return accounts.EXIT_ERROR
        _delete_use_journal()
        info("recovered interrupted switch: {} -> {}".format(old_target, new_target))
        return None
    if for_restore:
        return None
    error("~/.codex was recreated as a directory while switching from {}; move it away, then rerun "
          "multi-codex use {}".format(old_target, use_journal_account(journal)), phase="use", path=link)
    return accounts.EXIT_ERROR


def _use_windows(link: str, kind: str, current: Optional[str], target: str) -> int:
    """Windows 上切换 ~/.codex：先删再建（junction 不能被 os.replace 覆盖），删之前写中断记录。"""
    if kind == KIND_MISSING:
        try:
            platform.create_link(target, link)
        except Exception as exc:  # noqa: BLE001 —— 建链失败时 ~/.codex 仍不存在，原样报错
            error("{}: {}".format(type(exc).__name__, exc), phase="use", path=link)
            return accounts.EXIT_ERROR
        _delete_use_journal()
        return accounts.EXIT_OK
    assert current is not None
    atomic_write(use_journal_path(), json.dumps({
        "link": link, "old_target": current, "new_target": target,
    }, indent=2, ensure_ascii=False) + "\n", mode=0o600)
    try:
        migrate._test_hook("use-remove")
        platform.remove_link(link)
    except (OSError, migrate.InjectedFailure) as exc:
        _delete_use_journal()
        error("cannot remove ~/.codex (winerror {}); nothing was changed".format(getattr(exc, "winerror", None)),
              phase="use", path=link)
        return accounts.EXIT_ERROR
    migrate._test_hook("use-unlinked")
    try:
        migrate._test_hook("use-link")
        platform.create_link(target, link)
    except (OSError, migrate.InjectedFailure) as exc:
        error("{}: {}".format(type(exc).__name__, exc), phase="use", path=link)
        try:
            platform.create_link(current, link)
        except OSError as restore_exc:
            error("{}; restore it by hand: mklink /J \"{}\" \"{}\"".format(restore_exc, link, current),
                  phase="use restore-old-link failed", path=link)
            info("phase=use restore-old-link failed path={} target={}".format(link, current))
            return accounts.EXIT_ERROR
        _delete_use_journal()
        info("phase=use restore-old-link ok path={} target={}".format(link, current))
        return accounts.EXIT_ERROR
    _delete_use_journal()
    return accounts.EXIT_OK


def _unregistered(config: Config, canonical: str) -> Config:
    """注销 canonical 之后的配置：删账号与指向它的绑定。预检与真正注销必须用同一份。"""
    new = config.copy()
    current = new.find(canonical)
    if current is not None:
        del new.accounts[current.name]
    binding.drop_account(new, canonical)
    return new
