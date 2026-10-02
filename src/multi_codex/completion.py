"""生成 bash / zsh / fish 的补全脚本（feature-cli-ergonomics §5.1.1）。

子命令、选项、哪些选项带值，都在生成时从 argparse 解析器读出，新增选项不必另外维护列表。
账号名在补全时调用 `multi-codex completion --list-accounts` 动态读取。

三种 shell 判断“当前是第几个参数”的规则相同：按空白切分已输入的部分，以 `-` 开头的词不算，
带值选项后面紧跟的那个词也不算；`run` 的 `--` 之后不再补全。
bash 不能用 COMP_WORDS / COMP_CWORD：它们按 COMP_WORDBREAKS 切分，默认含 `@` 和 `=`，
会把邮箱形式的账号名拆成几段，参数位置就数错了。
"""

import argparse
from typing import Dict, List, Tuple

# 第一个位置参数是账号名的子命令；usage 的每个位置参数都是账号名。
ACCOUNT_COMMANDS = ("add", "set", "rename", "login", "proxy", "remove", "migrate-default", "run", "path", "env",
                    "usage", "use", "restore", "bind", "code", "app")
MULTI_ACCOUNT_COMMANDS = ("usage",)
SHELLS = ("bash", "zsh", "fish")


def command_spec(parser: argparse.ArgumentParser) -> Dict[str, Tuple[List[str], List[str]]]:
    """返回 {子命令: (全部选项, 带值的选项)}；不含 -h/--help 和隐藏选项。"""
    spec: Dict[str, Tuple[List[str], List[str]]] = {}
    for action in parser._actions:  # noqa: SLF001 —— argparse 没有公开的子命令遍历接口
        if not isinstance(action, argparse._SubParsersAction):  # noqa: SLF001
            continue
        for name, sub in action.choices.items():
            options: List[str] = []
            valued: List[str] = []
            for sub_action in sub._actions:  # noqa: SLF001
                if not sub_action.option_strings or sub_action.help == argparse.SUPPRESS:
                    continue
                if isinstance(sub_action, argparse._HelpAction):  # noqa: SLF001
                    continue
                options.extend(sub_action.option_strings)
                if sub_action.nargs != 0:
                    valued.extend(sub_action.option_strings)
            spec[name] = (options, valued)
    return spec


def _case_lines(spec: Dict[str, Tuple[List[str], List[str]]], index: int, indent: str) -> List[str]:
    lines = []
    for name in sorted(spec):
        words = spec[name][index]
        if words:
            lines.append('{}{}) echo "{}" ;;'.format(indent, name, " ".join(words)))
    return lines


def bash_script(spec: Dict[str, Tuple[List[str], List[str]]]) -> str:
    commands = " ".join(sorted(spec))
    return "\n".join([
        "# multi-codex bash completion. Load with: eval \"$(multi-codex completion bash)\"",
        "_multi_codex_options() {",
        '    case "$1" in',
    ] + _case_lines(spec, 0, "        ") + [
        "    esac",
        "}",
        "_multi_codex_value_options() {",
        '    case "$1" in',
    ] + _case_lines(spec, 1, "        ") + [
        "    esac",
        "}",
        "_multi_codex() {",
        '    local line="${COMP_LINE:0:COMP_POINT}"',
        "    local -a words",
        '    read -r -a words <<< "$line"',
        '    local count=${#words[@]} cur=""',
        '    if [[ ! "$line" =~ [[:space:]]$ ]] && (( count > 1 )); then',
        '        cur="${words[count-1]}"',
        "        count=$((count - 1))",
        "    fi",
        '    local cmd="" npos=0 skip=0 after_dd=0 i w',
        "    for ((i = 1; i < count; i++)); do",
        '        w="${words[i]}"',
        '        if [[ -z "$cmd" ]]; then',
        '            [[ "$w" == -* ]] || cmd="$w"',
        "            continue",
        "        fi",
        '        if [[ ( "$cmd" == run || "$cmd" == login ) && "$w" == "--" ]]; then after_dd=1; break; fi',
        "        if (( skip )); then skip=0; continue; fi",
        '        if [[ "$w" == -* ]]; then',
        '            case " $(_multi_codex_value_options "$cmd") " in *" $w "*) skip=1 ;; esac',
        "        else",
        "            npos=$((npos + 1))",
        "        fi",
        "    done",
        '    local candidates=""',
        '    if [[ -z "$cmd" ]]; then',
        '        candidates="{}"'.format(commands),
        "    elif (( after_dd || skip )); then",
        "        COMPREPLY=()",
        "        return 0",
        '    elif [[ "$cur" == -* ]]; then',
        '        candidates="$(_multi_codex_options "$cmd")"',
        "    else",
        '        case " {} " in'.format(" ".join(ACCOUNT_COMMANDS)),
        '            *" $cmd "*)',
        '                if (( npos == 0 )) || [[ "$cmd" == usage ]]; then',
        '                    candidates="$(multi-codex completion --list-accounts 2>/dev/null)"',
        "                fi ;;",
        "        esac",
        '        [[ "$cmd" == proxy ]] && (( npos == 1 )) && candidates="off inherit"',
        '        [[ "$cmd" == completion ]] && (( npos == 0 )) && candidates="{}"'.format(" ".join(SHELLS)),
        "    fi",
        '    COMPREPLY=($(compgen -W "$candidates" -- "$cur"))',
        "    # bash 只会替换当前词最后一个 @ 之后的部分，候选里要去掉它之前的前缀。",
        '    if [[ "$cur" == *@* && "$COMP_WORDBREAKS" == *@* ]]; then',
        '        local prefix="${cur%"${cur##*@}"}"',
        '        COMPREPLY=("${COMPREPLY[@]#"$prefix"}")',
        "    fi",
        "    return 0",
        "}",
        "# -o default：候选为空时（带值选项之后、run -- 之后）退回 bash 自带的文件名补全。",
        "complete -o default -F _multi_codex multi-codex",
        "",
    ])


def zsh_script(spec: Dict[str, Tuple[List[str], List[str]]]) -> str:
    commands = " ".join(sorted(spec))
    return "\n".join([
        "# multi-codex zsh completion. Load after compinit with: eval \"$(multi-codex completion zsh)\"",
        "_multi_codex_options() {",
        '    case "$1" in',
    ] + _case_lines(spec, 0, "        ") + [
        "    esac",
        "}",
        "_multi_codex_value_options() {",
        '    case "$1" in',
    ] + _case_lines(spec, 1, "        ") + [
        "    esac",
        "}",
        "_multi_codex() {",
        '    local cur="${words[CURRENT]}" cmd="" w',
        "    local -i npos=0 skip=0 after_dd=0",
        "    for w in \"${(@)words[2,CURRENT-1]}\"; do",
        '        if [[ -z "$cmd" ]]; then',
        '            [[ "$w" == -* ]] || cmd="$w"',
        "            continue",
        "        fi",
        '        if [[ ( "$cmd" == run || "$cmd" == login ) && "$w" == "--" ]]; then after_dd=1; break; fi',
        "        if (( skip )); then skip=0; continue; fi",
        '        if [[ "$w" == -* ]]; then',
        '            [[ " $(_multi_codex_value_options "$cmd") " == *" $w "* ]] && skip=1',
        "        else",
        "            npos=$((npos + 1))",
        "        fi",
        "    done",
        "    local -a candidates",
        '    if [[ -z "$cmd" ]]; then',
        "        candidates=({})".format(commands),
        "    elif (( after_dd || skip )); then",
        "        _files",
        "        return",
        '    elif [[ "$cur" == -* ]]; then',
        '        candidates=(${=$(_multi_codex_options "$cmd")})',
        "    else",
        # 与 bash 版相同：先确认是收账号名的子命令，再看是第一个参数，或者是 usage（每个参数都是账号名）。
        '        if [[ " {} " == *" $cmd "* ]] && {{ (( npos == 0 )) || [[ "$cmd" == usage ]]; }}; then'.format(
            " ".join(ACCOUNT_COMMANDS)),
        '            candidates=(${(f)"$(multi-codex completion --list-accounts 2>/dev/null)"})',
        "        fi",
        '        [[ "$cmd" == proxy ]] && (( npos == 1 )) && candidates=(off inherit)',
        '        [[ "$cmd" == completion ]] && (( npos == 0 )) && candidates=({})'.format(" ".join(SHELLS)),
        "    fi",
        "    # 没有候选时（bind 的目录、code 的路径等）退回文件名补全，与 bash 的 -o default 一致。",
        "    if (( ${#candidates} == 0 )); then",
        "        _files",
        "        return",
        "    fi",
        '    compadd -- "${candidates[@]}"',
        "}",
        "compdef _multi_codex multi-codex",
        "",
    ])


def fish_script(spec: Dict[str, Tuple[List[str], List[str]]]) -> str:
    lines = [
        "# multi-codex fish completion. Load with: multi-codex completion fish | source",
        "function __multi_codex_value_options",
        "    switch $argv[1]",
    ]
    for name in sorted(spec):
        if spec[name][1]:
            lines.append("        case {}".format(name))
            lines.append("            printf '%s\\n' {}".format(" ".join(spec[name][1])))
    lines += [
        "    end",
        "end",
        "# 判断当前位置要补什么：account、proxy-value、shell，或什么都不补。",
        "function __multi_codex_is",
        "    set -l tokens (commandline -opc)",
        "    set -e tokens[1]",
        "    set -l cmd ''",
        "    set -l npos 0",
        "    set -l skip 0",
        "    for w in $tokens",
        "        if test -z \"$cmd\"",
        "            string match -q -- '-*' $w; or set cmd $w",
        "            continue",
        "        end",
        "        if contains -- \"$cmd\" run login; and test \"$w\" = --",
        "            return 1",
        "        end",
        "        if test $skip -eq 1",
        "            set skip 0",
        "            continue",
        "        end",
        "        if string match -q -- '-*' $w",
        "            contains -- $w (__multi_codex_value_options $cmd); and set skip 1",
        "        else",
        "            set npos (math $npos + 1)",
        "        end",
        "    end",
        "    test -n \"$cmd\"; or return 1",
        "    test $skip -eq 0; or return 1",
        "    set -l kind ''",
        "    if contains -- $cmd {}".format(" ".join(ACCOUNT_COMMANDS)),
        "        if test $npos -eq 0; or test \"$cmd\" = usage",
        "            set kind account",
        "        end",
        "    end",
        "    if test \"$cmd\" = proxy; and test $npos -eq 1",
        "        set kind proxy-value",
        "    end",
        "    if test \"$cmd\" = completion; and test $npos -eq 0",
        "        set kind shell",
        "    end",
        "    test \"$kind\" = \"$argv[1]\"",
        "end",
        "complete -c multi-codex -f -n __fish_use_subcommand -a '{}'".format(" ".join(sorted(spec))),
    ]
    for name in sorted(spec):
        valued = set(spec[name][1])
        for option in spec[name][0]:
            flag = "-l {}".format(option[2:]) if option.startswith("--") else "-s {}".format(option[1:])
            lines.append("complete -c multi-codex -n '__fish_seen_subcommand_from {}' {}{}".format(
                name, flag, " -r" if option in valued else ""))
    lines += [
        "complete -c multi-codex -f -n '__multi_codex_is account' -a '(multi-codex completion --list-accounts)'",
        "complete -c multi-codex -f -n '__multi_codex_is proxy-value' -a 'off inherit'",
        "complete -c multi-codex -f -n '__multi_codex_is shell' -a '{}'".format(" ".join(SHELLS)),
        "",
    ]
    return "\n".join(lines)


def script(shell: str, parser: argparse.ArgumentParser) -> str:
    spec = command_spec(parser)
    return {"bash": bash_script, "zsh": zsh_script, "fish": fish_script}[shell](spec)
