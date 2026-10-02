# add --config-from：从另一个账号复制 config.toml（v0.5）

> 2026-10-01 注记：代码已落地（未提交，等待编译机验证）。实现：`accounts.py` 的 `plan_config_copy` 与 `extra_actions`；`cli.py` 的 `_config_copy_actions`。实现时有一处细化：`plan_config_copy` 接收调用方读好的内容（`content`），而不是源目录，读取与 UTF-8 校验放在 cli 中完成。§8 的用例在 `tests/test_config_copy.py` 中。
>
> 本文其余部分保持方案原文。

## 1. 背景

新建账号后，`config.toml` 是空的，模型、MCP 服务器、已信任的项目等设置都要重新配置。路线中的 U13 要求：创建或调整账号时，可以从另一个账号复制一份 `config.toml`。开源项目 codex-homes 的 `add --from` 也是这样做的。

## 2. 目标 / 非目标

**目标**：`multi-codex add NAME --config-from OTHER` 把账号 OTHER 的 `config.toml` 复制到账号 NAME 的目录；NAME 是新账号或已有账号都可以。

**非目标**

- 不做持续同步：只复制一次，之后两份文件各自独立。需要持续共享时，把 `config.toml` 加进共享项（README 的“哪些可以共享”表已说明风险）。
- 不覆盖已有的不同内容：目标账号已有不同的 `config.toml` 时判为冲突。
- 不复制 `config.toml` 以外的文件。
- 不支持 `apply -f` 文件中声明这个操作：它是一次性动作，不是持续的状态。

## 3. 假设与约束

- 读取 OTHER 的 `config.toml` 时跟随软链（OTHER 可能已经把它共享出去），复制的是内容，不复制软链。
- 目标文件权限为 0600：配置里可能有 `experimental_bearer_token` 之类的密钥。
- 复制动作纳入现有的“先计划、有冲突就什么都不写”流程（`accounts.py:157-183` `execute`）：与其它动作一起计划，任何冲突都会让整条命令一个文件都不写。

## 4. 涉及模块

| 区域 | 行号锚点（基线 `8a4e559`） | 改动类型 | 改动点 |
| ---- | ---- | ---- | ---- |
| 收敛引擎 | `src/multi_codex/accounts.py:38` `plan`、`:186` `converge` | 修改 | 新增关键字参数 `extra_actions: Sequence[Action] = ()`，追加在全部账号动作之后（即在账号目录创建之后执行） |
| 配置复制 | `src/multi_codex/accounts.py` `_plan_account_dir` 之后 | 新增 | `plan_config_copy(config, account, source_dir) -> List[Action]`：`config` 与 `account` 是应用了本条命令其它选项（如 `--shared`）之后的新配置，用来判断共享冲突，见 §5.1 |
| 命令行 | `src/multi_codex/cli.py:58-69` `add` 子命令 | 修改 | 在 `_add_dry_run(p_add)`（`:69`）之前新增 `--config-from OTHER` |
| 命令行 | `src/multi_codex/cli.py:275-289` `add` 分支 | 修改 | 校验 OTHER，生成复制动作，经 `extra_actions` 传给 `converge` |
| 测试 | 新文件 `tests/test_config_copy.py` | 新增 | 见 §8 |
| 文档 | `README.md`、`README.zh-CN.md` 命令表；`CHANGELOG.md`；`docs/feature/feature-account-manager.md` 命令表 | 修改 | |

## 5. 方案

### 5.1 实现要点

`add NAME --config-from OTHER`：

1. 命令行校验（不满足就退出，什么都不写）：
   - OTHER 必须已登记，否则退出码 1；
   - OTHER 与 NAME 不能是同一个账号（大小写不敏感），否则退出码 2；
   - OTHER 的目录中必须有 `config.toml`（跟随软链后是普通文件），否则退出码 1；
   - 它的内容必须是合法的 UTF-8（TOML 规定必须是 UTF-8），否则退出码 1。
2. `plan_config_copy`，设 T = NAME 目录下的 `config.toml`。按下表**从上到下**判断，命中第一行即停；共享检查最先执行，所以 T 已经是指向共享目录的软链时，即使内容相同也判为冲突，并提示不需要复制：

| T 的状态 | 动作 |
| ---- | ---- |
| NAME 开启了共享，且 `config.toml` 在共享项中 | 冲突：`config.toml` 应当是指向共享目录的软链，复制会把软链替换成普通文件（`atomic_write` 用 `os.replace` 替换目录项本身，`fsutil.py:80`），之后 `apply` 会判冲突。原因中提示：共享的 `config.toml` 不需要复制 |
| 不存在 | `create config-file T (copied from OTHER)`；执行时用 `atomic_write` 写入 OTHER 的内容，权限 0600 |
| 存在，内容与 OTHER 的相同（跟随软链比较） | `unchanged config-file T` |
| 存在，内容不同 | `conflict config-file T (already exists with different content)`，整条命令返回 3，什么都不写 |
| 存在但不是文件（例如目录） | 冲突，同上 |

3. 内容在计划阶段读取一次，执行时写入的就是这份内容；计划与执行之间 OTHER 的文件被修改，不影响这次写入的内容。
4. 与 `--proxy`、`--shared` 等选项可以同时使用；`--dry-run` 只打印动作。

### 5.2 接口变更

| 类型 | 接口 | 兼容性 |
| ---- | ---- | ---- |
| 新增 | `add NAME --config-from OTHER` | 新增选项，不带时行为不变 |
| 新增 | 动作类型 `config-file` 出现在 `add` 的输出中 | 新增输出行 |
| 修改 | `accounts.plan` / `converge` 新增可选参数 `extra_actions` | 内部接口，默认值保持原行为 |

仓库中没有 `docs/reference/` 目录，不涉及 reference 章节的同步检查。

## 6. 备选方案与决策

| 方案 | 结论 |
| ---- | ---- |
| 在收敛完成后再单独复制文件 | 否决：复制冲突时，账号已经创建了，违反“有冲突就什么都不写”的约定 |
| 把 `config_from` 存进配置，由 `apply` 持续维护 | 否决：会变成持续同步，两个账号的配置无法再各自修改 |

## 7. 影响分析

- `plan` / `converge` 只多一个默认为空的参数，其它调用方（`init`、`proxy`、`remove`、`apply`、迁移、`restore`、`doctor`）不传它，行为不变。
- `doctor` 的 drift 检查调用 `plan` 时不传 `extra_actions`，不会因为这个功能报告差异。
- 复制只发生在执行这条命令时，不产生持续状态，`config.json` 不变化（除非同时改了其它选项）。
- 写入失败（例如磁盘已满）时，按 `execute` 的现有顺序，配置已经保存、账号已经登记，复制失败的错误照常输出并返回 1；重跑同一条命令会重新计划 `create config-file` 并完成。
- 被复制的配置可能包含 `cli_auth_credentials_store = "keyring"`，或指向源账号目录的绝对路径，新账号会原样沿用。README 提示这一点。

## 8. 回归测试

新文件 `tests/test_config_copy.py`：

1. `add new --config-from work`（work 有 `config.toml`）：new 的目录中出现内容相同的 `config.toml`，权限 0600；输出中有 `create config-file`。
2. 再执行一次：`unchanged config-file`，退出码 0。
3. new 已有不同的 `config.toml`：退出码 3；new 的文件与 `config.json` 都没有变化。
4. 新账号加上冲突：用一个目录占住 new 的 `config.toml` 位置后执行，退出码 3，new 没有被登记。
5. work 的 `config.toml` 是指向共享目录的软链：复制的是内容，new 中是普通文件。
6. `--config-from ghost`：退出码 1；`--config-from new`（自己）：退出码 2；work 没有 `config.toml`：退出码 1；work 的 `config.toml` 不是 UTF-8：退出码 1。都不做任何修改。
6a. 共享项包含 `config.toml` 时执行 `add new --shared --config-from work`：退出码 3，什么都不写。
7. `--dry-run`：什么都不写。
8. `doctor` 的 drift 检查在复制前后都为 ok。
9. 现有用例全部通过；编译机 Python 3.8 运行全部用例；CI 通过。

## 9. 日志 / 观测点

动作行：`[multi-codex] create|unchanged|conflict config-file <路径> (...)`。
