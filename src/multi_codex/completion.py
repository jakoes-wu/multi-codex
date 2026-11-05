"""生成 bash / zsh / fish / PowerShell 的补全脚本（feature-cli-ergonomics §5.1.1）。

子命令、选项、哪些选项带值，都在生成时从 argparse 解析器读出，新增选项不必另外维护列表。
账号名在补全时调用 `multi-codex completion --list-accounts` 动态读取。

三种 shell 判断“当前是第几个参数”的规则相同：按空白切分已输入的部分，以 `-` 开头的词不算，
带值选项后面紧跟的那个词也不算；`run` 的 `--` 之后不再补全。
bash 不能用 COMP_WORDS / COMP_CWORD：它们按 COMP_WORDBREAKS 切分，默认含 `@` 和 `=`，
会把邮箱形式的账号名拆成几段，参数位置就数错了。
"""

import argparse
import re
from typing import Dict, List, Tuple

# 第一个位置参数是账号名的子命令；usage 的每个位置参数都是账号名。
ACCOUNT_COMMANDS = ("add", "set", "rename", "login", "proxy", "remove", "migrate-default", "run", "path", "env",
                    "usage", "use", "restore", "bind", "code", "app", "sessions", "netcheck")
MULTI_ACCOUNT_COMMANDS = ("usage",)
SHELLS = ("bash", "zsh", "fish", "powershell")

# 取值来源标记（feature-completion-values §5.1）：不是字面取值，补全脚本见到它就在按 Tab 时调用对应命令现查。
# 字面取值都不以 @ 开头（value_spec 里有断言），所以标记不会和真实取值混淆。
ACCOUNTS = "@accounts"
SHARED_ITEMS = "@shared-items"
SOURCE_COMMANDS = {ACCOUNTS: "multi-codex completion --list-accounts",
                   SHARED_ITEMS: "multi-codex completion --list-shared-items"}

# argparse 里没有 choices、但只接受这几个关键字的位置参数：{(子命令, 第几个位置参数): 取值}。
# proxy 的第 2 个参数也可以是端口或地址，这里只补关键字；clash-api 的 auto / off 由 cmd_clash_api 按字符串判断。
EXTRA_POSITIONAL = {("proxy", 1): ["off", "inherit"], ("clash-api", 0): ["auto", "off"]}
# 值是账号名或共享项名的选项；凡是带这个选项的子命令都适用（现为 add、set）。
OPTION_SOURCES = {"--config-from": ACCOUNTS, "--shared-exclude": SHARED_ITEMS, "--shared-include": SHARED_ITEMS}

_SAFE_WORD = re.compile(r"^[A-Za-z0-9._-]+$")

PositionalValues = Dict[Tuple[str, int], List[str]]
OptionValues = Dict[Tuple[str, str], List[str]]


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


def value_spec(parser: argparse.ArgumentParser) -> Tuple[PositionalValues, OptionValues]:
    """返回 (位置参数取值表, 选项取值表)：哪些位置、哪些选项后面该补出什么词。

    argparse 里写了 choices 的参数自动读出，新增这类参数不用改本文件；再合入 EXTRA_POSITIONAL 与 OPTION_SOURCES
    两张手写表，两者冲突时以 argparse 为准。选项取值表的值可以是来源标记（ACCOUNTS / SHARED_ITEMS）。
    位置参数按出现顺序从 0 编号，与补全脚本里的 npos 一致（脚本数 npos 时不算选项和选项的值）。
    """
    positional: PositionalValues = {}
    option_values: OptionValues = {}
    for action in parser._actions:  # noqa: SLF001 —— 与 command_spec 相同的遍历方式
        if not isinstance(action, argparse._SubParsersAction):  # noqa: SLF001
            continue
        for name, sub in action.choices.items():
            index = 0
            for sub_action in sub._actions:  # noqa: SLF001
                if sub_action.help == argparse.SUPPRESS or isinstance(sub_action, argparse._HelpAction):  # noqa: SLF001
                    continue
                if not sub_action.option_strings:
                    if sub_action.choices:
                        positional[(name, index)] = _literal_words(sub_action.choices)
                    # nargs='*' 吃掉其后所有位置参数，不再给后面编号（现只有 usage 的账号列表，且排在最后）。
                    if sub_action.nargs != "*":
                        index += 1
                    continue
                if sub_action.nargs == 0:
                    continue
                words = _literal_words(sub_action.choices) if sub_action.choices else None
                if words is None:
                    sources = [OPTION_SOURCES[option] for option in sub_action.option_strings if option in OPTION_SOURCES]
                    words = sources[:1] or None
                if words:
                    for option in sub_action.option_strings:
                        option_values[(name, option)] = words
    for key, words in EXTRA_POSITIONAL.items():
        positional.setdefault(key, _literal_words(words))
    # 账号位置与固定取值表不能重合：bash/zsh/PowerShell 是取值表覆盖账号名，fish 两条规则是叠加，重合时四种 shell 结果不同。
    for name, index in positional:
        if (name in ACCOUNT_COMMANDS and index == 0) or name in MULTI_ACCOUNT_COMMANDS:
            raise ValueError("completion values overlap account position: {}:{}".format(name, index))
    return positional, option_values


def _literal_words(choices) -> List[str]:
    words = [str(choice) for choice in choices]
    # 取值会原样写进四种 shell 的脚本（case 分支、单引号串），只允许不需要转义的字符；也不能以 @ 开头冒充来源标记。
    for word in words:
        # 用 raise 而不是 assert：python -O 会去掉 assert，不安全的词就会原样写进脚本。
        if not _SAFE_WORD.match(word):
            raise ValueError("completion value {!r} needs quoting".format(word))
    return words


def _case_values(table: Dict, indent: str) -> List[str]:
    """把取值表写成 bash / zsh 的 case 分支，键为 `子命令:序号` 或 `子命令:选项`，值为空格分隔的词或来源标记。"""
    return ['{}{}:{}) echo "{}" ;;'.format(indent, key[0], key[1], " ".join(words))
            for key, words in sorted(table.items(), key=lambda item: (item[0][0], str(item[0][1])))]


def _value_functions(positional: PositionalValues, option_values: OptionValues) -> List[str]:
    """bash 与 zsh 共用的三个函数：按位置查取值、按选项查取值、把来源标记换成现查结果。"""
    lines = ["_multi_codex_positional() {", '    case "$1:$2" in']
    lines += _case_values(positional, "        ")
    lines += ["    esac", "}", "_multi_codex_option_value() {", '    case "$1:$2" in']
    lines += _case_values(option_values, "        ")
    lines += ["    esac", "}", "# 参数是取值表里的一项：来源标记就现查（失败时静默为空），否则原样输出。",
              "_multi_codex_expand() {", '    case "$1" in']
    for marker, command in sorted(SOURCE_COMMANDS.items()):
        lines.append("        {}) {} 2>/dev/null ;;".format(marker, command))
    lines += ['        *) echo "$1" ;;', "    esac", "}"]
    return lines


def _case_lines(spec: Dict[str, Tuple[List[str], List[str]]], index: int, indent: str) -> List[str]:
    lines = []
    for name in sorted(spec):
        words = spec[name][index]
        if words:
            lines.append('{}{}) echo "{}" ;;'.format(indent, name, " ".join(words)))
    return lines


def bash_script(spec: Dict[str, Tuple[List[str], List[str]]], values: Tuple[PositionalValues, OptionValues]) -> str:
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
    ] + _value_functions(*values) + [
        "_multi_codex() {",
        '    local line="${COMP_LINE:0:COMP_POINT}"',
        "    local -a words",
        '    read -r -a words <<< "$line"',
        '    local count=${#words[@]} cur=""',
        '    if [[ ! "$line" =~ [[:space:]]$ ]] && (( count > 1 )); then',
        '        cur="${words[count-1]}"',
        "        count=$((count - 1))",
        "    fi",
        '    local cmd="" npos=0 skip=0 after_dd=0 last_opt="" i w',
        "    for ((i = 1; i < count; i++)); do",
        '        w="${words[i]}"',
        '        if [[ -z "$cmd" ]]; then',
        '            [[ "$w" == -* ]] || cmd="$w"',
        "            continue",
        "        fi",
        '        if [[ ( "$cmd" == run || "$cmd" == login ) && "$w" == "--" ]]; then after_dd=1; break; fi',
        "        if (( skip )); then skip=0; continue; fi",
        '        if [[ "$w" == -* ]]; then',
        '            case " $(_multi_codex_value_options "$cmd") " in *" $w "*) skip=1; last_opt="$w" ;; esac',
        "        else",
        "            npos=$((npos + 1))",
        "        fi",
        "    done",
        '    local candidates=""',
        '    if [[ -z "$cmd" ]]; then',
        '        candidates="{}"'.format(commands),
        "    elif (( after_dd )); then",
        "        COMPREPLY=()",
        "        return 0",
        "    elif (( skip )); then",
        "        # 光标在带值选项之后：补这个选项的取值；没有取值表的选项（--proxy、--country 等）候选为空，",
        "        # 由 -o default 补文件名，与原来一样。",
        "        local value",
        '        for value in $(_multi_codex_option_value "$cmd" "$last_opt"); do',
        '            candidates="$candidates $(_multi_codex_expand "$value")"',
        "        done",
        '    elif [[ "$cur" == -* ]]; then',
        '        candidates="$(_multi_codex_options "$cmd")"',
        "    else",
        '        case " {} " in'.format(" ".join(ACCOUNT_COMMANDS)),
        '            *" $cmd "*)',
        '                if (( npos == 0 )) || [[ "$cmd" == usage ]]; then',
        '                    candidates="$(multi-codex completion --list-accounts 2>/dev/null)"',
        "                fi ;;",
        "        esac",
        "        # 只接受固定几个词的位置参数（netcheck 的档位、proxy 的 off/inherit 等），见 value_spec。",
        "        local fixed",
        '        fixed="$(_multi_codex_positional "$cmd" "$npos")"',
        '        [[ -n "$fixed" ]] && candidates="$fixed"',
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


def zsh_script(spec: Dict[str, Tuple[List[str], List[str]]], values: Tuple[PositionalValues, OptionValues]) -> str:
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
    ] + _value_functions(*values) + [
        "_multi_codex() {",
        '    local cur="${words[CURRENT]}" cmd="" last_opt="" w',
        "    local -i npos=0 skip=0 after_dd=0",
        "    for w in \"${(@)words[2,CURRENT-1]}\"; do",
        '        if [[ -z "$cmd" ]]; then',
        '            [[ "$w" == -* ]] || cmd="$w"',
        "            continue",
        "        fi",
        '        if [[ ( "$cmd" == run || "$cmd" == login ) && "$w" == "--" ]]; then after_dd=1; break; fi',
        "        if (( skip )); then skip=0; continue; fi",
        '        if [[ "$w" == -* ]]; then',
        '            [[ " $(_multi_codex_value_options "$cmd") " == *" $w "* ]] && { skip=1; last_opt="$w"; }',
        "        else",
        "            npos=$((npos + 1))",
        "        fi",
        "    done",
        "    local -a candidates",
        '    if [[ -z "$cmd" ]]; then',
        "        candidates=({})".format(commands),
        "    elif (( after_dd )); then",
        "        _files",
        "        return",
        "    elif (( skip )); then",
        "        # 与 bash 版相同：补带值选项的取值，没有取值表的选项落到下面的 _files。",
        "        local value",
        '        for value in ${=$(_multi_codex_option_value "$cmd" "$last_opt")}; do',
        '            candidates+=(${(f)"$(_multi_codex_expand "$value")"})',
        "        done",
        '    elif [[ "$cur" == -* ]]; then',
        '        candidates=(${=$(_multi_codex_options "$cmd")})',
        "    else",
        # 与 bash 版相同：先确认是收账号名的子命令，再看是第一个参数，或者是 usage（每个参数都是账号名）。
        '        if [[ " {} " == *" $cmd "* ]] && {{ (( npos == 0 )) || [[ "$cmd" == usage ]]; }}; then'.format(
            " ".join(ACCOUNT_COMMANDS)),
        '            candidates=(${(f)"$(multi-codex completion --list-accounts 2>/dev/null)"})',
        "        fi",
        "        local fixed",
        '        fixed="$(_multi_codex_positional "$cmd" "$npos")"',
        '        [[ -n "$fixed" ]] && candidates=(${=fixed})',
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


def _fish_values(words: List[str]) -> str:
    """fish 的 -a 参数：来源标记换成现查命令，词表原样列出。"""
    if len(words) == 1 and words[0] in SOURCE_COMMANDS:
        return "'({})'".format(SOURCE_COMMANDS[words[0]])
    return "'{}'".format(" ".join(words))


def fish_script(spec: Dict[str, Tuple[List[str], List[str]]], values: Tuple[PositionalValues, OptionValues]) -> str:
    positional, option_values = values
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
        "# 扫描已输入的词，打印一行“子命令 位置参数个数 光标是否在带值选项之后(0/1)”；",
        "# 还没有子命令、或在 run/login 的 -- 之后时不打印、返回 1（这些位置不补参数取值）。",
        "function __multi_codex_scan",
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
        "    echo $cmd $npos $skip",
        "end",
        "# 当前位置是否该补账号名（收账号名子命令的第一个参数，usage 的每个参数）。",
        "function __multi_codex_is",
        "    set -l state (string split ' ' -- (__multi_codex_scan)); or return 1",
        "    test \"$state[3]\" = 0; or return 1",
        "    test \"$argv[1]\" = account; or return 1",
        "    contains -- $state[1] {}; or return 1".format(" ".join(ACCOUNT_COMMANDS)),
        "    test $state[2] -eq 0; or test \"$state[1]\" = usage",
        "end",
        "# 只接受固定几个词的位置参数（netcheck 档位、proxy 的 off/inherit 等），每行打印一个候选。",
        "function __multi_codex_positional",
        "    set -l state (string split ' ' -- (__multi_codex_scan)); or return 1",
        "    test \"$state[3]\" = 0; or return 1",
        "    switch \"$state[1]:$state[2]\"",
    ]
    for (name, index), words in sorted(positional.items(), key=lambda item: (item[0][0], item[0][1])):
        lines.append("        case '{}:{}'".format(name, index))
        lines.append("            printf '%s\\n' {}".format(" ".join(words)))
    lines += [
        "    end",
        "end",
        "complete -c multi-codex -f -n __fish_use_subcommand -a '{}'".format(" ".join(sorted(spec))),
    ]
    for name in sorted(spec):
        valued = set(spec[name][1])
        for option in spec[name][0]:
            flag = "-l {}".format(option[2:]) if option.startswith("--") else "-s {}".format(option[1:])
            words = option_values.get((name, option))
            if words:
                # 有取值表的选项只登记这一行（-x：要参数且不补文件名），避免与 -r 那行候选叠加。
                extra = " -x -a {}".format(_fish_values(words))
            else:
                extra = " -r" if option in valued else ""
            lines.append("complete -c multi-codex -n '__fish_seen_subcommand_from {}' {}{}".format(name, flag, extra))
    lines += [
        "complete -c multi-codex -f -n '__multi_codex_is account' -a '({})'".format(SOURCE_COMMANDS[ACCOUNTS]),
        # 不用 $(...)：那是 fish 3.4 才有的写法，CI 的 ubuntu-22.04 装的 fish 更老。
        "complete -c multi-codex -f -n 'count (__multi_codex_positional) >/dev/null' -a '(__multi_codex_positional)'",
        "",
    ]
    return "\n".join(lines)


def _ps_quote(text: str) -> str:
    # PowerShell 单引号串里只有 ' 需要写成 ''
    return "'" + text.replace("'", "''") + "'"


def _ps_array(items: List[str]) -> str:
    return "@(" + ", ".join(_ps_quote(item) for item in items) + ")"


def _ps_table(spec: Dict[str, Tuple[List[str], List[str]]], index: int) -> List[str]:
    return ["        {} = {}".format(_ps_quote(name), _ps_array(spec[name][index]))
            for name in sorted(spec) if spec[name][index]]


def _ps_value_table(table: Dict) -> List[str]:
    """取值表写成 PowerShell 哈希表的行，键为 `子命令:序号` 或 `子命令:选项`。"""
    return ["        {} = {}".format(_ps_quote("{}:{}".format(key[0], key[1])), _ps_array(words))
            for key, words in sorted(table.items(), key=lambda item: (item[0][0], str(item[0][1])))]


def powershell_script(spec: Dict[str, Tuple[List[str], List[str]]], values: Tuple[PositionalValues, OptionValues]) -> str:
    """PowerShell 补全（feature-windows-support §5.1.9），数参数位置的规则与 bash 版相同。
    参数取值（feature-completion-values）：拼键用 ($cmd + ':' + $npos)，写成 "$cmd:$npos" 会被当成带作用域的变量名。

    只含 ASCII：Windows PowerShell 5.1 按系统代码页读不带 BOM 的脚本，非 ASCII 字符会乱码。
    补全器什么都不返回时 PowerShell 退回文件名补全，与 bash 的 -o default 一致。
    """
    positional, option_values = values
    sources = ["        {} = {}".format(_ps_quote(marker), _ps_quote(command))
               for marker, command in sorted(SOURCE_COMMANDS.items())]
    return "\n".join([
        "# multi-codex PowerShell completion. Install with:",
        "#   multi-codex completion powershell | Out-String | Add-Content $PROFILE",
        "Register-ArgumentCompleter -Native -CommandName multi-codex, multi-codex.exe -ScriptBlock {",
        "    param($wordToComplete, $commandAst, $cursorPosition)",
        "    $commands = {}".format(_ps_array(sorted(spec))),
        "    $options = @{",
    ] + _ps_table(spec, 0) + [
        "    }",
        "    $valueOptions = @{",
    ] + _ps_table(spec, 1) + [
        "    }",
        "    $positional = @{",
    ] + _ps_value_table(positional) + [
        "    }",
        "    $optionValues = @{",
    ] + _ps_value_table(option_values) + [
        "    }",
        "    # Value lists may hold a source marker instead of words; it is replaced by the command output",
        "    $sources = @{",
    ] + sources + [
        "    }",
        "    $accountCommands = {}".format(_ps_array(list(ACCOUNT_COMMANDS))),
        "    # Words fully typed before the cursor; the first one is the command name itself",
        "    $before = @($commandAst.CommandElements | Where-Object { $_.Extent.EndOffset -lt $cursorPosition } |",
        "        ForEach-Object { $_.Extent.Text })",
        "    $cmd = ''",
        "    $npos = 0",
        "    $skip = $false",
        "    $lastOpt = ''",
        "    foreach ($w in ($before | Select-Object -Skip 1)) {",
        "        if ($cmd -eq '') {",
        "            if (-not $w.StartsWith('-')) { $cmd = $w }",
        "            continue",
        "        }",
        "        if (($cmd -eq 'run' -or $cmd -eq 'login') -and $w -eq '--') { return }",
        "        if ($skip) { $skip = $false; continue }",
        "        if ($w.StartsWith('-')) {",
        "            if ($valueOptions[$cmd] -contains $w) { $skip = $true; $lastOpt = $w }",
        "        } else {",
        "            $npos++",
        "        }",
        "    }",
        "    $candidates = @()",
        "    if ($cmd -eq '') {",
        "        $candidates = $commands",
        "    } elseif ($skip) {",
        "        # No value list for this option (--proxy, --country ...): return nothing, PowerShell completes file names",
        "        $values = $optionValues[($cmd + ':' + $lastOpt)]",
        "        if (-not $values) { return }",
        "        foreach ($v in $values) {",
        "            if ($sources.ContainsKey($v)) {",
        "                $candidates += @(Invoke-Expression ($sources[$v] + ' 2>$null'))",
        "            } else {",
        "                $candidates += $v",
        "            }",
        "        }",
        "    } elseif ($wordToComplete.StartsWith('-')) {",
        "        $candidates = $options[$cmd]",
        "    } else {",
        "        if (($accountCommands -contains $cmd) -and ($npos -eq 0 -or $cmd -eq 'usage')) {",
        "            $candidates = @(multi-codex completion --list-accounts 2>$null)",
        "        }",
        "        $fixed = $positional[($cmd + ':' + $npos)]",
        "        if ($fixed) { $candidates = $fixed }",
        "    }",
        "    $candidates | Where-Object { $_ -and $_.StartsWith($wordToComplete, "
        "[System.StringComparison]::OrdinalIgnoreCase) } |",
        "        ForEach-Object { [System.Management.Automation.CompletionResult]::new($_, $_, 'ParameterValue', $_) }",
        "}",
        "",
    ])


def script(shell: str, parser: argparse.ArgumentParser) -> str:
    spec = command_spec(parser)
    return {"bash": bash_script, "zsh": zsh_script, "fish": fish_script,
            "powershell": powershell_script}[shell](spec, value_spec(parser))
