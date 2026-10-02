# multi-codex v0.8：日常操作补齐

> 2026-10-02 注记：已落地（PR #22，main `3a94b42`），随 v0.8.0 发布。主要实现位置：`src/multi_codex/cli.py` 的 `_add_account_options`、`_unknown_command`、`dispatch` 的 add/set 分支、`_hint_shared_dir`、`_print_list_summary`、`_usage_cell`；`src/multi_codex/shellpath.py`；`install.sh` 的 PATH 提示。测试：`tests/test_everyday.py`。基线：main `e79b4b1`（v0.7.0）。
>
> 用户的决定（2026-10-02）：
>
> - 先做 v0.8，范围共 5 项：`set` 命令、`--shared [DIR]`、子命令拼写建议、`list` 简表加 `--verbose`、按 shell 给出 PATH 提示。
> - `add` 保持兼容：对已登记的账号照旧可以修改选项，README 改为推荐使用 `set`。
> - `list` 简表显示额度，数据取自本机会话记录，不联网。
>
> 第 1 轮评审：没有高级问题，有 5 条中级、12 条低级；第 2 轮：只有 5 条低级。均已修订。其中“共享目录缺项时默认不打印 skip”一项已从方案中删除：它不在用户选定的范围内，而且会推翻 `feature-onboarding-commands.md` 中“skip 必须保留”的约定。

## 1. 背景

- **修改已有账号也要用 `add`**：`dispatch` 的 `add` 分支（`cli.py:394-412`）对已登记的账号就是“修改选项”，命令名容易误导。
- **开启共享要两步**：`add NAME --shared` 在 `shared.dir` 未设置时会判为冲突（`shared.py:31-33`，提示先执行 `multi-codex init --shared-dir DIR`）。共享目录里一个条目都没有时，每个条目各打印一行 `skip`（`shared.py:36-39`），但不告诉用户该往哪里放什么。
- **子命令拼错时只列出全部选项**：`main` 直接调用 `parser.parse_args`（`cli.py:272`），argparse 报 `invalid choice` 并列出全部 20 个子命令。账号名拼错时已经有建议（`config.py:104-105`），子命令拼错时还没有。
- **`list` 一张大表**：固定输出 4 行全局信息和 7 列表格（`cli.py:726-743`），看不出哪个账号有问题，也看不到额度。
- **PATH 提示是通用写法**：`_hint_after_setup`（`cli.py:539-540`）、`doctor` 的 `path` 检查（`doctor.py:112-113`）、`install.sh:283` 都只说“加进 shell 配置文件”，没有给出针对用户 shell 的具体命令。

## 2. 目标 / 非目标

**目标**

1. 新增 `set NAME …`：只修改已登记的账号，选项与 `add` 相同。
2. `--shared` 可以带目录：不带值时，`shared.dir` 已设置就沿用，未设置就设为 `~/.codex-shared`；带 `DIR` 时把全局 `shared.dir` 改为 `DIR`。本次命令开启共享后，如果共享目录里一个条目都没有，提示该放什么。
3. 子命令拼错时给出建议；`list` 默认输出简表（含额度与状态），`--verbose` 输出与现在完全相同的完整信息；PATH 提示按用户的 shell 给出一行可直接执行的命令。

**非目标**

- `add` 的行为不变：对已登记账号照旧可以修改选项（用户决定保持兼容）。`COMMAND_SUMMARY["add"]` 的文字保留 “or change its options”。
- 写命令输出中的 `skip` 行照旧打印（`feature-onboarding-commands.md` 中 B3 的约定不变）。
- 不自动创建共享目录，也不往里面复制任何内容。
- `list --json` 的输出不变。
- `proxy`、`env`、`remove` 等保持独立命令，不并入 `set`。
- 不修改用户的 shell 配置文件：PATH 只给提示。`install.sh` 结尾的补全提示（`install.sh:294-298`，macOS 的 bash 也写 `~/.bashrc`）本次不改。
- `apply -f` 读入的配置里 `shared.dir` 为空时，不自动补默认值：声明式配置以文件为准。
- `init --shared-dir` 相对路径的处理不改（§7 有说明）。

## 3. 假设与约束

- **`--shared` 改成可选值**：
  - argparse 写法为 `nargs="?"`、`const=True`、`metavar="DIR"`、`dest="shared_to"`；`--no-shared` 改为 `dest="no_shared"`（`store_true`）；两者仍在同一个互斥组里。
  - 已用 Python 3.11 最小复现核对过以下写法：
    - `add work --shared` 得到 `True`；
    - `add work --shared ~/s2` 得到 `'~/s2'`；
    - `add --shared ~/s work` 解析正常；
    - `--shared --adopt`、`--shared --config-from x`、`--shared -v` 中，后面的选项都不会被当成 DIR；
    - `--shared --no-shared` 无论哪种顺序都退出 2；
    - `add --shared work`（选项在账号名前面）中，`work` 会被当成 DIR、账号名缺失，argparse 报 `the following arguments are required: name` 并退出 2。

    README 一律写成 `add NAME --shared`。
- **`DIR` 的形式与规范化**：
  - 必须像一个路径，即含 `/`，或以 `~`、`.` 开头；否则判为参数错误，避免把账号名之类的单词误当成目录。
  - 以 `~` 开头的值原样写入配置，用到时再展开；其它值先 `os.path.abspath`，再写入配置。原因：`fsutil.expand`（`fsutil.py:23-25`）按每次运行时的当前目录解析相对路径，原样写入相对路径后，换一个目录执行 `apply` 就会把全部共享链接判为指向别处。
- **额度数据**：
  - 复用 `usage.local_snapshot`（`usage.py:193`），只读本机会话日志，不启动进程、不联网。
  - `_candidate_files` 最多取 20 个会话文件。本机 3 个账号实测，每个账号耗时 0–17 毫秒。
  - `local_snapshot` 永远返回 `ok=True`，读取错误都被吞掉（`usage.py:119-122`、`:188-189`），所以简表不设“读取出错”的取值。
  - 窗口长度不固定：本机实测有账号只有一个 43200 分钟（30 天）的窗口。所以简表不固定成 5h、7d 两列，而是按快照中实际存在的窗口显示，标签用 `usage._window_label`（`usage.py:393`；对 300、10080、43200 分钟分别返回 `5h`、`7d`、`30d`，已实测）。
- **判断 macOS**：本仓库有自己的 `platform` 模块，因此用 `sys.platform == "darwin"`，不导入标准库的 `platform`。
- **复用现有逻辑**：
  - 修改 `shared.dir` 后，受管链接改指向新目录，由现有的 `old_shared_dir` 分支完成；
  - 新目录里缺少的条目，原来的受管链接会被删除（`shared.py:74-88`），本方案不改这个行为，只在提示里如实说明；
  - 账号名拼错的建议沿用 `config.not_registered`（`config.py:96-112`）。

## 4. 涉及模块

| 区域 | 行号锚点（基线 `e79b4b1`） | 改动类型 | 改动点 |
| ---- | ---- | ---- | ---- |
| `src/multi_codex/cli.py` | `COMMAND_GROUPS` :31-58 | 修改 | Everyday 组第一行加 `("set", "change an existing account: proxy, sharing, adopted links")` |
| `src/multi_codex/cli.py` | `build_parser` 的 `add` :117-131 | 修改 | 选项抽成 `_add_account_options(parser)`，供 `add` 与 `set` 共用；`--shared [DIR]`、`--no-shared` 改用新的 `dest` |
| `src/multi_codex/cli.py` | `build_parser`，`add` 之后（:131 后） | 新增 | `set` 子命令 |
| `src/multi_codex/cli.py` | `build_parser` 的 `list` :149-150 | 修改 | 增加 `-v/--verbose` |
| `src/multi_codex/cli.py` | `main` :270 与 :271 之间 | 新增 | 调用 `_unknown_command`（§5.1.3） |
| `src/multi_codex/cli.py` | `main` 中 `list` 的分派 :306-307 | 修改 | 把 `args.verbose` 传给 `cmd_list` |
| `src/multi_codex/cli.py` | `dispatch` 的 `add` :394-412 | 修改 | `add` 与 `set` 共用一段（§5.1.1）；`--shared` 的目录处理 |
| `src/multi_codex/cli.py` | `dispatch` 末尾 :470-481 | 修改 | 收敛成功后输出共享目录的提示（§5.1.2）；“下一步登录”提示仍只给 `add` |
| `src/multi_codex/cli.py` | `--adopt` 报错 :407 | 修改 | 文字中的 `add --shared` 改为 `--shared` |
| `src/multi_codex/cli.py` | `_hint_after_setup` :537-540 | 修改 | PATH 提示改用 `shellpath.path_hint`，保留 `is not on PATH` 这句 |
| `src/multi_codex/cli.py` | `cmd_list` :680-745 | 修改 | 新增 `verbose` 参数；`verbose` 时走原代码 :726-745，输出逐字不变；否则输出简表（§5.1.4） |
| `src/multi_codex/shellpath.py` | 新文件 | 新增 | `add_to_path_command`、`path_hint`（§5.1.5） |
| `src/multi_codex/doctor.py` | `_check_path` :109-113 | 修改 | `hint` 改用 `shellpath.path_hint` |
| `install.sh` | :281-284 | 修改 | 按 `$SHELL` 与 `uname` 给出同一张表的命令（§5.1.5） |
| `src/multi_codex/config.py` | `DEFAULT_SHARED_ITEMS` :18 之后 | 新增 | `DEFAULT_SHARED_DIR = "~/.codex-shared"` |
| `src/multi_codex/completion.py` | `ACCOUNT_COMMANDS` :16-17 | 修改 | 加 `set` |
| `tests/test_insight.py` | :85、:111、:135 | 修改 | 三处断言完整表格，改为 `list --verbose` |
| `tests/test_onboarding.py` | :228 | 修改 | `root:` 行只在 `--verbose` 输出中，改为 `list --verbose` |
| `tests/test_ergonomics.py` | :221 | 修改 | 断言表格最后一行以 ` ok` 结尾，改为 `list --verbose` |
| `tests/test_everyday.py` | 新文件 | 新增 | §8 的新用例 |
| `README.md`、`README.zh-CN.md` | Install（:35-55）、快速开始第 4 步（:87-93）、Common tasks 表（:106-121）、Good to know（:125）、Commands 表（:132-155）、表后关于 `-v` 与只读命令的说明段（:156）、Login and usage 一节（:158-164，`list` 不再有 PLAN 列，PLAN 改为在 `list --verbose` 中）、Shared resources 一节（:313-322） | 修改 | `set`、默认共享目录与 `--shared DIR`、`list --verbose`（`list` 的 `-v` 表示完整输出，与写命令的 `-v` 含义不同，需写明）、PATH 提示；中文版对应章节同步 |
| `CHANGELOG.md` | Unreleased | 修改 | Added / Changed |

**依赖 `list` 文本输出的测试全集**（`grep -rn '"list"' tests/ | grep -v -- '--json'`，不截断）共 13 处：

- 需要改为 `--verbose` 的 5 处：见上表。
- 简表下仍然成立的 8 处：
  - `test_accounts.py:96`：STATUS 含 `missing-dir`；
  - `test_accounts.py:290`：断言不含 `.migration`；
  - `test_accounts.py:367`：以 `("list",)` 元组经 `run_cli(*command)` 调用，只检查退出码；
  - `test_accounts.py:379`：只检查退出码；
  - `test_accounts.py:410`：检查 stderr 中的告警；
  - `test_ergonomics.py:225`：STATUS 含 `launcher stale`；
  - `test_install.py:61`：`self.tool("list")`，只检查退出码；
  - `test_switch.py:135`：简表保留 `default:` 行。

**`args.shared` 的读取点全集**（`grep -rn "args\.shared\b" src/`，不截断）只有 `cli.py:402-403` 一处；`cli.py:406` 读的是 `account.shared`，不受影响。

## 5. 方案

### 5.1 实现要点

#### 5.1.1 `set` 与 `add` 共用

`_add_account_options(parser)` 加入 `add` 现有的全部选项：`--proxy`、互斥组（`--shared [DIR]` / `--no-shared`）、`--adopt`、`--config-from`、`--dry-run`、`-v/--verbose`。`add` 和 `set` 都调用它。

`dispatch` 中：

```python
elif args.command in ("add", "set"):
    name = _checked_name(args.name)
    account = new.find(name)
    if account is None:
        if args.command == "set":
            error(_not_registered_for_set(new, name))      # 退出 1；先于“没有选项”判断
            return accounts.EXIT_ERROR
        account = Account(name)
        new.accounts[name] = account
    if args.command == "set" and not _any_account_option(args):
        raise UsageError("nothing to set; give at least one option, e.g. `multi-codex set {} --proxy 7901`".format(name))
    old_shared_dir = new.shared_dir                         # 给 §5.1.2 的提示用
    if args.proxy is not None: ...                          # 原样保留
    if args.shared_to is not None:
        if args.shared_to is not True:
            new.shared_dir = _checked_shared_dir(args.shared_to)   # 修改全局共享目录
        elif not new.shared_dir:
            new.shared_dir = DEFAULT_SHARED_DIR                     # "~/.codex-shared"
        account.shared = True
    elif args.no_shared:
        account.shared = False
    ...                                                     # --adopt、--config-from 原样保留
```

- **判断顺序**：`set` 遇到未登记的账号时退出 1，这一判断先于“没有选项”（退出 2）。
- **`_not_registered_for_set(config, name)`**：取 `config.not_registered(name)`。它在找到相近账号名（`config.py:103-105`）、或者还没有任何账号（`config.py:106-108`）时，已经附带了下一步建议；只有这两种情况都不成立时，才追加 ``; use `multi-codex add NAME` to create it``，避免出现两条建议。判断这两种情况时按条件重新计算（同样的 casefold + `difflib.get_close_matches(cutoff=0.6)` 规则，以及 `config.accounts` 是否为空），不解析 `not_registered` 返回的文字。
- **`_any_account_option(args)`**：`proxy`、`shared_to`、`no_shared`、`adopt`、`config_from` 中任一项有值即为真。
- `set` 不新建账号，所以没有“下一步登录”提示；`dispatch` 末尾 `:479` 的条件维持 `args.command == "add"`。

#### 5.1.2 `--shared [DIR]` 与提示

- **`_checked_shared_dir(value)`**：
  - `value` 不含 `/`、也不以 `~` 或 `.` 开头时，抛出 `UsageError("--shared DIR must be a path (contains '/' or starts with '~' or '.'), got {!r}")`，退出 2；
  - 以 `~` 开头时原样返回；
  - 其它情况返回 `os.path.abspath(value)`。
- **收敛成功后的提示**：只在 `add` / `set` 退出 0、且不是 `--dry-run` 时输出，用 `info()`，路径一律用 `display_path(expand(...))` 显示。
  1. **共享目录变了**：旧值为空，或 `expand(new.shared_dir) != expand(old_shared_dir)` 时（包括未设置时用了默认值）输出下面这句；旧值为空时写 `not set`。只是写法不同、展开后是同一目录时不输出（配置里的文字仍会按新写法保存，链接不变）。

     ```text
     shared directory is now {新} (was {旧}); every shared account follows it, and links to items missing there are removed
     ```

  2. **共享目录里什么都没有**：本次命令带了 `--shared`（`args.shared_to is not None`），`new.shared_items` 非空，并且共享目录不存在、或 `new.shared_items` 中每一项在共享目录里都不存在时，输出下面这句。存在与否的判定与 `shared.py:37` 一致，即 `entry_kind(...) != KIND_MISSING`；条目名取自 `new.shared_items`，用 `, ` 连接。

     ```text
     note: {目录} has none of {条目} yet; put what every account should share there, then run `multi-codex apply`
     ```

#### 5.1.3 子命令拼写建议

`_unknown_command(parser, argv)`：在 `main` 中插在 :270 与 :271 之间，即“不带参数时的上手指引”之后、`try` 与 `parse_args` 之前。此时 `argv` 非空，并且 `_split_run_command` 已经切掉了 `run`、`code`、`login` 后面 `--` 之后的部分。

1. 取第一个不以 `-` 开头的参数作为命令名；没有这样的参数时返回 `None`，交给 argparse（`-h`、`--version` 等不受影响）。
2. 候选命令名取自解析器中 `_SubParsersAction.choices`；命令名是其中之一时返回 `None`。
3. 否则用 `difflib.get_close_matches(word, names, n=1, cutoff=0.6)` 找最接近的命令：
   - 找到时报 `unknown command 'lsit'; did you mean 'list'?`；
   - 找不到时报 `unknown command 'xyz'; run multi-codex -h for the list`。

   两种情况都经 `error()` 输出到 stderr，退出码 2，与 argparse 对非法子命令的退出码一致。

#### 5.1.4 `list` 简表

不带 `--verbose` 时输出：

```text
default: work
NAME  LOGIN          PROXY                  SHARED  USAGE           STATUS
work  w@example.com  http://127.0.0.1:7901  yes     5h 23%, 7d 41%  ok
home  -              inherit                no      -               not logged in
run `multi-codex doctor` for details
```

- **`default:` 行**：与现在完全相同，即 `switch.describe_default(config)[1]`。
- **LOGIN**：`identity.display_login(found)`；账号目录不存在（`found is None`）时为 `-`。
- **PROXY**、**SHARED**：与现在相同。
- **USAGE**：账号目录存在时调用 `usage.local_snapshot(name, directory)`。
  - 选用的额度：在“带窗口的额度”中，优先选 `limit_id == "codex"` 的那项，否则选第一项；没有带窗口的额度时为 `-`。
  - 每个窗口格式为 `"{} {:.0f}%".format(_window_label(window_minutes), used_percent)`，取整方式与 `format_result` 的 `{:>3.0f}` 一致；多个窗口用 `, ` 连接。
  - `resets_at` 不晚于当前时间的窗口（快照之后已经重置）显示为 `<标签> reset`，判定与 `format_result`（`usage.py:446-449`）一致。
  - `result.sessions_shared` 为真（`sessions` 是软链，额度可能属于别的账号）时，单元格末尾加 `*`，表格之后加一行 `* sessions is shared with other accounts; usage may belong to another account`。
  - 账号目录不存在或没有快照时为 `-`。
- **STATUS**：按以下顺序收集问题，用 `, ` 连接；没有问题时为 `ok`。
  1. `missing-dir`：账号目录不存在。此时不再判断登录状态。
  2. `launcher <状态>`：`accounts.launcher_status` 不是 `ok` 时，即 `missing`、`stale` 或 `conflict`。
  3. `not logged in`：`found` 不为 `None`，并且 `found.login == identity.LOGIN_LOGGED_OUT`。
- **表格之后**：任何一行的 STATUS 不是 `ok` 时，在 stdout 加一行 ``run `multi-codex doctor` for details``。
- 重复登录告警（`_warn_duplicates`）与隔离告警（`migrate.warn_isolation_env`）照旧输出到 stderr。
- 配置不存在时的提示、没有账号时的 `no accounts registered`，两种模式都与现在相同。

`list --verbose`：走原代码 `cli.py:726-745`，输出逐字不变。

#### 5.1.5 PATH 提示

新模块 `shellpath.py`：

```python
def add_to_path_command(directory: str, shell: Optional[str], is_macos: bool) -> Optional[str]
def path_hint(directory: str, shell: Optional[str], is_macos: bool) -> str
def current_path_hint(directory: str) -> str   # 取当前进程的 $SHELL 与 sys.platform，cli 与 doctor 共用
```

`directory` 必须是 `expand()` 之后的绝对路径。写进 rc 文件的那一行里 `~` 不会被展开，所以调用方不能传 `display_path` 的结果。

| `shell` 的文件名 | `add_to_path_command` 返回 |
| ---- | ---- |
| zsh | `echo 'export PATH="<目录>:$PATH"' >> ~/.zshrc` |
| bash，且 `is_macos` | `echo 'export PATH="<目录>:$PATH"' >> ~/.bash_profile` |
| bash，其它系统 | `echo 'export PATH="<目录>:$PATH"' >> ~/.bashrc` |
| fish | `fish_add_path '<目录>'` |
| 其它或未设置 | `None` |

目录中含 `'`、`"`、`$`、`` ` ``、`\` 任一字符时返回 `None`：这些字符写进单引号或双引号后，会改变命令的含义。

`path_hint`：命令不为 `None` 时返回 `run: <命令>, then open a new terminal`；否则返回 `add it to PATH in your shell profile`。

使用位置：

- **`_hint_after_setup`**（`add` 与 `migrate-default` 都会调用，`cli.py:370`、`:480`）：

  ```python
  warn("{} is not on PATH, so `codex-{}` will not be found; {}".format(display_path(bin_dir), name, path_hint(bin_dir, ...)))
  ```

  `is not on PATH` 这句保留，现有用例依赖它。
- **`doctor` 的 `path` 检查**：`Check.hint`（`doctor.py:32`）改为 `path_hint(bin_dir, ...)`。
- **`install.sh`**：在 `case ":${PATH}:"` 的 `*)` 分支里，用 `case "$(basename "${SHELL:-}")"` 与 `[ "$(uname)" = Darwin ]` 实现同一张表。为通过 CI 上 0.8、0.9 版 shellcheck，写法规定如下：
  - rc 文件名直接写在 `log` 的双引号字符串里，例如 `"... >> ~/.zshrc"`，不先赋值给变量（赋值写成 `rc="~/.zshrc"` 会触发 SC2088）；
  - `$PATH` 写成 `\$PATH`；
  - 不使用单引号包住 `$PATH`（会触发 SC2016）；
  - 不认识的 shell 保留现有的通用提示。

  输出为 `note: ${BIN_DIR} is not on PATH; run: <命令>, then open a new terminal`。`BIN_DIR` 由 `--prefix` 拼成，用户可能给相对路径（`install.sh:71`、`:83`）；写进 rc 文件的相对路径会随当前目录变化，所以只有 `BIN_DIR` 以 `/` 开头、且不含上述特殊字符时才给命令，否则走通用提示。

### 5.2 接口变更

| 接口 | 变更 | 兼容性 |
| ---- | ---- | ---- |
| `set NAME …` | 新增。账号未登记时退出 1（先判断）；没有任何选项时退出 2 | 新增 |
| `add/set --shared [DIR]` | 由开关变为可带值。不带值且 `shared.dir` 未设置时，不再判冲突，而是设为 `~/.codex-shared` | 原来会冲突的命令现在会成功；其它情况不变 |
| `config.json` 的 `shared.dir` | 可能被写成 `~/.codex-shared`，或 `--shared DIR` 给出的绝对路径 | 与 `init --shared-dir` 写入的格式相同 |
| `list` 默认输出 | 改为简表 | README 已写明“表格排版不保证稳定，脚本请用 `--json`”；需要旧输出的用 `--verbose` |
| `list --verbose` | 新增，输出与旧版默认输出逐字相同 | 新增 |
| 拼错的子命令 | 退出码仍为 2，报错文字改为建议 | 只改 stderr 文字 |
| `doctor --json` 中 `path` 检查的 `hint` | 改为按 shell 给出的命令 | `hint` 本来就是给人看的说明 |

本方案不涉及 `docs/reference/*`。

## 6. 备选方案与决策

- **`--shared` 保持开关，另加 `--shared-dir DIR`**：参数更多；`--shared DIR` 一步完成“开启共享并指定目录”，选项更少，也不会出现“开启了共享却没指定目录”的中间状态，因此采用后者。
- **`add` 对已有账号报错，强制改用 `set`**：用户选择保持兼容。
- **自动创建共享目录**：建出来的是空目录，链接仍然不会出现，只给提示更直接，因此不采用。
- **简表固定为 5h、7d 两列**：本机实测存在只有 30 天窗口的账号，固定两列会显示错误或空白，因此按实际窗口显示。
- **共享目录缺项时默认不打印 skip**：会推翻 `feature-onboarding-commands.md` 的 B3 约定，也不在用户选定的范围内，因此不做。

## 7. 影响分析

**正向**

- **`add`**：只改了参数的 `dest`，对已有和新建账号的行为都不变。例外是 `shared.dir` 未设置时的 `--shared`（见 §5.2）。
- **`set`**：复用 `add` 的收敛路径（`accounts.converge`），没有新的写文件逻辑。
- **`--shared DIR` 影响全部共享账号**：改的是全局 `shared.dir`。每个开启了共享的账号，受管链接都会改指向新目录；新目录里缺少的条目，原来的受管链接会被删除（`shared.py:74-88`）。新建账号时写 `add new --shared DIR` 同样会波及已有的共享账号。所有这些动作都会打印出来（create / update / delete / skip），再加上 §5.1.2 第 1 条提示。
- **`list`**：只读。每个账号多一次 `local_snapshot`，实测每个账号 0–17 毫秒（最多取 20 个会话文件）。`--json` 不读额度。
- **`doctor`**：只改了 `path` 检查的 `hint` 文字。
- **`migrate-default`**：也调用 `_hint_after_setup`（`cli.py:370`），PATH 提示的文字同样改变。

**反向**

- **`plan` 与 `execute`**：本方案不改这两个函数，`use`、`restore`、`migrate-default` 的输出不受影响。
- **`args.shared` 的读取点**：只有 `cli.py:402-403`，已改为 `shared_to` / `no_shared`（见 §4 末尾的枚举）。
- **`apply -f`**：不经过 `add` 分支，不受影响（非目标）。
- **补全**：
  - `completion.command_spec` 从解析器生成子命令和选项，`set` 会自动包含；账号名补全依赖 `ACCOUNT_COMMANDS`，必须加 `set`。`test_onboarding.py:121` 会核对所有子命令和选项都出现在补全脚本里。
  - `--shared` 改为可带值后，`command_spec` 会把它算作带值选项（`completion.py:37-38`，`nargs != 0`）。bash、zsh 下 `--shared` 之后的那个词会被当成它的值，不再补全；fish 会为它生成 `-r`。这只影响补全，不影响解析，可以接受。
- **帮助分组**：`test_onboarding.py:113` 要求 `COMMAND_GROUPS` 覆盖全部子命令，加 `set` 后必须同步。
- **`install.sh`**：只改 PATH 提示所在的 `case`。`grep -rn 'not on PATH' tests/`（不截断）只命中 `test_onboarding.py` 的 4 处，都是 `add` 或 `migrate-default` 之后的提示；`test_install.py` 不检查这句。
- **shellcheck**：按 §5.1.5 的写法，避免 SC2088、SC2016；最终以 PR 上 CI 的结果为准。
- **`init --shared-dir` 的相对路径**：同样存在“按当前目录解析”的问题，但不在本次范围内（非目标），本次只保证 `--shared DIR` 写入绝对路径。

## 8. 回归测试

**环境**：本机，以及编译机 ubuntu20 的 Python 3.8.10（按验证手册跑两次）。命令：`python3 -m unittest discover -s tests -t tests`。

| 编号 | 用例 | 判据 |
| ---- | ---- | ---- |
| T1 | 未设置 `shared.dir` 时 `add work --shared` | 退出 0；配置中 `shared.dir` 为 `~/.codex-shared`；`~/.codex-shared/skills` 存在时建立链接；输出含 `shared directory is now ~/.codex-shared (was not set)` |
| T2 | 已设置 `shared.dir` 时 `add work --shared`；已设置为 `~/s` 时 `set work --shared $HOME/s`（同一目录换写法） | 前者沿用原目录，配置中的 `shared.dir` 不变；两者都不输出 `shared directory is now` |
| T3 | 新目录 `~/s2` **预先放好**与旧目录相同的条目；两个共享账号指向旧目录时 `set a --shared ~/s2` | `shared.dir` 改为 `~/s2`；两个账号的受管链接都指向新目录；输出含 `shared directory is now` |
| T3b | 同 T3，但新目录缺少 `skills` | 两个账号的 `skills` 受管链接被删除（输出 `delete shared-link`），其它条目改指向新目录 |
| T4 | `add work --shared word`；`add work --shared --no-shared`；`add --shared work` | 都退出 2，配置不变；第三个报错含 `required: name` |
| T4b | 在某目录执行 `add work --shared ./s` | 配置中的 `shared.dir` 是绝对路径 |
| T5 | 本次 `--shared` 开启共享，共享目录不存在、为空、有一个条目；另外 `set work --proxy 1`（已共享、目录为空） | 前两种输出含 `has none of AGENTS.md, skills, rules, agents`；有一个条目时不输出；`set work --proxy 1` 不输出 |
| T6 | `set work --proxy 7901`、`set work --no-shared`、`set work --shared --adopt` | 结果分别与对应的 `add` 写法相同 |
| T7 | `set nobody --proxy 7901`（有一个账号 `work`，名字不相近）；`set wrk --proxy 1`；`set work`（没有选项）；`set nobody`（未登记且没有选项） | 第一个退出 1，提示含 `use \`multi-codex add NAME\``；第二个退出 1，提示含 `did you mean 'work'`，不含 `use \`multi-codex add`；第三个退出 2；第四个退出 1；配置与文件系统都不变 |
| T8 | `multi-codex lsit`；`multi-codex xyz`；`multi-codex -h`；`multi-codex --version`；`multi-codex run work -- lsit` | 前两个退出 2，分别含 `did you mean 'list'` 与 `run multi-codex -h`；后三个行为与现在相同（`run` 的 `--` 之后不参与判断） |
| T9 | `list` 简表 | 表头为 `NAME LOGIN PROXY SHARED USAGE STATUS`；已登录账号的 STATUS 为 `ok`；未登录为 `not logged in`，表格后有 `multi-codex doctor` 一行；删除启动命令后为 `launcher missing`；删除账号目录后只有 `missing-dir`，LOGIN 为 `-` |
| T10 | `list` 的 USAGE 列 | 构造会话日志快照：只有 300 分钟窗口 → `5h N%`；300 与 10080 两个窗口 → `5h N%, 7d M%`；只有 43200 分钟窗口 → `30d N%`；`resets_at` 已过 → `<标签> reset`；没有日志 → `-`；`sessions` 为软链 → 单元格末尾有 `*`，表格后有说明行 |
| T11 | `list --verbose` | §4 中改为 `--verbose` 的 5 处断言全部通过（它们原本就是对旧输出格式的逐列断言）；另新增一例，用固定配置比较 `--verbose` 的完整输出与预先写好的期望文本 |
| T12 | `shellpath` 的函数级测试 | 直接调用 `add_to_path_command`：zsh → `>> ~/.zshrc`；bash 且 `is_macos=True` → `~/.bash_profile`；bash 且 `is_macos=False` → `~/.bashrc`；fish → `fish_add_path`；未知 shell → `None`；目录含 `'`、`"`、`$` 时 → `None`；返回的命令中目录部分是绝对路径，不含 `~/` 形式 |
| T12b | 三处使用一致 | 测试设置 `SHELL=/bin/zsh`：`add`（bin_dir 不在 PATH）的 stderr、`doctor --json` 中 `path` 检查的 `hint`、`install.sh`（fakebin 中放一个假 `uname`，分别输出 `Linux` 与 `Darwin`）的输出，都包含格式相同的 `echo 'export PATH="<绝对目录>:$PATH"' >> ~/.zshrc`，目录分别是各自的绝对 bin 目录（`add`、`doctor` 为 `$HOME/.local/bin`，`install.sh` 为 `--prefix` 下的 `bin`）；`SHELL=/bin/bash` 加假 `uname` 输出 `Darwin` 时，`install.sh` 给出 `~/.bash_profile`；相对 `--prefix` 时给通用提示 |
| T13 | skip 行照旧打印 | `test_onboarding_commands.py:141-148`（`test_skip_lines_are_kept`）不修改，照常通过 |
| T14 | 帮助与补全 | `-h` 的 Everyday 组含 `set`；三种 shell 的补全脚本含 `set` 及其选项；bash 下 `set ` 补账号名 |
| T15 | 回归 | 现有全部用例通过（§4 中修改的 5 处测试改用 `--verbose` 后通过） |

## 9. 日志 / 观测点

- `set` 的报错：`account 'x' is not registered…`（可能带 `did you mean` 或 `use \`multi-codex add NAME\``）、`nothing to set; …`。
- 共享目录变化：`shared directory is now <DIR> (was <旧值>); every shared account follows it, and links to items missing there are removed`。
- 空共享目录：`note: <目录> has none of … yet; …`。
- 拼写建议：`error: unknown command '<词>'; did you mean '<命令>'?`。
- PATH：`… is not on PATH, …; run: <命令>, then open a new terminal`。
- `list` 简表：STATUS 列，以及表格后的 `run \`multi-codex doctor\` for details`、`* sessions is shared…`。
