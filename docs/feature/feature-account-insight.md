# 账号可见性：身份、额度、体检与 JSON 输出（v0.3）

> 2026-10-01 注记：代码已落地（未提交，等待编译机验证）。缺陷修复 U1 另见 `docs/bugfix/fix-keyring-migration.md`，两份方案共用新模块 `identity.py`。
> - 身份：`src/multi_codex/identity.py` 的 `read_identity`、`duplicate_groups`。
> - 额度：`src/multi_codex/usage.py`，其中 `local_snapshot` 读本地快照，`live_snapshot` 与 `_AppServer` 负责实时查询。
> - 体检：`src/multi_codex/doctor.py` 的 `run_checks`。
> - 命令行：`src/multi_codex/cli.py` 的 `cmd_list`、`cmd_usage`、`cmd_doctor`。
> - §8 的用例由 `tests/test_insight.py` 覆盖，假 app-server 在 `tests/fake_app_server.py`。
> - 实现时有一处细化：`doctor` 的 `account:<名>` 只在“凭据确实需要从钥匙串读取”时报 warn，即 `keyring` 模式，或者 `auto` 模式且没有 `auth.json`；`auto` 模式下有 `auth.json` 时照常读取身份。

## 1. 背景

目前 `multi-codex list` 只显示目录、代理、共享和启动命令的状态，看不到每个账号登录的是谁、套餐是什么、额度还剩多少，也没有一条命令能把安装和配置问题一次查出来。输出只有给人看的表格，脚本无法可靠解析。

本方案涉及的 Codex 本地数据格式，都已在 openai/codex 源码中核对（基线 `6b4daafdb445340e5af66f067ad4057e6ed9fd81`，路径省略 `codex-rs/` 前缀）：

| 数据 | 格式 | 出处 |
| ---- | ---- | ---- |
| `auth.json` | 顶层字段 `auth_mode`、`OPENAI_API_KEY`、`tokens`、`last_refresh`；`tokens.id_token` 是原始 JWT 字符串；文件权限 0600 | `login/src/auth/storage.rs:47-73,214-231`；`login/src/token_data.rs:10-25,208-213` |
| `auth_mode` | `chatgpt`、`apikey`，另有 `chatgptAuthTokens`、`agentIdentity` 等 | `protocol/src/auth.rs:7-38` |
| id_token 中的身份 | 邮箱取 `email`，没有时取 `https://api.openai.com/profile`.`email`；`https://api.openai.com/auth` 下有 `chatgpt_plan_type`、`chatgpt_user_id`（缺省时用 `user_id`）、`chatgpt_account_id`；Codex 自己也只做 base64url 解码、不验签 | `login/src/token_data.rs:71-99,129-140,174-198` |
| 会话记录路径 | `sessions/YYYY/MM/DD/rollout-*.jsonl`（按本地时间分目录）；归档在 `archived_sessions/` 下平铺 | `rollout/src/recorder.rs:1723-1745`；`rollout/src/lib.rs:86-87` |
| 会话记录中的额度 | 行格式 `{"timestamp","type":"event_msg","payload":{"type":"token_count","rate_limits":{...}}}`；`rate_limits` 可能为 null；`primary` / `secondary` 窗口含 `used_percent`（浮点）、`window_minutes`、`resets_at`（epoch 秒）；另有 `limit_id`、`plan_type`、`credits` | `protocol/src/protocol.rs:1422,2343-2408`；`rollout/src/policy.rs:113`；`core/src/state/session.rs:125,334-338` |
| 压缩的会话记录 | 存在 `.jsonl.zst` 变体，但由默认关闭的开发中特性控制，且只压缩超过 7 天的文件 | `rollout/src/compression.rs:29,363`；`features/src/lib.rs:1198-1201` |
| app-server | `codex app-server` 默认走 stdio，每行一条 JSON；不带 `jsonrpc` 字段；先 `initialize` 再调其它方法；关闭 stdin 即退出（45 秒看门狗兜底） | `cli/src/main.rs:568-579`；`app-server-transport/src/transport/stdio.rs:73,124-178`；`app-server-protocol/src/rpc.rs:1-2`；`app-server/src/message_processor.rs:975-976` |
| `account/rateLimits/read` | 非实验方法，0.48.0 起提供，params 可以省略。当前版本的响应含 `rateLimits`、`rateLimitsByLimitId`，窗口字段为驼峰命名的 `usedPercent`（整数）、`windowDurationMins`、`resetsAt`（epoch 秒）。0.48.0 的响应只有 `rateLimits`，窗口字段是蛇形命名的 `used_percent`（浮点）、`window_minutes`、`resets_at`，也没有 `limitId` | 当前：`app-server-protocol/src/protocol/common.rs:1322-1326`、`v2/account.rs:315-345,752-768`；0.48.0：tag `rust-v0.48.0` 的 `app-server-protocol/src/protocol.rs:533-535`、`protocol/src/protocol.rs:648-663` |
| app-server 的错误码 | 未登录：-32600，信息为 `codex account authentication required to read rate limits`；用 API key 登录：-32600，信息为 `chatgpt authentication required to read rate limits`；后端调用失败：-32603；未知方法：-32600，信息以 `Invalid request:` 开头，内容来自 serde 的 `unknown variant` 报错 | `app-server/src/request_processors/account_processor.rs:1194-1237`；`app-server/src/message_processor.rs:111-116`；`app-server/src/error_code.rs` |
| 会话记录的时间戳 | 格式为 `YYYY-MM-DDTHH:MM:SS.mmmZ`；Python 3.8 的 `datetime.fromisoformat` 不接受末尾的 `Z` | `rollout/src/recorder.rs:927-928` |
| 读额度的副作用 | 一定会联网；如果 access token 将在 5 分钟内过期，或上次刷新已超过 8 天，会先刷新 token 并写回该账号的凭据存储 | `app-server/src/request_processors/account_processor.rs:1208-1230`；`login/src/auth/manager.rs:203-204,1600-1622,3001-3023` |
| `codex --version` | 输出 `codex-cli X.Y.Z` | `cli/src/main.rs:117-127`；本机实测 `codex-cli 0.159.2` |

本机实测：Plus 账号的会话记录里有 `primary`（300 分钟）和 `secondary`（10080 分钟）两个窗口；Free 账号只有 `primary`，为 43200 分钟（30 天），`secondary` 为 null。

## 2. 目标 / 非目标

**目标**

1. U2：`list` 显示每个账号登录的邮箱和套餐；两个账号目录登录的是同一个 ChatGPT 账号和工作区时给出警告。
2. U3：新命令 `usage`，默认从本地会话记录读出各账号最近一次的额度快照；`--live` 时通过官方 `codex app-server` 联网读取实时额度。
3. U4：新命令 `doctor`，只读地检查安装、配置、环境变量、各账号状态以及配置与实际文件是否一致，并给出对应的修复命令。
4. U5：`list`、`usage`、`doctor` 支持 `--json`。

**非目标**

- 不自己调用 ChatGPT 后端接口，不在任何输出中显示令牌。
- `doctor` 不提供 `--fix`：已有的 `apply` 就是修复手段，`doctor` 只给出建议命令。
- 不读取钥匙串中的凭据：存储模式为 keyring，或者为 auto 且没有 `auth.json` 时，身份显示为 `keyring`。
- 不解码 `.jsonl.zst` 压缩记录（标准库没有 zstd，且这类文件只会是 7 天前的旧数据）。
- 不按额度自动选账号，也不做低额度提醒。
- 不改动任何写命令的行为。

## 3. 假设与约束

- 只依赖 Python 3.8 标准库。
- `list`、`usage`、`doctor` 都是只读命令：不加写锁，有未完成的迁移时也照常运行，与现在的 `list` 一致（`src/multi_codex/cli.py:103-104`）。
- 迁移事务记录损坏时，现有的 `cli.main` 在分派前调用 `migrate.pending_journal_notice()`（`cli.py:100`），会抛出 `JournalError` 并以退出码 1 结束（`cli.py:112-117`）。`doctor` 正是要查这类问题，`usage` 也与迁移无关，所以这两个命令在 `cli.py:100` 之前分派；`list` 保持现状。
- 存储模式沿用 U1 的 `identity.credentials_store`，包括账号目录与系统级配置 `/etc/codex/config.toml` 两层。
- 从 id_token 读出的套餐是**该 token 签发时**的值，下次刷新 token 时才会更新。输出中套餐一栏不做额外说明，README 写明这一点。
- `usage --live` 通过该账号的启动命令 `codex-<名> app-server` 运行，所以会使用该账号的代理设置；启动命令状态不为 `ok` 时不运行，提示先执行 `multi-codex apply`。
- 要求 Codex ≥ 0.48.0 才能使用 `--live`（`account/rateLimits/read` 的最早版本）。

## 4. 涉及模块

| 区域 | 行号锚点（基线 `fb548a8`） | 改动类型 | 改动点 |
| ---- | ---- | ---- | ---- |
| 身份 | `src/multi_codex/identity.py`（由 U1 新建） | 新增 | `read_identity(account_dir) -> Identity`、`duplicate_groups(identities)`，见 §5.1.1 |
| 额度 | 新文件 `src/multi_codex/usage.py` | 新增 | 读本地快照 `local_snapshot(account_dir)`、读实时额度 `live_snapshot(launcher_path, timeout)`、格式化输出，见 §5.1.2 |
| 体检 | 新文件 `src/multi_codex/doctor.py` | 新增 | `run_checks(config, exists) -> List[Check]`，见 §5.1.3 |
| 命令行 | `src/multi_codex/cli.py:79` `list` 子命令 | 修改 | 增加 `--json` |
| 命令行 | `src/multi_codex/cli.py:79` 之后 | 新增 | `usage [NAME ...] [--live] [--timeout SEC] [--json]`、`doctor [--json]` 子命令 |
| 命令行 | `src/multi_codex/cli.py:99-100` 之间 | 修改 | `usage`、`doctor` 在调用 `pending_journal_notice` 之前分派，不加锁 |
| 命令行 | `src/multi_codex/cli.py:247-268` `cmd_list` | 修改 | 新增 LOGIN、PLAN 两列；重复登录警告；`--json` 分支 |
| 命令行 | `src/multi_codex/cli.py:247` 之后 | 新增 | `cmd_usage`、`cmd_doctor` |
| 测试 | 新文件 `tests/test_insight.py`；`tests/helpers.py` 中的假 codex | 新增、修改 | 见 §8；假 codex 增加 `app-server` 与 `--version` 的模拟 |
| 文档 | `README.md`、`README.zh-CN.md` 的命令表、`list` 说明与退出码；`CHANGELOG.md`；`docs/feature/feature-account-manager.md` §5.1.2 命令表 | 修改 | 说明新命令与新列 |

## 5. 方案

### 5.1 实现要点

#### 5.1.1 身份（U2）

`read_identity(account_dir)` 只读，返回以下字段：

| 字段 | 取值 |
| ---- | ---- |
| `login` | `chatgpt`、`apikey`、`logged-out`、`keyring`、`unreadable`、`other` |
| `email`、`plan` | 只在 `chatgpt` 时有值；`plan` 取 `chatgpt_plan_type` 原值，例如 `plus`、`free` |
| `user_id`、`workspace_id` | 只在内部比较重复登录时使用，不显示，JSON 中也不输出。`workspace_id` 取 `chatgpt_account_id`，缺失时用 `tokens.account_id`；后者是 Codex 登录时从同一个 claim 复制过来的（上游 `login/src/server.rs:856-861`） |
| `store` | 存储模式，取自 U1 的 `credentials_store` |

判定顺序：

1. 存储模式为 `keyring`：一律为 `keyring`，不看 `auth.json`。keyring 模式只读钥匙串、不回落读文件（上游 `login/src/auth/storage.rs:310-313`），从 file 模式切过来时残留的旧 `auth.json` 不代表当前登录。
2. `auth.json` 不存在：存储模式为 `auto` 时为 `keyring`，否则为 `logged-out`。
3. 读不了或不是合法 JSON：`unreadable`。
4. `tokens.id_token` 存在：按 JWT 第二段做 base64url 解码（补齐 `=`），读出邮箱、套餐、用户 ID 和工作区 ID，`login` 为 `chatgpt`；解码失败为 `unreadable`。`auth_mode` 缺失时也按这一条处理（旧版 Codex 写的文件没有 `auth_mode`）。
5. `auth_mode` 为 `apikey`，或者只有非空的 `OPENAI_API_KEY`：`apikey`。
6. 其它情况：`other`。

令牌字符串只在函数内部解码时使用，不保存到返回值，也不写进任何输出或异常信息。

`list` 的表格改为 `NAME  DIR  PROXY  SHARED  LAUNCHER  LOGIN  PLAN`。LOGIN 列显示邮箱、`api-key`、`-`（未登录）、`keyring` 或 `unreadable`；PLAN 列在非 ChatGPT 登录时显示 `-`。

重复登录：只有 `user_id` 和 `workspace_id` 都非空的账号参与比较；两者都相同的账号归为一组，组内超过一个时，对每组输出一条警告：`accounts a, b are logged in as the same ChatGPT account (<邮箱>); they share one usage quota`。同一个邮箱在不同工作区登录不算重复。

#### 5.1.2 额度（U3）

命令：`multi-codex usage [NAME ...] [--live] [--timeout SEC] [--json]`。不给账号名时列出全部账号；给了未登记的名字时报错，该账号记为失败。

**本地快照（默认）**

1. 候选文件为 `sessions/*/*/*/rollout-*.jsonl` 和 `archived_sessions/rollout-*.jsonl`，按修改时间从新到旧排序，最多看 20 个。
2. 从文件末尾向前分块读取（块大小 64 KiB），只解析包含 `"rate_limits":{` 的行；行内容必须满足 `type == "event_msg"`、`payload.type == "token_count"`，且 `payload.rate_limits` 是对象。
3. 在第一个含有快照的文件里，按 `limit_id` 各保留最新的一条（缺失时记为 `codex`），然后停止扫描。快照时间取该行的 `timestamp`，用 `strptime("%Y-%m-%dT%H:%M:%S.%fZ")` 按 UTC 解析，失败时再试不带毫秒的格式；仍然失败就退回文件修改时间。
4. 20 个文件里都没有快照时，结果为“暂无数据”，不算失败。
5. 账号目录下的 `sessions` 是软链（例如被加进了共享项）时，输出中注明“会话记录与其它账号共享，额度可能不属于本账号”。

**实时额度（`--live`）**

1. 只对启动命令状态为 `ok` 的账号运行（复用 `accounts.launcher_status`）。
2. 用 `subprocess.Popen([启动命令, "app-server"], start_new_session=True)` 启动，stdin 和 stdout 用管道，stderr 写入临时文件，失败时取最后 20 行附进错误信息。必须开新会话：npm 安装的 `codex` 是 node 包装器，再拉起真正的程序，包装器只转发 SIGINT、SIGTERM、SIGHUP（上游 `codex-cli/bin/codex.js:241,259-271`），只对包装器发 SIGKILL 会让真正的 app-server 变成孤儿进程。
3. 依次发送：
   - `{"id":1,"method":"initialize","params":{"clientInfo":{"name":"multi-codex","title":"multi-codex","version":"<版本>"}}}`；
   - 收到 id 1 的响应后，发 `{"method":"initialized"}`；
   - 再发 `{"id":2,"method":"account/rateLimits/read"}`。
4. 用读线程加队列读取 stdout。只有同时带 `id`、带 `result` 或 `error`、且不带 `method` 的行才算响应；其它行（通知、服务端发来的请求）跳过。一直读到 id 2 的响应，或超过 `--timeout`。`--timeout` 是从启动子进程起算的总时限，包括 `initialize`；默认 30 秒，必须是大于 0 的数字，否则按参数错误返回 2。
5. 收尾分两种情况。正常拿到响应时：关闭 stdin，最多等 5 秒，仍未退出再按下面的方式结束进程组。超时或出错时：不再等待，直接 `os.killpg(pgid, SIGTERM)`，等 2 秒后**一律**再执行 `os.killpg(pgid, SIGKILL)`，进程组已不存在时（`ProcessLookupError`）忽略。不能只用 `proc.wait` 判断是否退出：它只看包装器进程，包装器退出后，忽略 SIGTERM 的子进程仍会残留。所以超时路径的总耗时不超过 `--timeout` 加 2 秒。任何异常路径（包括超时、解析失败、KeyboardInterrupt）都走同一个 `finally` 回收。
6. 结果优先用 `rateLimitsByLimitId`；它为空或缺失时，用 `rateLimits`，`limit_id` 取它的 `limitId`（缺失时记为 `codex`）。窗口字段两种命名都接受：驼峰的 `usedPercent`、`windowDurationMins`、`resetsAt`，蛇形的 `used_percent`、`window_minutes`、`resets_at`。统一换算成与本地快照相同的内部结构；多个 `limit_id` 按名字排序输出。
7. 不传 `excludeResetCreditDetails`：旧版本是否接受这个字段尚未核实，多一次查询的代价可以接受。
8. 错误处理：
   - 错误码为 -32601，或者为 -32600 且信息包含 `unknown variant`：判为方法不存在，提示需要 Codex 0.48.0 或更高版本；
   - 其它错误响应（包括 -32600 的未登录、API key 登录，以及 -32603 的后端失败）：原样显示错误码和信息；
   - 超时、子进程提前退出、输出不是合法 JSON：都记为该账号失败。
9. 多个账号依次查询，不并发：避免同时为多个账号拉起 app-server。

**显示格式**

```text
work  (plus, live)
  codex  5h   12% used  resets 14:05 (in 2h31m)
         7d   40% used  resets 10-06 09:00 (in 5d)
personal  (free, snapshot 2026-09-30 01:20, 23h ago)
  codex  30d   1% used  resets 10-30 13:03 (in 29d)
```

- 窗口名由 `window_minutes` 推出：小于 1440 分钟显示 `<小时>h`，否则显示 `<天>d`；缺失时显示 `?`。
- 本地快照中 `resets_at` 已经过去时，这一行显示 `reset since snapshot`，不显示百分比。
- 时间按本地时区显示：当天只显示 `HH:MM`，否则显示 `MM-DD HH:MM`。

**未配置或没有账号**：给人看的输出为 `no accounts registered`，退出码 0；`--json` 时输出 `{"version": 1, "accounts": []}`。

**退出码**：0 表示所有账号都处理完毕（“暂无数据”也算）；1 表示至少一个账号失败，包括名字未登记、实时查询出错；2 表示参数不合法，例如 `--timeout` 不是正数。

#### 5.1.3 体检（U4）

`multi-codex doctor [--json]`。每项检查输出一行 `ok`、`warn` 或 `fail`，附说明；不是 `ok` 时再附一条建议命令。

| 检查 | fail 条件 | warn 条件 |
| ---- | ---- | ---- |
| `codex` | PATH 上找不到 `codex` | `codex --version`（超时 10 秒）无法解析；或版本低于 0.48.0，提示 `usage --live` 不可用 |
| `config` | 配置文件存在但无法解析 | 配置文件不存在。此时跳过后面的账号检查 |
| `migration` | 有未完成的迁移事务（复用 `migrate.pending_journal_notice`）；或者事务记录损坏（捕获 `JournalError`，把它的信息作为说明） | — |
| `path` | — | `bin_dir` 不在 PATH 中（逐项按真实路径比较） |
| `env` | — | 设置了 `CODEX_HOME`，或者设置了 `platform.ISOLATION_BREAKING_ENV` 中的任一变量 |
| `default-dir` | — | `~/.codex` 是软链，但指向的不是任何已登记的账号目录 |
| `drift` | 收敛计划中有冲突 | 收敛计划中有将会执行的动作（create、update、delete）。建议命令为 `multi-codex apply` |
| `account:<名>` | 账号目录不存在 | 未登录；凭据不可读；身份为 `keyring`（即存储模式为 keyring，或者为 auto 且没有 `auth.json`，登录身份无法从文件读取；本地额度读的是会话记录，不受影响）；`sessions` 是软链 |
| `duplicates` | — | 存在 §5.1.1 中的重复登录 |

- `default-dir` 在 `~/.codex` 是普通目录或不存在时为 `ok`，说明里写明“未迁移”或“不存在”；是指向已登记账号的软链时也为 `ok`，说明里写出账号名。
- 收敛计划中的 skip 动作是持久状态，运行 `apply` 也不会消失，例如共享目录里本来就没有某个条目（`shared.py:36-40`）。这类动作只作为 `drift` 项的说明列出，不改变它的状态，也不建议 `apply`。
- `drift` 的做法：`accounts.plan(config, config.copy(), orphan_scope=ALL_ORPHANS)`。`plan_shared` 会就地修改第二个参数的 `managed_links`，所以必须传副本，不能把同一个对象传两次。计划只读文件系统，不执行任何动作。
- `doctor` 不运行 `codex login status`，也不联网。
- **退出码**：没有 `fail` 时为 0（允许有 `warn`），有任一 `fail` 时为 1。

#### 5.1.4 JSON 输出（U5）

- 加了 `--json` 时，stdout 上只输出一个 JSON 对象（`ensure_ascii=False`，缩进 2，末尾换行）；所有警告照常写到 stderr。
- 命令整体失败（例如配置文件无法解析）时，stdout 不输出任何内容，错误写到 stderr，退出码 1，与非 JSON 模式一致；消费方应先检查退出码。现在 `cmd_list` 在未配置时用 `info()` 往 stdout 输出提示（`cli.py:250-253`），JSON 模式下不再调用它。
- 每个 JSON 对象都带 `"version": 1`。以后只会新增字段；删除字段或改变字段含义时提升版本号。

`list --json`：

```json
{"version": 1, "configured": true, "root": "...", "bin_dir": "...", "shared_dir": null,
 "accounts": [{"name": "work", "dir": "...", "dir_status": "ok", "proxy": "inherit", "shared": false,
               "launcher": "ok", "credentials_store": "file",
               "login": {"type": "chatgpt", "email": "a@b.c", "plan": "plus"}}],
 "duplicates": [["work", "work2"]]}
```

未配置时为 `{"version": 1, "configured": false, "accounts": [], "duplicates": []}`。

`usage --json`：

```json
{"version": 1, "accounts": [{"name": "work", "source": "local", "ok": true, "error": null,
  "snapshot_time": "2026-09-30T01:20:00Z", "sessions_shared": false,
  "limits": [{"limit_id": "codex", "plan_type": "plus",
              "windows": [{"kind": "primary", "used_percent": 12.0, "window_minutes": 300,
                           "resets_at": 1790000000}],
              "credits": {"has_credits": false, "unlimited": false, "balance": null}}]}]}
```

- `source` 为 `local` 或 `live`；`--live` 时 `snapshot_time` 为查询时刻。
- `used_percent` 一律输出数值：本地快照是浮点数，实时结果是整数，消费方不应依赖类型差异。
- 失败时 `ok` 为 false，`error` 为错误信息，`limits` 为空数组。

`doctor --json`：`{"version": 1, "ok": true, "checks": [{"id": "codex", "status": "ok", "message": "codex-cli 0.159.2", "hint": null}]}`。

### 5.2 接口变更

| 类型 | 接口 | 兼容性 |
| ---- | ---- | ---- |
| 新增 | `usage` 子命令及其 `--live`、`--timeout`、`--json` 参数 | 新增 |
| 新增 | `doctor` 子命令及其 `--json` 参数 | 新增 |
| 新增 | `list --json` | 新增 |
| 修改 | `list` 的表格增加 LOGIN、PLAN 两列 | 给人看的输出，不承诺格式稳定。按列位置解析旧输出的脚本前 5 列不变；README 建议改用 `--json` |
| 新增 | 三份 JSON 结构，以 `version` 字段标示格式版本 | 新增，规则见 §5.1.4 |

仓库中没有 `docs/reference/` 目录，不涉及 reference 章节的同步检查。

## 6. 备选方案与决策

| 问题 | 方案 | 结论 |
| ---- | ---- | ---- |
| 实时额度从哪里取 | 自己带着 access token 请求 `chatgpt.com/backend-api/wham/usage`（Loongphy/codex-auth 的默认做法） | 否决：要读取并发送令牌；接口没有公开文档；该项目的 README 自己承认可能违反服务条款 |
| 实时额度从哪里取 | 官方 `codex app-server` 的 `account/rateLimits/read` | 采纳：官方的非实验接口，令牌全程由 Codex 自己处理 |
| 登录状态怎么判断 | 逐个账号运行 `codex login status` | 否决：每个账号都要起一个进程，`list` 会变慢；结果和读 `auth.json` 一样，只多了钥匙串这一种情况 |
| `--live` 失败时 | 自动退回本地快照 | 否决：两者的新旧程度不同，悄悄退回会让用户误以为看到的是实时数据。失败就如实报错 |

## 7. 影响分析

- **正向调用链**：
  - `identity.read_identity` 由 `cmd_list`、`cmd_usage`（读套餐）、`doctor` 调用；
  - `usage.live_snapshot` 通过账号启动命令拉起 `codex app-server`，启动命令导出 `CODEX_HOME` 和代理变量后 `exec codex`（`launcher.py:28-54`），所以查询走的是该账号自己的目录和代理；
  - `doctor` 调用 `accounts.plan`（`accounts.py:38`），该函数只读文件系统，副作用全部在动作的 `run` 回调里，`doctor` 不执行这些回调。
- **反向**：
  - 写命令和迁移流程都不调用新模块（U1 的 `credentials_store` 除外，见 bugfix 方案）。
  - `accounts.plan` 会修改传入的 new 配置，`doctor` 传入副本，不影响后续逻辑，也不会写回 `config.json`。
  - 新命令不加写锁，可以和写命令同时运行。最坏情况是读到写命令执行到一半的状态，再运行一次即可；它们不写任何文件，不会造成损坏。
- **运行时**：
  - `list` 每个账号多读一个 `auth.json`、一个 `config.toml`，都是 KB 级。
  - `usage` 本地模式每个账号最多扫 20 个文件，只解析包含 `"rate_limits":{` 的行，读到第一个含快照的文件就停止。
  - `usage --live` 每个账号拉起一个 codex 进程，一次请求，之后由我们关闭；进程数量与账号数相同，依次运行。
  - `doctor` 运行一次 `codex --version`。
- **对 Codex 数据的影响**：`--live` 期间 Codex 可能刷新令牌并写回该账号的 `auth.json`，也会在该账号目录中写日志数据库。这与用户自己启动一次 Codex 相同；每个账号的目录各自独立，不会写到别的账号。README 中写明这一点。
- **对外语义**：`list` 的表格多了两列；新增 3 种 JSON 输出；退出码契约中 0 和 1 的含义不变，`doctor` 和 `usage` 按 §5.1.2、§5.1.3 使用它们。
- **部署形态**：
  - 不登录、只用 API key、或使用钥匙串存储的账号，身份显示为 `-`、`api-key` 或 `keyring`；本地额度照常从会话记录读取；
  - Codex 版本低于 0.48.0 时，`--live` 报错并提示升级；
  - 代理不可用时，`--live` 报超时或连接错误，本地模式不受影响。

## 8. 回归测试

新文件 `tests/test_insight.py`，全部在临时 HOME 中运行。假 codex 扩展为：收到 `--version` 时输出 `codex-cli 0.159.2`；收到 `app-server` 时用 Python 实现一个按行应答的模拟服务，由环境变量 `FAKE_APP_SERVER_MODE` 决定行为（`ok`、`auth-error`、`no-method`、`hang`、`garbage`、`exit`）。

**身份（U2）**

1. 用测试密钥现场构造的 id_token（邮箱、套餐、用户 ID、工作区 ID）：`list` 显示邮箱和套餐；stdout 和 stderr 中都不含令牌原文。
2. 只有 `profile.email`、没有顶层 `email`：显示 profile 中的邮箱。
3. `{"auth_mode":"apikey","OPENAI_API_KEY":"sk-x"}` 显示为 `api-key`；两个账号的 id_token 都缺少用户 ID 和工作区 ID 时，不报重复；没有 `auth.json` 显示为 `-`；存储模式为 keyring 时显示 `keyring`；`auth.json` 内容损坏时显示 `unreadable`，命令退出码仍为 0。
4. 两个账号的用户 ID 和工作区 ID 相同：有重复警告；用户 ID 相同但工作区 ID 不同：没有警告。
5. 前 5 列与 v0.2.0 的输出逐字一致（回归）。
5a. 系统级配置（`MULTI_CODEX_TEST_SYSTEM_CONFIG`）设为 keyring：`list` 中各账号显示 `keyring`。
5b. 存储模式为 keyring 但残留旧 `auth.json`：显示 `keyring`，不参与重复登录比较。

**额度（U3）**

6. 本地：构造两个 rollout 文件，新文件中最后一个 `token_count` 的 `rate_limits` 为 null、前面一个有值：取到有值的那条；旧文件中更新的值不会被取用。
7. 本地：同一文件中有两个 `limit_id`，各取最新的一条。
8. 本地：只在 `archived_sessions/` 中有快照时也能读到；没有任何快照时显示“暂无数据”，退出码 0。
9. 本地：`resets_at` 已经过去时显示 `reset since snapshot`；`sessions` 为软链时输出共享提示。
10. 本地：单个文件超过 64 KiB、快照位于块边界附近时仍能正确读到（覆盖反向分块读取）。
11. `--live` + `ok`：显示实时数据，`source` 为 `live`；模拟服务记录收到的请求，核对 `initialize` 在前、请求中没有 `jsonrpc` 字段。
12. `--live` + `auth-error` / `no-method` / `garbage` / `exit`：对应账号失败，错误信息分别包含服务端返回的信息、版本提示、“无效响应”和 stderr 尾部内容；退出码 1。`auth-error` 与 `no-method` 都按真实格式返回 -32600，前者信息为 `codex account authentication required to read rate limits`，后者为 `Invalid request: unknown variant ...`。
13. `--live` + `hang`，`--timeout 2`：失败，总耗时在 2 到 5 秒之间（超时 2 秒，加最多 2 秒的 SIGTERM 等待）。模拟服务由一层 shell 包装器拉起一个忽略 SIGTERM 的子进程（模拟 npm 的 node 包装器），测试结束时用 `ps` 核对包装器和子进程都已退出。
13a. `--live` 时服务端在响应前先发一条通知和一条带 `id`、`method` 的服务端请求：都被跳过，仍拿到正确结果。
13b. `--live` 返回 0.48.0 的蛇形字段结构（只有 `rateLimits`、没有 `limitId`）：正确显示，`limit_id` 记为 `codex`。
13c. `--timeout 0`、`--timeout abc`：退出码 2。
14. `--live` 时启动命令状态为 stale：不运行，提示 `multi-codex apply`。
15. 未登记的账号名：该项失败，其它账号照常输出，退出码 1。

**体检（U4）**

16. 一切正常（测试中先把临时 HOME 的 `~/.local/bin` 加进 PATH）：所有检查为 `ok`，退出码 0。
17. PATH 中没有 codex：`codex` 为 fail，退出码 1。
18. `bin_dir` 不在 PATH；设置了 `CODEX_HOME`：对应项为 warn，退出码 0。
19. 删掉一个启动命令、删掉一个受管共享软链：`drift` 为 warn，建议 `multi-codex apply`；之后运行 `apply` 再运行 `doctor`，回到全部 `ok`。
20. 共享软链位置被换成真实目录：`drift` 为 fail。
21. 运行 `doctor` 前后 `config.json` 的字节完全相同（证明传了副本且没有写回）。
22. 有未完成的迁移事务：`migration` 为 fail；`doctor` 本身仍能运行。
22a. 事务记录内容损坏：`doctor` 仍输出全部检查，`migration` 为 fail；`doctor --json` 的 stdout 可以解析。
22b. 共享目录中缺少某个共享条目：`drift` 为 ok，说明中列出该 skip；运行 `apply` 后仍为 ok。

**JSON（U5）**

23. 三个命令加 `--json` 时，stdout 能被 `json.loads` 解析，包含 `version: 1`；未配置时的 `list --json` 符合 §5.1.4。
24. 有警告时，警告只出现在 stderr，不混入 stdout。

**整体回归**

25. 现有测试全部通过；在编译机上用 Python 3.8 跑完整测试；CI 中 macOS、Linux 两个平台通过。
26. 人工实机项（本机，不在 CI 中）：对本机三个真实账号运行 `list`、`usage`、`usage --live`、`doctor`，与 `codex-<名> login status` 及 Codex 中 `/status` 显示的额度对照。

## 9. 日志 / 观测点

- `list`：重复登录时 stderr 输出 `[multi-codex] warning: accounts a, b are logged in as the same ChatGPT account (...)`。
- `usage`：失败的账号在 stdout 中显示 `<名>  error: <原因>`；`--live` 失败时原因中附带 app-server 的 stderr 尾部；子进程被强制结束时附带 `terminated after timeout`。
- `doctor`：每项一行 `ok|warn|fail <检查名> <说明>`，有建议时下一行缩进输出 `fix: <命令>`；最后一行汇总 `N ok, N warn, N fail`。
- 所有 JSON 输出都可以用 `jq` 校验；`version` 字段用来判断格式版本。
