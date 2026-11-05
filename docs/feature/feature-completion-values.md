# 补全参数取值（选项值、档位、关键字、共享项名）

> 2026-11-05 注记：方案已定、代码待落地。基线：main `ebef759`（0.14.0 发布后），以下 `file:line` 均指该基线。

## 1. 背景

`multi-codex completion bash|zsh|fish|powershell` 输出补全脚本（`src/multi_codex/completion.py`，方案见 `docs/feature/feature-cli-ergonomics.md` §5.1.1）。现状能补：

- 子命令名、各子命令的选项名：生成时从 argparse 读出（`completion.py:22-40` `command_spec`）；
- 账号名：`ACCOUNT_COMMANDS` 里的子命令第一个位置参数、`usage` 的每个位置参数（`completion.py:16-18`），按 Tab 时调用 `multi-codex completion --list-accounts`（`cli.py:1502-1510`）；
- 两处手写的固定取值：`proxy NAME` 之后的 `off inherit`（`completion.py:105`、`166`、`228`、`245`、`316`），`completion` 之后的 shell 名（`completion.py:106`、`167`、`231`、`317`）。

补不到的位置（用户 2026-11-05 选“补齐全部取值”）：

| 位置 | 应补出 | 取值来源 |
| ---- | ---- | ---- |
| `netcheck NAME` 之后的档位 | `off basic strict` | argparse `choices`（`cli.py` `p_netcheck` 的 `level`） |
| `netcheck` 的 `--clash` / `--clash-tun` / `--clash-global` 之后 | `on off` | argparse `choices` |
| `clash-api` 之后 | `auto off` | 关键字，不在 argparse 里（`cli.py:1815-1817` 按字符串判断） |
| `add` / `set` 的 `--config-from` 之后 | 账号名 | `cli.py:459` 的值是另一个账号 |
| `add` / `set` 的 `--shared-exclude` / `--shared-include` 之后 | 已配置的共享项名 | `config.shared_items`（`config.py:115`，默认 `config.py:18`） |

现状里带值选项之后：bash / zsh / PowerShell 都退回文件名补全（bash `-o default`、zsh `_files`、PowerShell 补全器不返回时由 PowerShell 自己补文件名，`completion.py:270`），fish 按 `-r` 补文件名；上表各位置都按这一规则处理，补不出取值。

## 2. 目标 / 非目标

目标：

1. 上表 5 处在 bash / zsh / fish / PowerShell 四种补全脚本里都能补出候选。
2. 凡 argparse 里写了 `choices` 的位置参数与选项，取值在生成脚本时自动读出；以后新增带 `choices` 的参数不用再改 `completion.py`。
3. 已有补全行为不变（子命令、选项名、账号名、`proxy` 的 `off inherit`、`completion` 的 shell 名、`run` / `login` 的 `--` 之后不补、无候选时退回文件名）。

非目标：

- `--opt=value` 写法的值补全（现状也不支持，按空白切分）。
- `--country`、`--proxy`、`--timeout`、`--grep`、`env` 的 `KEY=VALUE` 等自由取值。
- `codex-<名>` 启动命令的补全。
- 改 README 里 PowerShell 的安装写法（见 §10）。

## 3. 假设与约束

- 只依赖 Python 3.8 标准库；bash 脚本要能在 macOS 自带 bash 3.2 上运行（不用关联数组、不依赖 bash-completion 包），与 `feature-cli-ergonomics.md` §3 一致。
- PowerShell 脚本只含 ASCII（`completion.py:266-270` 的约束）。
- argparse 子命令的 `choices` 在新函数 `value_spec` 里读取，沿用 `command_spec` 对 `parser._actions` 的遍历方式（`completion.py:25-31`）。`command_spec` 的签名与返回值不变：它有 6 个调用点（源码 `completion.py:328`；测试 `test_onboarding.py:107`、`:176`，`test_everyday.py:389`，`test_isolation.py:417`，`test_router.py:366`，测试按二元组解包），`grep -rn command_spec src tests` 无截断枚举得出。

## 4. 涉及模块

| 区域 | 行号锚点（基线 `ebef759`） | 改动类型 | 改动点 |
| ---- | ---- | ---- | ---- |
| 补全表 | `src/multi_codex/completion.py:16-19` 常量之后 | 新增 | `EXTRA_POSITIONAL`（`proxy` 第 2 个、`clash-api` 第 1 个参数的关键字）、`OPTION_SOURCES`（`--config-from` → 账号名，`--shared-exclude` / `--shared-include` → 共享项名）、标记常量 `ACCOUNTS` / `SHARED_ITEMS` |
| 读取规格 | `completion.py:40` `command_spec` 之后 | 新增 | `value_spec(parser)` 返回 `(positional, option_values)`：`{(子命令, 序号): [词]}` 与 `{(子命令, 选项): [词] 或标记}`；`command_spec` 不改 |
| bash | `completion.py:52-119` `bash_script` | 修改 | 新增 `_multi_codex_positional CMD NPOS`、`_multi_codex_option_value CMD OPT` 两个 case 函数；扫描时记下最后一个带值选项；`skip` 时按该选项查取值；位置参数改为先查表；删除 `:105-106` 两行手写判断 |
| zsh | `completion.py:122-178` `zsh_script` | 修改 | 同 bash；删除 `:166-167` |
| fish | `completion.py:181-249` `fish_script` | 修改 | 扫描抽成 `__multi_codex_scan`；`__multi_codex_is` 改用它（只留 `account` 一种）；新增 `__multi_codex_positional`；`:241-242` 对有取值的选项只生成一行 `-x -a`；`:245-246` 两行换成一行位置参数补全。写法见 §5.1 |
| PowerShell | `completion.py:266-324` `powershell_script` | 修改 | 新增两张哈希表 `$positional`（键 `cmd:npos`）、`$optionValues`（键 `cmd:option`）；扫描时记下最后一个带值选项；删除 `:316-317` |
| 规格入口 | `completion.py:327-330` `script` | 修改 | 另调用 `value_spec`，把两张表传给四个生成函数（生成函数多一个参数） |
| 命令行 | `src/multi_codex/cli.py:365` `--list-accounts` 之后 | 新增 | 隐藏选项 `--list-shared-items` |
| 命令行 | `cli.py:1501-1514` `cmd_completion` | 修改 | 处理 `--list-shared-items`：输出 `config.shared_items`，配置读不了时静默输出空 |
| 测试 | `tests/test_ergonomics.py:56-111` `CompletionTest` | 新增 | bash 与 fish 行为用例、zsh 函数用例，见 §8 |
| 测试 | `tests/test_windows.py:629` `test_24_completion` 之后 | 新增 | PowerShell 用 `TabExpansion2` 的行为用例，见 §8 |
| 测试 | 现有 `command_spec` 调用点（见 §3） | 不改 | 签名不变 |
| 文档 | `README.md:252-262`、`README.zh-CN.md:268-280` “Shell completion”一节 | 修改 | 末句补“以及选项与参数的取值（如 netcheck 档位、on/off、共享项名）”；PowerShell 用户升级后需重新生成一次 |
| 文档 | `CHANGELOG.md:7` Unreleased | 修改 | 一条 Added |
| 文档 | `docs/feature/feature-cli-ergonomics.md` 文件头 | 修改 | 加一行注记指向本方案 |

## 5. 方案

### 5.1 实现要点

**规格（新函数 `value_spec`）**：遍历每个子命令的 `_actions`（`command_spec` 在 `completion.py:32` 跳过位置参数，`value_spec` 单独处理它们）：

- 位置参数：按出现顺序编号 0、1、…；`choices` 非空时记入 `positional[(cmd, i)] = list(choices)`。`nargs` 为 `*` 的不编号到后续（`usage` 只有一个）。
- 带值选项：`choices` 非空时，对它的每个选项写法记入 `option_values[(cmd, opt)] = list(choices)`。
- 再合入两张手写表：
  - `EXTRA_POSITIONAL = {("proxy", 1): ["off", "inherit"], ("clash-api", 0): ["auto", "off"]}`；
  - `OPTION_SOURCES = {"--config-from": ACCOUNTS, "--shared-exclude": SHARED_ITEMS, "--shared-include": SHARED_ITEMS}`，对凡是含该选项的子命令生效（现为 `add`、`set`）。
- `completion` 的 shell 名来自 argparse `choices`（`cli.py:363`），不再手写。

取值顺序保持 `choices` 的声明顺序。手写表与 argparse 表冲突时以 argparse 为准（现无冲突）。取值里有需要转义的字符或以 `-` 开头、一个位置参数吃多个值又带 `choices`、或固定取值表与账号位置重合（四种 shell 合并方式不同）时，生成脚本直接报错（`ValueError`，不用 `assert`）。`--list-shared-items` 不输出含空白、`=`、`:`、引号的共享项名（bash 会拆开或重复已输入的前缀），这类名字退回文件名补全。

**候选决定顺序**（四种 shell 一致，扫描规则不变，只多记一个 `last_opt`）：

1. 还没有子命令 → 子命令名（不变）；
2. `run` / `login` 的 `--` 之后 → 不补（不变）；
3. 光标前一个词是带值选项（`skip`）→ 查 `option_values[(cmd, last_opt)]`：词表直接用；`ACCOUNTS` 调 `multi-codex completion --list-accounts`；`SHARED_ITEMS` 调 `multi-codex completion --list-shared-items`；查不到 → 退回文件名补全（不变）；
4. 当前词以 `-` 开头 → 选项名（不变）；
5. 否则：账号位置按现有规则（`ACCOUNT_COMMANDS` 第 0 个、`usage` 每个）；再查 `positional[(cmd, npos)]`，有则用它。

bash 示例（生成结果，插入位置：替换 `completion.py:93-106` 一段）：

```bash
_multi_codex_positional() {
    case "$1:$2" in
        clash-api:0) echo "auto off" ;;
        completion:0) echo "bash zsh fish powershell" ;;
        netcheck:1) echo "off basic strict" ;;
        proxy:1) echo "off inherit" ;;
    esac
}
_multi_codex_option_value() {
    case "$1:$2" in
        add:--config-from|set:--config-from) echo "@accounts" ;;
        netcheck:--clash|netcheck:--clash-tun|netcheck:--clash-global) echo "on off" ;;
        ...
    esac
}
```

`@accounts` / `@shared-items` 是标记，不会和真实取值冲突（argparse 取值与关键字都不以 `@` 开头；生成时断言这一点）。

fish 写法：

- 扫描：现有 `__multi_codex_is` 的循环（`completion.py:195-226`）原样抽成 `__multi_codex_scan`，结束时打印一行 `<cmd> <npos> <skip>`（`run`/`login` 的 `--` 之后或无子命令时不打印、返回 1）。
- `__multi_codex_is account`：读 `__multi_codex_scan` 的结果，按现有规则判断账号位置（`skip` 为 1 时为假）。
- `__multi_codex_positional`：读 `__multi_codex_scan` 结果，`skip` 为 0 时 `switch "$cmd:$npos"` 打印取值（每行一个），否则不打印。
- 末尾 `:245-246` 两行换成一行：`complete -c multi-codex -f -n 'count (__multi_codex_positional) >/dev/null' -a '(__multi_codex_positional)'`。不用 `$(...)`：它是 fish 3.4 才有的写法，CI 的 ubuntu-22.04（`ci.yml:14`）装的 fish 更老，且写在 `-n` 引号里语法检查查不出。
- 选项（`:241-242`）：有取值的选项**只生成一行**，`-x -a 'on off'` 或 `-x -a '(multi-codex completion --list-accounts)'` / `--list-shared-items`；没有取值的带值选项仍为 `-r`，不带值的不变。同一选项不会登记两次。

PowerShell 拼键用 `($cmd + ':' + $npos)` 与 `($cmd + ':' + $lastOpt)`，不用 `"$cmd:$npos"`（`$cmd:` 会被当成带作用域的变量名）。

**`--list-shared-items`**：与 `--list-accounts` 相同的静默规则——配置读不了（`ConfigError`、`OSError`）时输出空、退出 0；每行一个 `config.shared_items` 的条目。

### 5.2 接口变更

- 新增隐藏选项 `multi-codex completion --list-shared-items`（不出现在帮助里，供补全脚本调用）。与 `--list-accounts` 同时给出时两者都输出，账号在前。
- 补全脚本内容变化：新增函数 / 表，旧脚本仍可用；用户需重新加载（bash / zsh / fish 的 `eval` / `source` 写法每次开 shell 自动重新生成；按 README 把 PowerShell 脚本快照写进 `$PROFILE` 的用户要重新生成）。
- 不涉及配置文件、退出码、其它命令输出。

**reference sibling 回补检查**：N/A——本方案不涉及 `docs/reference/*`。

## 6. 备选方案与决策

- 全部手写取值表：新增参数要记得同步，曾因此只补了 `proxy`、`completion` 两处。否决。
- 补全时调用 `multi-codex` 现算候选（`multi-codex completion --complete "<行>"`）：四种 shell 只剩一个薄壳，但每按一次 Tab 都起一次 Python（现在只有账号名位置才起），Windows 上明显变慢。否决。

## 7. 影响分析

1. **补全脚本整体**（正向）：四个生成函数都被改动，补全行为是唯一消费方。现有用例 `tests/test_ergonomics.py:82-90` 覆盖的行为必须保持；位置参数改为查表后，`proxy` 与 `completion` 的取值来源变了，输出词必须不变。
2. **`command_spec` 不变**（反向）：取值表放进新函数 `value_spec`，`command_spec` 的 6 个调用点（§3）不受影响；四个生成函数只由 `completion.script` 调用（`completion.py:329-330`），多一个参数不影响别处。
3. **按 Tab 的耗时**：新增两处会起 Python 的位置（`--config-from`、`--shared-*` 之后），与现有账号名位置同一量级（一次 `load_config`）；其它新位置是脚本内常量，不起进程。
4. **生成脚本的大小**：每种 shell 增加十余行；`eval "$(multi-codex completion zsh)"` 开 shell 的耗时由 Python 启动主导，不变。
5. **bash 3.2 兼容**：只用 `case` 与普通变量，不用关联数组。
6. **PowerShell ASCII 约束**：新增内容只含 ASCII 字符；已有测试检查。
7. **帮助输出**：`--list-shared-items` 用 `argparse.SUPPRESS`，不进入帮助与补全的选项列表（`command_spec` 跳过 SUPPRESS，`completion.py:32`），`HelpTest` 不受影响。

## 8. 回归测试

bash（`CompletionTest`，用现有 `bash_complete` 驱动）：

| 编号 | 输入 | 期望 |
| ---- | ---- | ---- |
| C1 | `multi-codex netcheck work ` | `off basic strict` |
| C2 | `multi-codex netcheck work strict --clash ` | `on off`；`--clash-tun `、`--clash-global ` 同 |
| C3 | `multi-codex clash-api ` | `auto off` |
| C4 | `multi-codex add new --config-from jak` | 两个 `jakoes.wu@…` 账号 |
| C4b | `multi-codex add new --config-from jakoes.wu@ic` | `icloud.com`（经过 `@` 前缀处理，对应 `test_ergonomics.py:86`） |
| C5 | `multi-codex set work --shared-exclude ` | 默认共享项 `AGENTS.md skills rules agents` |
| C6 | `multi-codex netcheck work --country ` | 空（退回文件名） |
| C7 | 现有用例 `test_ergonomics.py:83-90` | 全部不变 |
| C8 | `multi-codex completion --list-shared-items` | 每行一项；配置损坏时输出空、退出 0 |
| C9 | `value_spec` 读取 argparse | 给一个假解析器加带 `choices` 的位置参数与选项，生成的 bash 脚本含其取值（证明自动读取） |

fish（CI Linux 已装 fish，`.github/workflows/ci.yml:47-50`；本机没装时跳过）：用 `fish --no-config -c 'source <脚本>; complete -C "<行>"'` 取候选，核 C1、C2、C4、C5，以及原有的 `proxy work ` → `off inherit`、`completion ` → 四个 shell 名、`run jak` → 账号名。另保留语法检查。

PowerShell（`tests/test_windows.py`，Windows CI 与 Windows 测试机）：前提——在 fakebin 写 `multi-codex.cmd`，内容 `@"%FAKE_PYTHON%" -m multi_codex %*`（与 bash 用例 `test_ergonomics.py:59-61` 的转发脚本对应），用例开头先 `add` 测试账号；先核 `proxy work ` 能出候选，证明补全器被调到（补全器按 `multi-codex, multi-codex.exe` 注册，`completion.py:275`，`TabExpansion2` 按命令名字面匹配），再核 C4。`powershell.exe -NoProfile -Command` 里加载脚本后，用 `TabExpansion2 -inputScript '<行>' -cursorColumn <长度>` 取 `CompletionMatches`，核 C1、C2、C4、`proxy work `、`completion `。另保留只含 ASCII 的检查。

zsh：`zsh -n` 语法检查；`zsh -f -c 'autoload -U compinit; compinit -u; source <脚本>; _multi_codex_positional netcheck 1; _multi_codex_option_value netcheck --clash'` 核两个查表函数输出与 bash 相同。按 Tab 的完整行为在 Mac 上手工核 C1、C2、C4。

另需：本机全量测试、CI 三平台全过。

## 9. 日志 / 观测点

补全脚本不打日志（按 Tab 时任何输出都会弄乱命令行）。观测方式：

- `multi-codex completion bash | grep -A8 _multi_codex_positional` 查看生成的取值表；
- `multi-codex completion --list-shared-items` 直接看共享项来源；
- 测试 C1–C9 与 §8 的 fish、PowerShell、zsh 用例。

## 10. 开放问题

- README 建议 PowerShell 用户把脚本快照写进 `$PROFILE`（`Add-Content`），升级后不会自动更新。改成在 `$PROFILE` 里写 `multi-codex completion powershell | Out-String | Invoke-Expression`（与 bash / zsh 一样每次动态生成）可以避免，但每开一个 PowerShell 窗口要多起一次 Python。本方案不改，只在 README 提示升级后重新生成。
- 选项缩写：argparse 接受唯一前缀（如 `--clash-t` 等于 `--clash-tun`），四种补全脚本的扫描只认完整写法，缩写后按 Tab 补出的是位置取值或文件名，不是 `on` / `off`。这是本次之前就有的扫描规则（数位置参数时同样不认缩写），不影响命令本身的执行；要支持需在四种 shell 里各加一段前缀匹配，本次不做（PR #43 评论提出，2026-11-05）。
