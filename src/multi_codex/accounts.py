"""账号收敛引擎：对比新旧配置与实际文件状态，生成并执行动作列表（方案 §5.1.1、§5.1.2）。

所有写命令（init、add、proxy、remove、apply，以及迁移最后的登记步骤）都走这里：
1. 调用方算出新配置；
2. plan() 只读地生成完整动作列表，并就地更新新配置里的 managed_links；
3. 有冲突就直接返回 3，什么都不写；
4. 否则先原子写入 config.json，再逐个执行文件动作。

先写配置再执行文件动作，是为了让“文件动作中途失败”可以靠重跑收敛：
配置已经是新的，下次 plan() 会补齐缺的文件。反过来先改文件再写配置，
失败时会留下配置里没有记录的链接，之后再也不会被清理。
"""

import os
from typing import FrozenSet, Iterable, List, Optional, Set, Union

from . import launcher, shared
from .actions import (CONFLICT, CREATE, DELETE, SKIP, UNCHANGED, UPDATE, Action, error,
                      has_conflict, info, print_action)
from .config import Config, config_path, dump_config, save_config
from .fsutil import KIND_DIR, KIND_MISSING, atomic_write, entry_kind, expand, read_text

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_CONFLICT = 3
EXIT_BUSY = 4

# orphan_scope 取这个值时，清理启动命令目录里所有不在配置中的受管启动命令。
ALL_ORPHANS = "all"
NO_ORPHANS: FrozenSet[str] = frozenset()


def account_dir(config: Config, name: str) -> str:
    return os.path.join(expand(config.root), name)


def plan(old: Config, new: Config, *, config_exists: bool = True,
         orphan_scope: Union[str, FrozenSet[str]] = NO_ORPHANS,
         assume_dirs: Iterable[str] = ()) -> List[Action]:
    """生成从 old 收敛到 new 所需的全部动作。只读文件系统，不做任何修改。

    orphan_scope：要清理的孤儿启动命令的账号名集合（casefold 后比较）；ALL_ORPHANS 表示全部清理，
    NO_ORPHANS 表示不清理。只有 apply 和 remove 需要清理孤儿。
    assume_dirs：视为已存在的账号目录。迁移在移动数据前做预检时，目标目录还不存在，
    但迁移完成后它一定存在，不应计划“创建目录”。
    """
    actions: List[Action] = []
    assumed = {expand(path) for path in assume_dirs}
    old_root, new_root = expand(old.root), expand(new.root)
    old_bin, new_bin = expand(old.bin_dir), expand(new.bin_dir)

    # 工具不会搬迁账号目录，所以已有账号时改根目录只能判冲突。
    if old.accounts and old_root != new_root:
        actions.append(Action(CONFLICT, "config", config_path(),
                              "cannot change root from {} to {} while accounts are registered".format(
                                  old_root, new_root)))

    planned_deletes: Set[str] = set()
    for account in new.accounts.values():
        old_account = old.find(account.name)
        if old_account is not None and old_account.name != account.name:
            # 只改了大小写：在区分大小写的文件系统上会新建另一份目录和启动命令，
            # 而旧启动命令因为按大小写不敏感匹配，既不算孤儿也不会被清理。
            actions.append(Action(CONFLICT, "config", config_path(),
                                  "account {!r} is registered as {!r}; renaming is not supported".format(
                                      account.name, old_account.name)))
            continue
        directory = os.path.join(new_root, account.name)
        actions.extend(_plan_account_dir(directory, assumed))
        actions.extend(_plan_launcher(new_bin, account.name, directory, account.proxy))
        if old_account is not None and old_bin != new_bin:
            actions.extend(_plan_launcher_delete(old_bin, old_account.name, planned_deletes,
                                                 "bin_dir changed"))
        actions.extend(shared.plan_shared(new, account, directory, old.shared_dir))

    for old_account in old.accounts.values():
        if new.find(old_account.name) is None:
            actions.extend(_plan_launcher_delete(old_bin, old_account.name, planned_deletes,
                                                 "account removed"))
            actions.extend(shared.plan_remove_links(old_account, os.path.join(old_root, old_account.name),
                                                    old.shared_dir))

    if orphan_scope:
        for name, path in launcher.scan_managed(new_bin).items():
            if new.find(name) is not None:
                continue
            if orphan_scope == ALL_ORPHANS or name.casefold() in orphan_scope:
                actions.extend(_plan_launcher_delete(new_bin, name, planned_deletes,
                                                     "orphan launcher", path=path))

    config_changed = not config_exists or dump_config(old) != dump_config(new)
    config_action = Action(CREATE if not config_exists else (UPDATE if config_changed else UNCHANGED),
                           "config", config_path())
    return [config_action] + actions


def _plan_account_dir(directory: str, assumed: Set[str]) -> List[Action]:
    if directory in assumed:
        return []
    kind = entry_kind(directory)
    if kind == KIND_MISSING:
        return [Action(CREATE, "account-dir", directory, run=lambda: _make_private_dir(directory))]
    # 账号目录本身是软链（例如用户手工迁移后留的链接）也可以接受，只要最终指向目录。
    if kind == KIND_DIR or os.path.isdir(directory):
        return [Action(UNCHANGED, "account-dir", directory)]
    return [Action(CONFLICT, "account-dir", directory, "exists but is not a directory")]


def _make_private_dir(directory: str) -> None:
    # 目录里会保存 auth.json，所以账号目录和根目录都只允许本人访问。
    parent = os.path.dirname(directory)
    if not os.path.isdir(parent):
        os.makedirs(parent, mode=0o700)
    os.mkdir(directory, 0o700)
    os.chmod(directory, 0o700)


def _plan_launcher(bin_dir: str, name: str, directory: str, proxy: str) -> List[Action]:
    path = launcher.launcher_path(bin_dir, name)
    content = launcher.render(name, directory, proxy)
    kind = entry_kind(path)
    if kind == KIND_MISSING:
        return [Action(CREATE, "launcher", path, run=_write_launcher(path, content))]
    owner = launcher.managed_account(path)
    if owner is None:
        return [Action(CONFLICT, "launcher", path, "already exists and is not managed by multi-codex")]
    if owner.casefold() != name.casefold():
        return [Action(CONFLICT, "launcher", path, "managed by multi-codex for account {!r}".format(owner))]
    if read_text(path) == content and os.access(path, os.X_OK):
        return [Action(UNCHANGED, "launcher", path)]
    return [Action(UPDATE, "launcher", path, run=_write_launcher(path, content))]


def _write_launcher(path: str, content: str):
    def run() -> None:
        atomic_write(path, content, mode=launcher.LAUNCHER_MODE)
    return run


def _plan_launcher_delete(bin_dir: str, name: str, planned: Set[str], reason: str,
                          path: Optional[str] = None) -> List[Action]:
    path = path or launcher.launcher_path(bin_dir, name)
    if path in planned or entry_kind(path) == KIND_MISSING:
        return []
    owner = launcher.managed_account(path)
    if owner is None or owner.casefold() != name.casefold():
        return [Action(SKIP, "launcher", path, "not managed by multi-codex for this account; left untouched")]
    planned.add(path)
    return [Action(DELETE, "launcher", path, reason, lambda: os.unlink(path))]


def execute(old: Config, new: Config, actions: List[Action], *, dry_run: bool) -> int:
    """按 §5.1.1 的顺序执行：有冲突则全部不写；否则先写配置，再逐个执行文件动作。"""
    for action in actions:
        print_action(action, dry_run=dry_run)
    if has_conflict(actions):
        info("nothing was changed because of the conflicts above")
        return EXIT_CONFLICT
    if dry_run:
        return EXIT_OK
    config_action = actions[0]
    if config_action.changes:
        try:
            save_config(new)
        except OSError as exc:
            error(str(exc), phase="config", path=config_action.path)
            return EXIT_ERROR
    for action in actions[1:]:
        if not action.changes or action.run is None:
            continue
        try:
            action.run()
        except OSError as exc:
            # 配置已经写入，重跑同一命令或 `apply` 会从这里继续收敛。
            error("{}; rerun the command or `multi-codex apply` to finish".format(exc),
                  phase=action.kind, path=action.path)
            return EXIT_ERROR
    return EXIT_OK


def converge(old: Config, new: Config, *, config_exists: bool, dry_run: bool,
             orphan_scope: Union[str, FrozenSet[str]] = NO_ORPHANS, assume_dirs: Iterable[str] = ()) -> int:
    actions = plan(old, new, config_exists=config_exists, orphan_scope=orphan_scope,
                   assume_dirs=assume_dirs)
    return execute(old, new, actions, dry_run=dry_run)


def launcher_status(config: Config, name: str) -> str:
    """list 命令用：ok、missing、conflict 或 stale（内容与配置不一致）。"""
    account = config.find(name)
    path = launcher.launcher_path(expand(config.bin_dir), name)
    if entry_kind(path) == KIND_MISSING:
        return "missing"
    owner = launcher.managed_account(path)
    if owner is None or owner.casefold() != name.casefold():
        return "conflict"
    expected = launcher.render(name, account_dir(config, name), account.proxy if account else "inherit")
    return "ok" if read_text(path) == expected and os.access(path, os.X_OK) else "stale"
