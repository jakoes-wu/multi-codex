"""命令行入口：解析参数、加锁、组装新配置，再交给收敛引擎或迁移模块。

退出码契约（方案 §5.2）：0 成功或已是目标状态；1 运行错误；2 参数不合法；
3 存在冲突且未做任何修改；4 迁移源目录正被占用。
"""

import argparse
import json
import os
import sys
import time
from typing import List, Optional

from . import __version__, accounts, doctor, identity, launcher, migrate, platform, usage
from .actions import error, info, warn
from .config import (DEFAULT_SHARED_ITEMS, Account, Config, ConfigError, is_socks, load_config, normalize_proxy,
                     parse_config, validate_name)
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


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
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
        notice = migrate.pending_journal_notice()
        if notice:
            warn(notice)
        if args.command == "list":
            return cmd_list(args.json)
        if _blocked_by_migration(args, notice):
            return accounts.EXIT_ERROR
        with WriteLock():
            # 加锁前的检查与加锁之间，另一条命令可能刚开始一次迁移，拿到锁后再确认一次。
            if _blocked_by_migration(args, migrate.pending_journal_notice()):
                return accounts.EXIT_ERROR
            return dispatch(args)
    except migrate.JournalError as exc:
        error("{}. Check where the complete data of the unfinished migration is (the source, "
              "the target account directory, or a *.multi-codex-bak.* backup next to the source), "
              "make sure it is back at the source path, then delete the journal file".format(exc),
              phase="resume")
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
        orphan_scope = frozenset([name.casefold()])
    elif args.command == "apply":
        orphan_scope = accounts.ALL_ORPHANS
        if args.file:
            new = _load_apply_file(args.file, old)

    return accounts.converge(old, new, config_exists=exists, dry_run=args.dry_run,
                             orphan_scope=orphan_scope, adopt_accounts=adopt_accounts)


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
            "accounts": [{
                "name": name,
                "dir": directory,
                "dir_status": "ok" if is_dir else "missing-dir",
                "proxy": account.proxy,
                "shared": account.shared,
                "launcher": accounts.launcher_status(config, name),
                "credentials_store": found.store if found else None,
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


if __name__ == "__main__":
    sys.exit(main())
