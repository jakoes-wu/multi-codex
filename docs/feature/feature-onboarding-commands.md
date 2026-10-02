# 新手门槛 B 组：login 子命令、migrate-default 名称可省略、写命令只打印变化（v0.7）

> 2026-10-01 注记：代码已落地（未提交，等待编译机验证）。
> - B1：`src/multi_codex/cli.py` 的 `login` 子命令与 `main` 中的分派；补全见 `completion.py` 的 `ACCOUNT_COMMANDS` 与三处 `--` 判断。
> - B2：`cli.py` 的 `_derive_migrate_name`、`_NO_EMAIL_REASONS`。
> - B3：`accounts.py` 的 `execute(verbose=...)`、`converge(verbose=...)`；`cli.py` 的 `_add_verbose`。
> - §8 的用例在 `tests/test_onboarding_commands.py` 中。

## 1. 背景

v0.6.0（`docs/feature/feature-onboarding-hints.md`）分析新手路径时，有三项会改变命令形式，当时记为待办 06，用户 2026-10-01 决定实施（基线 main `2b39bbf`）：

- **B1**：登录只能用 `codex-NAME login`。启动命令目录不在 `PATH` 时这条命令找不到，v0.6 的提示只好改成 `multi-codex run NAME -- codex login`（`src/multi_codex/cli.py:474-477`）。
- **B2**：`migrate-default` 必须写名称（`cli.py:100` 是必填位置参数）。新用户接管现有 `~/.codex` 时得自己想一个名字，而 `~/.codex/auth.json` 里本来就有邮箱，`identity.read_identity` 能读出来（`src/multi_codex/identity.py:113-150`）。
- **B3**：写命令把每个对象都打印一行（`src/multi_codex/accounts.py:187-188`），重复执行时满屏 `unchanged`，真正的变化反而不显眼。

## 2. 目标 / 非目标

**目标**

1. B1：新增 `multi-codex login NAME [-- ARGS]`，在账号环境下运行 `codex login [ARGS]`，不依赖 `PATH`。
2. B2：`multi-codex migrate-default [NAME]`：省略 NAME 时，从源目录的 `auth.json` 读邮箱作为名称；读不到时报错并要求填写。
3. B3：通过 `accounts.converge` 打印动作的写命令，默认不打印 `unchanged` 行；没有任何要显示的行时输出一句 `already up to date`；加 `-v` / `--verbose` 恢复完整输出。

**非目标**

- `login` 不支持省略 NAME 去用目录绑定（`run` 的绑定逻辑不复用），也不检查登录结果。
- B2 不改迁移本身（`migrate.py` 不动），只在调用前推导名称。
- B3 不改 `--json` 输出（写命令本来就没有 `--json`），不改 `migrate-default`、`use`、`restore` 的输出：这三条命令主要用 `info` 输出过程（`migrate.py` 的 `info("migrating …")` 等），内部调用 `converge` 时沿用新的默认值（不打印 `unchanged`），但不新增 `-v`。
- 不改 `migrate-default` 完成时提示重新登录的文案（`migrate.py` 的 `log in again: codex-<名> login`，`tests/test_migrate.py:485` 断言它）。

## 3. 假设与约束

- 只用 Python 3.8 标准库。
- `codex login` 接受哪些参数由上游决定，本工具不校验、原样透传；README 不列具体参数。
- B3 会改变写命令的默认输出。README 已声明“表格 / 文本格式不保证稳定，脚本请用 `--json`”，但写命令没有 `--json`，所以把 `-v` 写进 CHANGELOG 的 Changed，并说明需要旧输出时加 `-v`。版本号按语义化版本升到 0.7.0。
- 邮箱用作账号名必须通过 `validate_name`（`src/multi_codex/config.py:254`：首字符为字母或数字，只含 `[A-Za-z0-9._@+-]`，最长 64）。不合规的邮箱不做转换，直接要求用户填写。

## 4. 涉及模块

| 区域 | 行号锚点（基线 `2b39bbf`） | 改动类型 | 改动点 |
| ---- | ---- | ---- | ---- |
| 命令表 | `src/multi_codex/cli.py:31-36` `COMMAND_GROUPS` 的 Get started 组 | 修改 | 在 `add` 之后加 `("login", "log in to an account (runs codex login with its environment)")` |
| 参数 | `src/multi_codex/cli.py:162` `p_run` 之前 | 新增 | `login` 子命令：必填 `name`，`usage="multi-codex login NAME [-- ARGS ...]"` |
| 参数 | `src/multi_codex/cli.py:100` `p_mig.add_argument("name")` | 修改 | 改为 `nargs="?"` |
| 参数 | `src/multi_codex/cli.py:228-229` `_add_dry_run` 之后 | 新增 | `_add_verbose(parser)`：`-v`, `--verbose` |
| 参数 | `cli.py:97`、`:127`、`:132`、`:136`、`:140`、`:170`、`:174`、`:194` | 修改 | 这 8 处 `_add_dry_run` 之后各加一行 `_add_verbose(p_xxx)`（init、add、proxy、remove、apply、bind、unbind、env） |
| `--` 拆分 | `src/multi_codex/cli.py:238` `_split_run_command` | 修改 | `(["run"], ["code"])` 加上 `["login"]` |
| 分派 | `src/multi_codex/cli.py:269-272` `main` 中 `run` / `code` 的分派旁 | 新增 | `login` 分派到 `cmd_run(args.name, ["codex", "login"] + run_command)`，与 `run` 一样不加锁 |
| 迁移名称 | `src/multi_codex/cli.py:339-347` `dispatch` 的 migrate 分支 | 修改 | `args.name` 为空时调用 `_derive_migrate_name(args.source)`，见 §5.1.2 |
| 收尾 | `src/multi_codex/cli.py:447-455` `dispatch` 的 `converge` 调用与绑定行 | 修改 | 传 `verbose=args.verbose`；绑定行为 `unchanged` 且非 verbose 时不打印 |
| A1 提示 | `src/multi_codex/cli.py:470-477` `_hint_after_setup` | 修改 | 登录提示统一为 `multi-codex login <name>`，去掉按 PATH 二选一 |
| 帮助示例 | `src/multi_codex/cli.py:58-63` `HELP_EXAMPLES` | 修改 | `codex-work login` 改为 `multi-codex login work` |
| 上手指引 | `src/multi_codex/cli.py:506` `print_getting_started` | 修改 | `codex-NAME login` 改为 `multi-codex login NAME`；`migrate-default NAME` 一行改为 `migrate-default [NAME]` |
| 执行输出 | `src/multi_codex/accounts.py:185-211` `execute` | 修改 | 新增关键字参数 `verbose: bool = False`，见 §5.1.3 |
| 收敛入口 | `src/multi_codex/accounts.py:214-219` `converge` | 修改 | 新增 `verbose: bool = False` 并透传给 `execute` |
| 体检 | `src/multi_codex/doctor.py:178`、`:181` | 修改 | 未登录 / auth.json 读不了时的 `fix:` 改为 `multi-codex login <name>` |
| 补全 | `src/multi_codex/completion.py:16-17` `ACCOUNT_COMMANDS` | 修改 | 加入 `"login"` |
| 补全 | `src/multi_codex/completion.py:82`、`:144`、`:206` | 修改 | `run` 之后遇到 `--` 停止补全的判断，同样适用于 `login` |
| 测试 | 新文件 `tests/test_onboarding_commands.py` | 新增 | §8 的用例 |
| 测试 | `tests/test_accounts.py:196`、`tests/test_config_copy.py:34`、`tests/test_ergonomics.py:234` | 修改 | 断言 `unchanged` 行的 3 处加 `-v` |
| 测试 | `tests/test_insight.py:437`、`tests/test_onboarding.py:31-41` | 修改 | 登录提示断言改为 `multi-codex login <name>` |
| 文档 | `README.md`、`README.zh-CN.md` | 修改 | 快速开始第 2 步、接管 `~/.codex` 段落、速查表、命令表、Good to know / 使用前须知、`--dry-run` 说明 |
| 文档 | `CHANGELOG.md` `[Unreleased]` | 修改 | Added：`login`、`migrate-default` 名称可省略、`-v`；Changed：写命令默认不打印 `unchanged` 行 |

## 5. 方案

### 5.1 实现要点

#### 5.1.1 B1：`login`

- 参数：`login NAME`，NAME 必填。`--` 之后的内容原样追加在 `codex login` 后面（借用 `_split_run_command`，它不经 argparse，避免不同 Python 版本对 `--` 的处理差异，见 `cli.py:232-237` 的注释）。
- 分派：在 `main` 中与 `run` 并列，`return cmd_run(args.name, ["codex", "login"] + run_command)`。账号不存在时沿用 `cmd_run` 的报错，带 v0.6 的拼写提示。
- 使用方同步改为 `multi-codex login <name>`：A1 提示（不再区分是否在 PATH）、`print_getting_started`、`doctor` 的 `fix:`。`migrate-default` 完成时的“log in again”文案不改（非目标）。

#### 5.1.2 B2：推导迁移名称

新增 `_derive_migrate_name(source: Optional[str]) -> str`，只在 `args.name is None` 时调用，结果作为 `name` 交给原有流程（`_checked_name` → `migrate.migrate_default`）：

1. **有未完成的迁移**：`migrate.load_journal()` 不为空时，直接用记录中的 `name`。迁移中途 `~/.codex` 可能已被移走，读不到 `auth.json`，而续跑本来就必须用记录里的名称（`migrate.py:335-340`）。`load_journal` 抛出的 `JournalError` 交给 `main` 已有的处理（`cli.py` 的 `except migrate.JournalError`）。
2. **读邮箱**：`identity.read_identity(expand(source or platform.default_source()))`。`login == LOGIN_CHATGPT` 且 `email` 非空、并通过 `validate_name` 时，用该邮箱，并 `info("using account name '<email>' from <display_path(源目录)>/auth.json")`。
3. **读不到**：抛 `UsageError`（退出码 2）：`cannot tell the account name from <源目录>: <原因>; pass a NAME, e.g. multi-codex migrate-default main`。原因按 `login` 取值给出：
   - `logged-out`：`not logged in`；
   - `apikey`：`logged in with an API key (no e-mail)`；
   - `keyring`：`credentials are in the system keyring`；
   - `unreadable`：`auth.json cannot be read`；
   - `chatgpt` 但没有邮箱：`the login has no e-mail`；
   - 邮箱不合规：`e-mail <email> is not a valid account name`；
   - 其它：`unrecognized login type`。

源目录不存在、是软链等情况不在这里判断：`read_identity` 会读出 `logged-out`，给出上面的报错。用户填写名称后，由 `migrate_default` 原有预检报告真正的原因。

#### 5.1.3 B3：只打印变化

`accounts.execute(old, new, actions, *, dry_run, verbose=False)`：

- `verbose` 为真：与现在完全相同，逐行打印。
- 否则只打印 `status != UNCHANGED` 的动作（create、update、delete、skip、conflict 都打印；skip 是提示信息，例如共享目录里缺少某项，必须保留）。
- 一行都没打印、且没有冲突时，`info("already up to date")`。dry-run 下同样适用（什么都不会改时也说 already up to date）。
- 冲突的处理与写入顺序不变。

`dispatch` 的收尾（`cli.py:447-455`）：`converge(..., verbose=args.verbose)`；绑定行 `binding_change` 为 `UNCHANGED` 且非 verbose 时不打印。绑定没变时配置也没变，`execute` 已经输出了 `already up to date`。

`migrate._register`、`switch.py:208`、`switch.py:274` 调用 `converge` 时不传 `verbose`，采用默认值：只打印变化的行。

### 5.2 接口变更

- **新增子命令** `login NAME [-- ARGS ...]`：退出码就是 `codex login` 的退出码；账号未登记时为 1。
- **`migrate-default` 的 NAME 改为可选**：原来带 NAME 的用法不变；省略且推导失败时退出码 2。
- **新增选项** `-v` / `--verbose`：init、add、proxy、remove、apply、bind、unbind、env 八条写命令。
- **默认输出变化**：上述写命令不再打印 `unchanged` 行；无变化时打印 `already up to date`。`migrate-default`、`use`、`restore` 内部收敛的输出同样不再含 `unchanged` 行。
- **提示文案**：A1 提示、上手指引、`doctor` 的 `fix:` 中的登录命令改为 `multi-codex login <name>`。
- 不涉及 `docs/reference/*`；`--json`、配置文件、启动命令均不变。

## 6. 备选方案与决策

- **B3 改为全局开关 `multi-codex -v add …`**：否决。要写在子命令前面不符合习惯；改为每条子命令各自的 `-v`，补全也能自动列出。
- **B2 把邮箱转换成合法名称（替换非法字符）**：否决。生成的名字用户预料不到，还可能撞上已有账号；不合规时让用户自己填写更清楚。
- **B1 让 `login` 也支持目录绑定**：否决。登录是一次性动作，必须明确是哪个账号；省略 NAME 时登录错账号的代价比少打一个词大。

## 7. 影响分析

1. **`execute` 的默认输出（B3）**：`execute` 由 `converge` 调用，`converge` 的调用方有 4 处，按动作无截断枚举：`cli.py:447`、`migrate.py:296`、`switch.py:208`、`switch.py:274`。只有 `cli.py:447` 传 `verbose`，其余三处默认不打印 `unchanged`。现有测试中断言 `unchanged` 行的共 3 处（`tests/test_accounts.py:196`、`tests/test_config_copy.py:34`、`tests/test_ergonomics.py:234`），改为加 `-v`；`tests/test_install.py:50`、`:123` 里的 `unchanged` 是安装脚本自己的日志，不受影响。`install.sh --config` 调用 `apply -f`，输出随之变短。
2. **`doctor` 的 `fix:` 文案（B1）**：只改文字，check 的 id 和状态不变；`tests/test_insight.py:437` 断言旧文案，同步修改。
3. **`_split_run_command` 加入 `login`**：只在第一个参数是 `login` 时生效，不影响其它命令。
4. **`migrate-default` 的 NAME 改为可选（B2）**：原来带 NAME 的调用走原路径，`_derive_migrate_name` 不执行。补全的 `ACCOUNT_COMMANDS` 已包含 `migrate-default`，位置参数可选不影响补全。
5. **运行时**：`login` 不加锁、不写文件，执行 `os.execve` 替换为 `sh`（与 `run` 相同）。B2 多读一次 `auth.json`。B3 不新增 IO。
6. **`-v` 与现有选项冲突**：这 8 条子命令现有选项中没有 `-v`（`cli.py` 中 `grep -n '"-v"'` 无命中），顶层的 `--version` 只有长选项。

## 8. 回归测试

新文件 `tests/test_onboarding_commands.py`：

1. `login work`：假 codex 收到的参数是 `login`，`CODEX_HOME` 指向 `~/.cx/work`；`PATH` 中没有启动命令目录也能执行。
2. `login work -- --device-auth`：假 codex 收到 `login`、`--device-auth`。
3. `login wrk`：退出码 1，stderr 含 `did you mean 'work'?`。
4. `-h` 的 Get started 组含 `login`；`COMMAND_GROUPS` 与 parser 子命令集合仍完全相等（沿用 `tests/test_onboarding.py` 的用例）。
5. 三种 shell 的补全脚本都含 `login`；三种 shell 中“遇到 `--` 停止补全”的判断都包含 `login`（`completion.py:82`、`:144`、`:206` 三处）。
6. 未登录的 `add work`：stderr 含 `multi-codex login work`；`PATH` 中有启动命令目录时也是同一条文案。
7. `doctor --json`：未登录账号的 `hint` 为 `multi-codex login <name>`。
8. 不带参数运行（无账号）：stdout 含 `multi-codex login NAME`。
9. `migrate-default`（无 NAME），源目录 `auth.json` 有合法邮箱：迁移成功，账号名为该邮箱，stdout 含 `using account name`。
10. `migrate-default`（无 NAME），分别构造未登录、API key、keyring（`config.toml` 中 `cli_auth_credentials_store = "keyring"`）、邮箱不合规（如带空格）四种情况：退出码 2，stderr 含 `pass a NAME` 和对应原因，`~/.codex` 原样未动。
11. `migrate-default --dry-run`（无 NAME）：推导出名称，输出 dry-run 计划，不移动任何东西。
12. 未完成迁移的续跑：用测试钩子（`tests/test_migrate.py` 已有的中断方法）中断一次带 NAME 的迁移，再执行不带 NAME 的 `migrate-default`，能用记录中的名称完成。
13. B3：`add work` 连续执行两次，第二次 stdout 只有 `already up to date`，没有 `unchanged`；加 `-v` 后有 3 行 `unchanged`。
14. B3：首次 `add work` 的 stdout 含 3 行 `create`，不含 `unchanged`；共享目录缺少某项时 `skip` 行仍然打印。
15. B3：`add work --dry-run`（已存在且无变化）输出 `already up to date`；新账号的 dry-run 仍打印 `create (dry-run)`。
16. B3：`bind work` 执行两次，第二次只输出 `already up to date`；`-v` 时有 `unchanged binding` 行。
17. B3：冲突时（启动命令位置被别的文件占用）仍打印 `conflict` 行，退出码 3。
18. 改为 `-v` 的 3 个旧用例（§4 表）通过；全量 `python3 -m unittest discover -s tests -t tests` 通过，并在编译机用 Python 3.8 独立跑两次。

## 9. 日志 / 观测点

- `login` 不新增日志；账号未登记的报错带 `did you mean`。
- B2：推导成功时 stdout 有 `[multi-codex] using account name '<email>' from …/auth.json`；失败时 stderr 有 `[multi-codex] error: cannot tell the account name from …`。
- B3：无变化时 stdout 有 `[multi-codex] already up to date`；`-v` 可恢复逐行输出用于排查。
