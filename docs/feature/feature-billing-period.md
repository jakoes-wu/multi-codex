# multi-codex：`list -v` 与 `list --json` 显示订阅账期（BILLING UNTIL）

> 2026-10-16 注记：已落地，待发版。基线：main `b1579d0`（v0.11.0）。plan-review 两轮收敛（第 1 轮 0 高 1 中 6 低，第 2 轮 0 高 0 中 0 低）；code-reviewer 两轮，第 2 轮“可合并”（第 1 轮的中级问题：T7 漏测 keyring，已补）。实现位置：`src/multi_codex/identity.py` 的 `Identity`（:61-62）、`_parse_iso`（:120）、`read_identity`（:173-175）；`src/multi_codex/cli.py` 的 `cmd_list` JSON（:948）与 `list -v` 表格（:966-982）、`_billing_cells`（:1070）、`_subscription_json`（:1092）。测试：`tests/test_billing.py`。验证：本机 Python 3.11 全量通过；编译机 Python 3.8.10 全量 362 个用例通过（跳过 11 个）；T13 本机实测 gmail 行为 `2026-09-20?` / `2026-08-20`，JSON 为 `2026-09-21T02:36:15Z`。
>
> 用户的决定（2026-10-16）：
>
> - 用户问“`list` 能否加一列显示套餐的过期时间”。评估结论：登录令牌里的订阅日期常常过时，只表示“截至 OpenAI 上次核对时，这一期账单到哪天”，不是套餐失效时间。
> - 建议“默认简表不加；`list -v` 与 `list --json` 加上，叫 BILLING UNTIL，带上核对日期；free 套餐显示 `-`；日期已过时加标记，不说成已过期”，用户回复“需要”，即按此写方案。

## 1. 背景

- `identity.read_identity` 已经解码每个账号 `auth.json` 里 `tokens.id_token` 的 payload（只做 base64url 解码，不验签，`src/multi_codex/identity.py:96-106`、`:135-148`），从 `https://api.openai.com/auth` 这组字段里取了 `chatgpt_plan_type` 作为 PLAN（`identity.py:148`），在 `list -v` 的 PLAN 列显示（`cli.py:964-969`），`list --json` 的 `login.plan` 输出（`cli.py:944-946`）。
- 同一组字段里还有三个与订阅有关的字段：`chatgpt_subscription_active_start`、`chatgpt_subscription_active_until`、`chatgpt_subscription_last_checked`。2026-10-16 在本机三个账号的 `auth.json` 上实测（只解码、打印字段名与日期，不打印令牌）：

| 账号目录 | plan | 令牌签发（iat） | active_until | last_checked |
| ---- | ---- | ---- | ---- | ---- |
| `~/.codex` | plus | 2026-08-26 | 2026-08-24T06:08:44+00:00 | 2026-08-06T09:41:00.416728+00:00 |
| `~/.cx/jakoes.wu.us@gmail.com` | plus | 2026-10-05 | 2026-09-21T02:36:15+00:00 | 2026-08-21T05:44:13.926850+00:00 |
| `~/.cx/jakoes.wu@icloud.com` | free | 2026-10-07 | 2026-10-07T13:45:38+00:00 | 2026-09-30T08:21:46.423096+00:00 |

- 从实测可以确定的事实：
  - 令牌刷新时这几个字段不一定更新：gmail 的令牌 10-05 签发，`active_until` 仍是 09-21，`last_checked` 仍是 08-21。
  - free 套餐的账号也带 `active_until`。
  - 取值格式：带 `+00:00` 时区偏移的 ISO 8601，`last_checked` 带 6 位小数秒。
- 仓库里没有这些字段语义的上游源码或文档依据；字段在令牌里的含义按字面理解为“订阅当期的起止与最后核对时间”。这是本方案显示文案“截至核对日的账期结束日”而非“过期时间”的原因。

## 2. 目标 / 非目标

**目标**

1. `list -v` 表格在 PLAN 之后追加两列：BILLING UNTIL（账期结束日）与 CHECKED（OpenAI 最后核对订阅的日期）。
2. `list --json` 每个账号的 `login` 里加 `subscription`：`{"active_until": ..., "last_checked": ...}`。

**非目标**

- 默认的 `list` 简表不变。
- `doctor`、`usage` 不变，不做“快过期”提醒。
- 不联网、不刷新令牌、不读 `active_start`。
- 不判断订阅是否真的有效（本地数据做不到）。

## 3. 假设与约束

- **读取位置**：与 `chatgpt_plan_type` 同一个 `auth` 字典（`identity.py:141-142`），字段名 `chatgpt_subscription_active_until`、`chatgpt_subscription_last_checked`。
- **解析**（新增 `_parse_iso(value) -> Optional[float]`，返回 epoch 秒）：
  - 只接受字符串；末尾 `Z` 先换成 `+00:00`（Python 3.8 的 `datetime.fromisoformat` 不接受 `Z`，与 `usage.py:151` 的注释同一个限制）；
  - 用 `datetime.fromisoformat` 解析，结果不带时区（naive）时返回 None，不猜时区；
  - 任何 `ValueError` / `TypeError` 返回 None。`fromisoformat` 在 3.11 起接受的写法比 3.8 宽（如任意位数小数秒），这些边缘写法在不同版本下结果可能不同，测试只用两个版本结果一致的输入；
  - 解析出的时间若超出本平台 `time.localtime` / `time.gmtime` 的可表示范围（`OverflowError` / `OSError`，如 32 位系统上 2038 年以后），同样返回 None，避免 `list` 格式化时中断（PR #32 评审意见补充）；
  - 两个字段各自独立解析，一个失败不影响另一个。
- **显示**（`list -v`）：
  - 日期按本机时区显示为 `YYYY-MM-DD`（`time.strftime("%Y-%m-%d", time.localtime(epoch))`，cli.py 已 import `time`，不新增 import）；
  - BILLING UNTIL：`active_until <= now` 时末尾加 `?`，表格后输出一行脚注（§5.1.3）；
  - plan 恰为字面值 `free` 时两列都显示 `-`（free 账号带的是旧账期，显示只会误导）。套餐取值全集在仓库内无依据，只有 `free` 这一个样本（§1 icloud），其它任何取值（含未知值与 None）照常显示；
  - 非 ChatGPT 登录、字段缺失或解析失败：`-`。
- **JSON**：给事实，不按套餐过滤：两个字段各自为 UTC ISO 字符串（`%Y-%m-%dT%H:%M:%SZ`，秒向下截断，复用 `cli._iso_utc`，`cli.py:1054-1058`）或 null；两个都没有时 `subscription` 为 null。
- **令牌安全**：只新增两个日期，不输出令牌，现有 `assert_no_secret` 用例继续覆盖。

## 4. 涉及模块

| 区域 | 行号锚点（基线 `b1579d0`） | 改动类型 | 改动点 |
| ---- | ---- | ---- | ---- |
| `src/multi_codex/identity.py` | import 区 :11-15 | 修改 | 加 `from datetime import datetime` |
| `src/multi_codex/identity.py` | `Identity` :44-56 | 修改 | 末尾追加 `subscription_until: Optional[float] = None`、`subscription_checked: Optional[float] = None`，docstring 说明含义与“可能过时” |
| `src/multi_codex/identity.py` | `_text` :109-110 之后 | 新增 | `_parse_iso(value)` |
| `src/multi_codex/identity.py` | `read_identity` :148 | 修改 | ChatGPT 分支多传两个字段 |
| `src/multi_codex/cli.py` | `cmd_list` docstring :905 | 修改 | “逐字不变”改为“0.7 的列在前，后面追加了账期两列” |
| `src/multi_codex/cli.py` | `cmd_list` JSON :944-946 | 修改 | `login` 加 `subscription` |
| `src/multi_codex/cli.py` | `cmd_list` 表格 :963-972 | 修改 | 表头追加两列；每行追加 `_billing_cells`；有 `?` 时输出脚注 |
| `src/multi_codex/cli.py` | `_iso_utc` :1054-1058 之后 | 新增 | `_billing_cells(found, now) -> Tuple[str, str, bool]`（两格内容 + 是否带 `?`）与 `_subscription_json(found) -> Optional[dict]` |
| `tests/test_everyday.py` | :298-299 | 修改 | `test_verbose_is_the_old_table` 期望文本加两列 |
| `tests/test_insight.py` | :87-88、:91、:112-116 | 修改 | :87 的 `header.split()` 列表末尾追加 `"BILLING", "UNTIL", "CHECKED"` 三个元素；`split()[5:]` 改为 `split()[5:7]`；`login` 相等断言加 `"subscription": None` |
| `tests/test_billing.py` | 新文件 | 新增 | §8 |
| `README.md`、`README.zh-CN.md` | `README.md:199`、`README.zh-CN.md:206` | 修改 | `list -v` 一段加两列的说明与 `?` 含义；`list --json` 的 `login.subscription` |
| `CHANGELOG.md` | `## [Unreleased]` :7 | 修改 | Added |

**依赖 `list -v` 输出的测试全集**（`grep -rn '"list", "--verbose"\|"list", "-v"' tests`，不截断，共 8 处）：

- `test_everyday.py:292`：逐字相等，必须改；
- `test_insight.py:85`（:87 表头、:88 `split()[5:]`）、`:111`（:112-116 `split()[5:]`）：必须改；
- `test_insight.py:135`：只断言前 5 列，不受影响；
- `test_onboarding.py:228`、`test_isolation.py:234`：子串断言，不受影响；
- `test_last_used.py:162`：断言不含 `LAST USED`，新列名不含该串，不受影响；
- `test_ergonomics.py:224`：最后一行含 ` ok`。表格后新增的脚注只在有 `?` 时输出，该用例没有登录，不输出脚注，不受影响。

**依赖 JSON `login` 的测试**（`grep -rn '\["login"\]' tests`）：只有 `test_insight.py:91` 是整个字典相等，必须改；:100、:109、:147、:157 只取单个键，不受影响。

**`Identity(...)` 的构造点全集**（`grep -rn "Identity(" src tests`）：全部在 `identity.py`（:122、:126、:131、:133、:140、:148、:150、:151），都是位置参数且只给前 6 个字段；新字段有默认值，除 :148 外不用改。

## 5. 方案

### 5.1 实现要点

#### 5.1.1 `identity.py`

`read_identity` 的 ChatGPT 分支（`identity.py:148`）改为：

```python
return Identity(LOGIN_CHATGPT, email, _text(auth.get("chatgpt_plan_type")), user_id, workspace_id, store,
                _parse_iso(auth.get("chatgpt_subscription_active_until")),
                _parse_iso(auth.get("chatgpt_subscription_last_checked")))
```

`_parse_iso` 按 §3 实现；`datetime.fromisoformat` 得到带时区的结果后取 `.timestamp()`。

#### 5.1.2 `list --json`

`login` 字典（`cli.py:944-946`）追加：

```python
"subscription": _subscription_json(found),
```

`found` 为 None、非 ChatGPT、或两个时间都为 None 时为 `None`；否则 `{"active_until": _iso_utc(found.subscription_until), "last_checked": _iso_utc(found.subscription_checked)}`。`_subscription_json` 与 `_billing_cells` 放在一起（`_iso_utc` 之后）。

#### 5.1.3 `list -v`

- 表头（`cli.py:964`）改为 `(..., "LOGIN", "PLAN", "BILLING UNTIL", "CHECKED")`，前 7 列不变。
- 每行追加 `_billing_cells(found, now)` 的两格；`now = time.time()` 在循环前取一次。
- `_billing_cells`：`found` 为 None、非 ChatGPT、`found.plan == "free"` 时返回 `("-", "-", False)`；否则 BILLING UNTIL 为本地日期（None 为 `-`），`subscription_until <= now` 时加 `?`；CHECKED 为本地日期或 `-`。
- 打印表格后、`_warn_duplicates` 之前，任一行带 `?` 时用 `print` 输出到 stdout（与简表 `*` 脚注 `cli.py:1023` 一致）：
  `? the billing period end date has passed; the subscription may have renewed or lapsed since OpenAI last checked it (CHECKED)`
- `?` 只看 `subscription_until <= now`，与 `subscription_checked` 的先后无关，文案也不假设先后。
- 行尾 `rstrip()` 照旧，没有账期时行尾两个 `-` 仍保留（与 PLAN 列为 `-` 时一致）。

### 5.2 接口变更

| 接口 | 变更 | 兼容性 |
| ---- | ---- | ---- |
| `list -v` | PLAN 之后追加 BILLING UNTIL、CHECKED 两列；可能多一行 `?` 脚注 | 前 7 列不变。列名带空格（与简表 LAST USED 相同），SHARED 列本身也可能含空格（`yes (not: ...)`），表格本来就不能稳定按空白切分；README 已写明排版不保证稳定，脚本应使用 `--json` |
| `list --json` 的 `login.subscription` | 新增字段 | 只加字段，`version` 仍为 1 |
| `identity.Identity` | 末尾追加两个带默认值的字段 | 内部接口；位置参数构造不受影响 |
| 默认 `list`、`doctor`、`usage` | 不变 | — |

本方案不涉及 `docs/reference/*`。

## 6. 备选方案与决策

- **在默认简表加 EXPIRES 列**：实测日期常过时（§1 gmail 行），直接显示会把正在用的账号看成“已过期”。不采用（用户已同意）。
- **一列写成 `2026-09-21 (checked 08-21)`**：跨年时要写年份，单元格变长且不好按列解析。拆两列。
- **按令牌签发时间判“可能已续费”**：令牌签发晚于账期结束，只能说明令牌刷新过，不能说明订阅续上了（gmail 行就是这样）。统一用 `?` 表示“已过账期结束日，状态未知”。
- **JSON 也对 free 隐藏**：JSON 给脚本用，给出原始事实更有用；隐藏只在表格里做。

## 7. 影响分析

**正向**

- **`read_identity`**：多两次字典取值与字符串解析，无新文件读取；调用方 `list`、`doctor`（`doctor.py:174`）、`_derive_migrate_name`（`cli.py:721`）、`cli.py:747` 都只用已有字段，不受影响。
- **`duplicate_groups`**（`identity.py:154-164`）：只看 `login`、`user_id`、`workspace_id`，不受影响。
- **`list -v`**：多两列，可能多一行脚注。
- **`list --json`**：`login` 多一个键。

**反向**

- **默认 `list`**（`_print_list_summary`）：不调用 `_billing_cells`，输出不变（现有 `test_everyday.py` 简表用例覆盖）。
- **令牌内容**：不新增任何令牌片段输出。
- **时区**：表格按本机时区显示日期，JSON 用 UTC；同一时刻在不同时区可能显示为相邻两天，属预期。

**运行时**：无新进程、无网络、无新文件读取。

## 8. 回归测试

**环境**：本机，以及编译机 ubuntu20 的 Python 3.8.10（`fromisoformat` 的行为以 3.8 为准）。命令：`python3 -m unittest discover -s tests -t tests`。用例在 `tests/test_billing.py`，复用 `test_insight.py` 的 `InsightBase`、`make_jwt`、`chatgpt_claims`。

| 编号 | 用例 | 判据 |
| ---- | ---- | ---- |
| T1 | `_parse_iso` | `2026-09-21T02:36:15+00:00`、`2026-08-21T05:44:13.926850+00:00`、`2026-09-21T02:36:15Z` 解析为对应 epoch；`2026-09-21T02:36:15`（无时区）、`abc`、`123`、None 均为 None（不放各版本结果不同的边缘写法） |
| T2 | plus，账期结束在未来 10 天，核对在 5 天前 | `list -v` 行末两格为对应本地日期，无 `?`、无脚注；JSON `subscription` 两个值等于 `_iso_utc` 结果 |
| T3 | plus，账期结束在 3 天前 | BILLING UNTIL 带 `?`，`result.out` 中有 `? the billing period end date has passed` 脚注 |
| T4 | free，账期在未来 | 表格两格为 `-`，无脚注；JSON `subscription` 仍给出两个日期 |
| T5 | 只有 `last_checked` | BILLING UNTIL `-`，CHECKED 为日期；JSON `active_until` 为 null |
| T6 | 两个字段都没有；字段为无法解析的字符串 | 表格 `-` `-`；JSON `subscription` 为 null |
| T7 | api-key、未登录、keyring、unreadable | 两格为 `-`；JSON `subscription` 为 null（keyring 账号同时留一份带账期的 `auth.json`，确认不会被读到） |
| T8 | 令牌不泄露 | T2 的 stdout/stderr 不含 `SECRET`（`assert_no_secret`） |
| T9 | 默认 `list` | 不含 `BILLING`，与改动前一致（现有简表用例 + 新增一条 `assertNotIn`） |
| T10 | `list -v` 前 7 列 | `test_insight.py:135` 前 5 列用例、改后的 :87-88 通过 |
| T11 | README | 中英两份 `list -v` 段说明两列、`?` 与 free 显示 `-`，`login.subscription` 写明 |
| T12 | 回归 | 现有全部用例通过 |
| T13 | 本机实测 | 本机运行 `multi-codex list -v`：gmail 行为按本机时区换算的日期（PDT 下 `2026-09-20?`、`2026-08-20`）并有脚注，icloud 行为 `-` `-`；`list --json` 中 gmail 的 `active_until` 为 `2026-09-21T02:36:15Z`。表格与 JSON 相差一天属预期（§7 时区条） |

## 9. 日志 / 观测点

- `list -v` 的 BILLING UNTIL、CHECKED 两列与 `?` 脚注；`list --json` 的 `login.subscription`。
- 不新增日志：`list` 是只读命令。
