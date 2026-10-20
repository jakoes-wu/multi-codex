# multi-codex：跨账号、跨目录的会话查询 `sessions`

> 2026-10-19 注记：代码已落地（未提交），单测通过，代码评审第 1 轮的中级问题（孤立代理字符让打印崩溃）已修。实现位置：`src/multi_codex/sessions.py`（`read_session`、`clean_title`、`_UNPRINTABLE`），`src/multi_codex/cli.py` 的 `cmd_sessions` 及其辅助函数。本机实跑列出 640 个会话，与 T27 预期一致；2026-10-19 用户手工运行 gmail 账号一条归档会话（`01a0f99b-…`）的续聊命令，Codex 提示 “This conversation is archived”，可选 “Unarchive and resume”（取消归档并续聊）或 Cancel，见 §3。基线：main `59e382b`（v0.12.1）。

## 1. 背景

用户 2026-10-19 要求把 multi-claude 的 `sessions` 功能复刻到 multi-codex。multi-claude 的现状（v0.11.1，仓库 `multi-claude`）由两份方案定义：

- `docs/feature/feature-session-list.md`：`sessions [NAME] [--grep TEXT] [--limit N] [--json]`，别名 `conversations`，列出各账号全部会话的账号、标题、会话 ID、创建与最后更新时间、续聊命令；包含未登记的默认目录。
- `docs/feature/feature-sessions-cmd-column.md`：文本输出每条一行，列顺序 `TITLE ACCOUNT CREATED UPDATED SESSION ID`；`--cmd` 才显示续聊命令（终端放得下时作为 `CMD` 列并去掉 `SESSION ID`，放不下时命令单独一行），命令里主目录写成 `~`。

本文按 multi-claude 的最终行为复刻，只在 Codex 会话存储与 Claude 不同的地方改写读法。用户已确认（2026-10-19）：归档会话列出并标注；`codex exec` 产生的非交互会话也列出。

### 1.1 已核实的事实

数据取自 2026-10-19 本机默认目录 `~/.codex` 与 3 个登记账号，共 1027 个会话文件（约 3.1 GB，单个最大 955 MB）。统计脚本只看键名、类型、计数与偏移，不输出对话内容。

| 事实 | 依据 |
| ---- | ---- |
| 会话文件在 `<账号目录>/sessions/<年>/<月>/<日>/rollout-*.jsonl` 与 `<账号目录>/archived_sessions/rollout-*.jsonl`，两处都没有别的文件 | `src/multi_codex/usage.py:113-115` 用同样的两个模式；本机非 `.jsonl` 文件 0 个 |
| 文件名形如 `rollout-2026-10-19T22-24-01-<会话ID>.jsonl`，会话 ID 是 36 位 UUID；文件第一行 `type` 为 `session_meta`，`payload.id` 与文件名里的 ID 一致 | 1027/1027 |
| `session_meta.payload` 有 `id`、`timestamp`（会话开始时间，形如 `2026-10-19T14:24:01.123Z`）、`cwd`（绝对路径）、`source`、`originator` | 本机键名统计；`cwd` 全部为绝对路径 |
| `payload.source` 为字符串 `cli` / `vscode` / `exec` 时是用户发起的会话；为对象（`{"subagent": …}`）时是子代理会话，共 387 个 | 本机统计 |
| 第一行最长 48 KB（含 `base_instructions`），p90 为 21 KB | 本机统计 |
| 会话名称在账号目录的 `session_index.jsonl`，每行 `{"id", "thread_name", "updated_at"}`；同一 ID 可出现多行（改名），约三分之一的顶层会话有名称 | 默认目录 204 个 ID 中 15 个重复；顶层会话 640 个中 259 个有名称 |
| 用户提问在 `{"type":"response_item","payload":{"type":"message","role":"user","content":[{"type":"input_text","text":…}]}}`；同类行里还有 Codex 自动注入的 `<environment_context>`、`<recommended_plugins>` 等（以 `<` 开头）与 `# AGENTS.md instructions for …` | 本机按开头分类 |
| 旧版本文件里还有 `{"type":"event_msg","payload":{"type":"user_message","message":…}}` | 默认目录全部文件共 4 行（不在顶层会话的前 1 MiB 内；兼容它无害） |
| 首条普通提问所在行的结束偏移：p50 125 KB、p99 431 KB、最大 4.6 MB；34 个顶层会话没有任何普通提问 | 本机统计 |
| 尾部 64 KB 内最后一个带 `timestamp` 的行，与通读全文件的最后一个时间戳一致 | 640/640 |
| 顶层会话中 354 个含超过 64 KB 的单行 | 本机统计 |
| `state_5.sqlite` 的 `threads` 表也有标题、目录、时间，但 icloud 账号的库用只读方式打不开（`unable to open database file`），文件名带版本号 | 本机实测；`src/multi_codex/config.py:31-32` 注释 |
| `codex resume [SESSION_ID]` 接受会话 UUID；`--all` 关闭按当前目录的过滤 | `codex resume --help`（codex-cli 0.162.0） |
| 账号启动命令是 `<bin_dir>/codex-<名称>`，设好 `CODEX_HOME` 后清除 `CODEX_SQLITE_HOME`、`CODEX_API_KEY`、`CODEX_ACCESS_TOKEN` | `launcher.py:17`、`:76-80`；`platform.py:17` |
| 默认目录是 `~/.codex`；`use` / `migrate-default` 后它是指向账号目录的软链 | `platform.py:24-25`、`switch.py:37-38` |

## 2. 目标 / 非目标

**目标**

1. `multi-codex sessions [NAME] [--grep TEXT] [--limit N] [--cmd] [--json]`：只读列出各账号的 Codex 会话，给出标题、账号、创建时间、最后更新时间、会话 ID；`conversations` 是同一命令的别名。
2. 包含未登记的默认目录 `~/.codex`，账号显示为 `default`；归档会话列出并标注。
3. `--cmd` 时给出可直接复制运行的续聊命令，排版与 multi-claude 一致。

**非目标**

- 不输出对话正文、不做全文检索；只取名称，没有名称时取首条提问的开头。
- 不删除、移动、改写任何文件；不建索引、不缓存；不读 SQLite 数据库。
- 不列子代理会话。
- 不合并同一会话 ID 的多份副本；账号的 `sessions` 是指向别的账号的软链时，两个账号下各列一次，不去重。
- 不联网、不读凭据。

## 3. 假设与约束

- 会话记录格式未写入官方文档：解析失败、不是对象、字段类型不符的行一律跳过；整份文件取不到的字段按 §5.1.2 兜底，不报错。
- 续聊沿用 multi-claude 的写法，先 `cd` 到会话启动目录再运行 `codex-<账号> resume <ID>`：这样无论 Codex 按不按当前目录处理续聊，都回到原目录。
- 归档会话：2026-10-19 用户实测，`codex resume <ID>` 能找到归档会话，先弹出提示，让用户选择“取消归档并续聊”或取消，不会直接打开；选前者后会话不再是归档状态。`exec` 会话能否续聊未核实。文本输出给归档会话的标题加 `[archived]` 前缀。
- 兼容 Python 3.8（`pyproject.toml:11`）。
- 已知限制：第一行超过 1 MiB 时被截断、解析失败，按“不是子代理”处理，子代理会话会被列出；本机第一行最长 48 KB（§1.1），接受。

## 4. 涉及模块

| 区域 | 行号锚点（基线 `59e382b`） | 改动类型 | 改动点 |
| ---- | ---- | ---- | ---- |
| `src/multi_codex/sessions.py` | 新文件 | 新增 | `SessionInfo`、`list_sessions`、`read_session`、`read_names`、`clean_title` 及内部函数（§5.1.1–§5.1.3） |
| `src/multi_codex/cli.py` import | `:16-17` | 修改 | 导入列表加 `sessions`；`:7-14` 之间加 `import shlex`、`import shutil`、`import unicodedata`，`from datetime import datetime` |
| `src/multi_codex/cli.py` `COMMAND_GROUPS` | `:45` 之后 | 新增 | Everyday 组加 `("sessions", "find a session across accounts and directories")` |
| `src/multi_codex/cli.py` `COMMAND_EXAMPLES` | `:132` 之后（`usage` 一组之后） | 新增 | `sessions` 的 4 条示例（§5.1.6） |
| `src/multi_codex/cli.py` `build_parser` | `:295` 之后（`p_usage` 之后） | 新增 | `sessions` 子命令与选项 |
| `src/multi_codex/cli.py` `main` | `:461` 与 `:462` 之间 | 新增 | 别名 `conversations` 换成 `sessions`（§5.1.5） |
| `src/multi_codex/cli.py` `main` | `:483` 之后（`usage` 分派之后） | 新增 | 分派到 `cmd_sessions`，不加锁、不看迁移记录 |
| `src/multi_codex/cli.py` | `:1509` 之后（`cmd_path` 之后） | 新增 | `_SessionSource`、`_session_sources`、`cmd_sessions`、`_resume_command`、`_tilde_path`、`_display_width`、`_aligned`、`_local_minute`、`_resolve_command_alias` |
| `src/multi_codex/completion.py` `ACCOUNT_COMMANDS` | `:16-17` | 修改 | 加 `"sessions"` |
| `tests/test_sessions.py` | 新文件 | 新增 | §8 |
| `README.md`、`README.zh-CN.md` | 常用操作表 `:131-145`；命令表 `:170` 之后；`:183` 只读命令清单；`:205` `### Health check` 之前（中文版 `:212` `### 体检` 之前） | 新增 | 常用操作一行、命令表一行、`:183` 的只读命令清单加上 `sessions`、新小节“Finding a session / 查找会话”（含隐私说明与示例） |
| `SECURITY.md` | `:19` 之后 | 新增 | 说明 `sessions` 会把会话名称与首条提问开头打印到终端 |
| `CHANGELOG.md` | `:7` `[Unreleased]` | 新增 | Added |

## 5. 方案

### 5.1 实现要点

#### 5.1.1 枚举会话文件

`list_sessions(account_dir) -> (List[SessionInfo], 读不了而跳过的文件数)`：

- 用 `os.listdir` 逐层枚举 `sessions/<年>/<月>/<日>/` 与 `archived_sessions/`（不用 `glob`：便于对读不了的目录静默跳过），只取常规文件，文件名必须整串匹配 `ROLLOUT_NAME = re.compile(r"rollout-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-([0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12})\.jsonl")`（`fullmatch`），会话 ID 取捕获组。这样会话 ID 只含十六进制与 `-`、不以 `-` 开头，拼进命令安全。
- 目录不存在或读不了时视为没有会话；单个文件 `open` / 读取抛 `OSError` 时跳过并计数。
- `read_session` 判定为子代理会话的返回 None，不计数。
- 名称表 `read_names(account_dir)` 每个账号读一次（§5.1.2）。

#### 5.1.2 读取一个会话

所有逐行解析统一走 `_record(raw: bytes)`：文件一律以二进制打开（`readline` 的上限才是字节数），按 `utf-8` 的 `replace` 方式解码，`json.loads` 同时捕获 `ValueError` 与 `RecursionError`（嵌套上千层的行；它不是 `ValueError`），结果不是对象时返回 None。第一行、首条提问扫描、尾部与 `read_names` 都复用它。

`read_session(path, names, archived) -> Optional[SessionInfo]`，字段：`session_id`、`directory`（可为 None）、`title`、`title_source`、`created`（UTC 秒，可为 None）、`updated`（UTC 秒）、`archived`、`path`。

1. **第一行**：`readline(HEAD_BUDGET + 1)`（`HEAD_BUDGET = 1 MiB`）。能解析且 `type == "session_meta"`、`payload` 是对象时：
   - `payload.source` 存在且不是字符串 → 子代理会话，返回 None。
   - `payload.id` 是字符串且不等于文件名里的 ID → 以文件名为准（ID 要拼进命令，文件名已过正则）。
   - `directory` = `payload.cwd`，必须是绝对路径且不含 §5.1.3 列出的不可打印字符，否则为 None。
   - `created` = `payload.timestamp` 解析结果，解析不了时用该行顶层 `timestamp`。
   第一行不是 `session_meta` 或解析失败时：不是子代理，`directory`、`created` 为 None，照常列出。
2. **名称**：`names` 里有该 ID 且清洗后非空 → `title_source = "name"`。
3. **首条提问**（只在没有名称时）：从第二行起用带上限的 `readline` 逐行读，第一行在内累计不超过 `HEAD_BUDGET`；一行不以换行结尾且累计已超额度时停止（单行再长也不整行读进内存）。“普通提问”：
   - `type == "response_item"`、`payload.type == "message"`、`payload.role == "user"`、`payload.content` 是列表：依次看 `type == "input_text"` 的块，取第一个去掉首尾空白后不以 `<` 开头、不以 `# AGENTS.md instructions` 开头、且经 `clean_title` 清洗后非空的 `text`；
   - 或 `type == "event_msg"`、`payload.type == "user_message"`、`payload.message` 是字符串，按同样规则判断。
   取到即停，`title_source = "prompt"`；读满额度仍没有 → `title = ""`、`title_source = "none"`。首条提问在 1 MiB 之后的会话（本机 p99 为 431 KB）显示 `-`，接受。
4. **更新时间**：读文件最后 64 KB（`TAIL_BYTES`），按行解析，取最后一个能解析的 `timestamp`；没有时用文件修改时间。

时间解析复用 `usage._parse_timestamp`（`usage.py:150-158`）。

`read_names(account_dir) -> Dict[str, str]`：逐行读 `session_index.jsonl`，`id` 与 `thread_name` 都是字符串的行按出现顺序覆盖（后写的名称生效）；文件不存在或读不了时返回空表，不计入 skipped。

#### 5.1.3 标题清洗

照搬 multi-claude 的 `clean_title(text, limit)`：连续空白压成一个空格，删除 Unicode 类别 `Cc`、`Cf`、`Zl`、`Zp`、`Cs` 的字符（`Cs` 是孤立代理字符：JSON 里只有半个表情的 `\ud83d` 会被解成它，原样 `print` 会抛 `UnicodeEncodeError`；multi-claude 没有这一类）（保留 U+200C、U+200D），去掉首尾空白，超过 `limit` 个字符时截到 `limit` 个、`rstrip` 后接 `…`。文本输出上限 60 字符，JSON 200 字符。`directory` 的合法性检查用同一套不可打印字符判定。

#### 5.1.4 `cmd_sessions`

**数据来源**（`_session_sources`）：

- 配置里登记的每个账号：目录 `accounts.account_dir`（`accounts.py:34`），启动段 `codex-<账号名>`，`registered = True`。
- 未登记的默认目录：`expand(platform.default_source())` 是目录，且其真实路径不等于任何登记账号目录的真实路径时加入，账号名 `default`、`registered = False`，启动段为 `env -u CODEX_HOME -u CODEX_SQLITE_HOME -u CODEX_API_KEY -u CODEX_ACCESS_TOKEN codex`（变量取 `"CODEX_HOME"` 加 `platform.ISOLATION_BREAKING_ENV`，在别的账号的终端里运行也落到默认目录）。
- 没有 `config.json` 时只看默认目录。

**参数**（与 multi-claude 一致）：

- `NAME`：先 `_checked_name`（`cli.py:1007-1012`，不合法退出 2，没有配置文件或配置损坏时也一样，所以放在读配置之前），再 `config.find`。登记账号优先；未登记但名字不分大小写等于 `default` 且默认目录已加入时选中默认目录；否则报 `config.not_registered(name)`（`config.py:127`）并退出 1。
- `--grep TEXT`：不区分大小写的子串匹配，对象是清洗后未截断的标题、目录（None 时不参与）、会话 ID。
- `--limit N`：0 不限；文本默认 30，`--json` 默认不限；负数退出 2，报 `--limit must be 0 or more`。
- `--cmd`：只影响文本输出。
- `--json`：机器可读输出，`--cmd` 不改变它。

**排序**：`updated` 从新到旧，相同时按账号名、会话 ID。

**文本输出**：

```text
TITLE                         ACCOUNT           CREATED           UPDATED           SESSION ID
Evaluate the session list     work@example.com  2026-10-19 16:05  2026-10-19 17:42  01a157c4-3f6d-7452-96cf-e9f4cb89daa9
[archived] Fix proxy check    default           2026-10-01 15:34  2026-10-01 16:10  01a0f99b-174f-7a53-a15b-e63aca9de061
```

- 时间按本机时区 `YYYY-MM-DD HH:MM`；None 或超出可显示范围（`OverflowError`、`OSError`、`ValueError`）显示 `-`。
- 标题为空显示 `-`；归档会话在清洗、截断后的标题前加 `[archived] `（`--grep` 不匹配这个前缀）。
- 列按显示宽度对齐（`_display_width`：`east_asian_width` 为 `W`/`F` 记 2 格，`Mn`/`Me` 与 U+200C/U+200D 记 0 格），列间两个空格，行尾 `rstrip`。
- `--cmd`：去掉 `SESSION ID` 列；整张表加 `CMD` 列后最宽一行不超过 `shutil.get_terminal_size((80, 24)).columns` 时用单行排版，否则每条会话下一行 `"  " + 续聊命令`。按整张表决定。
- 续聊命令：`cd <目录> && <启动段> resume <ID>`；文本里目录经 `_tilde_path` 把主目录写成 `~`（`~/` 留在引号外，其余 `shlex.quote`），JSON 里是 `shlex.quote` 的绝对路径。目录为 None 时只给 `<启动段> resume <ID>`，文本里再附 `  (start directory unknown)`。
- 被 `--limit` 截掉时末尾打印 `showing 30 of 120 sessions; use --limit 0 for all, or --grep TEXT to narrow`。
- 没有会话打印 `no sessions found`，退出 0。
- 有读不了的文件时 stderr 告警 `skipped N session file(s) that could not be read`，退出码仍为 0。

**只读**：不加写锁，不看迁移记录，与 `usage` 同处分派（`cli.py:482-483`）。

#### 5.1.5 别名 `conversations`

`_resolve_command_alias(argv)`：第一个不以 `-` 开头的词是 `conversations` 时换成 `sessions`，其它位置不动。放在 `_split_run_command` 之后、`_unknown_command` 之前（`cli.py:461-466`）。不用 argparse 的 `aliases`：`tests/test_onboarding.py:124-126` 要求分组与解析器子命令集合完全相等，`COMMAND_EXAMPLES` 也按子命令名取（`cli.py:366-369`）。

#### 5.1.6 帮助示例

`COMMAND_EXAMPLES["sessions"]`（每行不超过 79 列，`tests/test_onboarding.py:144-166`）：

- `multi-codex sessions` — newest 30 sessions of every account
- `multi-codex sessions work --grep proxy` — only "work", title/dir/id matching
- `multi-codex sessions --cmd` — also show how to resume each one
- `multi-codex sessions --json` — machine-readable, all sessions

按 `_examples_text`（`cli.py:206-211`）实算，4 条排版后不超过 79 列（`tests/test_onboarding.py` 校验）。

子命令 `description` 写明只读，以及 `conversations` 是别名。

### 5.2 接口变更

| 接口 | 变更 | 兼容性 |
| ---- | ---- | ---- |
| `sessions [NAME] [--grep TEXT] [--limit N] [--cmd] [--json]` | 新增 | 新增命令 |
| `conversations …` | 新增，等同 `sessions` | 改动前此写法报未知命令退出 2 |
| 补全脚本 | `sessions` 第一个位置参数补账号名；别名 `conversations` 不参与补全（不在解析器里） | 需重新生成补全脚本 |

`sessions --json`：

```json
{
  "version": 1,
  "sessions": [
    {
      "account": "work@example.com",
      "registered": true,
      "directory": "/Users/me/proj",
      "session_id": "01a157c4-3f6d-7452-96cf-e9f4cb89daa9",
      "title": "Evaluate the session list",
      "title_source": "name",
      "archived": false,
      "created": "2026-10-19T08:05:12Z",
      "updated": "2026-10-19T09:42:40Z",
      "file": "/Users/me/.cx/work@example.com/sessions/2026/10/19/rollout-2026-10-19T16-05-12-01a157c4-3f6d-7452-96cf-e9f4cb89daa9.jsonl",
      "resume": "cd /Users/me/proj && codex-work@example.com resume 01a157c4-3f6d-7452-96cf-e9f4cb89daa9"
    }
  ],
  "total": 120,
  "skipped": 0
}
```

- 顶层键用 `version`（与 `list` / `usage` / `doctor` 的 `"version": 1` 一致，README `:183`），不用 multi-claude 的 `schema_version`。
- `title_source` 取 `name`、`prompt`、`none`；`directory`、`created` 可为 null；`resume` 在目录未知时不带 `cd`、不带括号说明。
- 时间用 `_iso_utc`（`cli.py:1200-1204`）。
- `total` 是过滤后、截断前的条数。

退出码：0 成功（含没有会话）；1 账号未登记或 `config.json` 无法解析（`load_config` 抛 `ConfigError`，由 `main` 的异常处理转为 1）；2 参数错误。

不涉及 `docs/reference/*`（仓库没有该目录）。

## 6. 备选方案与决策

- **读 `state_5.sqlite` 的 `threads` 表**：字段现成、速度快，但本机有账号的库以只读方式打不开，文件名带版本号会随 Codex 升级改变，Codex 运行时还持有 WAL。不采用，以会话文件为准。
- **全量解析每个文件**：本机 3.1 GB，不可接受。采用“第一行 + 名称表 + 必要时有上限的头部扫描 + 尾部 64 KB”。
- **名称也从会话文件里找**：Codex 把名称写在 `session_index.jsonl`，不写进会话文件。直接读名称表。

## 7. 影响分析

- **现有命令**：新模块不被其它模块导入；`cli.py` 只加分组、示例、子命令、别名替换、一条分派与新函数；`completion.ACCOUNT_COMMANDS` 多一个命令，只影响补全。
- **别名替换**：只在子命令位置恰好是 `conversations` 时生效，此前该写法退出 2，无现有用法受影响。`run` / `code` / `login` 的 `--` 之后部分已被 `_split_run_command` 切走，不参与替换。
- **帮助**：Everyday 组多一行；`tests/test_onboarding.py` 核对分组、示例与子命令一致，三处必须同时加。
- **运行时**：每个文件读第一行（≤48 KB 实测）与尾部 64 KB；无名称的会话再读到首条提问为止（p50 125 KB，上限 1 MiB）。本机约 640 个顶层会话预计读 100 MB 以内，秒级。不新启进程、不加锁、不联网。
- **正在写入的会话**：尾部半行解析失败被跳过，取上一行时间戳；`session_index.jsonl` 末尾半行同样被跳过。
- **隐私（对外语义扩大）**：首次把会话名称与首条提问开头打印到终端；README 与 SECURITY.md 写明只打印到本机终端或 `--json` 的标准输出，不保存、不外发。
- **部署形态**：没有 `config.json` 只列默认目录；`~/.codex` 是指向账号的软链时按真实路径去重；账号目录或 `sessions/` 不存在时该账号没有会话；macOS 与 Linux 行为相同（不用文件创建时间）。
- **续聊命令的限制**：启动目录已删除时 `cd` 失败；归档会话续聊前 Codex 会询问是否取消归档；`exec` 会话能否续聊未核实（§3）。

## 8. 回归测试

**环境**：本机全量 `python -m unittest discover -s tests`、`pyflakes src tests`；用合成会话文件，放在测试沙箱 HOME（`tests/helpers.py:47-66`）。

| 编号 | 用例 | 判据 |
| ---- | ---- | ---- |
| T1 | 两个账号、三条会话，文本输出 | 表头 `TITLE ACCOUNT CREATED UPDATED SESSION ID`；每条一行；时间按 `TZ=Asia/Shanghai` 换算正确；按更新时间从新到旧；没有 `resume` |
| T2 | 标题来源：有名称（且名称表里同一 ID 改过名）；无名称有提问；都没有 | `title_source` 依次 `name`（取最后一个名称）、`prompt`、`none`；`none` 文本显示 `-` |
| T3 | 首条提问判定：前面有 `<environment_context>`、`# AGENTS.md instructions for /x`、空文本块；旧格式 `event_msg/user_message` | 取到第一条普通提问 |
| T4 | 名称含换行、制表符、`ESC[31m`、U+202E、超过 60 字符；含中文与 U+200D | 输出无控制字符；文本截到 60 字符加 `…`；JSON 上限 200；中文与 U+200D 保留 |
| T5 | 首条提问在一个超过 64 KB 的行之后；首条提问在 1 MiB 之后 | 前者取到；后者 `none`；都不报错 |
| T6 | 子代理会话（`source` 为对象） | 不出现 |
| T7a | 第一行坏掉 / 不是 `session_meta`；`cwd` 为相对路径或含换行 | 照常列出，`directory` 为 null，`resume` 不带 `cd`，`--cmd` 文本含 `start directory unknown`；`created` 为 null（第一行坏掉时） |
| T7b | 全文件没有任何时间戳 | `updated` 等于文件修改时间 |
| T7c | 末尾是写了一半的行 | `updated` 等于前一个完整行的时间戳 |
| T7d | 超深嵌套的 JSON 行、非法 UTF-8 字节；时间为 0001 年与 9999 年末（UTC 以西、以东各一个时区） | 都退出 0、stderr 无 `Traceback`，越界时间显示 `-` |
| T7f | 名称、首条提问、目录里含 JSON 转义的孤立代理字符 `\ud83d` | 文本、`--cmd`、`--json` 都退出 0；该字符被删掉；目录为 null |
| T7e | `config.json` 内容损坏 | 退出 1，stderr 带解析错误 |
| T8 | 不符合命名的文件：`rollout-x.jsonl`、`sessions/2026/10/foo.jsonl`、`sessions` 下更深一层的文件、文件名结尾带换行 | 都不出现 |
| T9 | 归档会话 | 文本标题前有 `[archived] `；JSON `archived` 为 true；`--grep archived` 不因前缀命中 |
| T10 | 未登记的 `~/.codex` 有会话 | 账号 `default`、`registered` false、`resume` 含 `env -u CODEX_HOME -u CODEX_SQLITE_HOME -u CODEX_API_KEY -u CODEX_ACCESS_TOKEN codex resume`；`sessions default`、`sessions DEFAULT` 只列它 |
| T10b | 登记账号叫 `default`，同时有未登记的 `~/.codex` | 不带 NAME 列出两者；`sessions default` 只列登记账号 |
| T11 | `~/.codex` 是指向登记账号目录的软链 | 只列一次，账号为登记名 |
| T12 | 没有 `config.json`：默认目录有会话 / 没有 | 列出 / `no sessions found`，都退出 0 |
| T13 | `sessions work`；`sessions nosuch`；`sessions default`（默认目录不存在）；`sessions 'bad name'`（有、无配置） | 只列该账号；退出 1；退出 1；退出 2 |
| T14 | `--grep`：标题（大小写不同）、目录片段、会话 ID 前缀 | 只留命中的 |
| T15 | `--limit 1`；`--limit 0`；默认 30（31 条）；`--limit -1` | 1 条加 `showing 1 of N`；全部；30 条加 `showing 30 of 31`；退出 2 |
| T16 | `--json`；`--json --cmd` | 结构、`version`、`total`、`skipped` 正确，默认不限条数；两者输出相同 |
| T17 | 一个没有读权限的会话文件（root 下跳过） | stderr `skipped 1 session file`，退出 0 |
| T18 | `conversations`、`conversations work --json`；`convrsations` | 与 `sessions` 相同；后者未知命令退出 2 |
| T19 | `--cmd`，`COLUMNS=300` / `COLUMNS=40` | 前者表头 `TITLE ACCOUNT CREATED UPDATED CMD`、每条一行；后者无 `CMD`、无 `SESSION ID`，每条下一行 `"  " + 命令` |
| T20 | `--cmd` 宽度临界：`COLUMNS=W` 与 `W-1`（W 为单行排版最长行） | 前者单行，后者两行 |
| T21 | 中文标题对齐：`修复解析`、`abcdefgh`、`修复解析器` | 整行精确断言，列按显示宽度对齐 |
| T22 | 目录在 HOME 下、等于 HOME、含空格；不在 HOME 下 | `cd ~/sub`、`cd ~`、`cd ~/'my proj'`、`cd /w/proj`；JSON 仍为绝对路径 |
| T23 | 迁移未完成（有迁移记录）时运行 | 照常输出，退出 0 |
| T24 | 帮助与补全：`-h` 的 Everyday 组、`sessions -h`、bash 补全 `multi-codex sessions ` | 含 `sessions`；说明提到 `conversations`；补出账号名 |
| T25 | 只读：运行前后对账号目录做快照 | 无文件被创建、修改或删除 |
| T26 | 回归 | 现有全部用例通过 |
| T27 | 本机真实数据（手工） | `sessions --json --limit 0` 的条数等于非子代理会话文件数；耗时在秒级；抽 2 条运行续聊命令能打开对应会话（含 1 条归档会话，结果记入 §3） |

## 9. 日志 / 观测点

- 截断提示：`showing N of M sessions; use --limit 0 for all, or --grep TEXT to narrow`
- 没有会话：`no sessions found`
- 告警（stderr）：`skipped N session file(s) that could not be read`
- 报错（stderr）：`account 'x' is not registered…`、`--limit must be 0 or more`、配置文件解析错误
- `--json` 的 `total`、`skipped` 供脚本核对；`title_source`、`archived` 可判断标题来源与归档状态。
