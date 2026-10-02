# multi-codex v0.9：隔离与共享更安全

> 2026-10-02 注记：方案已定（plan-review 两轮收敛：第 1 轮 0 高 4 中 10 低，第 2 轮 0 高 0 中 4 低，均已修订），代码待落地。基线：main `7f0e4cf`（v0.8.0）。
>
> 用户的决定（2026-10-02）：
>
> - 启动命令默认清除 `CODEX_API_KEY`、`CODEX_ACCESS_TOKEN`、`CODEX_SQLITE_HOME`。需要 API key 的账号用 `multi-codex env NAME CODEX_API_KEY=…` 单独设置。不做“保留变量”的开关。
> - `rename OLD NEW` 只改账号名与启动命令名，目录不动；配置里账号新增记录目录名的字段。
> - 禁止共享清单 = README「What can be shared」表中标为不共享（No）的全部条目。
> - 按账号退出共享项：`add` / `set` 的 `--shared-exclude ITEM` 与 `--shared-include ITEM`，可重复；退出时只删本工具建的软链。
> - 升级后旧启动命令变为 stale，靠 CHANGELOG 与 README 提示运行 `multi-codex apply`，不改安装脚本。

## 1. 背景

- **隔离变量只告警、不清除**：`CODEX_SQLITE_HOME`、`CODEX_API_KEY`、`CODEX_ACCESS_TOKEN` 列在 `platform.py:16`，`list` 与 `migrate-default` 发现它们时告警（`migrate.py:197-201`），`doctor` 也报 warn（`doctor.py:116-121`）。启动命令（`launcher.render`，`launcher.py:53-95`）不处理它们，所以从设了这些变量的 shell 启动的每个账号都会用同一份值。
- **共享是账号级开关**：`shared.items` 是全局清单，账号只有 `shared` 一个布尔值。开启后清单里的每一项都会软链过来（`shared.py:35-41`），不能让某个账号单独不共享其中一项。
- **共享清单不校验内容**：`_valid_item`（`config.py:225-227`）只检查是否为一级名称，`auth.json`、`state_5.sqlite` 这类账号私有的条目也能配成共享项。
- **账号名就是目录名**：目录一律是 `<root>/<账号名>`（`accounts.py:34-35`、`:73`、`:86`），改名会被判冲突（`accounts.py:65-72`）。凭据存在系统钥匙串时，钥匙串条目的键由 `CODEX_HOME` 的路径算出（openai/codex `codex-rs/login/src/auth/storage.rs:246`），目录一改就会掉登录。

## 2. 目标 / 非目标

**目标**

1. 启动命令（以及共用同一段脚本的 `run`、`login`、`code`，和经启动命令运行的 `usage --live`）清除上述 3 个变量；账号自己用 `env` 设置的值照常生效。
2. 账号可以单独退出或恢复共享清单中的某些项；共享清单不能包含禁止共享的条目。
3. `rename OLD NEW`：改账号名与启动命令名，目录、登录、共享链接、默认账号、目录绑定都保留。

**非目标**

- 不做“保留某个隔离变量”的开关（用户决定）。
- 不清除 `CODEX_HOME` 以外的其它变量；`app`（桌面端）经 `open -n` 启动，不走启动命令脚本（`cli.py:1112-1116`），本方案不改它的环境。
- 退出共享时不把共享内容复制成账号自己的副本。
- 不能按账号追加共享项（只能从全局清单里退出）。
- `rename` 不搬迁目录，也不支持只改大小写。
- 不修改安装脚本：升级后需要用户自己运行一次 `apply`。
- 降级兼容：旧版本不认识目录名字段（见 §7）。

## 3. 假设与约束

- **清除变量的位置**：`unset` 行放在 `export CODEX_HOME` 之后、代理行与账号环境变量之前（`launcher.py:74-79`）。账号 `env` 里设了 `CODEX_API_KEY` 时，后面的 `export` 会把值重新设上。`env` 的保留键（`RESERVED_ENV_KEYS` `config.py:34`、`validate_env_key` `config.py:263-271`）不包含这 3 个变量，无需改动。
- **启动命令内容变化**：`render` 的注释与 `test_ergonomics.py:21-38` 的逐字节比对都约定“没有环境变量时与 0.3.0 逐字节相同”。本方案有意打破这一约定：每个启动命令多一行，升级后所有已有启动命令都会被判为 stale（`list` 的 STATUS 显示 `launcher stale`，`doctor` 的漂移检查报告 update），运行一次 `multi-codex apply` 即可重写。旧启动命令照常能用，只是不清除变量。
- **禁止共享清单**，按大小写不敏感比较（macOS 默认文件系统不区分大小写）：
  - 凭据：`auth.json`、`.credentials.json`、`secrets`、`.env`；
  - 安装标识：`installation_id`；
  - 数据库：`sqlite` 目录，以及名称以 `.sqlite`、`.sqlite-wal`、`.sqlite-shm` 结尾的条目（本机账号目录实测有 `state_5.sqlite`、`logs_2.sqlite-wal` 等 15 个）；
  - 会话：`sessions`、`archived_sessions`、`session_index.jsonl`；
  - 运行时状态：`app-server-control`、`app-server-daemon`、`packages`、`tmp`、`.tmp`、`log`、`shell_snapshots`。

  其中 `sqlite` 目录和 `.sqlite-wal`、`.sqlite-shm` 两种后缀是 SQLite 的附属文件（本机账号目录实测存在），README「What can be shared」表的数据库行（`README.md:354`）原来只写了 `*.sqlite`。本方案同步把该行（及 `README.zh-CN.md` 对应行）改为 `*.sqlite`、`*.sqlite-wal`、`*.sqlite-shm`、`sqlite/`，让清单与表中标 No 的行重新一一对应。本机与编译机现有配置的 `shared.items` 都不含这些条目（已读取两边 `config.json` 核实）。
- **`shared_exclude` 是用户配置**：`apply -f` 以文件为准；只在列表非空时写进配置，没有退出项的账号配置文本不变。
- **目录名字段 `dir`**：账号目录在根目录下的名字，缺省等于账号名；只在与账号名不同时写进配置。它是工具内部状态：`apply -f` 对已登记账号沿用当前值，对新账号取文件里的值（缺省等于名字）。
- **目录名的唯一性**：所有账号的 `dir` 大小写不敏感互不相同，且任一账号名不等于另一个账号的 `dir`（否则 `add` 新账号会落进别人的目录）。

## 4. 涉及模块

| 区域 | 行号锚点（基线 `7f0e4cf`） | 改动类型 | 改动点 |
| ---- | ---- | ---- | ---- |
| `src/multi_codex/launcher.py` | 导入 :12-13、`render` :53-95，`export CODEX_HOME` :74 之后 | 修改 | 导入 `ISOLATION_BREAKING_ENV`；插入 `unset CODEX_ACCESS_TOKEN CODEX_API_KEY CODEX_SQLITE_HOME`；更新文档字符串里“与 0.3.0 逐字节相同”的说明 |
| `src/multi_codex/platform.py` | `ISOLATION_BREAKING_ENV` :15-16 | 修改 | 注释说明启动命令会清除它们；元组内容与顺序不变 |
| `src/multi_codex/migrate.py` | `warn_isolation_env` :197-201 | 修改 | 告警文字改为“启动命令会清除，只有直接运行的 `codex` 仍会用到” |
| `src/multi_codex/doctor.py` | `_check_env` :116-121 | 修改 | 说明文字同上；`CODEX_HOME` 仍按原文报告 |
| `src/multi_codex/config.py` | `DEFAULT_SHARED_DIR` :20 之后 | 新增 | `UNSHAREABLE_ITEMS`、`UNSHAREABLE_SUFFIXES`、`is_unshareable(item)` |
| `src/multi_codex/config.py` | `Account.__init__` :51-57、`copy` :59-60、`to_dict` :62-70 | 修改 | 新增 `shared_exclude`、`dir_name` 两个字段 |
| `src/multi_codex/config.py` | `parse_config` :188-190、账号循环 :197-205 之后 | 修改 | `shared.items` 含禁止项时报错；目录名唯一性检查 |
| `src/multi_codex/config.py` | `_parse_account` :230-253 | 修改 | 解析 `shared_exclude` 与 `dir` |
| `src/multi_codex/accounts.py` | `account_dir` :34-35 | 修改 | 用账号的 `dir_name`，未登记时用 name |
| `src/multi_codex/accounts.py` | `plan` :73、:86 | 修改 | 两处直接拼接改用 `dir_name`；新增目录占用冲突；改名时跳过旧账号的 `plan_remove_links` |
| `src/multi_codex/accounts.py` | `plan_config_copy` :124 | 修改 | `config.toml` 被该账号排除时不算共享 |
| `src/multi_codex/shared.py` | `plan_shared` :35-41 | 修改 | 遍历 `shared_items` 时跳过 `account.shared_exclude` 中的项 |
| `src/multi_codex/switch.py` | `restore_account` :185-195 | 修改 | 账号已注销、按标记续跑时，target 取标记里的 `target`，不再按账号名拼 |
| `src/multi_codex/apps.py` | `gui_data_dir` :36-40、`desktop_log` :43-48 | 修改 | 参数 `name` 改为账号目录名，改名后 VS Code 与桌面端数据目录不变 |
| `src/multi_codex/cli.py` | `COMMAND_GROUPS` Advanced 组 :53-59 | 修改 | 加 `("rename", "rename an account and its launcher (the directory stays)")` |
| `src/multi_codex/cli.py` | `build_parser`，`remove` 之后（:134 后） | 新增 | `rename OLD NEW [--dry-run] [-v]` |
| `src/multi_codex/cli.py` | `_add_account_options` :223-243 | 修改 | 加 `--shared-exclude ITEM`、`--shared-include ITEM`（`action="append"`） |
| `src/multi_codex/cli.py` | `dispatch` 的 `init` :433-438 | 修改 | `--shared-items` 含禁止项时 `UsageError` |
| `src/multi_codex/cli.py` | `dispatch` 的 `add/set` :438-471 | 修改 | 应用退出 / 恢复与提示 |
| `src/multi_codex/cli.py` | `dispatch`，`remove` 分支 :478 之前 | 新增 | `rename` 分支（§5.1.5） |
| `src/multi_codex/cli.py` | `_any_account_option` :546-548 | 修改 | 计入两个新选项 |
| `src/multi_codex/cli.py` | `_load_apply_file` :744-761 | 修改 | 已登记账号沿用当前 `dir_name`；文件里显式写了不同 `dir` 时 warn |
| `src/multi_codex/cli.py` | `cmd_list` JSON :826-840、完整表格 :859、简表 :902 | 修改 | JSON 加 `shared_exclude`；两种表格的 SHARED 列显示退出项 |
| `src/multi_codex/cli.py` | `cmd_code` :1077、`cmd_app` :1110-1111 | 修改 | 传账号目录名 |
| `src/multi_codex/completion.py` | `ACCOUNT_COMMANDS` :16-17 | 修改 | 加 `rename` |
| `tests/test_ergonomics.py` | `V030_HTTP` / `V030_OFF` :21-38、:176-177 | 修改 | 期望内容加 `unset` 行，常量与注释改为 v0.9 |
| `tests/test_isolation.py` | 新文件 | 新增 | §8 的新用例 |
| `README.md`、`README.zh-CN.md` | Common tasks 表（加 `rename`）、Commands 表（`add`、`set`、`rename` 行）、Per-account environment variables、迁移一节的隔离变量说明（`README.md:306`、`README.zh-CN.md:371`）、Shared resources、What can be shared 的数据库行（`README.md:354`）、升级说明 | 修改 | 文档同步 |
| `CHANGELOG.md` | Unreleased | 修改 | Added / Changed，写明升级后运行 `multi-codex apply` |

**`account_dir(` 的调用点全集**（`grep -rn "account_dir(" src/`，不截断，不含定义 `accounts.py:34` 与 `_plan_account_dir` 的两行，共 15 处）：

- `accounts.py:123`、`:244`；
- `doctor.py:136`、`:169`；
- `cli.py:650`、`:699`、`:811`、`:971`、`:1016`、`:1058`、`:1113`、`:1148`；
- `switch.py:70`、`:97`、`:194`。

改 `account_dir` 的实现即全部生效。另有三处直接拼接：`accounts.py:73`、`:86` 本方案修改；`migrate.py:131` 是新建账号（`dir_name` 等于名字），不改。名称等于某个改过名账号的目录名时有两条路径：一般情况在预检阶段因目标目录已存在退出 3（`migrate.py:227-228`）；`~/.codex` 已链到该目录时经 `_register`（`migrate.py:210-216`、`:294-296`）走到 §5.1.4 的计划冲突，同样退出 3。

**`shared_items` 的读取点全集**（`grep -rn "shared_items" src/`，不截断）：`config.py` 的构造、复制、写出、解析；`shared.py:35`（本方案修改）、`:90-92`（排序，不改）；`accounts.py:124`（本方案修改）；`cli.py:433-438`（`init`，本方案加校验）、`_hint_shared_dir`（v0.8 的空目录提示，不改）。

**依赖启动命令内容的测试全集**（`grep -rn "V030\|export CODEX_HOME" tests/`）：只有 `test_ergonomics.py:21-38`、`:176-177`。其它测试通过运行启动命令比较环境，不比较文本。

## 5. 方案

### 5.1 实现要点

#### 5.1.1 启动命令清除隔离变量

`render` 在 `"export CODEX_HOME"`（`launcher.py:74`）之后追加：

```sh
unset CODEX_ACCESS_TOKEN CODEX_API_KEY CODEX_SQLITE_HOME
```

- 变量列表取自 `sorted(platform.ISOLATION_BREAKING_ENV)`（`launcher.py` 新增 `from .platform import ISOLATION_BREAKING_ENV`；`platform` 只依赖 `fsutil`，没有循环导入），两处不会各自维护一份；排序结果是 `CODEX_ACCESS_TOKEN CODEX_API_KEY CODEX_SQLITE_HOME`。
- 位置在代理行与账号 `env` 行之前：`env` 里设置的同名变量会被重新导出，所以 `multi-codex env work CODEX_API_KEY=…` 照常生效。
- `run`、`login`（经 `cmd_run`，`cli.py:335-337`）与 `code` 用 `command_mode=True` 的同一份脚本（`cli.py:1010-1020`），`usage --live` 经启动命令运行 `codex app-server`，四者自动一致。
- **提示文字**：
  - `warn_isolation_env`：`{var} is set in this shell; codex-<name> launchers clear it, but plain codex still uses it`；
  - `doctor` 的 `env` 检查：消息改为 `set in this shell: …; launchers override or clear them, but plain codex still uses them`，级别仍为 warn，修复建议不变（`unset …`）。

#### 5.1.2 禁止共享清单

```python
UNSHAREABLE_ITEMS = ("auth.json", ".credentials.json", "secrets", ".env", "installation_id", "sqlite",
                     "sessions", "archived_sessions", "session_index.jsonl", "app-server-control",
                     "app-server-daemon", "packages", "tmp", ".tmp", "log", "shell_snapshots")
UNSHAREABLE_SUFFIXES = (".sqlite", ".sqlite-wal", ".sqlite-shm")

def is_unshareable(item: str) -> bool  # casefold 后比较名称与后缀
```

- **`parse_config`**：`shared.items` 中任一项满足时，报 `ConfigError("{source}: 'shared.items' must not include {item!r}: it holds account-specific state; edit {source} and remove it")`。所有命令都在读配置时失败，`init --shared-items` 也改不掉，所以报错直接指出要手工编辑的文件。走现有配置错误通道（退出 1），`doctor` 的 config 检查报 fail。
- **`init --shared-items`**：同一判定，报 `UsageError`（退出 2），文字同上去掉文件名前缀。
- **`shared_exclude`**：不做此限制，退出禁止项无害。

#### 5.1.3 按账号退出共享项

配置：

```json
"accounts": {"work": {"shared": true, "shared_exclude": ["skills"], "...": "..."}}
```

- **解析**：每项须满足 `_valid_item`，否则报 `'shared_exclude' must be a list of names`；重复项去重并保持首次出现的顺序。
- **匹配**：与 `shared.items` 做大小写敏感的精确匹配（`plan_shared` 按精确名称建链接），与禁止清单的大小写不敏感不同；`not in shared.items` 提示用同一判定。
- **建链接**：`plan_shared` 遍历 `new.shared_items` 时（`shared.py:35`）跳过 `account.shared_exclude` 中的项，放在“共享目录里没有该条目”的判断之前，所以被排除的项不会出现 skip 行。
- **删链接**：已在 `managed_links` 中、但被排除的项走现有“不在 desired 中”分支（`shared.py:74-87`）：仍指向共享目录的受管软链被删除，原因沿用 `sharing disabled for this item`；已被用户换成别的东西的记 skip，不动。不需要新逻辑。
- **复制配置**：`plan_config_copy`（`accounts.py:124`）的“`config.toml` 是共享的”判断加上 `"config.toml" not in account.shared_exclude`。同一条命令里既排除 `config.toml` 又 `--config-from` 会判内容冲突（计划按当前文件判断，那时它还是共享软链），需分两条命令，README 写明。
- **命令**：

  ```text
  multi-codex add|set NAME --shared-exclude ITEM [--shared-exclude ITEM ...] [--shared-include ITEM ...]
  ```

  - 应用顺序：先移除 `--shared-include` 的项，再追加 `--shared-exclude` 的项（不重复追加）。
  - 用法错误（退出 2）：同一名称同时出现在两个选项里；名称不满足 `_valid_item`。
  - 提示（`info`，收敛成功、非 `--dry-run` 时输出）：排除的名称不在 `shared.items` 里时 `note: ITEM is not in shared.items; the exclusion takes effect only if it is added there`；账号未开启共享时 `note: sharing is off for NAME; the exclusion applies once it is turned on`。
  - 不要求共享已开启；`set` 只带这两个选项也算“有选项”（`_any_account_option`）。
  - v0.8 的空目录提示（`_hint_shared_dir`，`cli.py:593-599`）不考虑退出项：开启共享时排除了共享目录里仅有的那一项，不会有提示。这种组合是用户自己选的，不改。
- **输出**：
  - `list --json` 每个账号加 `"shared_exclude": [...]`；
  - 两种表格的 SHARED 列：共享开启且有退出项时显示 `yes (not: skills, rules)`，其余仍为 `yes` / `no`。没有退出项时 `list -v` 输出与 v0.8 逐字相同。

#### 5.1.4 账号目录名字段

- `Account.dir_name: str`，构造缺省为 `name`；配置键名为 `dir`，`to_dict` 仅当 `dir_name != name` 时写出。
- `_parse_account`：`dir` 缺省为账号名；存在时必须是字符串且匹配 `NAME_PATTERN`，否则 `ConfigError("{source}: account {name!r}: 'dir' must be a valid account directory name")`。
- `parse_config` 在账号循环之后检查：所有账号的 `dir_name.casefold()` 互不相同；任一账号的 `name.casefold()` 不等于另一个账号的 `dir_name.casefold()`。违反时 `ConfigError`（这类配置只能来自手写文件或 `apply -f`）。
- **`apply -f`**：已登记账号沿用当前 `dir_name`；只有文件里**显式出现** `dir` 键且值与当前不同时才 `warn("… ignored for registered account …")`（`parse_config` 会给省略的 `dir` 填默认值，所以要在 `_load_apply_file` 里另读原始 JSON 判断是否显式写了）；省略 `dir` 不告警。配置里的 `dir` 是目录名，`list --json` 已有的 `"dir"` 字段（`cli.py:828`）是完整路径，README 说明两者不同。
- `account_dir(config, name)`：`config.find(name)` 找到时用其 `dir_name`，否则用 `name`。
- `plan`：
  - `accounts.py:73` 改为 `os.path.join(new_root, account.dir_name)`；
  - `accounts.py:86` 改为 `os.path.join(old_root, old_account.dir_name)`；
  - 在账号循环之前加目录占用检查：新配置中某账号的 `name` 或 `dir_name` 与另一个账号的 `dir_name` 大小写不敏感相同时，加 `CONFLICT config`，原因 `account {name!r} would use the directory of account {other!r}`。典型触发：`rename work job` 之后再 `add work`。
- `apps.gui_data_dir` / `apps.desktop_log` 的第二个参数改为目录名，`cmd_code`、`cmd_app` 传 `account.dir_name`。未改名的账号路径与现在逐字相同。

#### 5.1.5 `rename OLD NEW`

```text
multi-codex rename OLD NEW [--dry-run] [-v]
```

1. OLD 必须已登记，否则 `error(config.not_registered(OLD))`，退出 1。
2. NEW 必须合法（`_checked_name`，否则退出 2）；`NEW.casefold() == OLD.casefold()` 时退出 2：`only the letter case differs; renaming that way is not supported`。
3. NEW 已是另一个账号的名字时，`error("account {NEW!r} already exists")`，退出 3，不进入收敛。NEW 等于另一个账号的目录名时，由 §5.1.4 的计划冲突判出（退出 3，什么都不写）。
4. 新配置：在 `new.accounts` 中按原顺序把键 OLD 换成 NEW，值是 OLD 账号对象的完整副本（`proxy`、`shared`、`managed_links`、`env`、`shared_exclude`、`dir_name` 全部保留），只把 `name` 改为 NEW；`dir_name` 保持原值（第一次改名时即为 OLD）；`new.bindings` 中值为 OLD（casefold 比较）的改为 NEW。
5. 收敛：`orphan_scope=frozenset([OLD.casefold()])`。计划中：
   - 旧启动命令 `codex-OLD` 走既有“账号移除”路径删除（`accounts.py:83-85`）；
   - 新启动命令 `codex-NEW` 创建，内容里的 `account_dir=` 仍是原目录；
   - 旧账号的 `plan_remove_links`（`accounts.py:86`）必须跳过：新配置中存在 `dir_name` 相同（casefold）的账号时不计划删除共享链接，否则会删掉仍在用的软链；
   - 目录、共享链接、`managed_links`、登录都不变。
6. 成功（非 `--dry-run`）后 `info("renamed OLD to NEW; directory {display_path(目录)} is unchanged")`；若 `~/.codex` 指向该目录（默认账号），无需改动，`use` 与 `list` 会显示 NEW。
7. 未完成的迁移或 restore 存在时，与其它写命令一样被 `_blocked_by_migration`（`cli.py` `main`）拦下。
8. `rename` 归入 `_add_dry_run` 与 `_add_verbose`；补全第一个位置参数补账号名。

#### 5.1.6 `restore` 续跑用标记里的目录

`restore_account`（`switch.py:185-195`）：`account is None` 且标记存在、标记名字与输入 casefold 相同时，`target = journal["target"]`；其余情况仍为 `accounts.account_dir(config, canonical)`。基线下两者总相同，未改名的账号行为不变。

### 5.2 接口变更

| 接口 | 变更 | 兼容性 |
| ---- | ---- | ---- |
| 启动命令内容 | 多一行 `unset CODEX_ACCESS_TOKEN CODEX_API_KEY CODEX_SQLITE_HOME` | 升级后所有启动命令判为 stale，`apply` 重写；未重写前照常可用 |
| 启动命令、`run`、`login`、`code`、`usage --live` 的环境 | 不再继承 shell 中的这 3 个变量 | 依赖 shell 中 `CODEX_API_KEY` 的用户需改用 `env NAME CODEX_API_KEY=…`；CHANGELOG Changed 写明 |
| `config.json` `shared.items` | 收紧：禁止项报错 | 含禁止项的配置升级后无法加载，按报错删掉该项；CHANGELOG Changed 写明 |
| `config.json` 账号字段 `shared_exclude` | 新增，可省略 | 旧配置不受影响；旧版本读到会忽略，写配置时会丢掉 |
| `config.json` 账号字段 `dir` | 新增，可省略，仅改名后出现 | 旧版本忽略它、按账号名找目录（见 §7 降级） |
| `add` / `set` `--shared-exclude` / `--shared-include` | 新增 | 新增 |
| `init --shared-items` | 含禁止项时退出 2 | 收紧 |
| `rename OLD NEW [--dry-run] [-v]` | 新增子命令 | 新增 |
| `list --json` 每账号 `shared_exclude` | 新增字段 | 只加字段 |
| `list` 两种表格的 SHARED 列 | 可能出现 `yes (not: …)` | 面向人阅读 |
| `list` / `migrate-default` 的隔离告警、`doctor` 的 `env` 检查文字 | 改为说明启动命令会清除 | 只改文字 |

本方案不涉及 `docs/reference/*`。

## 6. 备选方案与决策

- **启动命令提供“保留变量”开关**：用户决定不做；需要 API key 的账号用 `env` 单独设置，比全局继承更明确。
- **改名时搬迁目录**：钥匙串模式下会掉登录，也会让正在运行的会话失去目录。不做。
- **独立子命令 `share` 管理退出项**：与 `--shared`、`--no-shared`、`--adopt` 职责重叠，用户选定放在 `add` / `set` 上。
- **退出时复制共享内容**：会产生两份需要各自维护的内容，用户选定只删链接。
- **安装脚本自动 `apply`**：会让安装脚本改动账号文件，pipx 与 Homebrew 安装也不经过它；用户选定只提示。

## 7. 影响分析

**正向**

- **启动命令**：每个启动命令多一行，`launcher_file_ok`（`launcher.py:37-50`）按内容比较，`plan` 遍历全部账号（`accounts.py:64-75`），所以升级后任何走收敛的写命令（`add`、`set`、`proxy`、`env`、`bind`、`unbind`、`remove`、`apply`）都会把所有 stale 启动命令一起判为 update 并重写；只读命令（`list`、`doctor`）显示 stale。
- **`run` / `code`**：经同一脚本，行为与启动命令一致；`test_ergonomics` 中“`run` 与启动命令看到的环境相同”的对拍用例照常成立。
- **`usage --live`**：经启动命令启动 `codex app-server`（`usage.py:218`），不再继承 shell 的 `CODEX_API_KEY`。另外它要求启动命令状态为 ok（`cli.py:961-966`）：升级后、运行 `apply` 之前，所有账号的 `usage --live` 都会失败并提示 `` launcher is stale; run `multi-codex apply` first ``（`cli.py:965`；测试只断言 `launcher is stale` 与 `multi-codex apply` 两段），CHANGELOG 与 README 的升级说明写明。
- **`login`**：经 `cmd_run(name, ["codex", "login"])`（`cli.py:335-337`）走同一段脚本，也不再继承 shell 的 3 个变量。
- **共享**：`plan_shared` 只多一个跳过条件；没有退出项的账号，计划结果与现在逐项相同。
- **改名**：`account_dir` 实现变化影响 §4 列出的全部 15 个调用点；未改名的账号 `dir_name == name`，路径逐字相同。

**反向**

- **`managed_links`**：被排除的项不再进入 `kept`（`shared.py:90-92`），名单随之移除；之后 `--shared-include` 恢复时按 create 重建，期间用户在那里建了真实目录的，按现有规则判冲突，不覆盖。
- **`apply -f`**：文件里没写 `shared_exclude` 的账号会清空退出项，这是声明式配置的预期语义，README 写明；`dir` 对已登记账号沿用当前值，文件改不动它。
- **目录绑定**：`rename` 同步改写绑定的账号名；`remove` 的 `binding.drop_account` 不变。
- **默认账号**：`switch.describe_default` 按路径找账号（`switch.py:68-72`），改名后自动显示新名字；`use`、`restore` 都经 `account_dir`。
- **`restore` 续跑**：`restore` 注销账号的收敛失败时保留标记（`switch.py:274-280`），重跑时账号已不在配置里，`account_dir` 会回退为 `<root>/<账号名>`；改过名的账号目录名与账号名不同，与标记里的 `target` 对不上，会在 `switch.py:168-171` 判冲突。所以 `restore_account` 在账号未登记、标记名字匹配时，直接用 `journal["target"]` 作为 target（§5.1.6）。
- **迁移**：`migrate-default` 的目标仍是 `<root>/<名称>`；名称等于某个改过名账号的目录名时，由预检或计划冲突拦下（见 §4 末尾），不会两个账号共用一个目录。
- **正在运行的会话**：改名删除 `codex-OLD` 文件，已启动的进程不受影响；用户脚本或别名里的 `codex-OLD` 失效，README 写明。
- **桌面端与 VS Code 数据目录**：按目录名存放，改名后不变（§5.1.4）。

**运行时**：没有新进程；`plan` 多一次 O(账号数²) 的目录名比较，账号数量级为个位数。

**部署形态**

- **降级**：旧版本不认识 `dir`，会把改过名的账号目录当成 `<root>/<新名字>`，计划创建空目录并把启动命令改指向它（登录与数据仍在原目录，没有删除）；旧版本执行一次写命令还会把 `dir` 从配置里丢掉，之后即使再升级回来，该账号也仍指向 `<root>/<新名字>`。README 写明：降级前先把账号改回原名（`rename NEW OLD` 后 `dir` 与名字相同，不再写出该字段）。
- **其它机器的配置**：`shared.items` 含禁止项的配置在升级后无法加载，报错指出具体条目。

## 8. 回归测试

**环境**：本机，以及编译机 ubuntu20 的 Python 3.8.10（按验证手册跑两次）。命令：`python3 -m unittest discover -s tests -t tests`。

| 编号 | 用例 | 判据 |
| ---- | ---- | ---- |
| T1 | shell 中设置 3 个变量后运行 `codex-work` | 假 codex 看到的环境中没有这 3 个变量 |
| T2 | `env work CODEX_API_KEY=k` 后同 T1 | 假 codex 看到 `CODEX_API_KEY=k`，另两个仍被清除 |
| T3 | `run work -- env`、`login work`（假 codex 记录环境） | 与 T1 / T2 结论相同（`run` 与启动命令对拍；`code` 共用 `_account_exec_args`，不单独启动 VS Code） |
| T4b | 用 v0.8 内容预置启动命令后 `usage --live` | 退出 1，提示 `` launcher is stale; run `multi-codex apply` first ``（`cli.py:965`；测试只断言 `launcher is stale` 与 `multi-codex apply` 两段）；`apply` 后正常返回额度 |
| T4 | 用 v0.8 的启动命令内容预置文件后 `list`、`apply` | `list` STATUS 为 `launcher stale`；`apply` 后为 `ok`，文件含 `unset` 行 |
| T5 | `render` 逐字节 | `test_ergonomics.py` 更新后的期望内容 |
| T6 | `list` 与 `doctor` 在设置了 `CODEX_API_KEY` 时 | 告警含 `launchers clear it` 与变量名；`doctor` env 检查为 warn |
| T7 | `config.json` 的 `shared.items` 含 `auth.json`、`State_5.SQLITE`、`sessions` 之一 | `list` 退出 1 并指出该项；`doctor` config 检查为 fail |
| T8 | `init --shared-items skills,history.jsonl` 与 `init --shared-items skills,logs_2.sqlite-wal` | 前者成功（history.jsonl 允许）；后者退出 2，配置不变 |
| T9 | 共享账号 `set a --shared-exclude skills` | `a/skills` 受管软链被删除，输出 `sharing disabled for this item`；其它项不变；`managed_links` 不含 `skills`；共享目录内容不变；配置写出 `shared_exclude` |
| T10 | 被排除的项原本在 `managed_links` 中，之后被用户换成真实目录或指向别处的软链；另一账号的同名项从未受管 | 前者原样保留并记 skip（`no longer the link multi-codex created`）；后者原样保留且没有输出 |
| T11 | `set a --shared-include skills` | 重建软链，`managed_links` 恢复；配置不再写 `shared_exclude` |
| T12 | 未共享账号 `set b --shared-exclude skills`，再 `set b --shared` | 第一步只改配置并提示 `sharing is off`；第二步不建 `skills` 链接 |
| T13 | 同名同时 include 与 exclude；名称带 `/`；排除不在清单里的名称 | 前两者退出 2，配置与文件系统不变；第三个有 `not in shared.items` 提示，退出 0 |
| T14 | `config.toml` 在共享清单中：同一条命令 `--shared-exclude config.toml --config-from b`；先排除、再单独 `--config-from b` | 前者退出 3（计划按当前文件判断，config.toml 仍是共享软链，内容不同）；后者正常复制 |
| T15 | `list`、`list -v`、`list --json` | 表格 SHARED 为 `yes (not: skills)`；JSON 含 `shared_exclude`；没有退出项时 `list -v` 与 v0.8 逐字相同 |
| T16 | `apply -f` 写了 / 没写 `shared_exclude`；改过名的账号在文件里省略 `dir` / 显式写了不同的 `dir` | 前两者分别按文件生效；省略 `dir` 时目录不变且无告警，写了不同 `dir` 时目录不变并有告警 |
| T17 | `rename work job` | `codex-job` 创建、`codex-work` 删除；`<root>/work` 及其中文件不变；共享软链仍在且仍受管；配置中 `job` 带 `"dir": "work"`；经 `codex-job` 运行时 `CODEX_HOME` 为 `<root>/work`；输出含 `directory … is unchanged`；之后 `apply` 显示 already up to date，`doctor` 的 drift 检查为 ok |
| T18 | 改名与默认账号、绑定 | `use work` 后 `rename work job`：`use` 显示 `job`；绑定到 `work` 的目录改为 `job`，`run` 省略账号名时用 `job` |
| T19 | 改名后 `rename job work` | 配置中不再有 `dir` 字段，与改名前逐字相同 |
| T20 | 改名的失败路径 | 未登记退出 1；只改大小写退出 2；NEW 已存在退出 3；`rename work job` 后 `add work` 与 `rename x work`（x 为另一个账号）都退出 3，配置与文件系统不变 |
| T20b | `rename work job` 后 `migrate-default work`（`~/.codex` 为真实目录；以及 `~/.codex` 已链到 `<root>/work`） | 两种都退出 3，配置与文件系统不变 |
| T20c | `use work`、`rename work job` 后 `restore job`；另模拟“注销收敛失败、标记仍在”后重跑 `restore job` | 前者 `<root>/work` 移回 `~/.codex`、`codex-job` 删除；后者能续跑完成并删除标记 |
| T21 | 手写配置：两个账号 `dir` 相同、账号名等于另一个账号的 `dir`、`dir` 非法 | `list` 退出 1 并指出原因 |
| T22 | 改名后 `code` / `app` 的数据目录 | `apps.gui_data_dir` 返回 `<root>/.apps/work/…`（函数级测试，不实际启动 VS Code） |
| T23 | `rename --dry-run` | 输出计划，配置与文件系统不变 |
| T24 | 帮助与补全 | `-h` Advanced 组含 `rename`；三种 shell 补全含 `rename`、`--shared-exclude`、`--shared-include`；`rename ` 补账号名 |
| T25 | 回归 | 现有全部用例通过（`test_ergonomics.py` 的期望内容按 §4 更新） |

## 9. 日志 / 观测点

- 收敛动作沿用现有输出：`[multi-codex] <状态> <种类> <路径> (<原因>)`。改名时可见 `delete launcher …/codex-OLD (account removed)` 与 `create launcher …/codex-NEW`；退出共享时可见 `delete shared-link … (sharing disabled for this item)`。
- 改名成功：`renamed OLD to NEW; directory … is unchanged`。
- 冲突：`account 'X' would use the directory of account 'Y'`、`account 'NEW' already exists`。
- 配置错误：`'shared.items' must not include 'X': it holds account-specific state`；`'dir' must be a valid account directory name`。
- 隔离告警：`… is set in this shell; codex-<name> launchers clear it, but plain codex still uses it`。
- 退出共享提示：`note: … is not in shared.items …`、`note: sharing is off for …`。
