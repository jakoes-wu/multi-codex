# 降低新手门槛：下一步提示、上手指引、分组帮助、拼写提示与报错改进（v0.6）

> 2026-10-01 注记：代码已落地（未提交，等待编译机验证）。
> - 公共函数：`fsutil.display_path`、`actions.hint`、`platform.dir_on_path`、`Config.not_registered`（`src/multi_codex/config.py`）。
> - 命令行：`src/multi_codex/cli.py` 中的 `COMMAND_GROUPS`、`print_getting_started`、`_hint_after_setup`、`cmd_use_show`。
> - 安装脚本：`install.sh` 末尾的下一步与补全提示。
> - §8 的用例在 `tests/test_onboarding.py` 中。

## 1. 背景

2026-10-01 在临时 HOME 中按 README 快速开始实跑（`PATH` 不含 `~/.local/bin`），观察到以下现状（基线 `3a92223`）：

| # | 操作 | 现状 |
| ---- | ---- | ---- |
| 1 | `multi-codex add work` | 打印 3 行 `create …`（绝对路径），没有下一步提示，也不检查启动命令目录是否在 `PATH` 中 |
| 2 | `multi-codex`（无参数） | argparse 报 `the following arguments are required: COMMAND`，退出码 2 |
| 3 | `multi-codex -h` | 19 个子命令按注册顺序平铺，`init`、`migrate-default` 排在最前 |
| 4 | `multi-codex use`（`~/.codex` 不存在） | 只输出 `(none)` |
| 5 | `multi-codex proxy work abc` | `unsupported proxy scheme ''; use one of http, https, socks5, socks5h` |
| 6 | `multi-codex remove nope` | `nope is not registered`，随后 3 行 `unchanged`，退出码 0 |
| 7 | `multi-codex run wrk` | `account 'wrk' is not registered`，不提示相近的账号名 |
| 8 | 写命令的输出 | 路径全是绝对路径，例如 `/Users/<用户>/.local/bin/codex-work` |
| 9 | `install.sh` 结束 | 只在 `PATH` 缺目录时提示一行，不提示下一步，也不提补全 |

用户决定本次做 A1–A7，作为 v0.6.0 发布。

## 2. 目标 / 非目标

**目标**

1. 新用户照着命令输出就能走完“建账号 → 登录 → 使用”，不需要回头查 README（A1、A2、A7）。
2. 帮助和报错能直接告诉用户该怎么做（A3、A4、A5）。
3. 写命令输出的路径更短、更易读（A6）。

**非目标**

- 不新增子命令，不改任何子命令的参数（B1、B2 另列待办）。
- 不减少写命令的 `unchanged` 行（B3 另列待办）。
- 不改 `--json` 输出、配置文件格式、启动命令内容。
- 不改 `list` 表格（`tests/test_insight.py:112` 按行位置解析）、`path` 的输出（脚本会拿它做路径）、`use` 不带参数时 stdout 的内容。
- 不做交互式向导。

## 3. 假设与约束

- 只用 Python 3.8 标准库；拼写提示用 `difflib.get_close_matches`。
- **新增的提示一律写到 stderr**，stdout 保持原样：`use` 不带参数的 stdout 是账号名（`tests/test_switch.py:60` 断言 `(none)`），写命令的 stdout 可能被脚本逐行读取。
- 退出码约定不变，唯一例外是 A2：不带参数运行由 2 改为 0（它不再是参数错误，而是显示指引）。
- `remove` 不存在的账号时**保持退出码 0**：README 的退出码表把 0 定义为“成功，或已处于目标状态”；而且这次收敛带着 `orphan_scope={name}`（`cli.py:342`），负责清理该名字残留的启动命令，不能跳过。

## 4. 涉及模块

| 区域 | 行号锚点（基线 `3a92223`） | 改动类型 | 改动点 |
| ---- | ---- | ---- | ---- |
| 输出 | `src/multi_codex/actions.py:40-46` `print_action` | 修改 | 路径经 `display_path` 缩写（A6） |
| 输出 | `src/multi_codex/actions.py:49` `info` 之后 | 新增 | `hint(message)`：写 stderr，前缀 `[multi-codex] hint:` |
| 路径工具 | `src/multi_codex/fsutil.py` 末尾 | 新增 | `display_path(path)`（A6） |
| 平台 | `src/multi_codex/platform.py:31-33` `default_bin_dir` 之后 | 新增 | `dir_on_path(directory)`：从 `doctor._check_path` 抽出的判断 |
| 体检 | `src/multi_codex/doctor.py:109-114` `_check_path` | 修改 | 改为调用 `platform.dir_on_path`，输出不变 |
| 配置 | `src/multi_codex/config.py:82-92` `Config.find` 之后 | 新增 | `Config.not_registered(name)`：返回带拼写提示的报错文本（A4） |
| 代理解析 | `src/multi_codex/config.py:274-276` | 修改 | 值里没有 `://` 时给出完整的可选值说明（A5） |
| 命令行 | `src/multi_codex/cli.py:28-156` `build_parser` | 修改 | 子命令说明集中到表 `COMMAND_GROUPS`，顶层帮助按组输出并附示例（A3） |
| 命令行 | `src/multi_codex/cli.py:190-197` `main` 开头（`_split_run_command` 之后、`parse_args` 之前） | 修改 | 无参数时调用 `print_getting_started()`，返回 0（A2） |
| 命令行 | `src/multi_codex/cli.py:281-286` `dispatch` 的 migrate 分支 | 修改 | 成功后调用 `_hint_after_setup`（A1，只检查 PATH） |
| 命令行 | `src/multi_codex/cli.py:305-323` add 分支、`:384-391` 收尾 | 修改 | add 成功后调用 `_hint_after_setup`（A1） |
| 命令行 | `src/multi_codex/cli.py:338` remove 分支 | 修改 | 文本改为 `Config.not_registered` + “nothing to remove”（A5） |
| 命令行 | `cli.py:331`、`:351`、`:357`、`:398`、`:676`、`:689`、`:779`、`:792` | 修改 | 改用 `Config.not_registered`（A4） |
| 命令行 | `src/multi_codex/cli.py:593` `cmd_usage` | 修改 | 结果文本改用 `Config.not_registered`（A4） |
| 命令行 | `src/multi_codex/cli.py:547-549` `cmd_list` 表头 3 行 | 修改 | 路径经 `display_path`（A6） |
| 命令行 | `src/multi_codex/cli.py:799-802` `cmd_use_show` | 修改 | 没有默认账号时追加 stderr 提示（A5） |
| 默认账号 | `src/multi_codex/switch.py:95`、`:163`、`:203` | 修改 | 改用 `Config.not_registered`（A4） |
| 安装脚本 | `install.sh:281-289` | 修改 | 结束时打印下一步与补全开启方法（A7） |
| 测试 | 新文件 `tests/test_onboarding.py` | 新增 | §8 的用例 |
| 测试 | `tests/test_accounts.py:195`、`:213` | 修改 | 动作行断言改为缩写路径（A6 的直接后果，见 §7 第 1 条） |
| 文档 | `README.md`、`README.zh-CN.md` 快速开始第 1 步 | 修改 | 补一句“不带参数运行 `multi-codex` 会显示这些步骤” |
| 文档 | `CHANGELOG.md` `[Unreleased]` | 修改 | 记录 A1–A7 |

## 5. 方案

### 5.1 实现要点

#### 5.1.1 公共函数

- `fsutil.display_path(path)`：`home = os.path.expanduser("~")`；`home` 为空或为 `/` 时原样返回；`path == home` 返回 `~`；以 `home + os.sep` 开头时返回 `~` + 余下部分；其余原样返回。
- `actions.hint(message)`：`print("[multi-codex] hint: " + message, file=sys.stderr)`。
- `platform.dir_on_path(directory) -> bool`：逐字移植 `doctor.py:110-111` 的判断（对 `PATH` 各项做 `expand` + `realpath`，与 `realpath(directory)` 比较）。`doctor._check_path` 改为调用它，check 的 id、状态和文字都不变。
- `Config.not_registered(name) -> str`：
  - 基础文本 `account {name!r} is not registered`，与现有文本相同；
  - 用 `difflib.get_close_matches(name.casefold(), [各账号名的 casefold], n=1, cutoff=0.6)` 找相近的名字，命中时追加 `; did you mean {原始大小写的名字!r}?`；
  - 没有命中且已登记账号不超过 5 个时追加 `; registered: a, b`；一个账号都没有时追加 `; run \`multi-codex add NAME\` first`。

#### 5.1.2 A1：add 与 migrate-default 之后的提示

新增 `_hint_after_setup(config, name, check_login)`，只在退出码为 0 且不是 `--dry-run` 时调用：

1. 账号不存在（被冲突拦下等情况）时直接返回。
2. 先算 `on_path = platform.dir_on_path(expand(config.bin_dir))`。
3. `check_login` 为真，且 `identity.read_identity(账号目录).login == LOGIN_LOGGED_OUT` 时提示登录。只有未登录才提示；keyring、unreadable 等状态交给 `doctor`。提示文字按 `on_path` 二选一：
   - `on_path` 为真：`hint("next: log in with `codex-<name> login`")`；
   - 为假：`hint("next: log in with `multi-codex run <name> -- codex login`")`。这时 `codex-<name>` 还找不到，`run` 用同样的环境、不依赖 PATH（`cli.py:_account_exec_args`）。
4. `on_path` 为假时：`warn("<display_path(bin_dir)> is not on PATH, so `codex-<name>` will not be found; add it in your shell profile, e.g. export PATH=\"<bin_dir>:$PATH\"")`。
   - 代码里的 `<bin_dir>` 用展开后的绝对路径，复制后可以直接执行；
   - 用户一般会把这行粘贴进 `~/.zshrc` 等 shell 配置文件，那里的双引号字符串会展开 `$PATH`。

调用点：

- add 分支：`converge` 返回之后、`return code` 之前。`check_login=True`，`config` 用 `new`。
- migrate 分支：`migrate.migrate_default` 返回 0 且不是 dry-run 时，重新 `load_config()` 取最新配置，`check_login=False`（迁移过来的目录本来就带着登录）。

#### 5.1.3 A2：不带参数运行

`main` 在 `_split_run_command` 之后，`argv` 为空时调用 `print_getting_started()` 并返回 0，不交给 argparse。`print_getting_started` 写到 stdout：

- `load_config()` 抛 `ConfigError` 或 `OSError` 时，不读配置，只输出通用部分。
- 没有配置，或配置里一个账号都没有：

  ```text
  multi-codex: run several Codex accounts side by side.

  Get started:
    multi-codex add NAME          create an account and its launcher codex-NAME
    codex-NAME login              log in once
    codex-NAME                    use it instead of codex
  ```

  `~/.codex` 是普通目录时（`entry_kind(switch.default_link()) == KIND_DIR`；`platform.default_source()` 返回未展开的字面量 `"~/.codex"`，`platform.py:23-24`，不能直接拿来判断），再加一行：`  multi-codex migrate-default NAME   keep your current ~/.codex login as an account`。
- 已有账号：第一行之后输出 `Accounts: a, b (multi-codex list for details)`，再列 `codex-NAME`、`multi-codex usage`、`multi-codex doctor` 三行常用命令。
- 末尾统一输出 `Run \`multi-codex -h\` for all commands.`。

#### 5.1.4 A3：分组帮助

`cli.py` 新增模块级表 `COMMAND_GROUPS`，每组是 `(组名, [(子命令, 一句话说明), ...])`：

| 组 | 子命令（按此顺序） |
| ---- | ---- |
| Get started | `add`, `migrate-default`, `list`, `doctor` |
| Everyday | `run`, `usage`, `use`, `bind`, `unbind`, `code`, `app`, `proxy`, `path`, `completion` |
| Advanced | `env`, `remove`, `restore`, `apply`, `init` |

- 一句话说明沿用现在各个 `add_parser(help=...)` 的文本，不改措辞。
- `build_parser` 中每个 `sub.add_parser(name, ...)` 去掉 `help=`、改为 `description=<说明>`。argparse 只为带 `help` 的子命令生成列表行（`_SubParsersAction.add_parser`），去掉后顶层自动列表为空；`multi-codex add -h` 仍会显示说明。
- 顶层 `ArgumentParser` 改用 `formatter_class=argparse.RawDescriptionHelpFormatter`。`description` 保留原来的一句话，后面接按组渲染的命令列表（命令名左对齐到同一列）；`epilog` 放 4 行示例：`multi-codex add work`、`codex-work login`、`multi-codex list`、`multi-codex use work`。
- 新增一条测试，断言 `COMMAND_GROUPS` 覆盖的子命令集合与 parser 中注册的子命令集合**完全相等**，防止以后新增子命令时漏进帮助。
- 补全只读子命令名和选项（`completion.py:22-38`），并且按名字排序（`completion.py:43`），不受影响。

#### 5.1.5 A4：账号名拼写提示

§4 表中列出的 12 处 `account {!r} is not registered`，都改为 `config.not_registered(name)`（`switch.py` 中用传入的 `config`）。`cli.py:670`（绑定指向已注销账号）的语义不同，保持不变。

#### 5.1.6 A5：三条报错

1. `normalize_proxy`：在 `urlsplit`（`config.py:273`）之前加判断，值里不含 `://` 时抛出 `ValueError("invalid proxy {!r}: use a port number (e.g. 7901), a URL such as http://127.0.0.1:7901, 'off' or 'inherit'")`。原有的 scheme 检查保留，用来拦 `ftp://…` 这类值。配置文件的解析走同一个函数，报错也随之变好。
2. `remove` 不存在的账号：`cli.py:338` 改为 `info("nothing to remove: {}".format(new.not_registered(name)))`（先说结论，避免拼在拼写提示后面读起来别扭），其余不变（退出码 0、照常收敛）。
3. `cmd_use_show`：stdout 照旧输出 `describe_default(config)[1]`；默认账号为 `None` 时，按 `entry_kind(switch.default_link())` 追加一条 `hint`：
   - 不存在：`no default account yet; \`multi-codex use NAME\` makes ~/.codex point to an account`；
   - 是普通目录：`~/.codex is not managed yet; run \`multi-codex migrate-default NAME\` first`；
   - 其它情况（软链指向未登记目录等）：不追加。`doctor` 已覆盖。

#### 5.1.7 A6：输出路径缩写

- `print_action` 输出的 `action.path` 改为 `display_path(action.path)`。`reason` 不处理。
- `cmd_list` 表头的 `root:`、`bin_dir:`、`shared.dir:` 三行改用 `display_path`。行数和顺序不变。
- 不改 `path` 命令、`--json`、`error()` 的 `path=` 字段、`info` 里的路径。

#### 5.1.8 A7：安装脚本的收尾提示

`install.sh:286-289`（apply 之后），在非卸载路径的最后追加：

```sh
if [ -z "$CONFIG_FILE" ]; then
  log "next: multi-codex add NAME   (or multi-codex migrate-default NAME to keep your current ~/.codex login)"
fi
case "$(basename "${SHELL:-}")" in
  bash) log "tab completion: add  eval \"\$(multi-codex completion bash)\"  to ~/.bashrc" ;;
  zsh)  log "tab completion: add  eval \"\$(multi-codex completion zsh)\"  to ~/.zshrc (after compinit)" ;;
  fish) log "tab completion: add  multi-codex completion fish | source  to ~/.config/fish/config.fish" ;;
esac
```

`$SHELL` 不是这三种时不输出补全提示。卸载在 `install.sh:86` 的分支里提前退出，不会走到这里。脚本仍需通过 shellcheck（`tests/test_install.py:133`）。

### 5.2 接口变更

- **命令行行为**：
  - `multi-codex` 不带参数：由“报错，退出码 2”改为“输出上手指引，退出码 0”。
  - `-h` 输出的版式改变，子命令集合不变。
- **stderr 新增内容**：`hint:` 行和 PATH 警告。stdout 只有两处变化：A6 的路径缩写，以及 `remove` 不存在的账号时那一行的措辞。
- **报错文本**：未登记账号的报错在原文后追加内容，前缀不变；`proxy` 的非法值报错改写。退出码均不变。
- **不变**：`--json`、配置文件、启动命令、`path` 输出、`use` 不带参数的 stdout、`list` 表格行。
- 不涉及 `docs/reference/*`。

## 6. 备选方案与决策

- **A6 只在终端时缩写（`isatty`）**：否决。输出被管道或测试捕获时行为不同，反而更难排查；`--json` 已经是给脚本的稳定接口。
- **A3 继续用 argparse 自动列表、只调整注册顺序**：否决。分不了组、加不了示例，实际上只省掉一张表。
- **A1 的 PATH 提示放在 `list` 里**：否决。新用户 add 完通常直接敲 `codex-NAME`，等到运行 `list` 时已经卡过一次了。

## 7. 影响分析

1. **stdout 内容变化（A6、A5-2）**：`print_action` 被 `accounts.execute`（`accounts.py:188`）和 `cli.py:388`（绑定）调用，覆盖所有写命令。现有测试中有 2 处断言动作行里的绝对路径（`tests/test_accounts.py:195`、`:213`，共享软链的 `unchanged` / `update (adopted)` 行），随本方案改为缩写后的 `~/.cx/work/skills`；其余只对 `unchanged config` 这类片段做 `assertIn`（`tests/test_ergonomics.py:234`、`tests/test_config_copy.py:34`）。README 已声明脚本应使用 `--json`。
2. **stderr 新增行**：会读 stderr 的测试只做 `assertIn`；新增行不影响现有断言。`use` 不带参数的 stdout 不变，`tests/test_switch.py:60` 继续通过。
3. **`doctor._check_path` 改为调用公共函数**：只抽出判断，check 的 id、状态和文字不变。由 §8 第 10 条回归。
4. **`Config.not_registered`**：只读账号名，不改配置。所有调用点都是报错路径，正常路径没有变化。
5. **`normalize_proxy`**：被 `cli._checked_proxy`、`config` 解析和 `apply -f` 调用（`grep -n normalize_proxy src`）。合法值的返回结果不变；只有原来就会被拒绝的值，报错文本改变。
6. **`build_parser` 去掉 `help=`**：`completion.command_spec` 只读 `choices` 和选项（`completion.py:25-37`），不读子命令的 help，补全输出不变。由 §8 第 6 条回归。
7. **A2 拦截空参数**：只在 `argv` 为空时生效；`multi-codex -h`、`--version` 照旧交给 argparse。`run`、`code` 的 `--` 拆分在拦截之前完成，`argv` 只有在完全没有参数时才为空。
8. **install.sh**：只在末尾追加 `log` 行，不改安装流程。`test_install` 中按输出断言的用例只做 `assertIn`。
9. **运行时**：`_hint_after_setup` 读一次 `auth.json`、扫描一次 `PATH`；不新建进程，不加锁，在写锁内执行。

## 8. 回归测试

新文件 `tests/test_onboarding.py`，沿用 `tests/helpers.py` 的临时 HOME 与子进程方式：

1. `add work` 时 `PATH` 不含 bin_dir：退出码 0；stderr 含 `multi-codex run work -- codex login` 和 `is not on PATH`，不含 `codex-work login`。
2. 把 bin_dir 加进 `PATH` 再 `add work`：stderr 含 `hint: next: log in with \`codex-work login\``，不含 `is not on PATH`。
3. 写入假 `auth.json`（已登录）后 `add work`：stderr 不含 `log in with`。
4. `add work --dry-run`：stderr 没有 hint。
5. `migrate-default main` 成功：PATH 缺失时 stderr 有警告，没有登录提示。
6. 不带参数运行：退出码 0。无配置时 stdout 含 `multi-codex add NAME`；`~/.codex` 是目录时含 `migrate-default`；有账号时含 `Accounts: work`；配置文件损坏时退出码仍为 0，并输出通用部分。
7. `-h`：退出码 0；按顺序含 `Get started`、`Everyday`、`Advanced`；`COMMAND_GROUPS` 的子命令集合与 parser 的完全相等。
8. 三种 shell 的 `completion` 输出：对 `completion.command_spec(build_parser())` 中的每个子命令名和每个选项，断言 bash、zsh、fish 三份脚本都包含它；子命令总数与 `COMMAND_GROUPS` 一致。
9. 拼写提示：`run wrk` 报错含 `did you mean 'work'?`，退出码 1；`path zzz` 含 `registered: work`；无配置时 `path x` 含 `multi-codex add NAME`；`use wrk` 和 `restore wrk` 也带提示。
10. `doctor`：PATH 缺失与存在两种情况下，`path` 这项的状态和文字与改动前一致。
11. `proxy work abc`：退出码 2，stderr 含 `use a port number`；`proxy work ftp://h:1` 仍报 `unsupported proxy scheme`；`proxy work 7901` 照常成功。
12. `remove nope`：退出码 0，stdout 含 `nothing to remove`；残留的 `codex-nope` 启动命令（由本工具生成）仍会被删除。
13. `use` 不带参数：`~/.codex` 不存在时 stdout 仍为 `(none)`，stderr 含 `multi-codex use NAME`；是目录时 stderr 含 `migrate-default`。
14. 路径缩写：`add work` 的 stdout 含 `~/.local/bin/codex-work`，不含 HOME 的绝对路径；`list` 表头含 `root: ~/.cx`；`path work` 仍输出绝对路径；`list --json` 的 `root` 仍是绝对路径。
15. install.sh：本地安装的输出含 `next: multi-codex add NAME`；`SHELL=/bin/zsh` 时含 `completion zsh`；带 `--config` 时不含 `next:`；shellcheck 通过。
16. 全量 `python3 -m unittest discover -s tests -t tests` 通过，并在编译机上用 Python 3.8 再跑一遍。

## 9. 日志 / 观测点

- 新提示统一使用前缀 `[multi-codex] hint:`（stderr），可以用 `grep 'hint:'` 筛选；PATH 警告沿用 `[multi-codex] warning:`。
- 拼写提示可以搜 `did you mean`。
- 安装脚本新增的行沿用 `[multi-codex-install]` 前缀。
