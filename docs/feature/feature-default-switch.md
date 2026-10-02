# 切换默认账号与撤销迁移：use / restore（v0.4）

> 2026-10-01 注记：代码已落地（未提交，等待编译机验证）。
> - 实现：`src/multi_codex/switch.py`。
> - 拦截逻辑：`cli.py` 中的 `_blocked_by_migration`。
> - doctor 报告：`doctor.py` 中的 `_check_migration`。
> - §8 的用例在 `tests/test_switch.py` 中。
>
> 本文其余部分保持方案原文。与 `docs/feature/feature-cli-ergonomics.md` 同一版本落地（那份方案中 `use` / `restore` 的补全依赖本方案）。前置修复：`docs/bugfix/fix-doctor-default-dir.md`。

## 1. 背景

`migrate-default` 把 `~/.codex` 变成指向某个账号目录的软链之后，直接运行 `codex`、Codex 桌面端和 IDE 扩展都会使用这个账号。目前有两件事只能手工做：

- 换一个账号作为默认账号：手工改这个软链；
- 撤销迁移：按 README 的步骤删软链、把目录移回、注销账号。

本方案对应路线中的 U9。

### 1.1 上游事实

已核对 openai/codex 源码，基线 `6b4daafd`，路径省略前缀 `codex-rs/`：

| 事实 | 依据 |
| ---- | ---- |
| 设置了 `CODEX_HOME` 时，Codex 启动时就把它解析成真实路径（canonicalize）；没设置时使用字面路径 `~/.codex`，**不做 canonicalize** | `utils/home-dir/src/lib.rs:43`、`:52-61` |
| 启动时只确定一次 `codex_home`，之后每次读写都按 `codex_home.join(...)` 重新打开文件：auth.json、config.toml、history.jsonl、AGENTS.md 等 | `core/src/config/mod.rs:1513-1516`；`login/src/auth/storage.rs:162`；`message-history/src/lib.rs:85-87` |
| 由上面两条推出：没设置 `CODEX_HOME` 的进程（直接运行的 `codex`、它拉起的 app-server daemon，多半还有桌面端和 IDE 扩展）正在运行时，如果 `~/.codex` 的指向被改了，它已打开的句柄仍指向旧账号，之后新打开的文件却会落到新账号 | 同上 |
| 刷新 token 的流程是“读当前 auth.json → 替换 token → 保存”。软链如果恰好在这中间被改，旧账号的新 token 会写进新账号的 auth.json | `login/src/auth/manager.rs:1600-1622` |
| TUI 默认会自动启动 app-server daemon，daemon 长期运行，并持有 sqlite 连接和会话记录的句柄 | `features/src/lib.rs:965-968`；`tui/src/startup_orchestration.rs:509-566`；`state/src/sqlite.rs:365` |
| 钥匙串条目的键由真实路径计算 | `login/src/auth/storage.rs:243-257` |
| 桌面端、VS Code 扩展如何定位目录 | **未核实**（不在开源仓库中）。只能确认它们拉起的 codex 二进制按上面的规则解析 |

结论：切换默认账号前，**不能有任何进程正在使用当前的默认账号目录**，只提示、不阻止是不够的。占用检查用的是 `lsof`（或扫描 `/proc`），只能看到检查那一刻打开的文件、工作目录和内存映射。上游每次读写都重新打开文件，所以这项检查只能尽力而为：它能可靠地发现长期运行的 Codex 进程（daemon、TUI 持有的数据库连接），但无法证明“完全没有进程会再访问”。

## 2. 目标 / 非目标

**目标**

1. `multi-codex use NAME`：把 `~/.codex` 软链原子地改为指向账号 NAME 的目录；`multi-codex use` 不带参数时显示当前默认账号。
2. `multi-codex restore NAME`：撤销迁移：把账号 NAME 的目录移回 `~/.codex`，并注销该账号；中断后再执行同一命令能继续完成。
3. `list` 显示当前默认账号。

**非目标**

- 不自动关闭或重启桌面端、IDE 扩展、daemon，只列出占用的进程，并提示用户重启。
- 不区分“哪个进程是通过 `~/.codex` 用的、哪个是通过启动命令用的”：两者在 `lsof` 中看到的都是真实路径，一律视为占用。
- `restore` 不做跨文件系统的复制，账号目录与 `~/.codex` 不在同一个文件系统时拒绝，并给出手工步骤。
- 不处理 macOS 沙箱选项 `allow_symlinked_codex_home`（`config/src/config_toml.rs:225-231`）：它与 `migrate-default` 留下的软链是同一个问题，不是本方案新引入的，另行评估。

## 3. 假设与约束

- S = `expand(platform.default_source())`。`platform.default_source()` 返回的是字面字符串 `"~/.codex"`（`src/multi_codex/platform.py:23-24`），必须展开后才能使用，与 `migrate.py:130` 的写法一致。
- 占用检查复用 `migrate._busy_check`（`src/multi_codex/migrate.py:294`），返回 0、1（检查本身失败）或 4。它内部会先对路径做 `realpath`（`platform.py:78`）。
- `use NAME` 与 `restore NAME` 是写命令：加写锁，支持 `--dry-run`；有未完成的迁移时，沿用 `cli.py:143` 的 `_blocked_by_migration` 拒绝执行。
- `use`（不带参数）是只读命令，在 `cli.py:141-142` 分派 `list` 之后、`cli.py:143` 之前另起分支分派：不加锁，有未完成的迁移时也照常运行。

## 4. 涉及模块

| 区域 | 行号锚点（基线 `493818f`） | 改动类型 | 改动点 |
| ---- | ---- | ---- | ---- |
| 前置修复 | `src/multi_codex/doctor.py:116` | 修改 | 展开 `~`，见 `docs/bugfix/fix-doctor-default-dir.md` |
| 新模块 | 新文件 `src/multi_codex/switch.py` | 新增 | `current_default(config)`、`use_account(...)`、`restore_account(...)`，见 §5.1 |
| 迁移模块 | `src/multi_codex/migrate.py:236` `_check_credentials_store` | 修改 | 新增可选参数 `relogin_command: Optional[str] = None`（默认仍为 `codex-<名> login`），提示中的 “after migration” 改为 “after the move”。`migrate-default` 的调用不传这个参数，行为不变 |
| 迁移模块 | `src/multi_codex/migrate.py:294` `_busy_check` | 复用，不修改 | |
| 状态目录 | `<state_dir>/restore-journal.json` | 新增文件 | `restore` 的进度标记，见 §5.1.3 |
| 命令行 | `src/multi_codex/cli.py:170-175` `_blocked_by_migration` | 修改 | 同时检查 restore 标记：标记存在时，除了同名的 `restore` 和 `--dry-run`，其它写命令返回 1 |
| 命令行 | `src/multi_codex/cli.py:138-140` | 修改 | 标记存在时，与未完成的迁移一样，在每条命令开头打印一行提示 |
| 体检 | `src/multi_codex/doctor.py` `_check_migration` | 修改 | 标记存在时报 fail：`unfinished restore of NAME`，建议重跑 `restore NAME` |
| 命令行 | `src/multi_codex/cli.py:96-98` 之后 | 新增 | `use [NAME] [--skip-process-check] [--dry-run]`、`restore NAME [--skip-process-check] [--accept-relogin] [--dry-run]` |
| 命令行 | `src/multi_codex/cli.py:141-143` 之间 | 新增 | 不带参数的 `use` 在这里分派 |
| 命令行 | `src/multi_codex/cli.py:178` `dispatch` | 修改 | 新增 `use NAME`、`restore NAME` 分支 |
| 命令行 | `src/multi_codex/cli.py:309-329`（`list --json`）、`:333-335`（表头三行） | 修改 | `list` 多输出一行 `default: <名称>`；`list --json` 增加 `default_account` 字段 |
| 测试 | 新文件 `tests/test_switch.py` | 新增 | 见 §8 |
| 文档 | `README.md`、`README.zh-CN.md`：命令表、Exit codes 表（3、4 的含义扩展）、新增 Default account 一节，“Undoing a migration by hand” 改为介绍 `restore`；`CHANGELOG.md`；`docs/feature/feature-account-manager.md` 的命令表 | 修改 | |

## 5. 方案

### 5.1 实现要点

记号：S = 展开后的 `~/.codex`；T = `<root>/<NAME>`，即目标账号目录；X = S 当前指向的目录。

#### 5.1.1 `use`（不带参数）

只读，退出码一律为 0，按 S 的状态输出：

| S 的状态 | 输出 |
| ---- | ---- |
| 指向已登记账号目录的软链（按真实路径比较） | 该账号名 |
| 普通目录 | `(not migrated: ~/.codex is a real directory)` |
| 不存在 | `(none)` |
| 指向其它位置的软链 | `(unregistered: <真实路径>)` |

#### 5.1.2 `use NAME`

| 情况 | 结果 |
| ---- | ---- |
| NAME 未登记，或 T 不是真实目录（`entry_kind(T) != KIND_DIR`，不跟随软链） | 退出码 1 |
| S 是指向 T 的软链（按真实路径比较） | 输出 `already the default account`，退出码 0 |
| S 是指向另一个已登记账号目录 X 的软链 | 对 X 做占用检查：有占用返回 4，并列出进程；检查本身失败返回 1；否则原子地改指向 T |
| S 不存在 | 直接创建软链 S → T |
| S 是普通目录 | 冲突，返回 3，提示先执行 `migrate-default` |
| S 是指向未登记位置的软链 | 冲突，返回 3，不做任何改动 |

- **原子改指向**：在 S 所在目录创建临时软链 `<S>.multi-codex-tmp.<pid>`，指向 T；执行测试钩子 `use-replace`（沿用 `migrate._test_hook`）；然后用 `os.replace` 替换 S。任何时刻 S 要么指向 X，要么指向 T。`use_account` 内部捕获 `Exception`（包括测试注入的 `InjectedFailure`），先删除临时软链，再输出 `error phase=use`，返回 1。
- `--skip-process-check` 跳过占用检查，并在 stderr 警告：正在运行的 Codex 可能把两个账号的文件混在一起。
- 完成后输出 `default account is now NAME (~/.codex -> <T>); restart the Codex desktop app and IDE extensions`。当前 shell 设置了 `CODEX_HOME` 时，再警告：直接运行 `codex` 不会使用 `~/.codex`。
- 不检查钥匙串：`use` 不改变任何账号目录的真实路径，钥匙串的键不受影响。

#### 5.1.3 `restore NAME`

**进度标记**：开始移动之前，原子写入 `<state_dir>/restore-journal.json`，内容为 `{"name": NAME, "source": S, "target": T, "relogin_needed": bool}`；第 3 步的 `converge` 返回 0 之后才删除。返回 1 或 3 时（例如其它账号的启动命令冲突，`accounts.py:159-161`）保留标记，此时处于状态 C，修好问题后重跑即可。

“标记一致”指三项都相同：标记中的 name 与 NAME 相同（大小写不敏感），source 等于当前的 S，target 等于当前的 T。中断后的状态 A′、B、C 必须标记一致才会继续，避免把“手工删掉了账号目录”误判为“restore 做到一半”，也避免 HOME 或根目录配置变化后按旧标记去移动目录。是否需要提示重新登录，也从标记中读取。

| 状态 | 条件 | 接下来执行 |
| ---- | ---- | ---- |
| A | 没有标记；S 是指向 T 的软链；`entry_kind(T) == KIND_DIR`；NAME 已登记 | 检查 → 写标记 → 钩子 `restore-marked` → 第 1 步 → 第 2 步 → 第 3 步 → 删除标记 |
| A′ | 标记一致；S 是指向 T 的软链；`entry_kind(T) == KIND_DIR`（写完标记、还没删软链时中断） | 检查（不含钥匙串检查，结果以标记为准）→ 第 1 步 → 第 2 步 → 第 3 步 → 删除标记 |
| B | 标记一致；S 不存在；`entry_kind(T) == KIND_DIR` | 检查（不含钥匙串检查，结果以标记为准）→ 第 2 步 → 第 3 步 → 删除标记 |
| C | 标记一致；S 是普通目录；T 不存在 | 第 3 步 → 删除标记 |
| D | 没有标记；NAME 未登记；T 不存在 | 如果 bin_dir 中还有 NAME 的受管启动命令，用第 3 步的 `converge` 删除它，并输出 `restore of NAME finished`，退出码 0；否则输出 `NAME is not registered`，退出码 1 |
| 其它 | 例如：有标记但不一致；S 指向别处；T 本身是软链；S 是普通目录而 T 仍存在 | 冲突，返回 3，不做任何改动，并说明原因。没有标记、而 S 已被 `use` 改指向其它账号时，提示先执行 `use NAME` |

- **其它写命令的拦截**：标记存在期间，除了 `restore <标记中的名字>` 和带 `--dry-run` 的命令之外，其它写命令（`init`、`add`、`proxy`、`remove`、`apply`、`env` 的写操作、`use NAME`、`migrate-default`）一律拒绝，返回 1，并提示先重跑 `restore <名字>`。做法是扩展现有的 `_blocked_by_migration`（`cli.py:170-175`）：函数内部每次都重新读取标记，这样拿到写锁之后的第二次检查（`cli.py:147`）也能看到另一条 `restore` 刚写出的标记。标记读不出来（损坏）时，与迁移事务记录损坏（`JournalError`）一样处理：报错并返回 1，提示手工检查后删除标记文件。原因：restore 中途 S 可能不存在（状态 B）或是普通目录（状态 C），这时 `use` 会新建 S，`migrate-default` 的预检也能通过（`migrate.py:218-228` 只拦同名已登记的情况），两者都会让 restore 再也无法完成。
- **检查**：
  1. T 与 S 所在目录必须在同一个文件系统上（比较 `st_dev`），否则返回 3，并给出手工步骤；
  2. 状态 A 做钥匙串检查：调用 `migrate._check_credentials_store(T, NAME, accept_relogin, relogin_command="codex login")`。撤销迁移会把真实路径从 T 改回 S，与迁移一样会让钥匙串里的登录失效；被拒绝时返回 3，`--accept-relogin` 放行，`relogin_needed` 写进标记；
  3. 对 T 做占用检查，有占用时返回 4，检查失败时返回 1。
- **第 1 步**：删除软链 S（`os.unlink`，只删软链本身）。
- **第 2 步**：`os.rename(T, S)`。在第 1 步和第 2 步之间，如果有人恰好运行了 `codex`，它会新建一个空的 `~/.codex`。此时 `rename` 会成功并替换掉这个空目录（目录非空时才会失败，失败时返回 1，提示先把新建的 `~/.codex` 移走，再重跑）。
- **第 3 步**：注销 NAME。构造去掉 NAME 的新配置，调用 `accounts.converge(..., orphan_scope={NAME.casefold()})`，与 `remove` 命令相同，会删除启动命令 `codex-NAME`。原账号目录中由本工具管理的共享软链会随目录一起移到 S，之后不再受管，但仍然指向共享目录，照常可用。`converge` 先写配置、再删启动命令；如果在两者之间中断，下次进来就是状态 D，会把剩下的启动命令删掉。
- **`--dry-run`**：照常执行检查，然后按状态输出将要执行的步骤（`would unlink ~/.codex`、`would move <T> to ~/.codex`、`would unregister NAME and delete codex-NAME`）。不调用 `converge(dry_run=True)`：它会列出“删除 T 中的共享软链”，但真正执行时这些软链已经随目录移走了。
- **完成后**：输出 `restored NAME to ~/.codex`；标记中 `relogin_needed` 为真时，再提示 `log in again: codex login`。

#### 5.1.4 `list`

- 给人看的输出：在 `shared.dir:` 一行之后加一行 `default: <内容>`，内容与 §5.1.1 相同。
- `--json`：增加 `"default_account"` 字段。只有 S 是指向已登记账号的软链时，值为账号名，其它情况为 null。

### 5.2 接口变更

| 类型 | 接口 | 兼容性 |
| ---- | ---- | ---- |
| 新增 | `use [NAME] [--skip-process-check] [--dry-run]` | 新增 |
| 新增 | `restore NAME [--skip-process-check] [--accept-relogin] [--dry-run]` | 新增 |
| 新增 | 状态文件 `<state_dir>/restore-journal.json` | 新增，工具内部使用 |
| 修改 | `list` 多一行 `default:` | 给人看的输出，不承诺格式稳定 |
| 新增 | `list --json` 的 `default_account` 字段 | 新增字段，`version` 不变 |
| 扩展 | 退出码 3 增加“`use` / `restore` 状态冲突、跨文件系统、钥匙串拒绝”；退出码 4 增加“要切换或移回的账号目录正被占用” | 数值与含义类别不变，README 的 Exit codes 表同步更新 |

仓库中没有 `docs/reference/` 目录，不涉及 reference 章节的同步检查。

## 6. 备选方案与决策

| 问题 | 方案 | 结论 |
| ---- | ---- | ---- |
| 正在使用旧账号的进程怎么办 | 只警告，照常切换（codex-homes 的做法） | 否决：§1.1 已说明，切换后正在运行的进程会把两个账号的文件混在一起，甚至可能把一个账号的 token 写进另一个账号 |
| 正在使用旧账号的进程怎么办 | 读取每个占用进程的环境变量，设置了 `CODEX_HOME` 的不算占用 | 否决：在 macOS 上读取其它进程的环境变量需要 sysctl `KERN_PROCARGS2`，用标准库做不到稳定可靠；先保守地全部视为占用，必要时用 `--skip-process-check` |
| restore 中断后如何续跑 | 只靠文件状态判断 | 否决：评审发现，“手工删掉了账号目录”和“restore 做到一半”的文件状态相同，会误把账号注销；而且无法知道是否需要提示重新登录 |
| restore 中断后如何续跑 | 进度标记文件 | 采纳（§5.1.3） |
| restore 的目录移回方式 | 支持跨文件系统复制 | 否决：账号目录默认在 `~/.cx`，与 `~/.codex` 在同一个文件系统；跨文件系统时给出手工步骤即可 |

## 7. 影响分析

- **桌面端、IDE 和直接运行的 `codex`**：`use` 之后，这些入口会使用新账号（前提是它们按 `~/.codex` 定位目录，桌面端和 IDE 这一点未核实）。正在运行的实例应先关闭；占用检查会尽力发现它们（边界见 §1.1）。
- **启动命令**：启动命令设置 `CODEX_HOME=<账号目录>`，Codex 会把它解析成真实路径，与 `~/.codex` 无关，所以 `use` 不影响任何 `codex-<名>` 的行为。但通过启动命令运行的会话，也会被占用检查拦下（见 §2 非目标）。
- **`migrate-default`**：迁移完成后 S 是指向 T 的软链。`use` 改指向另一个账号之后，再执行 `migrate-default 原名称`，会走现有预检中“S 是指向别处的软链”的分支并返回冲突。这是合理的，因为迁移已经完成（§8 第 9 条）。
- **事务记录**：`restore-journal.json` 与迁移的 `migrate-journal.json` 是两个独立的文件。restore 标记存在期间，`_blocked_by_migration` 会拦下其它写命令，包括 `migrate-default`（§5.1.3）。反过来，迁移事务记录存在时 `restore` 同样被拦下（现有逻辑）。所以两者不会同时推进。`doctor` 的 migration 一项同时报告这两种未完成状态。
- **`doctor`**：前置修复（展开 `~`）之后，`default-dir` 能正确报告 S 指向哪个账号。如果对当前默认账号执行了 `remove`，S 会指向一个已注销的目录，`doctor` 报 warn。本方案不改变 `remove` 的行为，在 README 中说明。
- **`_check_credentials_store`**：只新增一个可选参数，`migrate-default` 的调用不传它，输出与 v0.3.0 相同（只有 “after migration” 改成了 “after the move”，现有用例只断言 `--accept-relogin`、`log in again` 等关键字，§8 第 14 条回归）。
- **运行时**：两个命令各执行一次占用检查（`lsof` 或扫描 `/proc`，与迁移相同），外加几次文件系统操作。

## 8. 回归测试

新文件 `tests/test_switch.py`，全部在临时 HOME 中运行。需要 mock 的用例（第 6、11 条）采用 `tests/test_migrate.py` 中 `InProcessTest` 的方式，在进程内调用；其余用例照常以子进程运行 CLI。占用检查沿用 `BusyCheckTest` 的做法：起一个真实子进程持有文件。

1. `use` 不带参数：S 分别为指向已登记账号的软链、普通目录、不存在、指向未登记位置的软链时，输出对应内容，退出码都是 0；有未完成的迁移事务时照常可用。
2. `use b`（S → a）：S 改为指向 b，退出码 0；再执行一次，输出 already，退出码 0。
3. `use b` 时有进程持有 a 中的文件：返回 4，列出该进程，S 仍指向 a；加 `--skip-process-check` 后切换成功，stderr 有警告。
4. 冲突与错误：S 是普通目录、S 指向未登记位置时都返回 3，什么都不改；`use ghost` 返回 1；T 是软链时返回 1。
5. `use --dry-run`：什么都不改。
6. 原子性：`MULTI_CODEX_TEST_FAIL_AT=use-replace` 时返回 1，S 仍指向 a，没有残留的临时软链。
7. `restore a`（状态 A）：`~/.codex` 变成普通目录，内容与原来的 T 相同（比较目录树）；`a` 已注销，`codex-a` 已删除；目录中的共享软链还在；`restore-journal.json` 不存在。
8. 续跑：在写完标记、第 1 步、第 2 步之后分别加测试钩子 `restore-marked`、`restore-unlinked`、`restore-renamed`；用 `MULTI_CODEX_TEST_CRASH_AT` 在这三处杀掉进程，分别得到状态 A′、B、C，重跑同一命令都能完成。状态 D 手工构造：从 `config.json` 中删掉 `a`，保留 `codex-a`，`restore a` 删掉它并返回 0。全部完成后再执行一次，返回 1（`not registered`）。
9. `use` 之后执行 `migrate-default a`：返回 3（现有行为未被破坏）。
10. 防误判：没有标记时，手工删掉 T，`~/.codex` 是普通目录，`restore a` 返回 3，`a` 仍然登记；`restore ghost`（从未登记）返回 1。
10a. 标记存在时的拦截：在状态 B 下，`use b`、`migrate-default x`、`add y` 都返回 1，提示先执行 `restore a`；`use b --dry-run` 照常运行；`doctor` 的 migration 项为 fail。重跑 `restore a` 完成后，这些命令恢复正常。
10b. 标记不一致：手工把标记中的 target 改成别的路径，`restore a` 返回 3。
10c. 第 3 步失败：制造一个其它账号的启动命令冲突，`restore a` 返回 3，标记保留；解决冲突后重跑，完成。
11. restore 的拒绝情况：
    - 有进程占用 T：返回 4；
    - 存储模式为 keyring：返回 3，提示中是 `codex login` 而不是 `codex-a login`；加 `--accept-relogin` 后成功，完成时提示重新登录；在状态 B 中断后续跑，也仍然提示；
    - 跨文件系统（进程内用 mock 让 `st_dev` 不同）：返回 3，并给出手工步骤；
    - S 被 `use` 改指向 b 时执行 `restore a`：返回 3，提示先执行 `use a`。
12. `restore --dry-run`：输出三条 `would ...`，不包含删除共享软链的动作；什么都不改。
13. `list`：输出包含 `default: a`，`list --json` 的 `default_account` 为 `"a"`；S 是普通目录时为 null。
14. 回归：`migrate-default` 的 `KeyringStoreTest` 全部通过；`doctor` 前置修复的 4 条用例（见 bugfix 方案 §9）通过；现有用例全部通过；在编译机上用 Python 3.8 运行全部用例；CI 通过。
15. 人工实机项（在本机执行，事先征得用户同意）：本机的 `~/.codex` 目前不存在，可以先 `use` 某个账号，用桌面端或 VS Code 扩展确认已切到该账号，然后删掉软链恢复原状。

## 9. 日志 / 观测点

- `use` 成功：stdout 输出 `[multi-codex] default account is now NAME (~/.codex -> <T>)`。被占用时，stderr 中每个进程一行 `pid=... command=... usage=... path=...`（复用 `_busy_check` 的格式）。
- `restore`：每一步输出一行，分别为 `step=unlink`、`step=rename`、`step=unregister`；从中途继续时，先输出 `resuming restore of NAME from state B` 或 `from state C`。
- 失败时一律输出 `[multi-codex] error phase=<use|restore> path=<路径>: <原因>`。
