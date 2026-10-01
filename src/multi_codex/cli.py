"""命令行入口：解析参数、加锁、组装新配置，再交给收敛引擎或迁移模块。

退出码契约（方案 §5.2）：0 成功或已是目标状态；1 运行错误；2 参数不合法；
3 存在冲突且未做任何修改；4 迁移源目录正被占用。
"""

import argparse
import json
import os
import subprocess
import sys
import time
from typing import List, Optional, Tuple

from . import (__version__, accounts, apps, binding, completion, doctor, identity, launcher, migrate, platform,
               switch, usage)
from .actions import CREATE, DELETE, UNCHANGED, UPDATE, Action, error, info, print_action, warn
from .config import (DEFAULT_SHARED_ITEMS, Account, Config, ConfigError, is_socks, load_config, normalize_proxy,
                     parse_config, validate_env_key, validate_env_value, validate_name)
from .fsutil import expand
from .lock import LockBusyError, WriteLock


class UsageError(Exception):
    """命令行参数不合法，对应退出码 2。"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="multi-codex",
        description="Manage multiple Codex CLI accounts: separate CODEX_HOME directories, "
                    "per-account launchers and per-account proxies.")
    parser.add_argument("--version", action="version", version="%(prog)s " + __version__)
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True

    p_init = sub.add_parser("init", help="create or update the global settings")
    p_init.add_argument("--root", help="directory that holds account directories (default ~/.cx)")
    p_init.add_argument("--bin-dir", help="directory for codex-<name> launchers (default ~/.local/bin)")
    p_init.add_argument("--shared-dir", help="directory whose items can be linked into accounts")
    p_init.add_argument("--shared-items", help="comma-separated items to share "
                        "(default {})".format(",".join(DEFAULT_SHARED_ITEMS)))
    _add_dry_run(p_init)

    p_mig = sub.add_parser("migrate-default", help="turn the default ~/.codex into a named account")
    p_mig.add_argument("name")
    p_mig.add_argument("--source", help="directory to migrate (default ~/.codex)")
    p_mig.add_argument("--copy", action="store_true",
                       help="copy and verify instead of renaming (used automatically across file systems)")
    p_mig.add_argument("--keep-backup", action="store_true",
                       help="in copy mode, keep the original directory as a backup")
    p_mig.add_argument("--proxy", help="proxy for the new account: port, URL, off or inherit")
    p_mig.add_argument("--skip-process-check", action="store_true",
                       help="do not check whether Codex is using the source directory")
    p_mig.add_argument("--accept-relogin", action="store_true",
                       help="migrate even though credentials are in the system keyring; "
                            "you will need to log in again afterwards")
    _add_dry_run(p_mig)

    p_add = sub.add_parser("add", help="add an account, adopt an existing directory, or change its options")
    p_add.add_argument("name")
    p_add.add_argument("--proxy", help="port, URL, off or inherit (new accounts default to inherit)")
    shared_group = p_add.add_mutually_exclusive_group()
    shared_group.add_argument("--shared", dest="shared", action="store_true", default=None,
                              help="link shared items into this account")
    shared_group.add_argument("--no-shared", dest="shared", action="store_false",
                              help="do not link shared items (default for new accounts)")
    p_add.add_argument("--config-from", metavar="OTHER",
                       help="copy config.toml from account OTHER (once; existing different content is a conflict)")
    p_add.add_argument("--adopt", action="store_true",
                       help="take over existing links that already point to the shared items, "
                            "so that turning sharing off later removes them too")
    _add_dry_run(p_add)

    p_proxy = sub.add_parser("proxy", help="set the proxy of an account")
    p_proxy.add_argument("name")
    p_proxy.add_argument("value", help="port (e.g. 7901), URL, off or inherit")
    _add_dry_run(p_proxy)

    p_remove = sub.add_parser("remove", help="unregister an account (its directory is kept)")
    p_remove.add_argument("name")
    _add_dry_run(p_remove)

    p_apply = sub.add_parser("apply", help="converge all accounts to the configuration")
    p_apply.add_argument("-f", "--file", help="use this file as the new configuration")
    _add_dry_run(p_apply)

    p_list = sub.add_parser("list", help="show accounts, their status and who is logged in")
    _add_json(p_list)

    p_usage = sub.add_parser("usage", help="show rate-limit usage of accounts")
    p_usage.add_argument("names", nargs="*", metavar="NAME", help="accounts to show (default: all)")
    p_usage.add_argument("--live", action="store_true",
                         help="ask Codex (codex app-server, through the account's launcher) for live usage "
                              "instead of reading the last snapshot from local session logs")
    p_usage.add_argument("--timeout", type=_positive_seconds, default=usage.DEFAULT_LIVE_TIMEOUT_SEC,
                         help="total time limit per account for --live, in seconds (default 30)")
    _add_json(p_usage)

    p_doctor = sub.add_parser("doctor", help="check the installation, configuration and accounts (read-only)")
    _add_json(p_doctor)

    p_comp = sub.add_parser("completion", help="print a shell completion script")
    p_comp.add_argument("shell", nargs="?", choices=completion.SHELLS)
    # 补全脚本在按 Tab 时调用它读取账号名；不是给人用的，所以不出现在帮助里。
    p_comp.add_argument("--list-accounts", action="store_true", help=argparse.SUPPRESS)

    p_run = sub.add_parser("run", help="run a command with an account's environment (default: codex)",
                           usage="multi-codex run [NAME] [-- COMMAND [ARG ...]]")
    # 省略 NAME 时按当前目录的绑定选账号（bind）。
    p_run.add_argument("name", nargs="?")

    p_bind = sub.add_parser("bind", help="bind a directory to an account (no arguments: list bindings)")
    p_bind.add_argument("name", nargs="?")
    p_bind.add_argument("dir", nargs="?", help="directory to bind (default: current directory)")
    _add_dry_run(p_bind)

    p_unbind = sub.add_parser("unbind", help="remove the binding of a directory")
    p_unbind.add_argument("dir", nargs="?", help="directory to unbind (default: current directory)")
    _add_dry_run(p_unbind)

    p_code = sub.add_parser("code", help="open VS Code for an account (experimental)",
                            usage="multi-codex code NAME [PATH] [--bin CODE] [-- CODE_ARGS ...]")
    p_code.add_argument("name")
    p_code.add_argument("path", nargs="?")
    p_code.add_argument("--bin", help="path of the VS Code `code` command (default: `code` on PATH)")

    p_app = sub.add_parser("app", help="open the Codex desktop app for an account (macOS, experimental)")
    p_app.add_argument("name")
    p_app.add_argument("--app", help="path of the Codex desktop app (default {})".format(apps.DEFAULT_DESKTOP_APP))

    p_path = sub.add_parser("path", help="print an account's directory")
    p_path.add_argument("name")

    p_env = sub.add_parser("env", help="list or change an account's extra environment variables")
    p_env.add_argument("name")
    p_env.add_argument("assignments", nargs="*", metavar="KEY=VALUE", help="variables to set")
    p_env.add_argument("--unset", action="append", default=[], metavar="KEY", help="variable to remove")
    p_env.add_argument("--clear", action="store_true", help="remove all variables of the account")
    _add_dry_run(p_env)

    p_use = sub.add_parser("use", help="show or change the default account (what ~/.codex points to)")
    p_use.add_argument("name", nargs="?")
    p_use.add_argument("--skip-process-check", action="store_true",
                       help="switch even if a process may be using the current default account")
    _add_dry_run(p_use)

    p_restore = sub.add_parser("restore", help="undo migrate-default: move an account back to ~/.codex")
    p_restore.add_argument("name")
    p_restore.add_argument("--skip-process-check", action="store_true",
                           help="do not check whether the account directory is in use")
    p_restore.add_argument("--accept-relogin", action="store_true",
                           help="restore even though credentials are in the system keyring; "
                                "you will need to log in again afterwards")
    _add_dry_run(p_restore)
    return parser


def _add_json(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true",
                        help="print one JSON object on stdout; warnings still go to stderr")


def _positive_seconds(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a number of seconds")
    if not seconds > 0 or seconds == float("inf"):
        raise argparse.ArgumentTypeError("must be greater than 0")
    return seconds


def _add_dry_run(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--dry-run", action="store_true", help="show the planned actions without changing anything")


def _split_run_command(argv: List[str]) -> Tuple[List[str], List[str]]:
    """`run NAME -- CMD ...`：在第一个 `--` 处分开，`--` 之后原样作为要运行的命令。

    不交给 argparse 的 REMAINDER：不同 Python 版本对 `--` 的处理不一致（3.9 起会吞掉第一个 `--`），
    `run a -- -- x` 这种命令就会被解析成不同的样子。
    """
    if argv[:1] in (["run"], ["code"]) and "--" in argv:
        index = argv.index("--")
        return argv[:index], argv[index + 1:]
    return argv, []


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    argv, run_command = _split_run_command(list(sys.argv[1:] if argv is None else argv))
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse 在参数错误时以 2 退出、--help/--version 以 0 退出，与本工具的退出码契约一致。
        return int(exc.code or 0)

    if not platform.is_supported():
        error("this platform is not supported yet (macOS and Linux only)")
        return accounts.EXIT_ERROR

    try:
        # 只读命令，与迁移无关：放在读取事务记录之前分派，记录损坏时 doctor 才能把它报出来。
        if args.command == "usage":
            return cmd_usage(args)
        if args.command == "doctor":
            return cmd_doctor(args)
        if args.command == "completion":
            return cmd_completion(args, parser)
        if args.command == "run":
            return cmd_run(args.name, run_command)
        if args.command == "code":
            return cmd_code(args, run_command)
        if args.command == "app":
            return cmd_app(args)
        if args.command == "path":
            return cmd_path(args.name)
        notice = migrate.pending_journal_notice()
        if notice:
            warn(notice)
        restore_notice = switch.pending_restore_notice()
        if restore_notice:
            warn(restore_notice)
        if args.command == "list":
            return cmd_list(args.json)
        # 以下两种只读模式不加锁，未完成的迁移或 restore 也不拦。
        if args.command == "env" and not (args.assignments or args.unset or args.clear):
            return cmd_env_list(args.name)
        if args.command == "use" and args.name is None:
            return cmd_use_show()
        if args.command == "bind" and args.name is None:
            return cmd_bind_list()
        if _blocked_by_migration(args, notice):
            return accounts.EXIT_ERROR
        with WriteLock():
            # 加锁前的检查与加锁之间，另一条命令可能刚开始一次迁移或 restore，拿到锁后再确认一次。
            if _blocked_by_migration(args, migrate.pending_journal_notice()):
                return accounts.EXIT_ERROR
            return dispatch(args)
    except migrate.JournalError as exc:
        error("{}. Check where the complete data of the unfinished migration is (the source, "
              "the target account directory, or a *.multi-codex-bak.* backup next to the source), "
              "make sure it is back at the source path, then delete the journal file".format(exc),
              phase="resume")
        return accounts.EXIT_ERROR
    except switch.RestoreJournalError as exc:
        error("{}. Check whether the account data is at ~/.codex or in the account directory, "
              "then delete the marker file".format(exc), phase="restore")
        return accounts.EXIT_ERROR
    except LockBusyError as exc:
        error("another multi-codex command is running (pid {})".format(exc.holder_pid or "unknown"))
        return accounts.EXIT_ERROR
    except UsageError as exc:
        error(str(exc))
        return accounts.EXIT_USAGE
    except ConfigError as exc:
        error(str(exc), phase="config")
        return accounts.EXIT_ERROR
    except OSError as exc:
        error("{}: {}".format(type(exc).__name__, exc))
        return accounts.EXIT_ERROR


def _blocked_by_migration(args: argparse.Namespace, notice: Optional[str]) -> bool:
    # 迁移中途禁止其它写命令，防止根目录或账号在迁移过程中被改动。
    if notice and args.command != "migrate-default" and not args.dry_run:
        error("finish the unfinished migration before running `{}`".format(args.command))
        return True
    # restore 做到一半时 ~/.codex 可能不存在或是普通目录，这时 use 会新建它、migrate-default 的预检也能通过，
    # restore 就再也无法完成。每次都重新读取标记：拿到锁之后的第二次检查也要看到别的命令刚写出的标记。
    journal = switch.load_restore_journal()
    if journal and not args.dry_run and not (
            args.command == "restore" and args.name.casefold() == journal["name"].casefold()):
        error("finish the unfinished restore first: rerun `multi-codex restore {}`".format(journal["name"]))
        return True
    return False


def dispatch(args: argparse.Namespace) -> int:
    if args.command == "migrate-default":
        name = _checked_name(args.name)
        proxy = _checked_proxy(args.proxy) if args.proxy is not None else None
        return migrate.migrate_default(name, args.source, args.copy, args.keep_backup, proxy,
                                       args.skip_process_check, args.dry_run,
                                       accept_relogin=args.accept_relogin)

    old, exists = load_config()
    new = old.copy()
    orphan_scope = accounts.NO_ORPHANS
    adopt_accounts = frozenset()
    extra_actions: List = []
    # bind / unbind 的变化行：不放进 plan（execute 把第一项当配置动作，doctor 也调用 plan），
    # 等 converge 成功后再打印，避免冲突时用户看到 create 却什么都没写。
    binding_change: Optional[Action] = None

    if args.command == "init":
        if args.root:
            new.root = args.root
        if args.bin_dir:
            new.bin_dir = args.bin_dir
        if args.shared_dir:
            new.shared_dir = args.shared_dir
        if args.shared_items is not None:
            items = [item.strip() for item in args.shared_items.split(",") if item.strip()]
            if any(item in (".", "..") or "/" in item for item in items):
                raise UsageError("--shared-items must be plain names without '/'")
            new.shared_items = items
    elif args.command == "add":
        name = _checked_name(args.name)
        account = new.find(name)
        if account is None:
            account = Account(name)
            new.accounts[name] = account
        if args.proxy is not None:
            account.proxy = _checked_proxy(args.proxy)
        if args.shared is not None:
            account.shared = args.shared
        if args.adopt:
            # 接管只对开启了共享的账号有意义；关闭状态下工具本来就不管这些软链。
            if not account.shared:
                raise UsageError("--adopt requires sharing to be on for {!r}; add --shared".format(account.name))
            adopt_accounts = frozenset([account.name.casefold()])
        if args.config_from:
            extra_actions = _config_copy_actions(new, account, args.config_from)
            if extra_actions is None:
                return accounts.EXIT_ERROR
    elif args.command == "proxy":
        account = new.find(_checked_name(args.name))
        if account is None:
            error("account {!r} is not registered".format(args.name))
            return accounts.EXIT_ERROR
        account.proxy = _checked_proxy(args.value)
    elif args.command == "remove":
        name = _checked_name(args.name)
        account = new.find(name)
        if account is None:
            info("{} is not registered".format(name))
        else:
            del new.accounts[account.name]
            binding.drop_account(new, account.name)
        orphan_scope = frozenset([name.casefold()])
    elif args.command == "apply":
        orphan_scope = accounts.ALL_ORPHANS
        if args.file:
            new = _load_apply_file(args.file, old)
    elif args.command == "env":
        name = _checked_name(args.name)
        account = new.find(name)
        if account is None:
            error("account {!r} is not registered".format(name))
            return accounts.EXIT_ERROR
        _apply_env_changes(account, args)
    elif args.command == "bind":
        account = new.find(_checked_name(args.name))
        if account is None:
            error("account {!r} is not registered".format(args.name))
            return accounts.EXIT_ERROR
        target = args.dir or os.getcwd()
        if not os.path.isdir(target):
            error("not a directory: {}".format(target))
            return accounts.EXIT_ERROR
        key = binding.normalize_dir(target)
        previous = new.bindings.get(key)
        new.bindings[key] = account.name
        status = UNCHANGED if previous == account.name else (UPDATE if previous else CREATE)
        binding_change = Action(status, "binding", key, "-> " + account.name)
    elif args.command == "unbind":
        target = args.dir or os.getcwd()
        key = binding.normalize_dir(target)
        if not exists or key not in new.bindings:
            info("not bound: {}".format(key))
            effective = binding.resolve(new, target) if exists else None
            if effective is not None:
                info("the effective binding is {} -> {}".format(*effective))
            return accounts.EXIT_OK
        binding_change = Action(DELETE, "binding", key, "-> " + new.bindings.pop(key))
    elif args.command == "use":
        return switch.use_account(old, _checked_name(args.name), args.skip_process_check, args.dry_run)
    elif args.command == "restore":
        return switch.restore_account(old, exists, _checked_name(args.name), args.skip_process_check,
                                      args.accept_relogin, args.dry_run)

    code = accounts.converge(old, new, config_exists=exists, dry_run=args.dry_run,
                             orphan_scope=orphan_scope, adopt_accounts=adopt_accounts, extra_actions=extra_actions)
    if binding_change is not None:
        if code == accounts.EXIT_OK:
            print_action(binding_change, dry_run=args.dry_run)
        else:
            info("binding not changed because of the errors above")
    return code


def _config_copy_actions(new: Config, account: Account, other_name: str) -> Optional[List]:
    """校验 --config-from 并生成复制动作；校验失败时输出原因并返回 None（退出码 1），参数错误抛 UsageError。"""
    other = new.find(_checked_name(other_name))
    if other is None:
        error("account {!r} is not registered".format(other_name))
        return None
    if other.name.casefold() == account.name.casefold():
        raise UsageError("--config-from must name another account")
    source = os.path.join(accounts.account_dir(new, other.name), "config.toml")
    if not os.path.isfile(source):
        error("account {!r} has no config.toml".format(other.name), path=source)
        return None
    with open(source, "rb") as handle:
        raw = handle.read()
    try:
        # TOML 规定必须是 UTF-8；不是的话 Codex 自己也读不了，复制没有意义。
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        error("config.toml of {!r} is not valid UTF-8".format(other.name), path=source)
        return None
    return accounts.plan_config_copy(new, account, content, other.name)


def _apply_env_changes(account: Account, args: argparse.Namespace) -> None:
    """按命令行参数修改账号的环境变量；参数不合法时抛 UsageError（退出码 2），什么都不改。"""
    if args.clear and (args.assignments or args.unset):
        raise UsageError("--clear cannot be combined with KEY=VALUE or --unset")
    updates = {}
    for assignment in args.assignments:
        if "=" not in assignment:
            raise UsageError("expected KEY=VALUE, got {!r}".format(assignment))
        key, value = assignment.split("=", 1)
        try:
            validate_env_key(key)
            validate_env_value(value)
        except ValueError as exc:
            raise UsageError(str(exc))
        updates[key] = value
    for key in args.unset:
        try:
            validate_env_key(key)
        except ValueError as exc:
            raise UsageError(str(exc))
        if key in updates:
            raise UsageError("{} is both set and unset in the same command".format(key))
    if args.clear:
        account.env = {}
        return
    account.env.update(updates)
    for key in args.unset:
        account.env.pop(key, None)


def _load_apply_file(path: str, old: Config) -> Config:
    """读取 apply -f 的文件；managed_links 是工具内部状态，一律沿用当前配置里的值。"""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = handle.read()
    except OSError as exc:
        raise ConfigError("cannot read {}: {}".format(path, exc))
    new = parse_config(raw, path)
    for account in new.accounts.values():
        _warn_if_socks(account.proxy, account.name)
        current = old.find(account.name)
        account.managed_links = list(current.managed_links) if current else []
    # 绑定是本机状态，沿用当前值；文件中已删除的账号，它的绑定一并删除。
    new.bindings = dict(old.bindings)
    for name in set(new.bindings.values()):
        if new.find(name) is None:
            binding.drop_account(new, name)
    return new


def _checked_name(name: str) -> str:
    try:
        validate_name(name)
    except ValueError as exc:
        raise UsageError(str(exc))
    return name


def _checked_proxy(value: str) -> str:
    try:
        proxy = normalize_proxy(value)
    except ValueError as exc:
        raise UsageError(str(exc))
    _warn_if_socks(proxy)
    return proxy


def _warn_if_socks(proxy: str, account: Optional[str] = None) -> None:
    """socks 地址照常接受，但要提醒：实测 codex-cli 0.159.0 拿到 socks5h 地址时，
    14 个连接里有 13 个仍按 HTTP CONNECT 发往该端口（见方案 §10）。
    只支持 SOCKS 的端口会让大部分请求失败，同时支持 HTTP 的 mixed 端口才能正常工作。
    只提示、不拒绝，也不改变退出码。
    """
    if not is_socks(proxy):
        return
    where = " for account {!r}".format(account) if account else ""
    warn("SOCKS proxy{}: Codex sends most requests as HTTP CONNECT even with a SOCKS URL, so this only "
         "works if {} also accepts HTTP proxy requests (a \"mixed\" port); prefer an HTTP proxy".format(where, proxy))


def _print_json(data: dict) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


def cmd_list(as_json: bool = False) -> int:
    config, exists = load_config()
    migrate.warn_isolation_env()
    if not exists:
        if as_json:
            _print_json({"version": 1, "configured": False, "accounts": [], "duplicates": []})
        else:
            info("no configuration yet at {}; run `multi-codex add NAME` to start".format(
                os.path.join(platform.state_dir(), "config.json")))
        return accounts.EXIT_OK
    entries = []
    for name, account in config.accounts.items():
        directory = accounts.account_dir(config, name)
        is_dir = os.path.isdir(directory)
        # 目录不存在时 read_identity 只会得到“未登录”，这里直接按未登录显示，不去读不存在的文件。
        found = identity.read_identity(directory) if is_dir else None
        entries.append((name, account, directory, is_dir, found))
    duplicates = identity.duplicate_groups((name, found) for name, _, _, _, found in entries if found)

    if as_json:
        _print_json({
            "version": 1,
            "configured": True,
            "root": expand(config.root),
            "bin_dir": expand(config.bin_dir),
            "shared_dir": expand(config.shared_dir) if config.shared_dir else None,
            "default_account": switch.describe_default(config)[0],
            "accounts": [{
                "name": name,
                "dir": directory,
                "dir_status": "ok" if is_dir else "missing-dir",
                "proxy": account.proxy,
                "shared": account.shared,
                "launcher": accounts.launcher_status(config, name),
                "credentials_store": found.store if found else None,
                # 只给键名：值里可能有密钥。
                "env_keys": sorted(account.env),
                "login": {"type": found.login if found else identity.LOGIN_LOGGED_OUT,
                          "email": found.email if found else None,
                          "plan": found.plan if found else None},
            } for name, account, directory, is_dir, found in entries],
            "duplicates": duplicates,
        })
        _warn_duplicates(duplicates, entries)
        return accounts.EXIT_OK

    print("root: {}".format(expand(config.root)))
    print("bin_dir: {}".format(expand(config.bin_dir)))
    print("shared.dir: {}".format(expand(config.shared_dir) if config.shared_dir else "(not set)"))
    print("default: {}".format(switch.describe_default(config)[1]))
    if not config.accounts:
        print("no accounts registered")
        return accounts.EXIT_OK
    # 前 5 列与 0.2.0 相同，新列只追加在后面，按列位置解析旧输出的脚本不受影响。
    rows = [("NAME", "DIR", "PROXY", "SHARED", "LAUNCHER", "LOGIN", "PLAN")]
    for name, account, directory, is_dir, found in entries:
        rows.append((name, "ok" if is_dir else "missing-dir", account.proxy,
                     "yes" if account.shared else "no", accounts.launcher_status(config, name),
                     identity.display_login(found) if found else "-",
                     (found.plan if found and found.plan else "-")))
    widths = [max(len(row[index]) for row in rows) for index in range(len(rows[0]))]
    for row in rows:
        print("  ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip())
    _warn_duplicates(duplicates, entries)
    return accounts.EXIT_OK


def _warn_duplicates(duplicates: List[List[str]], entries) -> None:
    emails = {name: found.email for name, _, _, _, found in entries if found}
    for group in duplicates:
        warn("accounts {} are logged in as the same ChatGPT account ({}); they share one usage quota".format(
            ", ".join(group), emails.get(group[0]) or "same user and workspace"))


def cmd_usage(args: argparse.Namespace) -> int:
    """额度。只读、不加锁；一个账号失败不影响其它账号，最后以退出码 1 汇总。"""
    config, exists = load_config()
    if not exists or not config.accounts:
        if args.json:
            _print_json({"version": 1, "accounts": []})
        else:
            print("no accounts registered")
        return accounts.EXIT_OK
    targets = []
    for name in args.names or list(config.accounts):
        account = config.find(name)
        targets.append((name, account))

    results = []
    for name, account in targets:
        if account is None:
            result = usage.UsageResult(name, usage.SOURCE_LIVE if args.live else usage.SOURCE_LOCAL, False,
                                       "account is not registered", None, False, [])
        elif args.live:
            status = accounts.launcher_status(config, account.name)
            if status != "ok":
                result = usage.UsageResult(account.name, usage.SOURCE_LIVE, False,
                                           "launcher is {}; run `multi-codex apply` first".format(status),
                                           None, False, [])
            else:
                result = usage.live_snapshot(account.name, launcher.launcher_path(expand(config.bin_dir),
                                                                                  account.name), args.timeout)
        else:
            result = usage.local_snapshot(account.name, accounts.account_dir(config, account.name))
        results.append(result)
        if not args.json:
            # 实时查询每个账号要几秒，逐个输出，不等全部完成。
            for line in usage.format_result(result, time.time()):
                print(line)
            sys.stdout.flush()
    if args.json:
        _print_json({"version": 1, "accounts": [usage.to_json(result) for result in results]})
    return accounts.EXIT_OK if all(result.ok for result in results) else accounts.EXIT_ERROR


def cmd_doctor(args: argparse.Namespace) -> int:
    """体检。只读、不加锁；有任一 fail 时退出码为 1，只有 warn 时仍为 0。"""
    checks = doctor.run_checks()
    if args.json:
        _print_json(doctor.to_json(checks))
    else:
        for line in doctor.format_checks(checks):
            print(line)
    return accounts.EXIT_ERROR if any(check.status == doctor.FAIL for check in checks) else accounts.EXIT_OK


def cmd_completion(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    if args.list_accounts:
        # 补全时每按一次 Tab 调用一次：任何问题都静默，不能在用户的命令行上打出警告。
        try:
            config, _ = load_config()
        except (ConfigError, OSError):
            return accounts.EXIT_OK
        for name in config.accounts:
            print(name)
        return accounts.EXIT_OK
    if args.shell is None:
        raise UsageError("completion needs a shell: bash, zsh or fish")
    sys.stdout.write(completion.script(args.shell, parser))
    return accounts.EXIT_OK


def _account_exec_args(config: Config, account: Account, command: List[str]) -> Tuple[List[str], dict]:
    """返回在账号环境下运行 command 所需的 (argv, env)，交给 os.execve("/bin/sh", ...)；run 与 code 共用。

    环境由 launcher.render 生成的脚本设置，与启动命令逐字一致。脚本经环境变量交给 sh，
    不放进命令行参数：里面可能有账号环境变量里的密钥，命令行参数其它用户用 ps 就能看到。
    """
    script = launcher.render(account.name, accounts.account_dir(config, account.name), account.proxy,
                             account.env, command_mode=True)
    env = dict(os.environ)
    env[launcher.RUN_SCRIPT_ENV] = script
    return ["sh", "-c", 'eval "${}"'.format(launcher.RUN_SCRIPT_ENV), "sh"] + command, env


def cmd_run(name: Optional[str], command: List[str]) -> int:
    """在账号的环境下运行命令（默认 codex），成功时不返回：本进程被替换成 sh，再被替换成该命令。

    省略 name 时，从当前目录向上找最近一个绑定（feature-dir-binding §5.1.2）。
    """
    config, exists = load_config()
    if name is None:
        found = binding.resolve(config, os.getcwd()) if exists else None
        if found is None:
            error("no account is bound to {} or its parents; use `multi-codex run NAME` or "
                  "`multi-codex bind NAME`".format(os.getcwd()))
            return accounts.EXIT_ERROR
        bound_dir, name = found
        if config.find(name) is None:
            error("account {!r} bound to {} is not registered; run `multi-codex unbind {}`".format(
                name, bound_dir, bound_dir))
            return accounts.EXIT_ERROR
        print("[multi-codex] using account {} (bound to {})".format(name, bound_dir), file=sys.stderr)
    account = config.find(name) if exists else None
    if account is None:
        error("account {!r} is not registered".format(name))
        return accounts.EXIT_ERROR
    argv, env = _account_exec_args(config, account, command or ["codex"])
    sys.stdout.flush()
    sys.stderr.flush()
    os.execve("/bin/sh", argv, env)
    return accounts.EXIT_ERROR  # 不会执行到这里；execve 失败时抛 OSError，由 main 统一处理


def _registered_with_dir(name: str) -> Tuple[Optional[Config], Optional[Account]]:
    config, exists = load_config()
    account = config.find(name) if exists else None
    if account is None:
        error("account {!r} is not registered".format(name))
        return None, None
    directory = accounts.account_dir(config, account.name)
    if not os.path.isdir(directory):
        error("account directory does not exist", path=directory)
        return None, None
    return config, account


def cmd_code(args: argparse.Namespace, extra: List[str]) -> int:
    """以账号环境打开一个独立的 VS Code 实例（实验功能，feature-app-launch §5.1.1）。"""
    warn("experimental: `code` relies on undocumented behaviour of VS Code and its OpenAI extension "
         "(verified with {})".format(apps.VERIFIED_WITH))
    config, account = _registered_with_dir(args.name)
    if account is None:
        return accounts.EXIT_ERROR
    code_bin = apps.find_code(args.bin)
    if code_bin is None:
        error("VS Code's `code` command was not found{}; install it from VS Code (\"Shell Command: Install "
              "'code' command in PATH\") or pass --bin".format(" at " + args.bin if args.bin else ""))
        return accounts.EXIT_ERROR
    data_dir = apps.gui_data_dir(config, account.name, "vscode")
    if sys.platform == "darwin":
        if len(data_dir) > apps.SOCKET_DIR_WARN_LENGTH:
            warn("{} is long; VS Code's socket path inside it may exceed the 104-byte limit".format(data_dir))
        warn("on macOS, `code` passes the whole environment (including this account's variables) to "
             "`open --env`, so the values are briefly visible in the process list")
    command = [code_bin, "--user-data-dir", data_dir] + ([args.path] if args.path else []) + extra
    argv, env = _account_exec_args(config, account, command)
    sys.stdout.flush()
    sys.stderr.flush()
    os.execve("/bin/sh", argv, env)
    return accounts.EXIT_ERROR


def cmd_app(args: argparse.Namespace) -> int:
    """以账号的 CODEX_HOME 打开一个独立的 Codex 桌面端实例（仅 macOS，实验功能，feature-app-launch §5.1.2）。

    经 LaunchServices（open -n）启动，只传两个不含密钥的变量：桌面端会用登录 shell 的环境覆盖代理等变量，
    传账号环境变量既无效，又会把值放进 open 的命令行参数。
    """
    if sys.platform != "darwin":
        error("`app` is only supported on macOS")
        return accounts.EXIT_ERROR
    warn("experimental: `app` relies on undocumented behaviour of the Codex desktop app "
         "(verified with {})".format(apps.VERIFIED_WITH))
    config, account = _registered_with_dir(args.name)
    if account is None:
        return accounts.EXIT_ERROR
    app_path = args.app or apps.DEFAULT_DESKTOP_APP
    problem = apps.desktop_app_problem(app_path)
    if problem:
        error(problem)
        return accounts.EXIT_ERROR
    data_dir = apps.gui_data_dir(config, account.name, "desktop")
    log = apps.desktop_log(config, account.name)
    argv = [apps.open_command(), "-n",
            "--env", "CODEX_HOME=" + accounts.account_dir(config, account.name),
            "--env", "CODEX_ELECTRON_USER_DATA_PATH=" + data_dir,
            "--stdout", log, "--stderr", log,
            "-a", app_path, "--args", "--user-data-dir=" + data_dir]
    try:
        proc = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                              timeout=60)
    except subprocess.TimeoutExpired:
        error("`open` did not return within 60s")
        return accounts.EXIT_ERROR
    if proc.returncode != 0:
        error("`open` failed (exit code {}): {}".format(proc.returncode, proc.stderr.strip()[:500]))
        return accounts.EXIT_ERROR
    info("started Codex desktop for {} (log {})".format(account.name, log))
    return accounts.EXIT_OK


def cmd_bind_list() -> int:
    config, exists = load_config()
    if not exists or not config.bindings:
        print("no bindings")
        return accounts.EXIT_OK
    effective = binding.resolve(config, os.getcwd())
    for path in sorted(config.bindings):
        marker = "*" if effective and effective[0] == path else " "
        print("{} {}  {}".format(marker, path, config.bindings[path]))
    return accounts.EXIT_OK


def cmd_path(name: str) -> int:
    config, exists = load_config()
    account = config.find(name) if exists else None
    if account is None:
        error("account {!r} is not registered".format(name))
        return accounts.EXIT_ERROR
    directory = accounts.account_dir(config, account.name)
    print(directory)
    if not os.path.isdir(directory):
        warn("account directory does not exist: {}".format(directory))
    return accounts.EXIT_OK


def cmd_env_list(name: str) -> int:
    config, exists = load_config()
    account = config.find(name) if exists else None
    if account is None:
        error("account {!r} is not registered".format(name))
        return accounts.EXIT_ERROR
    for key in sorted(account.env):
        print("{}={}".format(key, account.env[key]))
    return accounts.EXIT_OK


def cmd_use_show() -> int:
    config, _ = load_config()
    print(switch.describe_default(config)[1])
    return accounts.EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
