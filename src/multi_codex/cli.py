"""命令行入口：解析参数、加锁、组装新配置，再交给收敛引擎或迁移模块。

退出码契约（方案 §5.2）：0 成功或已是目标状态；1 运行错误；2 参数不合法；
3 存在冲突且未做任何修改；4 迁移源目录正被占用。
"""

import argparse
import difflib
import json
import os
import subprocess
import sys
import time
from typing import List, Optional, Tuple

from . import (__version__, accounts, apps, binding, completion, doctor, identity, launcher, migrate, platform,
               shellpath, switch, usage)
from .actions import CREATE, DELETE, UNCHANGED, UPDATE, Action, error, hint, info, print_action, warn
from .config import (DEFAULT_SHARED_DIR, DEFAULT_SHARED_ITEMS, Account, Config, ConfigError, is_socks,
                     is_unshareable, load_config, normalize_proxy, parse_config, unique_items, validate_env_key,
                     validate_env_value, validate_name)
from .config import _valid_item as valid_shared_item  # 与配置解析用同一条名称规则
from .fsutil import KIND_DIR, KIND_MISSING, display_path, entry_kind, expand
from .lock import LockBusyError, WriteLock


class UsageError(Exception):
    """命令行参数不合法，对应退出码 2。"""


# 顶层帮助的子命令分组：新用户先看到上手要用的几条，进阶命令放最后。
# 这是子命令一句话说明的唯一来源：顶层分组列表与 `multi-codex <命令> -h` 的说明都从这里取；
# 新增子命令必须加进某一组，测试会核对这里与 parser 注册的子命令集合完全相等。
COMMAND_GROUPS = (
    ("Get started", (
        ("add", "add an account, adopt an existing directory, or change its options"),
        ("login", "log in to an account (runs codex login with its environment)"),
        ("migrate-default", "turn the default ~/.codex into a named account"),
        ("list", "show accounts, their status and who is logged in"),
        ("doctor", "check the installation, configuration and accounts (read-only)"),
    )),
    ("Everyday", (
        ("set", "change an existing account: proxy, sharing, adopted links"),
        ("run", "run a command with an account's environment (default: codex)"),
        ("usage", "show rate-limit usage of accounts"),
        ("use", "show or change the default account (what ~/.codex points to)"),
        ("bind", "bind a directory to an account (no arguments: list bindings)"),
        ("unbind", "remove the binding of a directory"),
        ("code", "open VS Code for an account (experimental)"),
        ("app", "open the Codex desktop app for an account (macOS, experimental)"),
        ("proxy", "set the proxy of an account"),
        ("path", "print an account's directory"),
        ("completion", "print a shell completion script"),
    )),
    ("Advanced", (
        ("env", "list or change an account's extra environment variables"),
        ("remove", "unregister an account (its directory is kept)"),
        ("rename", "rename an account and its launcher (the directory stays)"),
        ("restore", "undo migrate-default: move an account back to ~/.codex"),
        ("apply", "converge all accounts to the configuration"),
        ("init", "create or update the global settings"),
    )),
)
COMMAND_SUMMARY = {name: summary for _, commands in COMMAND_GROUPS for name, summary in commands}

HELP_EXAMPLES = """examples:
  multi-codex add work          create account "work" and its launcher codex-work
  multi-codex login work        log in once
  multi-codex list              who is logged in where
  multi-codex use work          make plain codex and the Dock apps use "work"
"""


def _grouped_commands_text() -> str:
    width = max(len(name) for name in COMMAND_SUMMARY) + 2
    lines = []
    for title, commands in COMMAND_GROUPS:
        lines.append("{}:".format(title))
        lines.extend("  {}{}".format(name.ljust(width), summary) for name, summary in commands)
        lines.append("")
    return "\n".join(lines).rstrip("\n")


def build_parser() -> argparse.ArgumentParser:
    # 子命令不传 help=：argparse 只为带 help 的子命令生成自动列表，去掉后由 description 里的分组列表代替。
    # RawDescriptionHelpFormatter 保留分组列表与示例的换行和对齐。
    parser = argparse.ArgumentParser(
        prog="multi-codex",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Manage multiple Codex CLI accounts: separate CODEX_HOME directories,\n"
                    "per-account launchers and per-account proxies.\n\n" + _grouped_commands_text(),
        epilog=HELP_EXAMPLES)
    parser.add_argument("--version", action="version", version="%(prog)s " + __version__)
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True

    p_init = sub.add_parser("init", description=COMMAND_SUMMARY["init"])
    p_init.add_argument("--root", help="directory that holds account directories (default ~/.cx)")
    p_init.add_argument("--bin-dir", help="directory for codex-<name> launchers (default ~/.local/bin)")
    p_init.add_argument("--shared-dir", help="directory whose items can be linked into accounts")
    p_init.add_argument("--shared-items", help="comma-separated items to share "
                        "(default {})".format(",".join(DEFAULT_SHARED_ITEMS)))
    _add_dry_run(p_init)
    _add_verbose(p_init)

    p_mig = sub.add_parser("migrate-default", description=COMMAND_SUMMARY["migrate-default"])
    # 省略时从源目录的 auth.json 读邮箱作为账号名（_derive_migrate_name）。
    p_mig.add_argument("name", nargs="?")
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

    p_add = sub.add_parser("add", description=COMMAND_SUMMARY["add"])
    _add_account_options(p_add)

    p_set = sub.add_parser("set", description=COMMAND_SUMMARY["set"])
    _add_account_options(p_set)

    p_proxy = sub.add_parser("proxy", description=COMMAND_SUMMARY["proxy"])
    p_proxy.add_argument("name")
    p_proxy.add_argument("value", help="port (e.g. 7901), URL, off or inherit")
    _add_dry_run(p_proxy)
    _add_verbose(p_proxy)

    p_rename = sub.add_parser("rename", description=COMMAND_SUMMARY["rename"])
    p_rename.add_argument("old", metavar="OLD")
    p_rename.add_argument("new", metavar="NEW")
    _add_dry_run(p_rename)
    _add_verbose(p_rename)

    p_remove = sub.add_parser("remove", description=COMMAND_SUMMARY["remove"])
    p_remove.add_argument("name")
    _add_dry_run(p_remove)
    _add_verbose(p_remove)

    p_apply = sub.add_parser("apply", description=COMMAND_SUMMARY["apply"])
    p_apply.add_argument("-f", "--file", help="use this file as the new configuration")
    _add_dry_run(p_apply)
    _add_verbose(p_apply)

    p_list = sub.add_parser("list", description=COMMAND_SUMMARY["list"])
    # 与写命令的 -v 含义不同：这里是“输出完整表格”（0.7 及以前的默认输出），不是“也打印 unchanged 项”。
    p_list.add_argument("-v", "--verbose", action="store_true",
                        help="show the full table (directories, launchers, plans) instead of the summary")
    _add_json(p_list)

    p_usage = sub.add_parser("usage", description=COMMAND_SUMMARY["usage"])
    p_usage.add_argument("names", nargs="*", metavar="NAME", help="accounts to show (default: all)")
    p_usage.add_argument("--live", action="store_true",
                         help="ask Codex (codex app-server, through the account's launcher) for live usage "
                              "instead of reading the last snapshot from local session logs")
    p_usage.add_argument("--timeout", type=_positive_seconds, default=usage.DEFAULT_LIVE_TIMEOUT_SEC,
                         help="total time limit per account for --live, in seconds (default 30)")
    _add_json(p_usage)

    p_doctor = sub.add_parser("doctor", description=COMMAND_SUMMARY["doctor"])
    _add_json(p_doctor)

    p_comp = sub.add_parser("completion", description=COMMAND_SUMMARY["completion"])
    p_comp.add_argument("shell", nargs="?", choices=completion.SHELLS)
    # 补全脚本在按 Tab 时调用它读取账号名；不是给人用的，所以不出现在帮助里。
    p_comp.add_argument("--list-accounts", action="store_true", help=argparse.SUPPRESS)

    # 不依赖 PATH：启动命令目录还没加进 PATH 时，codex-<名> login 找不到，这条照样能用。
    p_login = sub.add_parser("login", description=COMMAND_SUMMARY["login"],
                             usage="multi-codex login NAME [-- ARGS ...]")
    p_login.add_argument("name")

    p_run = sub.add_parser("run", description=COMMAND_SUMMARY["run"],
                           usage="multi-codex run [NAME] [-- COMMAND [ARG ...]]")
    # 省略 NAME 时按当前目录的绑定选账号（bind）。
    p_run.add_argument("name", nargs="?")

    p_bind = sub.add_parser("bind", description=COMMAND_SUMMARY["bind"])
    p_bind.add_argument("name", nargs="?")
    p_bind.add_argument("dir", nargs="?", help="directory to bind (default: current directory)")
    _add_dry_run(p_bind)
    _add_verbose(p_bind)

    p_unbind = sub.add_parser("unbind", description=COMMAND_SUMMARY["unbind"])
    p_unbind.add_argument("dir", nargs="?", help="directory to unbind (default: current directory)")
    _add_dry_run(p_unbind)
    _add_verbose(p_unbind)

    p_code = sub.add_parser("code", description=COMMAND_SUMMARY["code"],
                            usage="multi-codex code NAME [PATH] [--bin CODE] [-- CODE_ARGS ...]")
    p_code.add_argument("name")
    p_code.add_argument("path", nargs="?")
    p_code.add_argument("--bin", help="path of the VS Code `code` command (default: `code` on PATH)")

    p_app = sub.add_parser("app", description=COMMAND_SUMMARY["app"])
    p_app.add_argument("name")
    p_app.add_argument("--app", help="path of the Codex desktop app (default {})".format(apps.DEFAULT_DESKTOP_APP))

    p_path = sub.add_parser("path", description=COMMAND_SUMMARY["path"])
    p_path.add_argument("name")

    p_env = sub.add_parser("env", description=COMMAND_SUMMARY["env"])
    p_env.add_argument("name")
    p_env.add_argument("assignments", nargs="*", metavar="KEY=VALUE", help="variables to set")
    p_env.add_argument("--unset", action="append", default=[], metavar="KEY", help="variable to remove")
    p_env.add_argument("--clear", action="store_true", help="remove all variables of the account")
    _add_dry_run(p_env)
    _add_verbose(p_env)

    p_use = sub.add_parser("use", description=COMMAND_SUMMARY["use"])
    p_use.add_argument("name", nargs="?")
    p_use.add_argument("--skip-process-check", action="store_true",
                       help="switch even if a process may be using the current default account")
    _add_dry_run(p_use)

    p_restore = sub.add_parser("restore", description=COMMAND_SUMMARY["restore"])
    p_restore.add_argument("name")
    p_restore.add_argument("--skip-process-check", action="store_true",
                           help="do not check whether the account directory is in use")
    p_restore.add_argument("--accept-relogin", action="store_true",
                           help="restore even though credentials are in the system keyring; "
                                "you will need to log in again afterwards")
    _add_dry_run(p_restore)
    return parser


def _add_account_options(parser: argparse.ArgumentParser) -> None:
    """add 与 set 的参数完全相同，只在 dispatch 里区分“账号不存在时新建还是报错”。"""
    parser.add_argument("name")
    parser.add_argument("--proxy", help="port, URL, off or inherit (new accounts default to inherit)")
    shared_group = parser.add_mutually_exclusive_group()
    # 可选值：不带值时 shared_to 为 True（沿用已设置的 shared.dir，未设置时用默认目录），
    # 带值时是新的共享目录。把账号名写在 --shared 后面（add --shared work）会被当成目录，
    # argparse 随之报缺少 name，所以帮助与 README 一律写成 add NAME --shared。
    shared_group.add_argument("--shared", dest="shared_to", nargs="?", const=True, default=None, metavar="DIR",
                              help="link shared items into this account; DIR changes the shared directory of "
                                   "every shared account (default: keep the current one, or {} if none is "
                                   "set)".format(DEFAULT_SHARED_DIR))
    shared_group.add_argument("--no-shared", dest="no_shared", action="store_true",
                              help="do not link shared items (default for new accounts)")
    parser.add_argument("--config-from", metavar="OTHER",
                        help="copy config.toml from account OTHER (once; existing different content is a conflict)")
    parser.add_argument("--adopt", action="store_true",
                        help="take over existing links that already point to the shared items, "
                             "so that turning sharing off later removes them too")
    # 按账号退出 / 恢复全局共享清单中的某些项；都可重复。退出只删本工具建的链接，与关闭共享一致。
    parser.add_argument("--shared-exclude", action="append", metavar="ITEM",
                        help="do not link this shared item into this account (repeatable)")
    parser.add_argument("--shared-include", action="append", metavar="ITEM",
                        help="undo --shared-exclude for this item (repeatable)")
    _add_dry_run(parser)
    _add_verbose(parser)


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


def _add_verbose(parser: argparse.ArgumentParser) -> None:
    # 只加给经 dispatch 末尾 converge 输出动作行的写命令；默认不打印 unchanged 行。
    parser.add_argument("-v", "--verbose", action="store_true", help="also show items that are unchanged")


def _split_run_command(argv: List[str]) -> Tuple[List[str], List[str]]:
    """`run NAME -- CMD ...`：在第一个 `--` 处分开，`--` 之后原样作为要运行的命令。

    不交给 argparse 的 REMAINDER：不同 Python 版本对 `--` 的处理不一致（3.9 起会吞掉第一个 `--`），
    `run a -- -- x` 这种命令就会被解析成不同的样子。
    """
    if argv[:1] in (["run"], ["code"], ["login"]) and "--" in argv:
        index = argv.index("--")
        return argv[:index], argv[index + 1:]
    return argv, []


def _unknown_command(parser: argparse.ArgumentParser, argv: List[str]) -> Optional[str]:
    """子命令拼错时返回带建议的报错文字；命令名正确或交给 argparse 处理更合适时返回 None。

    只看第一个不以 `-` 开头的参数：顶层只有 -h 和 --version 两个选项，都不带值，
    所以它就是子命令的位置。没有这样的参数（如只给了 --version）时交给 argparse。
    调用方已用 _split_run_command 切掉了 run / code / login 的 `--` 之后的部分，那里的词不参与判断。
    """
    word = next((arg for arg in argv if not arg.startswith("-")), None)
    if word is None:
        return None
    names: List[str] = []
    for action in parser._actions:  # noqa: SLF001 —— argparse 没有公开的子命令遍历接口
        if isinstance(action, argparse._SubParsersAction):  # noqa: SLF001
            names.extend(action.choices)
    if word in names:
        return None
    close = difflib.get_close_matches(word, names, n=1, cutoff=0.6)
    if close:
        return "unknown command {!r}; did you mean {!r}?".format(word, close[0])
    return "unknown command {!r}; run multi-codex -h for the list".format(word)


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    argv, run_command = _split_run_command(list(sys.argv[1:] if argv is None else argv))
    if not argv:
        # 不带任何参数：新用户最常见的第一次运行，给上手指引而不是 argparse 的“缺少 COMMAND”报错。
        print_getting_started()
        return accounts.EXIT_OK
    unknown = _unknown_command(parser, argv)
    if unknown is not None:
        error(unknown)
        return accounts.EXIT_USAGE
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
        if args.command == "login":
            # `--` 之后的参数原样交给 codex login，本工具不校验（取决于上游支持哪些参数）。
            return cmd_run(args.name, ["codex", "login"] + run_command)
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
            return cmd_list(args.json, args.verbose)
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
        name = _checked_name(args.name if args.name is not None else _derive_migrate_name(args.source))
        proxy = _checked_proxy(args.proxy) if args.proxy is not None else None
        code = migrate.migrate_default(name, args.source, args.copy, args.keep_backup, proxy,
                                       args.skip_process_check, args.dry_run,
                                       accept_relogin=args.accept_relogin)
        if code == accounts.EXIT_OK and not args.dry_run:
            # 迁移过来的目录本来就带着登录，只提醒启动命令目录是否在 PATH 中。
            _hint_after_setup(load_config()[0], name, check_login=False)
        return code

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
            for item in items:
                if is_unshareable(item):
                    raise UsageError("--shared-items must not include {!r}: it holds account-specific "
                                     "state".format(item))
            new.shared_items = items
    elif args.command in ("add", "set"):
        # add 对已登记的账号也是“修改选项”（保持兼容）；set 只改已登记的账号，不会新建。
        name = _checked_name(args.name)
        account = new.find(name)
        if account is None:
            # 未登记先于“没有选项”判断：`set 拼错的名字` 应该得到名字上的建议，而不是“请给选项”。
            if args.command == "set":
                error(_not_registered_for_set(new, name))
                return accounts.EXIT_ERROR
            account = Account(name)
            new.accounts[name] = account
        if args.command == "set" and not _any_account_option(args):
            raise UsageError("nothing to set; give at least one option, e.g. `multi-codex set {} --proxy 7901`".format(
                account.name))
        if args.proxy is not None:
            account.proxy = _checked_proxy(args.proxy)
        if args.shared_to is not None:
            if args.shared_to is not True:
                # 改的是全局 shared.dir：所有开启共享的账号都会跟着改指向，见收敛后的提示。
                new.shared_dir = _checked_shared_dir(args.shared_to)
            elif not new.shared_dir:
                new.shared_dir = DEFAULT_SHARED_DIR
            account.shared = True
        elif args.no_shared:
            account.shared = False
        # 放在 --config-from 之前，plan_config_copy 才能看到排除项。注意同一条命令里排除 config.toml 并复制
        # 仍会判冲突：计划按当前文件判断，那时 config.toml 还是链到共享内容的软链；要分两条命令。
        _apply_shared_exclude(account, args)
        if args.adopt:
            # 接管只对开启了共享的账号有意义；关闭状态下工具本来就不管这些软链。
            if not account.shared:
                raise UsageError("--adopt requires sharing to be on for {!r}; use --shared".format(account.name))
            adopt_accounts = frozenset([account.name.casefold()])
        if args.config_from:
            extra_actions = _config_copy_actions(new, account, args.config_from)
            if extra_actions is None:
                return accounts.EXIT_ERROR
    elif args.command == "proxy":
        account = new.find(_checked_name(args.name))
        if account is None:
            error(new.not_registered(args.name))
            return accounts.EXIT_ERROR
        account.proxy = _checked_proxy(args.value)
    elif args.command == "rename":
        prepared = _rename_config(new, args)
        if isinstance(prepared, int):
            return prepared
        new, orphan_scope = prepared
    elif args.command == "remove":
        name = _checked_name(args.name)
        account = new.find(name)
        if account is None:
            # 退出码仍是 0（已处于目标状态）；下面照常收敛，负责清理这个名字残留的受管启动命令。
            info("nothing to remove: {}".format(new.not_registered(name)))
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
            error(new.not_registered(name))
            return accounts.EXIT_ERROR
        _apply_env_changes(account, args)
    elif args.command == "bind":
        account = new.find(_checked_name(args.name))
        if account is None:
            error(new.not_registered(args.name))
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
                             orphan_scope=orphan_scope, adopt_accounts=adopt_accounts, extra_actions=extra_actions,
                             verbose=args.verbose)
    if binding_change is not None:
        # 绑定没变时配置也没变，execute 已经说了 already up to date，非 verbose 不再重复。
        if code == accounts.EXIT_OK and (args.verbose or binding_change.status != UNCHANGED):
            print_action(binding_change, dry_run=args.dry_run)
        elif code != accounts.EXIT_OK:
            info("binding not changed because of the errors above")
    if args.command in ("add", "set") and code == accounts.EXIT_OK and not args.dry_run:
        _hint_shared_dir(old, new, args)
        _hint_shared_exclude(new, args)
    if args.command == "rename" and code == accounts.EXIT_OK and not args.dry_run:
        info("renamed {} to {}; directory {} is unchanged".format(
            args.old, args.new, display_path(accounts.account_dir(new, args.new))))
    # set 不新建账号，不需要“下一步登录”的提示。
    if args.command == "add" and code == accounts.EXIT_OK and not args.dry_run:
        _hint_after_setup(new, args.name, check_login=True)
    return code


def _any_account_option(args: argparse.Namespace) -> bool:
    return (args.proxy is not None or args.shared_to is not None or args.no_shared or args.adopt
            or bool(args.config_from) or bool(args.shared_exclude) or bool(args.shared_include))


def _apply_shared_exclude(account: Account, args: argparse.Namespace) -> None:
    """--shared-include 先撤销，--shared-exclude 再追加；同一名称同时出现在两边是用法错误。

    只改配置：链接的增删由收敛计划完成（shared.plan_shared 跳过被排除的项，原来的受管链接按关闭共享删除）。
    """
    exclude = args.shared_exclude or []
    include = args.shared_include or []
    for item in exclude + include:
        if not valid_shared_item(item):
            raise UsageError("shared item names must be plain names without '/', got {!r}".format(item))
    both = sorted(set(exclude) & set(include))
    if both:
        raise UsageError("{} given to both --shared-exclude and --shared-include".format(", ".join(both)))
    kept = [item for item in account.shared_exclude if item not in include]
    account.shared_exclude = unique_items(kept + exclude)


def _hint_shared_exclude(config: Config, args: argparse.Namespace) -> None:
    """退出项暂时不起作用的两种情况：名称不在共享清单里（精确匹配，与 plan_shared 相同），或账号没开共享。"""
    exclude = args.shared_exclude or []
    if not exclude:
        return
    account = config.find(args.name)
    for item in exclude:
        if item not in config.shared_items:
            info("note: {} is not in shared.items; the exclusion takes effect only if it is added there".format(
                item))
    if account is not None and not account.shared:
        info("note: sharing is off for {}; the exclusion applies once it is turned on".format(account.name))


def _rename_config(new: Config, args: argparse.Namespace):
    """rename 的新配置：返回 (新配置, orphan_scope)，或直接返回退出码。

    新账号是旧账号的完整副本（代理、共享、受管链接、环境变量、退出项、目录名），只改名字；
    目录名保持不变，所以目录、登录与共享链接都不动。旧启动命令由收敛计划按“账号移除”删除，
    accounts.plan 会因目录仍被新名字使用而跳过删除共享链接。
    """
    old_name = _checked_name(args.old)
    new_name = _checked_name(args.new)
    account = new.find(old_name)
    if account is None:
        error(new.not_registered(old_name), phase="rename")
        return accounts.EXIT_ERROR
    if new_name.casefold() == account.name.casefold():
        # 大小写不敏感的文件系统上 codex-Work 与 codex-work 是同一个文件，删旧建新会互相覆盖。
        raise UsageError("only the letter case differs; renaming that way is not supported")
    if new.find(new_name) is not None:
        error("account {!r} already exists".format(new.find(new_name).name), phase="rename")
        return accounts.EXIT_CONFLICT
    renamed = account.copy()
    renamed.name = new_name
    # 按原顺序重建字典，config.json 里账号的顺序不变。
    new.accounts = {(new_name if key == account.name else key): (renamed if key == account.name else value)
                    for key, value in new.accounts.items()}
    for path, bound in list(new.bindings.items()):
        if bound.casefold() == account.name.casefold():
            new.bindings[path] = new_name
    return new, frozenset([account.name.casefold()])


def _not_registered_for_set(config: Config, name: str) -> str:
    """set 遇到未登记账号的报错：not_registered 已带下一步建议时不再追加，避免出现两条建议。

    按条件重新判断，不解析 not_registered 返回的文字：那两种情况（有相近名字、还没有任何账号）
    与 config.not_registered 的判断规则相同（casefold + difflib，cutoff 0.6）。
    """
    message = config.not_registered(name)
    folded = [account.casefold() for account in config.accounts]
    has_suggestion = not folded or bool(difflib.get_close_matches(name.casefold(), folded, n=1, cutoff=0.6))
    if has_suggestion:
        return message
    return "{}; use `multi-codex add NAME` to create it".format(message)


def _checked_shared_dir(value: str) -> str:
    """`--shared DIR` 的目录：必须看得出是路径，相对路径转成绝对路径后再写入配置。

    只认含 `/`、或以 `~`、`.` 开头的值：`add work --shared other` 这种把账号名误写在后面的情况
    会被当成目录，必须拦下，否则会把全局共享目录改成当前目录下一个叫 other 的目录。
    不以 `~` 开头的值转成绝对路径：expand() 按每次运行时的当前目录解析相对路径，
    原样写入后换一个目录执行 apply，所有共享链接都会被判为指向别处。
    """
    if "/" not in value and not value.startswith(("~", ".")):
        raise UsageError("--shared DIR must be a path (contains '/' or starts with '~' or '.'), got {!r}".format(
            value))
    if value.startswith("~"):
        return value
    return os.path.abspath(value)


def _hint_shared_dir(old: Config, new: Config, args: argparse.Namespace) -> None:
    """add / set 收敛成功后的两条共享目录提示（只输出，不改文件、不影响退出码）。

    1. 共享目录变了：改的是全局设置，所有共享账号都跟着改指向，新目录里没有的条目原来的链接会被删掉；
       只是写法不同、展开后是同一个目录时不算变化。
    2. 本次命令开启了共享，但共享目录里一个条目都没有：链接一个都不会出现，告诉用户该往哪里放什么。
    """
    if new.shared_dir and (not old.shared_dir or expand(new.shared_dir) != expand(old.shared_dir)):
        info("shared directory is now {} (was {}); every shared account follows it, "
             "and links to items missing there are removed".format(
                 display_path(expand(new.shared_dir)),
                 display_path(expand(old.shared_dir)) if old.shared_dir else "not set"))
    if args.shared_to is None or not new.shared_dir or not new.shared_items:
        return
    shared_root = expand(new.shared_dir)
    # 判定与 shared.plan_shared 相同：条目不存在（KIND_MISSING）的才算缺。
    if all(entry_kind(os.path.join(shared_root, item)) == KIND_MISSING for item in new.shared_items):
        info("note: {} has none of {} yet; put what every account should share there, "
             "then run `multi-codex apply`".format(display_path(shared_root), ", ".join(new.shared_items)))


# identity.read_identity 的 login 取值 → 读不出邮箱的原因（给 _derive_migrate_name 的报错用）。
_NO_EMAIL_REASONS = {
    identity.LOGIN_LOGGED_OUT: "not logged in",
    identity.LOGIN_APIKEY: "logged in with an API key (no e-mail)",
    identity.LOGIN_KEYRING: "credentials are in the system keyring",
    identity.LOGIN_UNREADABLE: "auth.json cannot be read",
    identity.LOGIN_CHATGPT: "the login has no e-mail",
}


def _derive_migrate_name(source: Optional[str]) -> str:
    """`migrate-default` 省略 NAME 时推导账号名（feature-onboarding-commands B2）。

    有未完成的迁移时用记录里的名称：中途 ~/.codex 可能已被移走、读不到 auth.json，
    而续跑本来就必须用记录里的名称（migrate._resume 会核对）。
    否则读源目录 auth.json 里的邮箱；读不到或不是合法账号名时抛 UsageError（退出码 2），
    什么都不动——不把邮箱改写成“合法”的名字，生成的名字用户预料不到，还可能撞上已有账号。
    """
    journal = migrate.load_journal()
    if journal is not None:
        return journal["name"]
    source_path = expand(source or platform.default_source())
    found = identity.read_identity(source_path)
    reason = _NO_EMAIL_REASONS.get(found.login, "unrecognized login type")
    if found.login == identity.LOGIN_CHATGPT and found.email:
        try:
            validate_name(found.email)
        except ValueError:
            reason = "e-mail {} is not a valid account name".format(found.email)
        else:
            info("using account name {!r} from {}".format(
                found.email, display_path(os.path.join(source_path, "auth.json"))))
            return found.email
    raise UsageError("cannot tell the account name from {}: {}; pass a NAME, "
                     "e.g. multi-codex migrate-default main".format(display_path(source_path), reason))


def _hint_after_setup(config: Config, name: str, check_login: bool) -> None:
    """add / migrate-default 成功后告诉用户下一步：未登录时怎么登录，启动命令目录不在 PATH 时怎么加。

    只读：读一次 auth.json、扫描一次 PATH，不改任何文件，也不影响退出码。
    """
    account = config.find(name)
    if account is None:
        return
    bin_dir = expand(config.bin_dir)
    on_path = platform.dir_on_path(bin_dir)
    if check_login:
        found = identity.read_identity(accounts.account_dir(config, account.name))
        # 只提示“未登录”；keyring、unreadable 等情况交给 doctor，那里有完整说明。
        if found.login == identity.LOGIN_LOGGED_OUT:
            # 用 multi-codex login 而不是 codex-<名> login：前者不依赖 PATH，两种情况都能照做。
            hint("next: log in with `multi-codex login {}`".format(account.name))
    if not on_path:
        sys.stdout.flush()  # 同 hint()：让警告排在动作行之后
        warn("{} is not on PATH, so `codex-{}` will not be found; {}".format(
            display_path(bin_dir), account.name, shellpath.current_path_hint(bin_dir)))


def print_getting_started() -> None:
    """不带参数运行时的上手指引（stdout，退出码 0）。

    配置读不了时不报错，只给通用步骤：这里是新用户的第一眼，不能因为配置问题变成报错；
    配置问题由 `multi-codex doctor` 负责说明。
    """
    try:
        config, exists = load_config()
    except (ConfigError, OSError):
        config, exists = None, False
    print("multi-codex: run several Codex accounts side by side.")
    print()
    names = list(config.accounts) if config is not None and exists else []
    if names:
        print("Accounts: {} (multi-codex list for details)".format(", ".join(names)))
        print()
        print("  codex-NAME                    use an account instead of codex")
        print("  multi-codex usage             5-hour and weekly usage")
        print("  multi-codex doctor            find problems and how to fix them")
    else:
        print("Get started:")
        print("  multi-codex add NAME          create an account and its launcher codex-NAME")
        print("  multi-codex login NAME        log in once")
        print("  codex-NAME                    use it instead of codex")
        if entry_kind(switch.default_link()) == KIND_DIR:
            print("  multi-codex migrate-default [NAME]   keep your current ~/.codex login as an account")
    print()
    print("Run `multi-codex -h` for all commands.")


def _config_copy_actions(new: Config, account: Account, other_name: str) -> Optional[List]:
    """校验 --config-from 并生成复制动作；校验失败时输出原因并返回 None（退出码 1），参数错误抛 UsageError。"""
    other = new.find(_checked_name(other_name))
    if other is None:
        error(new.not_registered(other_name))
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
    # parse_config 会给省略的 dir 填默认值，要判断文件里是否显式写了，只能看原始 JSON。
    raw_accounts = json.loads(raw).get("accounts", {})
    for account in new.accounts.values():
        _warn_if_socks(account.proxy, account.name)
        current = old.find(account.name)
        account.managed_links = list(current.managed_links) if current else []
        if current is not None:
            # 目录名是工具内部状态：改了已登记账号的目录名，就等于让它指向另一个目录。
            written = raw_accounts.get(account.name, {})
            if isinstance(written, dict) and "dir" in written and written["dir"] != current.dir_name:
                warn("'dir' of {!r} in {} is ignored for a registered account; it keeps using {}".format(
                    account.name, path, display_path(accounts.account_dir(old, current.name))))
            account.dir_name = current.dir_name
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


def cmd_list(as_json: bool = False, verbose: bool = False) -> int:
    """账号一览。默认简表（登录、代理、共享、额度、状态）；verbose 时输出 0.7 及以前的完整表格，逐字不变。"""
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
                "shared_exclude": list(account.shared_exclude),
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

    if not verbose:
        return _print_list_summary(config, entries, duplicates)
    print("root: {}".format(display_path(expand(config.root))))
    print("bin_dir: {}".format(display_path(expand(config.bin_dir))))
    # 表头路径缩写成 ~/… 只为好读；行数不变，按行位置解析表格的脚本不受影响（脚本应使用 --json）。
    print("shared.dir: {}".format(display_path(expand(config.shared_dir)) if config.shared_dir else "(not set)"))
    print("default: {}".format(switch.describe_default(config)[1]))
    if not config.accounts:
        print("no accounts registered")
        return accounts.EXIT_OK
    # 前 5 列与 0.2.0 相同，新列只追加在后面，按列位置解析旧输出的脚本不受影响。
    rows = [("NAME", "DIR", "PROXY", "SHARED", "LAUNCHER", "LOGIN", "PLAN")]
    for name, account, directory, is_dir, found in entries:
        rows.append((name, "ok" if is_dir else "missing-dir", account.proxy,
                     _shared_cell(account), accounts.launcher_status(config, name),
                     identity.display_login(found) if found else "-",
                     (found.plan if found and found.plan else "-")))
    widths = [max(len(row[index]) for row in rows) for index in range(len(rows[0]))]
    for row in rows:
        print("  ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip())
    _warn_duplicates(duplicates, entries)
    return accounts.EXIT_OK


def _print_list_summary(config: Config, entries, duplicates: List[List[str]]) -> int:
    """list 的默认简表：一眼看出谁登录在哪、额度用了多少、哪个账号有问题。

    STATUS 只列需要处理的问题，细节交给 doctor；有任何问题时表格后面提示去跑 doctor。
    额度只读本机会话日志里的最近快照（usage.local_snapshot），不联网、不启动进程。
    """
    print("default: {}".format(switch.describe_default(config)[1]))
    if not config.accounts:
        print("no accounts registered")
        return accounts.EXIT_OK
    now = time.time()
    rows = [("NAME", "LOGIN", "PROXY", "SHARED", "USAGE", "STATUS")]
    has_problem = False
    has_shared_sessions = False
    for name, account, directory, is_dir, found in entries:
        problems = []
        cell = "-"
        if not is_dir:
            # 目录都不在了，登录状态没有意义，只报这一条。
            problems.append("missing-dir")
        else:
            result = usage.local_snapshot(name, directory)
            cell = _usage_cell(result, now)
            if result.sessions_shared and cell != "-":
                cell += "*"
                has_shared_sessions = True
        launcher_state = accounts.launcher_status(config, name)
        if launcher_state != "ok":
            problems.append("launcher {}".format(launcher_state))
        if found is not None and found.login == identity.LOGIN_LOGGED_OUT:
            problems.append("not logged in")
        has_problem = has_problem or bool(problems)
        rows.append((name, identity.display_login(found) if found else "-", account.proxy,
                     _shared_cell(account), cell, ", ".join(problems) or "ok"))
    widths = [max(len(row[index]) for row in rows) for index in range(len(rows[0]))]
    for row in rows:
        print("  ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip())
    if has_shared_sessions:
        print("* sessions is shared with other accounts; usage may belong to another account")
    if has_problem:
        print("run `multi-codex doctor` for details")
    _warn_duplicates(duplicates, entries)
    return accounts.EXIT_OK


def _shared_cell(account: Account) -> str:
    """SHARED 列：共享开启且有退出项时写出退出的项；没有退出项时与 0.8 相同（yes / no）。"""
    if not account.shared:
        return "no"
    if account.shared_exclude:
        return "yes (not: {})".format(", ".join(account.shared_exclude))
    return "yes"


def _usage_cell(result: usage.UsageResult, now: float) -> str:
    """简表的 USAGE 单元格，如 `5h 23%, 7d 41%`；没有快照时为 `-`。

    窗口按快照里实际有的显示（有的账号只有 30 天窗口），不固定成 5h、7d 两列。
    选额度时优先 codex（Codex 本身的额度），没有再取第一个带窗口的。
    已过重置时刻的窗口显示 `reset`：旧百分比已不代表当前用量，判定与 usage.format_result 相同。
    """
    with_windows = [limit for limit in result.limits if limit.windows]
    if not with_windows:
        return "-"
    chosen = next((limit for limit in with_windows if limit.limit_id == usage.DEFAULT_LIMIT_ID), with_windows[0])
    parts = []
    for window in chosen.windows:
        label = usage._window_label(window.window_minutes)  # noqa: SLF001 —— 与 usage 命令用同一套标签
        if window.resets_at is not None and window.resets_at <= now:
            parts.append("{} reset".format(label))
        else:
            parts.append("{} {:.0f}%".format(label, window.used_percent))
    return ", ".join(parts)


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
                                       config.not_registered(name), None, False, [])
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
        error(config.not_registered(name))
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
        error(config.not_registered(name))
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
    data_dir = apps.gui_data_dir(config, account.dir_name, "vscode")
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
    data_dir = apps.gui_data_dir(config, account.dir_name, "desktop")
    log = apps.desktop_log(config, account.dir_name)
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
        error(config.not_registered(name))
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
        error(config.not_registered(name))
        return accounts.EXIT_ERROR
    for key in sorted(account.env):
        print("{}={}".format(key, account.env[key]))
    return accounts.EXIT_OK


def cmd_use_show() -> int:
    config, _ = load_config()
    owner, description = switch.describe_default(config)
    # stdout 保持原样（脚本可能读取账号名或 `(none)`），下一步提示写 stderr。
    print(description)
    if owner is None:
        kind = entry_kind(switch.default_link())
        if kind == KIND_MISSING:
            hint("no default account yet; `multi-codex use NAME` makes ~/.codex point to an account")
        elif kind == KIND_DIR:
            hint("~/.codex is not managed yet; run `multi-codex migrate-default NAME` first")
    return accounts.EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
