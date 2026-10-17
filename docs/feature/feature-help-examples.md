# 帮助文本加使用示例（顶层 + 每个子命令）

> 2026-10-17 注记：已落地（PR #34，main `4db3440`），随 v0.12.1 发布。plan-review 两轮收敛（第 1 轮 0 高 2 中 4 低，第 2 轮 0 高 0 中 1 低）；code-reviewer 两轮，第 2 轮“可合并”（第 1 轮中级问题：示例校验没关缩写匹配，选项改名后旧名仍按前缀通过，已改为 `allow_abbrev = False`）。实现位置：`src/multi_codex/cli.py` 的 `TOP_EXAMPLES`（:69）、`COMMAND_EXAMPLES`（:79）、`_examples_text`（:195）、`HELP_EXAMPLES`（:203）、`build_parser` 末尾挂示例的循环（:367）。测试：`tests/test_onboarding.py` 的 `HelpTest.subcommand_parsers`（:110）、`test_every_command_has_examples`（:131）、`test_examples_parse_and_fit`（:145）。验证：本机 Python 3.11 全量通过；编译机 Python 3.8.10 全量 364 个用例通过（跳过 11 个）。基线：main `1c7edbf`（v0.12.0；方案正文里的行号按 `96154b0` 写，两者之间只改了版本号与 CHANGELOG）。

## 1. 背景

用户 2026-10-16 提出：在 `--help` 中给出所有功能的示例用法；同日确认范围为“两处都加”。

现状（基线 main `96154b0`）：

- 顶层 `multi-codex --help` 末尾的 `examples:` 只有 4 条（`src/multi_codex/cli.py:67-72` 的 `HELP_EXAMPLES`，经 `:93` 的 `epilog=` 输出），只覆盖 add / login / list / use。
- 23 个子命令的帮助（`multi-codex <命令> -h`）只有 usage、一句话说明（`COMMAND_SUMMARY`）与选项列表，没有任何示例。子命令在 `build_parser()`（`cli.py:85-233`）里用 `sub.add_parser(name, description=COMMAND_SUMMARY[name])` 注册，均未传 `epilog`。

## 2. 目标 / 非目标

目标：

1. 每个子命令的 `-h` / `--help` 末尾输出 `examples:` 段，2～4 条常用写法，每条附一句说明。
2. 顶层 `--help` 的 `examples:` 在现有 4 条之外补 3 条日常用法，并加一行提示“各命令的 `-h` 有更多示例”。
3. 测试保证：每个注册的子命令都有示例；示例里的 multi-codex 命令行都能被当前的参数解析器接受（选项改名后示例不会悄悄失效）。

非目标：

- 不改 usage 行、一句话说明、选项说明文字，不调整命令分组。
- 不改 README（README 的命令表与 Common tasks 已有示例，本次不同步复制）。
- 示例只用英文，与现有帮助一致；不做多语言。
- 不改补全脚本（`completion.py` 只读选项与位置参数，不读 epilog）。

## 3. 假设与约束

- 子命令帮助改用 `argparse.RawDescriptionHelpFormatter`：它只让 description 与 epilog 按原样输出，选项说明照旧自动折行（Python 标准库 `argparse.RawDescriptionHelpFormatter` 文档：“Indicates that description and epilog are already correctly formatted and should not be line-wrapped”）。各子命令的 description 都是单行的 `COMMAND_SUMMARY`，按原样输出与现在折行后的样子相同。
- `run` / `code` / `login` 的 `--` 之后的参数在解析前由 `main` 截掉（`cli.py:294-295`），测试校验示例时同样只把 `--` 之前的部分交给解析器。
- 示例只引用 multi-codex 自身的选项；`--` 之后不写 Codex 或 VS Code 的专有参数（这些参数的行为不在本仓库内，无法校验），只用 `env`、`git status` 这类通用命令。
- 排版后的每一整行（行首 2 格缩进 + 补齐到本组对齐宽度的命令行 + 说明）不超过 79 列，80 列终端不折行。对齐宽度按每组单独计算，所以同组里最长的命令行决定了这一组说明还剩多少列。

## 4. 涉及模块

| 区域 | 行号锚点（基线 `96154b0`） | 改动类型 | 改动点 |
| ---- | ---- | ---- | ---- |
| `src/multi_codex/cli.py` 常量 | `:67-72` `HELP_EXAMPLES` | 修改 | 补 3 条示例 + 提示行；改为由 `_examples_text` 生成 |
| `src/multi_codex/cli.py` 常量 | `:72` 之后追加 | 新增 | `COMMAND_EXAMPLES` 字典与 `_examples_text()` |
| `src/multi_codex/cli.py` `build_parser` | `:232`（最后一个 `_add_dry_run(p_restore)`）之后、`:233` `return parser` 之前 | 新增 | 遍历 `sub.choices`，为每个子命令设置 epilog 与 formatter_class |
| `tests/test_onboarding.py` | `:118-119` `test_subcommand_help_keeps_summary` 之后追加 | 新增 | 两条测试（见 §8 T1、T2） |
| `CHANGELOG.md` | `## [Unreleased]` 下 | 新增 | Added 条目 |

## 5. 方案

### 5.1 实现要点

1. 新增 `COMMAND_EXAMPLES: Dict[str, Tuple[Tuple[str, str], ...]]`，键是子命令名，值是若干 `(命令行, 说明)`。`_examples_text(examples)` 生成：

   ```text
   examples:
     <命令行，左对齐补空格到本组最长命令 + 2><说明>
   ```

   对齐宽度按每个命令自己那一组计算。`HELP_EXAMPLES` 改为 `_examples_text(TOP_EXAMPLES) + "\nRun multi-codex COMMAND -h for more examples.\n"`，`TOP_EXAMPLES` 为现有 4 条加 §5.3 的 3 条。现有 4 条文字不变，但对齐列从现在固定的第 32 列变为按本组最长命令行计算（最长的是 `multi-codex login work`（22 列），说明从第 27 列开始），排版会有一列左右的变化；现有测试只断言含 `examples:`，不受影响。

2. `build_parser()` 在 `return parser`（`:233`）前追加：

   ```python
   for name, command_parser in sub.choices.items():
       command_parser.formatter_class = argparse.RawDescriptionHelpFormatter
       command_parser.epilog = _examples_text(COMMAND_EXAMPLES[name])
   ```

   用循环统一设置，不改 23 处 `add_parser` 调用；`COMMAND_EXAMPLES` 缺某个命令时 `KeyError` 在构建解析器时就抛出，加上 T1 测试，不会漏掉。

3. 示例内容见 §5.3，编码时照抄，命令行与说明都不改。

### 5.2 接口变更

只改帮助输出文本：顶层与 23 个子命令的 `-h` 末尾多出 `examples:` 段。命令行参数、退出码、标准输出的其它内容、JSON 均不变。帮助文本不是机器可读接口，不涉及 `docs/reference/*`，无需 sibling 回补检查。

### 5.3 示例清单

顶层：前 4 条是现有示例，文字不变；后 3 条是新增的。

| 命令行 | 说明 |
| ---- | ---- |
| `multi-codex add work` | create account "work" and its launcher codex-work |
| `multi-codex login work` | log in once |
| `multi-codex list` | who is logged in where |
| `multi-codex use work` | make plain codex and the Dock apps use "work" |
| `multi-codex usage` | rate-limit usage of every account |
| `multi-codex bind work` | use "work" in this directory (codex-auto) |
| `multi-codex doctor` | check the setup when something looks wrong |

各子命令（命令行一列省略了开头的 `multi-codex `；“对齐宽度”指本组最长命令行加 2，不含行首 2 个空格的缩进）：

| 命令 | 命令行 | 说明 |
| ---- | ---- | ---- |
| init | `init` | write the default settings |
| init | `init --root ~/accounts` | put accounts elsewhere |
| init | `init --shared-items skills,rules` | share only these items |
| init | `init --dry-run` | show what would change |
| migrate-default | `migrate-default` | name it after the e-mail in ~/.codex |
| migrate-default | `migrate-default main` | name the account "main" |
| migrate-default | `migrate-default --dry-run` | show the plan, move nothing |
| add | `add work` | create "work" and codex-work |
| add | `add work --proxy 7901` | use http://127.0.0.1:7901 |
| add | `add work --shared` | link the shared items into "work" |
| add | `add new --config-from work` | copy config.toml from "work" once |
| set | `set work --proxy off` | stop using a proxy |
| set | `set work --shared` | turn sharing on |
| set | `set work --shared-exclude skills` | do not link skills into "work" |
| set | `set work --no-shared` | turn sharing off |
| proxy | `proxy work 7901` | same as http://127.0.0.1:7901 |
| proxy | `proxy work socks5://host:1080` | a SOCKS proxy |
| proxy | `proxy work off` | unset the proxy variables |
| proxy | `proxy work inherit` | keep your shell's proxy |
| rename | `rename work client-a` | codex-work becomes codex-client-a |
| rename | `rename work client-a --dry-run` | show what would change |
| remove | `remove work` | unregister "work"; its directory is kept |
| remove | `remove work --dry-run` | show what would change |
| apply | `apply` | converge to the configuration |
| apply | `apply -f accounts.json` | use this file as the configuration |
| apply | `apply --dry-run` | show what would change |
| list | `list` | who is logged in, usage, last used |
| list | `list -v` | full table: directories, plans, billing |
| list | `list --json` | machine-readable output |
| usage | `usage` | last known usage of every account |
| usage | `usage work` | only "work" |
| usage | `usage --live` | ask Codex for the current usage |
| usage | `usage work --live --timeout 60` | allow up to 60 seconds |
| doctor | `doctor` | check the installation and accounts |
| doctor | `doctor --json` | machine-readable output |
| completion | `completion zsh` | print the zsh script |
| completion | `completion bash` | print the bash script |
| completion | `completion fish` | print the fish script |
| login | `login work` | log in to "work" |
| login | `login work -- --help` | pass options to codex login |
| run | `run work` | start codex with "work" |
| run | `run` | use the account bound to this directory |
| run | `run work -- env` | run another command with its environment |
| bind | `bind work` | bind the current directory to "work" |
| bind | `bind work ~/src/project` | bind another directory |
| bind | `bind` | list the bindings |
| unbind | `unbind` | remove the binding of this directory |
| unbind | `unbind ~/src/project` | remove the binding of that directory |
| which | `which` | which account this directory uses |
| which | `which ~/src/project` | the same for another directory |
| code | `code work` | open VS Code for "work" |
| code | `code work ~/src/project` | open a folder |
| code | `code work . --bin ~/bin/code` | use another code command |
| app | `app work` | open the app for "work" |
| app | `app work --app ~/Apps/ChatGPT.app` | an app in another place |
| path | `path work` | print the directory of "work" |
| path | `cd "$(multi-codex path work)"` | go to it |
| env | `env work` | list the variables of "work" |
| env | `env work NO_PROXY=localhost` | set a variable |
| env | `env work --unset NO_PROXY` | remove a variable |
| env | `env work --clear` | remove all variables |
| use | `use` | show the default account |
| use | `use work` | make plain codex and the apps use "work" |
| use | `use work --dry-run` | show what would change |
| restore | `restore work` | move "work" back to ~/.codex |
| restore | `restore work --dry-run` | show what would change |

注：`path` 的第二条以 `cd` 开头，整行原样输出；代码里其余命令行统一带 `multi-codex ` 前缀。本表已按 §5.1 的排版规则逐行算过：顶层与每个子命令组排出来的整行（含缩进、补齐空格与说明）都不超过 79 列。编码时照抄，说明文字不再改动。

## 6. 备选方案与决策

- 每个 `add_parser(...)` 调用各自传 `epilog=`：要改 23 处，与“只追加”原则不符；循环统一设置只追加一段代码。
- 把全部示例都放进顶层帮助：顶层帮助会超过两屏，用户已选“两处都加”。

## 7. 影响分析

- 正向：`build_parser()` 的调用方有 `main`（解析命令行）与 `completion.command_spec`（`tests/test_onboarding.py:105` 读取子命令与选项生成补全）。补全只读 `_actions`，不读 epilog 与 formatter_class，补全脚本不变（T3 由现有 `test_completion_lists_every_command_and_option` 覆盖）。
- 反向：现有帮助相关测试 `test_groups_in_order` 断言顶层帮助含 `examples:`（`:107-111`），`test_subcommand_help_keeps_summary` 断言子命令帮助含一句话说明（`:118-119`），改动后两者仍成立。
- 运行时：每次启动多构建 23 段 epilog 字符串，量级为几 KB，只在构建解析器时做一次，对命令耗时无可感知影响。
- 输出：只有 `-h` 输出变化；`scripts/make-assets.py` 不截取帮助输出（已 grep），演示素材不受影响。

## 8. 回归测试

| 编号 | 内容 | 方式 |
| ---- | ---- | ---- |
| T1 | `COMMAND_EXAMPLES` 的键集合与注册的子命令集合相等；每个命令有 2～4 条；每个子命令 `-h` 输出含 `examples:` 与它的第一条命令行 | 新增单元测试 |
| T2 | 所有示例里以 `multi-codex ` 开头的命令行，取 `--` 之前的部分用 `shlex.split` 拆开，交给顶层与各子命令都关掉缩写匹配（`allow_abbrev = False`）的 `build_parser().parse_args()` 不报错（示例必须写选项全名，改名后旧名不会再按前缀匹配通过）（拦截 `SystemExit`，失败时报出是哪一条）；`HELP_EXAMPLES` 与每个子命令的 `_examples_text()` 输出逐行断言 `len(line) <= 79`（量的是排版后的整行，含缩进、补齐空格与说明）。只验证解析层：`set` 不带选项、`env` 的 KEY=VALUE 格式这类在命令执行阶段才检查的用法不在覆盖范围内（本次的示例已逐条人工核过） | 新增单元测试 |
| T3 | 补全脚本仍覆盖全部命令与选项 | 现有测试 `test_completion_lists_every_command_and_option` |
| T4 | 现有帮助测试与全量测试通过 | 本机 3.11 全量 + 编译机 Python 3.8.10 全量 |
| T5 | 人工看一遍 `multi-codex -h`、`multi-codex proxy -h`、`multi-codex run -h` 的输出排版 | 本机运行 |

## 9. 日志 / 观测点

无运行日志变化。观测点就是帮助输出本身：`multi-codex <命令> -h | sed -n '/^examples:/,$p'`。
