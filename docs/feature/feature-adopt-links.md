# add --adopt：接管已有的共享软链

> 2026-09-30 注记：代码已落地，见 `shared.py` 的 `plan_shared`（adopt 分支）、`accounts.py` 的 `plan`/`converge`（adopt_accounts）、`cli.py` 的 `add --adopt`；§8 的 6 条回归测试由 `tests/test_accounts.py` 中的 `SharedTest.test_adopt_*` 覆盖。

## 1. 背景

开启共享时，如果账号目录里某个条目已经是指向共享目录对应条目的软链（例如用户以前手工建的），现有实现把它记为 `unchanged`，但**不写入** `managed_links`（`src/multi_codex/shared.py:50-53`）。这样以后执行 `--no-shared`、从 `shared.items` 去掉某项、或者更换 `shared.dir` 时，工具都不会处理这些软链。

要让工具管理这些软链，目前只能先手工删掉，再让工具重建。用户要求提供一个直接接管的选项。

## 2. 目标 / 非目标

**目标**

1. `multi-codex add <名称> --adopt`：把账号目录里“已经指向对应共享条目”的软链写入该账号的 `managed_links`，不删除、不重建这些软链。
2. 幂等：再执行一次时，所有动作都是 `unchanged`。

**非目标**

- 不接管指向别处的软链、真实文件或真实目录。这些仍按现有规则判为冲突。
- 不给 `apply -f` 增加接管能力。`managed_links` 仍然是工具的内部状态，导入的文件里写的值一律忽略（方案 `feature-account-manager.md` §5.1.2）。
- 不提供“取消接管”的命令。

## 3. 假设与约束

- `--adopt` 只在该账号的共享为开启状态时才有意义：要么本次命令同时带了 `--shared`，要么账号原本就已开启。否则判为参数错误，返回 2。
- 判断“指向对应共享条目”沿用现有的 `same_target`：两边都按真实路径（realpath）比较。

## 4. 涉及模块

| 区域 | 行号锚点（基线 `3b331a7`） | 改动类型 | 改动点 |
| ---- | ---- | ---- | ---- |
| 共享计划 | `src/multi_codex/shared.py:17` `plan_shared` 签名 | 修改 | 新增关键字参数 `adopt: bool = False` |
| 共享计划 | `src/multi_codex/shared.py:50-53` | 修改 | 在“已是指向该共享条目的软链”分支中，`adopt` 为真且条目不在 `managed_links` 时，记一个 `update` 动作（原因为 `adopted`，没有文件操作），并把条目加入保留名单 |
| 收敛引擎 | `src/multi_codex/accounts.py:38` `plan` 签名 | 修改 | 新增关键字参数 `adopt_accounts: FrozenSet[str] = frozenset()`，内容为要接管的账号名（casefold 后） |
| 收敛引擎 | `src/multi_codex/accounts.py:75` | 修改 | 调用 `plan_shared` 时传入 `adopt=account.name.casefold() in adopt_accounts` |
| 收敛引擎 | `src/multi_codex/accounts.py:182` `converge` | 修改 | 透传 `adopt_accounts` |
| 命令行 | `src/multi_codex/cli.py:56-60` `add` 参数 | 新增 | 增加 `--adopt` 开关 |
| 命令行 | `src/multi_codex/cli.py:160-168` `add` 分支 | 修改 | 计算出该账号的最终 `shared` 状态；为假时抛出 `UsageError`；为真时以 `adopt_accounts={name.casefold()}` 调用 `converge` |
| 测试 | `tests/test_accounts.py` `SharedTest` | 新增 | 见 §8 |
| 文档 | `README.md`、`README.zh-CN.md` 的 Shared resources 一节，`CHANGELOG.md`，`docs/feature/feature-account-manager.md` §5.1.2 命令表与 §5.1.6 | 修改 | 说明 `--adopt` |

## 5. 方案

### 5.1 实现要点

`plan_shared` 中“已存在且指向该共享条目”的分支：

| 条件 | 现在的行为 | 改后的行为 |
| ---- | ---- | ---- |
| 条目在 `managed_links` 中 | `unchanged`，保留在名单里 | 不变 |
| 不在名单中，未带 `--adopt` | `unchanged`，不加入名单 | 不变 |
| 不在名单中，带了 `--adopt` | （无此分支） | 记一个 `update shared-link … (adopted)` 动作，没有文件操作，条目加入名单 |

动作列表中，只要还有任何冲突（例如另一项是真实目录），整条命令照旧返回 3，名单也不写入，与现有的收敛模型一致。

接管只修改 `config.json` 中该账号的 `managed_links`，配置动作为 `update`。

### 5.2 接口变更

- 新增命令行参数：`multi-codex add <名称> [--shared] --adopt`。
- 退出码：新增一种返回 2 的情形，即带 `--adopt` 但该账号的共享处于关闭状态。其它退出码不变。
- 输出：新增动作行 `[multi-codex] update shared-link <路径> (adopted)`。
- 配置文件格式不变。
- 向后兼容：不带 `--adopt` 时，行为与现在完全相同。

本方案不涉及 `docs/reference/*`。

## 6. 备选方案与决策

- **开启共享时默认接管**：会把用户有意保留、不想交给工具管理的软链也纳入管理，关闭共享时被删掉。违背原设计中“不删除用户自建软链”的约定，不采用。
- **单独做一个 `adopt` 子命令**：功能上与 `add --shared` 高度重合，增加命令面，不采用。

## 7. 影响分析

- 正向：只有带 `--adopt` 的 `add` 会走新分支。`init`、`apply`、`remove`、`migrate-default` 调用 `plan` 时 `adopt_accounts` 为空，行为不变。
- 反向：`managed_links` 的其它读写点也一并检查过：
  - 关闭共享或从清单去掉某项时，只删除名单内、并且仍指向共享目录的软链。接管后，这些软链在关闭共享时会被删除，这正是接管的目的，需要在 README 中说明；
  - `apply -f` 仍然按账号名沿用当前配置里的 `managed_links`，接管的结果会被保留。
- 运行时：没有新的文件操作，只多写一次 `config.json`。

## 8. 回归测试

1. 用户自建了指向共享目录的软链：执行 `add --shared --adopt` 后，`managed_links` 包含这些条目，软链的 inode 和目标都不变；再执行一次，全部为 `unchanged`。
2. 接管后执行 `add --no-shared`：这些软链被删除，共享目录里的内容完好。
3. 不带 `--adopt` 的行为不变：现有用例 `test_user_link_is_not_removed` 继续通过。
4. 账号共享为关闭状态、只带 `--adopt`：返回 2，文件系统不变。
5. 另一项是真实目录时带 `--adopt`：返回 3，`managed_links` 不变。
6. 软链指向别处：仍然判为冲突，不会被接管。

## 9. 日志 / 观测点

- 接管时输出 `[multi-codex] update shared-link <路径> (adopted)`；
- `config.json` 中该账号的 `managed_links`；
- `multi-codex list` 的 SHARED 列。
