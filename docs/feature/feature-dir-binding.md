# 目录绑定账号：bind / unbind，run 按目录选账号（v0.5）

> 2026-10-01 注记：代码已落地（未提交，等待编译机验证）。实现：`src/multi_codex/binding.py`；`cli.py` 的 `cmd_run`、`cmd_bind_list`，以及 `dispatch` 中的 bind / unbind 分支；`doctor.py` 的 `_check_bindings`。§8 的用例在 `tests/test_binding.py` 中。
>
> 本文其余部分保持方案原文。

## 1. 背景

v0.4 的 `multi-codex run NAME -- CMD` 必须写出账号名，账号名通常是邮箱，比较长。路线中的 U11 要求：把一个目录绑定到某个账号，在这个目录或它的子目录下执行 `run` 时可以省略账号名。

开源项目 Ducksss/codex-profiles 的做法是 `workspace bind . work`，按最近一个已绑定的父目录决定账号，绑定信息只存在工具自己的配置里，不改项目文件。本方案采用同样的规则。

## 2. 目标 / 非目标

**目标**

1. `multi-codex bind NAME [DIR]`：把目录（默认为当前目录）绑定到账号 NAME；`multi-codex unbind [DIR]`：解除绑定；`multi-codex bind`（不带参数）：列出全部绑定，并标出对当前目录生效的那一条。
2. `multi-codex run [NAME] [-- CMD ...]`：省略 NAME 时，从当前目录开始逐级向上，找最近一个已绑定的目录，使用它绑定的账号。
3. `doctor` 报告指向未登记账号或不存在目录的绑定。

**非目标**

- 不往项目目录写任何文件（不生成 `.codex-account` 之类的标记文件）。
- `codex-<名>` 启动命令不受绑定影响；也不新增“按目录自动选账号”的通用启动命令。
- 不支持通配符或排除规则。

## 3. 假设与约束

- 绑定的键是目录的**规范路径** `normalize_dir(path)`：先 `os.path.realpath`（解析软链，macOS 上 `/tmp` 会变成 `/private/tmp`），再对已存在的部分逐级修正大小写，规则为：
  - 这一级的原名在父目录的 `os.listdir` 中**精确存在**时，保留原名（区分大小写的文件系统上 `Foo` 与 `foo` 并存时不会选错）；
  - 精确匹配不到时，找 casefold 相同的条目；恰好一个时用它的名字，零个或多个时保留原名；
  - `listdir` 出错（例如目录只有执行权限）或这一级已不存在时，保留 `realpath` 的结果，不报错。

  原因：macOS 默认文件系统不区分大小写，`realpath("casedir")` 会保留输入时的大小写，而 `os.getcwd()` 返回磁盘上的真实大小写，只用 `realpath` 时键会匹配不上（评审第 1 轮实测）。
- 查找时对 `os.getcwd()` 做同样的规范化，再逐级取父目录比较。
- `unbind` 同样使用规范路径；目录已不存在时，`realpath` 仍会解析仍然存在的上级软链，不能改用 `abspath`（`/tmp/x` 与绑定时存的 `/private/tmp/x` 对不上）。
- 绑定属于本机状态，与 `managed_links` 一样：`apply -f` 时沿用当前配置中的绑定，文件里的 `bindings` 字段内容不采用（但字段本身仍要合法，否则整个文件按配置错误拒绝）。原因：绑定的路径因机器而异，而且已有用户的声明文件里没有这个字段，若按文件覆盖，执行 `apply -f` 就会清空全部绑定。
- 账号被注销时（`remove`、`restore` 的第 3 步、`apply -f` 删除账号），指向它的绑定一并删除，按 casefold 匹配账号名。三处共用一个函数 `binding.drop_account(config, name)`。
- 绑定中保存的是账号登记时的名称（`account.name`），不是用户输入的大小写。
- `config.json` 中没有绑定时不写 `bindings` 字段，序列化结果与 v0.4.0 相同（与 v0.4 的 `env` 字段同一做法，原因见 `docs/feature/feature-cli-ergonomics.md` §3）。

## 4. 涉及模块

| 区域 | 行号锚点（基线 `8a4e559`） | 改动类型 | 改动点 |
| ---- | ---- | ---- | ---- |
| 配置模型 | `src/multi_codex/config.py:70-101` `Config` | 修改 | 新增属性 `bindings: Dict[str, str]`（真实路径 → 账号名）；`__init__` 增加可选参数；`copy` 复制它；`to_dict` 只在非空时写 `bindings`，按路径排序 |
| 配置解析 | `src/multi_codex/config.py:138-177` `parse_config` | 修改 | 解析 `bindings`：必须是对象，键为绝对路径字符串，值为合法账号名；不要求账号已登记（悬空的绑定由 `doctor` 报告） |
| 绑定查找 | 新文件 `src/multi_codex/binding.py` | 新增 | `normalize_dir(path) -> str`（§3 的规范路径）、`resolve(config, cwd) -> Optional[Tuple[str, str]]`（返回绑定目录与账号名）、`drop_account(config, name)` |
| 命令行 | `src/multi_codex/cli.py:104-106` `run` 子命令 | 修改 | `name` 改为可选（`nargs="?"`），用法改为 `run [NAME] [-- COMMAND ...]` |
| 命令行 | `src/multi_codex/cli.py:546` `cmd_run` | 修改 | `name` 为空时调用 `binding.resolve`；没有绑定时报错，退出码 1；绑定的账号未登记时同样报错，退出码 1 |
| 命令行 | `src/multi_codex/cli.py:104` 附近 | 新增 | 子命令 `bind [NAME] [DIR] [--dry-run]`、`unbind [DIR] [--dry-run]` |
| 命令行 | `src/multi_codex/cli.py:200-203` 之间 | 新增 | 不带参数的 `bind`（列出）在这里分派：只读，不加锁 |
| 命令行 | `src/multi_codex/cli.py:250-322` `dispatch` | 修改 | `bind NAME`、`unbind`：在 cli 中打印绑定变化行（不进入 `accounts.plan`），修改 `new.bindings` 后走 `converge`，配置动作就是全部改动；`remove` 分支调用 `binding.drop_account` |
| 撤销迁移 | `src/multi_codex/switch.py:269-274` 第 3 步 | 修改 | 注销账号时调用 `binding.drop_account` |
| 命令行 | `src/multi_codex/cli.py:355-367` `_load_apply_file` | 修改 | 新配置的 `bindings` 沿用 `old.bindings`，再删除指向文件中已不存在账号的绑定 |
| 体检 | `src/multi_codex/doctor.py:36-62` `run_checks` | 修改 | 增加 `bindings` 检查，见 §5.1.4 |
| 补全 | `src/multi_codex/completion.py:16` `ACCOUNT_COMMANDS` | 修改 | 加入 `bind`。`unbind` 与 `bind NAME` 之后的参数是目录，交给 shell 的文件名补全；zsh 版在候选为空时调用 `_files`（与 U12 方案同一处修改，`completion.py:154-168`） |
| 测试 | 新文件 `tests/test_binding.py` | 新增 | 见 §8 |
| 文档 | `README.md`、`README.zh-CN.md` 的命令表与 Running other commands 一节；`CHANGELOG.md`；`docs/feature/feature-account-manager.md` 命令表 | 修改 | |

## 5. 方案

### 5.1 实现要点

#### 5.1.1 `bind` / `unbind`

- `bind NAME [DIR]`（键为 `normalize_dir(DIR)`）：
  - NAME 必须已登记，否则退出码 1；
  - DIR 默认为当前目录，必须是存在的目录，否则退出码 1；
  - 已有绑定时覆盖，动作输出 `update binding <目录> (-> NAME)`，新建时输出 `create binding ...`，相同时输出 `unchanged binding ...`；
  - 写命令：加写锁，支持 `--dry-run`，经 `converge` 写配置（绑定不产生文件动作，配置动作即为全部改动）。
- `unbind [DIR]`：键为规范路径（§3），目录已被删除时也能解除。没有这条绑定时输出 `not bound`，退出码 0；如果当前对 DIR 生效的是某个上级目录的绑定，再提示 `the effective binding is <上级目录> -> NAME`。还没有 `config.json` 时直接输出 `not bound`，不创建配置文件。
- `bind`（不带参数，带不带 `--dry-run` 都一样）：每行输出 `<目录>  <账号名>`，按目录排序；对当前目录生效的那一行前面加 `*`。没有任何绑定时输出 `no bindings`。退出码 0。

绑定变化行（`create|update|unchanged|delete binding ...`）由 cli 用现有的 `print_action` 格式打印，在 `converge` 返回之后才打印：返回 0 时照常输出（`--dry-run` 时与配置行一样带 `(dry-run)` 标记，`actions.py:41`）；返回非 0 时（例如原有的其它冲突）输出 `binding not changed because of the errors above`，避免用户看到 create 却什么都没写。**不**放进 `accounts.plan` 的动作列表：`execute` 把列表的第一项当作配置动作（`accounts.py:166-172`），`doctor` 的 drift 检查也调用 `plan`。实际改动只有配置动作写入的 `config.json`。

#### 5.1.2 查找规则

`resolve(config, cwd)`：从 `normalize_dir(cwd)` 开始，依次检查自己和每一级父目录是否在 `bindings` 中，返回最先命中的（最近的）一条；到根目录仍没有命中时返回 None。

#### 5.1.3 `run` 省略账号名

- `run -- CMD`、`run`（什么都不带）：按 §5.1.2 查找。
  - 找到：stderr 输出一行 `[multi-codex] using account NAME (bound to <目录>)`，然后与 `run NAME` 完全相同；
  - 找不到：退出码 1，提示 `no account is bound to <cwd> or its parents; use run NAME or multi-codex bind NAME`；
  - 绑定的账号未登记：退出码 1，提示执行 `unbind` 或重新 `add`。
- `run NAME ...` 的行为不变。

#### 5.1.4 `doctor`

新增检查项 `bindings`：
- 指向未登记账号的绑定：warn，建议 `multi-codex unbind <目录>`；
- 绑定的目录已不存在：warn，建议同上；
- 没有绑定或全部有效：ok，说明中写出绑定数量。

### 5.2 接口变更

| 类型 | 接口 | 兼容性 |
| ---- | ---- | ---- |
| 新增 | `bind [NAME] [DIR] [--dry-run]`、`unbind [DIR] [--dry-run]` | 新增 |
| 修改 | `run` 的 NAME 改为可选 | 带 NAME 的用法不变 |
| 新增 | 配置字段 `bindings` | 旧版本会忽略它，并在下一次写配置时丢掉；README 写明降级前会丢失绑定 |
| 修改 | `remove NAME`、`restore NAME`、`apply -f`（删除账号时）同时删除指向该账号的绑定 | 新行为；没有绑定时与 v0.4.0 相同 |
| 新增 | `doctor` 的 `bindings` 检查项 | `doctor --json` 的 `checks` 多一项 |

仓库中没有 `docs/reference/` 目录，不涉及 reference 章节的同步检查。

## 6. 备选方案与决策

| 问题 | 方案 | 结论 |
| ---- | ---- | ---- |
| 绑定存在哪里 | 在项目目录写一个标记文件 | 否决：会改动用户的项目，还可能被提交进项目仓库 |
| `apply -f` 怎么处理绑定 | 以文件为准 | 否决：已有的声明文件里没有这个字段，按文件覆盖会清空全部绑定 |
| 绑定的键 | 用户输入的路径原样保存 | 否决：同一个目录经不同软链进入时会匹配不上 |

## 7. 影响分析

- **配置序列化**：没有绑定时不写字段，v0.4.0 的配置在升级后第一次写入不会出现多余的 update（§8 第 1 条）。
- **`run`**：只有省略 NAME 时才走新逻辑；带 NAME 时代码路径不变。`_split_run_command`（`cli.py:154`）不受影响：`--` 之前最多一个位置参数。
- **`remove` / `restore` / `apply -f`**：注销账号时会多删除指向它的绑定，配置动作从 unchanged 变为 update。
- **`apply -f`**：沿用当前绑定，不受文件影响。
- **`doctor`**：多一项检查；悬空绑定只报 warn，退出码仍为 0。
- **运行时**：`run` 省略 NAME 时，最多做“目录层数”次字典查找，可以忽略。

## 8. 回归测试

新文件 `tests/test_binding.py`，全部在临时 HOME 中运行。

1. v0.4.0 格式的 `config.json`（没有 `bindings` 字段）执行 `apply`：配置字节不变。
2. `bind work`（在项目目录 P 中）：`bind` 列出 P → work，并带 `*`；`config.json` 中的键是 P 的真实路径。
3. 在 P 的子目录中执行 `run -- sh -c 'echo $CODEX_HOME'`：输出 work 的目录，stderr 有 `using account work`。
4. 最近优先：P 绑定 work、P/sub 绑定 home，在 P/sub/x 中 `run` 使用 home，在 P/other 中使用 work。
5. 经软链进入 P（`ln -s P L; cd L`）时，`run` 仍使用 work。
6. 没有绑定时 `run -- true`：退出码 1；`run work -- true` 照常。
7. 绑定的账号被手工从配置中删掉：`run -- true` 退出码 1，`doctor` 的 `bindings` 为 warn。
8. `unbind`：解除后 `run -- true` 退出码 1；对没有绑定的目录 `unbind`：退出码 0，输出 `not bound`；目录被删除后仍能 `unbind`。
9. `bind ghost`、`bind work /no/such/dir`：退出码 1，什么都不改。
10. `remove work`、`restore work`、`apply -f`（文件中没有 work）：都会同时删除指向 work 的绑定。
10a. 以错误大小写绑定（目录实际为 `CaseDir`，执行 `bind work casedir`）后，在 `CaseDir` 中 `run` 仍能命中；只在大小写不敏感的文件系统上断言（测试先探测）。
10b. 在 macOS 上用 `/tmp/...` 路径绑定，删除目录后用同一个 `/tmp/...` 路径 `unbind`，能解除。
10c. 执行 `use`、`migrate-default` 之后，绑定仍在（`Config.copy` 复制了 `bindings`）。
10d. 在子目录中 `unbind`，生效的是上级绑定时，输出 `not bound` 并提示上级目录。
10e. 区分大小写的文件系统上（测试先探测），`Foo` 与 `foo` 并存时 `bind work foo`，键为 `.../foo`，在 `foo` 中命中、在 `Foo` 中不命中。
10f. `normalize_dir` 遇到 `listdir` 失败（父目录权限为 0o111）时返回 `realpath` 的结果，不抛异常（以 root 运行时权限不生效，跳过）。
10g. `bind work` 时已有其它冲突（例如某个启动命令被换成不受管的文件）：退出码 3，输出 `binding not changed`，配置不变。
11. `apply -f` 一个不含 `bindings` 的文件：绑定保留。
12. `bind work --dry-run`、`unbind --dry-run`：什么都不改。
13. 补全：`multi-codex bind ` 补出账号名。
14. 现有用例全部通过；编译机 Python 3.8 运行全部用例；CI 通过。

## 9. 日志 / 观测点

- `bind` / `unbind`：输出 `[multi-codex] create|update|unchanged|delete binding <目录> (-> NAME)` 与配置动作行。
- `run` 省略 NAME：stderr 输出 `[multi-codex] using account NAME (bound to <目录>)`。
- `doctor`：`bindings` 一项列出每条悬空绑定。
