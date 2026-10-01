# 凭据存在系统钥匙串时，migrate-default 迁移后登录丢失

> 2026-10-01 注记：代码已落地（未提交，等待编译机验证）。
> - 检查函数：`src/multi_codex/migrate.py` 的 `_check_credentials_store`；调用点在 `migrate_default` 中预检之后、占用检查之前。
> - 存储模式解析：`src/multi_codex/identity.py` 的 `credentials_store`，返回 `(模式, 来源文件)`；错误信息中会带上来源文件。
> - 事务记录的两个新字段，以及回滚后重新开始时的传参，都在 `migrate.py` 中。
> - §11 的第 1–12 条由 `tests/test_migrate.py` 的 `KeyringStoreTest` 覆盖。

## 1. 背景

Codex 可以把登录凭据存到系统钥匙串（keyring），而不是 `CODEX_HOME/auth.json`。钥匙串条目的键由 `CODEX_HOME` 的真实路径算出。`migrate-default` 会把 `~/.codex` 移到 `~/.cx/<名>`，真实路径变了，Codex 就找不到原来的条目，迁移后两种启动方式都显示未登录。目前预检不检查存储模式，迁移会照常“成功”。

## 2. 目标 / 非目标

**目标**

1. `migrate-default` 预检时识别出“迁移后会丢登录”的存储模式，拒绝迁移并说明原因，不移动任何数据。
2. 提供 `--accept-relogin` 开关：用户明确接受迁移后重新登录时，照常迁移，并提示迁移后执行 `codex-<名> login`。

**非目标**

- 不搬迁钥匙串条目。需要调用各平台的钥匙串接口（macOS `security`、Linux Secret Service），而且条目格式属于 Codex 内部实现。
- 不处理已有事务记录的续跑：续跑时数据可能已经移动，预检不再适用。
- 不处理托管配置（managed requirements、MDM、企业下发配置）和项目目录 `.codex/` 中的配置：只读账号目录下的 `config.toml` 和系统级配置 `/etc/codex/config.toml`。
- `add` 不受影响：登记已有目录不改变路径。

## 3. 假设与约束

- 只读取 `config.toml` 的顶层键 `cli_auth_credentials_store`（上游 `codex-rs/config/src/config_toml.rs:290`，位于 `ConfigToml` 顶层；`profile_toml.rs` 中没有这个键）。
- 按上游的配置层优先级取值：先读账号目录下的 `config.toml`（User 层，优先级 20），没有这个键时再读 `/etc/codex/config.toml`（System 层，优先级 10）。依据：上游 `codex-rs/config/src/config_layer_source.rs` 的 `precedence`，以及 `config/src/loader/mod.rs:79` 的 `SYSTEM_CONFIG_TOML_FILE_UNIX`。两层都没有时，按默认值 `file` 处理（`config/defaults.toml:6`）。系统级配置的路径可以用环境变量 `MULTI_CODEX_TEST_SYSTEM_CONFIG` 替换，只供测试使用，沿用现有 `MULTI_CODEX_TEST_*` 测试钩子的命名（`src/multi_codex/migrate.py:45-55`）。
- 工具需要兼容 Python 3.8，没有 `tomllib`。只做最小解析：在第一个表头（以 `[` 开头的行）之前，匹配 `cli_auth_credentials_store = "<值>"`，单引号、双引号都接受，忽略行尾注释。文件不存在或读不到时视为没有这个键。
- 最小解析的已知局限：多行字符串中以 `[` 开头的行会被误当成表头；键名带引号（`"cli_auth_credentials_store" = ...`）时识别不了。这两种写法在 Codex 配置中很少见，出现时按“没有这个键”处理，最坏结果是漏报，与修复前的行为相同。
- 凭据存在环境变量或 `-c` 参数覆盖里的情况不处理。上游没有对应的环境变量；`-c` 只对单次命令生效。

## 4. 根因分析

- **现象**：在 `cli_auth_credentials_store = "keyring"` 下执行 `multi-codex migrate-default main` 后，`codex-main login status` 和 `codex login status` 都输出 `Not logged in`。
- **触发条件**：存储模式为 `keyring`；或者为 `auto`，且账号目录中没有 `auth.json`（说明凭据实际存在钥匙串里）。
- **失效路径**：
  1. 钥匙串条目的键是 `cli|` 加上真实路径 SHA-256 的前 16 位，计算前先 canonicalize（上游 `codex-rs/login/src/auth/storage.rs:243-257`）；`CODEX_HOME` 本身在解析时也先 canonicalize（`codex-rs/utils/home-dir/src/lib.rs:43-49`）。
  2. 迁移后 `~/.codex` 是指向 `~/.cx/<名>` 的软链，两种启动方式解析出的真实路径都是 `~/.cx/<名>`，算出的键都是新的，而条目还挂在旧键下。
  3. keyring 模式写入钥匙串成功后会删除 `auth.json`（`storage.rs:319-330`），所以目录里也没有可以回落的文件；auto 模式读取时先查钥匙串，查不到才读文件（`storage.rs:478-528`），目录里没有 `auth.json` 时同样丢失登录。
  4. Secrets 后端（特性 `secret_auth_storage`，默认只在 Windows 开启，macOS 和 Linux 也可以手动开启，见上游 `features/src/lib.rs:1002-1006`）会把加密文件 `secrets/codex_auth.age` 留在目录里，但解密密钥同样按真实路径存放在钥匙串（`codex-rs/secrets/src/lib.rs:184-203`），迁移后同样解不开。它只在 keyring 或 auto 模式下使用，所以按存储模式检查就能拦住，不需要单独处理。
- **错误代码点**：`src/multi_codex/migrate.py:132-145`，预检到占用检查之间没有检查存储模式。

## 5. 复现步骤

在 macOS 上用临时目录复现，不碰真实的 `~/.codex`：

```sh
export HOME=$(mktemp -d)
mkdir -p ~/.codex
printf 'cli_auth_credentials_store = "keyring"\n' > ~/.codex/config.toml
printf '%s' "$TEST_API_KEY" | codex login --with-api-key   # 写入钥匙串，不留 auth.json
codex login status                                          # Logged in using an API key
multi-codex migrate-default main
codex-main login status                                     # Not logged in
CODEX_HOME=~/.codex codex logout                            # 清理：不同路径对应不同条目，以下两条都要执行
CODEX_HOME=~/.cx/main codex logout
```

注意：`codex login --with-api-key` 是否在写入前在线校验密钥，尚未核实。如果会校验，需要使用真实的测试密钥。复现完成后要按上面最后两行删除钥匙串条目。

## 6. 影响版本

- multi-codex 0.1.0、0.2.0，所有支持的平台（macOS、Linux）。
- 只在存储模式为 `keyring`，或者为 `auto` 且凭据实际存在钥匙串时出现。默认的 `file` 模式不受影响。`ephemeral` 模式本来就不保存凭据。

## 7. 涉及模块

| 区域 | 行号锚点（基线 `fb548a8`） | 改动类型 | 改动点 |
| ---- | ---- | ---- | ---- |
| 身份与存储模式 | 新文件 `src/multi_codex/identity.py` | 新增 | `credentials_store(account_dir) -> Tuple[str, Optional[str]]`：按 §3 先读账号目录、再读系统级配置，返回 (模式, 来源文件)。模式为 `file`、`keyring`、`auto`、`ephemeral`，无法识别时返回原值；来源文件在两层都未设置时为 None。`feature-account-insight.md` 会在同一模块中继续添加内容 |
| 迁移预检 | `src/multi_codex/migrate.py:140-143` 之间 | 新增 | `done is None` 之后、占用检查之前，调用 `_check_credentials_store(source_path, accept_relogin)`，按返回值决定是否结束，见 §8.1 |
| 迁移预检 | `src/multi_codex/migrate.py:116-117` `migrate_default` 签名 | 修改 | 末尾新增参数 `accept_relogin: bool = False`，有默认值，已有调用不受影响 |
| 迁移预检 | `src/multi_codex/migrate.py` `_precheck_without_journal` 之后（约 :223） | 新增 | `_check_credentials_store`，见 §8.1 |
| 事务记录 | `src/multi_codex/migrate.py:158-171` | 修改 | 记录中新增 `accept_relogin` 和 `relogin_needed` 两个布尔字段 |
| 回滚后重新开始 | `src/multi_codex/migrate.py:349-351` | 修改 | 调用 `migrate_default` 时传入 `bool(journal.get("accept_relogin"))`，否则带开关开始的迁移在回滚重来时会被这项检查拒绝 |
| 迁移完成 | `src/multi_codex/migrate.py:516` `_step_register_and_cleanup` | 修改 | `relogin_needed` 为真时，在完成信息之后输出重新登录提示 |
| 迁移续跑 | `src/multi_codex/migrate.py:279-280` `_resume` | 不改 | 续跑不做这项检查，见 §2 非目标；重新登录提示按记录中的 `relogin_needed` 输出 |
| 命令行 | `src/multi_codex/cli.py:49-50` 之后 | 新增 | `--accept-relogin` 开关 |
| 命令行 | `src/multi_codex/cli.py:144-145` | 修改 | 传入 `accept_relogin=args.accept_relogin` |
| 测试 | `tests/test_migrate.py:356` | 不改 | 位置参数调用，新参数有默认值，无需修改 |
| 测试 | `tests/helpers.py:51-57` 默认环境变量 | 修改 | 加入 `MULTI_CODEX_TEST_SYSTEM_CONFIG`，指向临时目录中不存在的路径，避免开发机上真实的 `/etc/codex/config.toml` 影响测试结果 |
| 测试 | `tests/test_migrate.py` | 新增 | 见 §11 |
| 文档 | `README.md`、`README.zh-CN.md` 的 Migrating 一节与命令表，`CHANGELOG.md`，`docs/feature/feature-account-manager.md` §5.1.5 预检步骤与命令表 | 修改 | 说明新的预检项和开关 |

## 8. 方案

### 8.1 实现要点

`_check_credentials_store(source, accept_relogin)`：

| 存储模式 | `auth.json` | 未带 `--accept-relogin` | 带 `--accept-relogin` |
| ---- | ---- | ---- | ---- |
| `file`、`ephemeral`、未设置 | 任意 | 继续 | 继续 |
| `keyring` | 任意 | 拒绝，退出码 3 | 警告后继续 |
| `auto` | 不存在 | 拒绝，退出码 3 | 警告后继续 |
| `auto` | 存在 | 警告后继续 | 警告后继续 |
| 无法识别的值 | 任意 | 警告后继续 | 警告后继续 |

- `auto` 模式下存在 `auth.json`，说明当初写钥匙串失败、回落到了文件。迁移后读钥匙串查不到，仍会读到这个文件，所以只警告：钥匙串里可能还有旧条目，以文件为准。
- 拒绝时的错误信息：`credentials are stored in the system keyring (cli_auth_credentials_store = "keyring", from <配置文件路径>); the keyring entry is tied to the directory path, so after migration Codex will be logged out. Rerun with --accept-relogin and log in again with codex-<名> login, or switch to file storage first`。
- 返回方式：`_check_credentials_store` 不抛异常，返回 `(退出码或 None, relogin_needed)`：继续时退出码为 `None`，拒绝时为 `EXIT_CONFLICT`，并已用 `error(..., phase="precheck", path=source)` 输出原因；`relogin_needed` 表示“本来会被拒绝、因为带了开关才放行”，调用方把它写进事务记录。原因是调用点位于 `migrate.py:133-139` 的 `try` 之外，`cli.main` 的异常处理（`cli.py:112-129`）也不捕获 `MigrationStop`，抛异常会打出 traceback。
- 带 `--accept-relogin`、且本来会被拒绝时，把 `relogin_needed = true` 写进事务记录；迁移完成时（包括中断后续跑完成）输出 `log in again: codex-<名> login`。
- 回滚后重新开始（`migrate.py:347-351`）时，按记录中的 `accept_relogin` 重新执行这项检查，与第一次的结果一致。
- `--dry-run` 同样执行这项检查，结果和实际运行一致。
- 检查放在占用检查之前：拒绝时用户不需要先关闭 Codex。

### 8.2 接口变更

- `migrate-default` 新增开关 `--accept-relogin`，默认关闭。
- 存储模式为 `keyring`，或为 `auto` 且没有 `auth.json` 时，`migrate-default` 从“迁移成功、退出码 0”改为“拒绝、退出码 3、不改动任何东西”。这是有意的行为变更，写进 CHANGELOG。
- 退出码契约不变：3 的含义仍是“存在冲突，未做任何修改”。

## 9. 备选方案与决策

| 方案 | 结论 |
| ---- | ---- |
| 只警告、不拒绝 | 否决：警告滚过去后，迁移已经发生，用户只能发现登录没了 |
| 迁移后自动重新登录 | 否决：登录需要浏览器交互，迁移是非交互流程 |
| 搬迁钥匙串条目 | 否决：依赖各平台钥匙串接口和 Codex 内部的条目格式，见 §2 |

## 10. 影响分析

- **正向**：检查本身只在 `migrate-default` 的首次执行路径（`migrate.py:116` 起，没有事务记录的分支）和回滚后重新开始的路径（`:349-351`）中执行。续跑分支 `_resume` 不做检查，但会经过 `_step_register_and_cleanup`，按记录中的 `relogin_needed` 决定是否输出提示。
- **事务记录兼容**：0.2.0 写的记录没有两个新字段，读取一律用 `journal.get(..., False)`，按“未带开关、不需要提示”处理，与 0.2.0 的行为一致（§11 第 11 条）。新增的读取只涉及 `config.toml` 和判断 `auth.json` 是否存在，不读取凭据内容。
- **反向**：`list`、`add`、`apply` 等命令不调用这项检查。`identity.py` 是新模块，不改动已有模块的行为。
- **运行时**：多读一个小文件，耗时可以忽略；不新增进程、锁或网络访问。
- **对外语义**：钥匙串模式的用户从“迁移成功但丢登录”变为“被拒绝”，属于修复；其它模式行为不变。

## 11. 回归测试

在 `tests/test_migrate.py` 中新增，全部在临时 HOME 中运行，使用现有的假 codex：

1. 写入 `cli_auth_credentials_store = "keyring"`：迁移返回 3，错误信息包含 `--accept-relogin`；源目录原样保留，没有事务记录，`~/.cx/<名>` 不存在。
2. 同上，加 `--dry-run`：返回 3，输出与第 1 条一致。
3. 同上，加 `--accept-relogin`：迁移成功，返回 0；stderr 有警告，并提示重新登录。
4. `auto` 且没有 `auth.json`：返回 3。
5. `auto` 且有 `auth.json`：迁移成功并给出警告。
6. `file`、未设置该键、`config.toml` 不存在：迁移成功，没有新警告（覆盖原有路径）。
7. 解析边界：键写在 `[profiles.x]` 表头之后时不生效（按默认值 file 处理）；单引号、行尾注释、键名前后有空格都能识别。
8. 系统级配置：账号目录的 `config.toml` 没有这个键，`MULTI_CODEX_TEST_SYSTEM_CONFIG` 指向的文件中设为 `keyring` 时，返回 3；账号目录中设为 `file` 时，以账号目录为准，迁移成功。
9. 回滚后重新开始：在 keyring 配置下带 `--accept-relogin --copy` 迁移，同时设置 `MULTI_CODEX_TEST_FAIL_AT=journal-parked`（写入 parked 阶段后触发回滚，与现有用例 `tests/test_migrate.py:182-183` 的组合相同）和 `MULTI_CODEX_TEST_CRASH_AT=journal-rolled-back`（在 `migrate.py:561` 杀掉进程，留下 `rolled-back` 阶段的事务记录）。然后不设钩子，再执行一次同样的命令，断言三点：stdout 含 `previous rollback finished`（进入了 `migrate.py:346-351` 分支）；没有被这项检查拒绝；结束时输出重新登录提示。
10. 中断续跑（与第 9 条不同，这里不经过回滚）：在 keyring 配置下带 `--accept-relogin` 迁移，设置 `MULTI_CODEX_TEST_CRASH_AT=journal-moved`（此时记录中已有 `relogin_needed`）。然后**不带** `--accept-relogin` 再执行一次，续跑完成后仍输出重新登录提示，证明提示来自事务记录。
11. 兼容 0.2.0 留下的事务记录：手工写一份不含 `accept_relogin`、`relogin_needed` 字段的 rename 模式 `moved` 阶段记录，并把文件系统摆成对应状态（源目录不存在、目标目录存在、没有备份）。续跑能完成，并且不输出重新登录提示。
12. 原有的 `tests/test_migrate.py` 全部通过，其中 `:356` 的直接调用不需要修改。

在编译机上用 Python 3.8 跑同一套用例。§5 的实机复现留作人工验证项：需要真实的 codex 和钥匙串。

## 12. 日志 / 观测点

- 拒绝：stderr 输出 `[multi-codex] error phase=precheck path=<源目录>: credentials are stored in the system keyring ...`，退出码 3。
- 带开关继续：stderr 输出 `[multi-codex] warning: ... you will need to log in again`；迁移结束时 stdout 提示 `codex-<名> login`。
- auto 模式且有 `auth.json`：stderr 输出一条 `[multi-codex] warning:`，说明以文件中的凭据为准。
