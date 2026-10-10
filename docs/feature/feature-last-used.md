# multi-codex v0.11：`list` 显示账号最近使用时间（LAST USED）

> 2026-10-09 注记：已落地（PR #30，main `89dc9a6`），随 v0.11.0 发布。plan-review 两轮收敛（第 1 轮 0 高 2 中 9 低，第 2 轮 0 高 0 中 2 低）。实现位置：`src/multi_codex/usage.py` 的 `last_used`；`src/multi_codex/cli.py` 的 `_print_list_summary`、`_age_cell`、`_iso_utc` 与 `cmd_list` JSON；`scripts/make-assets.py` 的 `fit_mono`。测试：`tests/test_last_used.py`。PR 评审补充：最新候选文件枚举后被删时退到下一个。基线：main `e4f0b64`（v0.10.0 之后只合并了文档改动）。
>
> 用户的决定（2026-10-09）：
>
> - 参考姊妹项目 multi-claude 的 `list`，在本项目的 `list` 简表中加 LAST USED 列。
> - 时间取账号目录里会话文件（`sessions/`、`archived_sessions/` 下的 `rollout-*.jsonl`）与 `history.jsonl` 中最新的修改时间，只做 stat，不读内容。
> - `history.jsonl` 是软链（与其它账号共享）时不计入；`sessions` 是软链时，单元格末尾加 `*`，并沿用 USAGE 列的脚注。
> - 按完整流程做完并发布 v0.11.0。

## 1. 背景

- `list` 简表目前有 NAME、LOGIN、PROXY、SHARED、USAGE、STATUS 六列（`cli.py:987`），看不出各账号最近一次是什么时候用的。
- multi-claude 的做法：`history.last_used(account_dir)` 取 `history.jsonl` 与第一层会话文件的最大 mtime（multi-claude `src/multi_claude/history.py:154-178`），`_format_age` 把时间差显示为 `Nm ago` / `Nh ago` / `Nd ago`（multi-claude `src/multi_claude/cli.py:1576-1582`），`list --json` 给出 ISO 时间（同文件 `:1401`、`:1496-1497`）。
- 本项目中 Codex 的会话文件位置与 multi-claude 不同：`sessions/YYYY/MM/DD/rollout-*.jsonl` 和 `archived_sessions/rollout-*.jsonl`，`usage._candidate_files`（`usage.py:113-124`）已经按这两个模式枚举并按 mtime 排序。本机实测 gmail、icloud 两个账号的最新会话文件与 `history.jsonl` 修改时间相差都在 1 分钟以内。

## 2. 目标 / 非目标

**目标**

1. `list` 简表在 USAGE 与 STATUS 之间加 LAST USED 列，格式与 multi-claude 相同。
2. `list --json` 每个账号加 `last_used` 字段（UTC ISO 时间或 null）。

**非目标**

- `list -v`（完整表格）不变，与 multi-claude 一致。
- 不读会话内容、不统计 token 用量（multi-claude 的 `usage --history` 不在本次范围）。
- 不改变“什么算一次使用”：只看文件修改时间，不追踪进程。
- 不修改 `usage` 命令与 `doctor`。

## 3. 假设与约束

- **时间来源**：
  - `usage._candidate_files(account_dir)` 返回按 mtime 从新到旧排序的会话文件（最多 20 个，`usage.py:30`、`:123-124`），取第一个的 mtime；
  - 再取 `history.jsonl` 的 mtime（不存在则跳过）；
  - 两者取最大值；都没有时为 None。
- **共享的处理**：
  - `history.jsonl` 是软链（`os.path.islink`）时不计入：它可能被多个账号共享（README「What can be shared」表中 `history.jsonl` 为可共享），软链目标的 mtime 反映的是任意一个账号的使用；
  - `sessions` 是软链时，会话文件同样可能来自别的账号：单元格末尾加 `*`，判定与 USAGE 列相同（`usage.local_snapshot` 的 `sessions_shared`，`usage.py:195`）。
- **`archived_sessions` 的 mtime**：仓库里没有证据说明 Codex 归档会话时是改名（保留 mtime）还是复制 / 重写（mtime 变为归档时刻），属于未知。若是后者，归档一个旧会话会让 LAST USED 显示为刚刚；归档本身是用户在 Codex 里的操作，算作一次使用可以接受。
- **`archived_sessions` 是软链**：不加 `*`，与 USAGE 现状一致（`usage.py:195` 只判断 `sessions`）。
- **相对时间**（与 multi-claude `_format_age` 相同）：
  - 分钟数 = `max(0, int((now - mtime) // 60))`（未来时间按 0 处理）；
  - 小于 60 分钟：`{N}m ago`；小于 48 小时：`{N}h ago`（整小时向下取整）；否则 `{N}d ago`（整天向下取整）。
- **代价**：`_candidate_files` 对两个 glob 模式做 stat，本机账号实测 0–17 毫秒（v0.8 方案实测，`local_snapshot` 用的是同一个函数）；每个账号多一次 glob 与一次 stat。

## 4. 涉及模块

| 区域 | 行号锚点（基线 `e4f0b64`） | 改动类型 | 改动点 |
| ---- | ---- | ---- | ---- |
| `src/multi_codex/usage.py` | `local_snapshot` :193-200 之后 | 新增 | `last_used(account_dir) -> Optional[float]`（epoch 秒） |
| `src/multi_codex/cli.py` | `cmd_list` JSON :932-946 | 修改 | 每个账号加 `"last_used"` |
| `src/multi_codex/cli.py` | `_print_list_summary` :976-1018 | 修改 | 表头与每行加 LAST USED；`*` 与脚注 |
| `src/multi_codex/cli.py` | `_usage_cell` :1030 之前 | 新增 | `_age_cell(epoch, now)`、`_iso_utc(epoch)` |
| `tests/test_everyday.py` | :237 | 修改 | 表头按 `split()` 拆分后断言，改为 `["NAME", "LOGIN", "PROXY", "SHARED", "USAGE", "LAST", "USED", "STATUS"]` |
| `tests/test_last_used.py` | 新文件 | 新增 | §8 |
| `README.md`、`README.zh-CN.md` | 速查表 :113 的 list 说明、命令表 :169 的 `list` 行、Login and usage / 登录身份与额度一节的示例表（`README.md:189-195`）与说明段（`README.md:197`、`README.zh-CN.md:204`） | 修改 | 加上“最近使用时间”；示例表加 LAST USED 列；说明数据来源与 `*` 含义 |
| `scripts/make-assets.py` | `make_social` :157-177 | 修改 | 表格最长一行超出边框时自动调小等宽字号（§5.1.4） |
| `docs/assets/demo.gif`、`social-preview.png` | 重新生成 | 修改 | 运行 `scripts/make-assets.py`，`list` 输出多出 LAST USED 列 |
| `CHANGELOG.md` | Unreleased | 修改 | Added |

**依赖 `list` 简表文字的测试全集**（`grep -rn '"list"' tests/ | grep -v -- '--json'` 与 `grep -rn "split()\[\|sessions is shared" tests/`，不截断）：

- `test_everyday.py:237`：表头逐列断言，必须改；
- `test_everyday.py:270`：`none_row.split()[4]` 取 USAGE 列。LAST USED 列在其后，`none` 账号没有会话文件，LAST USED 为 `-`，第 5 个字段仍是 USAGE 的 `-`，不受影响；
- `test_everyday.py:271`、`:288`：脚注断言前缀 `* sessions is shared with other accounts`，脚注改写后前缀不变，不受影响；
- 其余简表断言都不依赖列数：`test_everyday.py` 的 STATUS 列用 `endswith`，`split()[0]`、`[1]` 在 LAST USED 之前；`test_accounts.py:96`、`:290`，`test_ergonomics.py:228`，`test_isolation.py:91`、`:98`、`:233`，`test_switch.py:135` 都是子串断言。
- `list --json`：没有测试对账号字段集合做相等断言，新增字段不影响现有用例。

**`_candidate_files` 的调用点全集**（`grep -rn "_candidate_files" src/`，不截断）：`usage.py:196`（`local_snapshot`），本方案新增 `last_used` 一处，函数本身不改。

## 5. 方案

### 5.1 实现要点

#### 5.1.1 `usage.last_used`

```python
def last_used(account_dir: str) -> Optional[float]:
    """账号最近一次使用的时间（epoch 秒）：最新会话文件与 history.jsonl 的最大 mtime；都没有时为 None。"""
    times = []
    for path in _candidate_files(account_dir):     # 已按 mtime 从新到旧排序
        try:
            times.append(os.stat(path).st_mtime)    # 取第一个还能 stat 的
            break
        except OSError:
            continue                               # 枚举之后被删，退到下一个（PR #30 评审意见）
    history = os.path.join(account_dir, "history.jsonl")
    if not os.path.islink(history):                # 共享的 history 反映的是任意账号的使用
        try:
            times.append(os.stat(history).st_mtime)
        except OSError:
            pass
    return max(times) if times else None
```

#### 5.1.2 简表

```text
default: work
NAME  LOGIN          PROXY                  SHARED  USAGE           LAST USED  STATUS
work  w@example.com  http://127.0.0.1:7901  yes     5h 23%, 7d 41%  12m ago    ok
home  -              inherit                no      -               -          not logged in
```

- 账号目录不存在时为 `-`（与 USAGE 相同，不读）。
- `sessions` 是软链且单元格不是 `-` 时，末尾加 `*`；只要 `sessions` 是软链就加，不论最大值来自会话文件还是本账号自己的 `history.jsonl`（偏保守，不会误导）。
- 是否输出脚注改为按“USAGE 或 LAST USED 任一单元格加了 `*`”判断（现在只看 USAGE，`cli.py:999-1001`）；脚注改为 `* sessions is shared with other accounts; usage and last used may belong to another account`（前缀不变）。
- `_age_cell(epoch, now)`：按 §3 的规则格式化；`epoch` 为 None 时返回 `-`。

#### 5.1.3 `list --json`

每个账号新增 `"last_used"`：`_iso_utc(epoch)`，格式 `YYYY-MM-DDTHH:MM:SSZ`（UTC，与 multi-claude 相同），写法沿用 `usage.to_json` 的 `time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))`（`usage.py:461`，秒向下截断）；没有任何文件或账号目录不存在时为 `null`。JSON 不标注共享（与 USAGE 一致，`list --json` 本来不含额度）。

#### 5.1.4 社交预览图的字号

评审实测（Menlo，与脚本同一字体）：表头行由 81 个字符变为 92 个字符后，21 号字宽约 1163 像素，从 x=112 画起会越过边框右沿 1200（`scripts/make-assets.py:165`、`:175`）。演示 GIF 用 15 号字，宽约 831 像素，加左边距后小于 940，不受影响。

`make_social` 改为：先用 21 号字量出表格中最长一行的宽度（`font.getlength`），超过 `box[2] - box[0] - 96`（左右各留 48 像素，避免文字贴着边框）就逐号减小，直到放得下；减到 14 号仍放不下时 `sys.exit` 报错，不生成越界的图。`load_fonts` 返回的 21 号社交字体作为起始字号，不改它的签名。

### 5.2 接口变更

| 接口 | 变更 | 兼容性 |
| ---- | ---- | ---- |
| `list` 简表 | 新增 LAST USED 列；共享脚注文字补上 last used | README 已写明表格排版不保证稳定，脚本请用 `--json` |
| `list --json` 每账号 `last_used` | 新增字段 | 只加字段，`version` 仍为 1 |
| `list -v` | 不变 | — |

本方案不涉及 `docs/reference/*`。

## 6. 备选方案与决策

- **读会话内容里最后一条记录的时间戳**：更准确但要读文件，`list` 的开销随会话大小增长；mtime 已足够反映“最近使用”。不采用。
- **只看会话文件**：用户选定同时计入 `history.jsonl`（与 multi-claude 一致）。
- **账号目录的 mtime**：目录 mtime 只在增删条目时变化，不可靠（multi-claude 方案同样否决）。
- **与 `local_snapshot` 共用一次枚举**：要改 `local_snapshot` 的签名或返回值；两次枚举都是毫秒级，不值得。
- **“ago”格式与 `usage` 命令不同**：`usage` 显示 `3h12m ago`（`usage.py:401-410` `_human_duration`），`list` 显示 `3h ago`，与 multi-claude 一致，是有意的取舍：简表要窄。

## 7. 影响分析

**正向**

- **`list` 简表**：多一列；每个账号多一次 `_candidate_files`（两个 glob + stat）与一次 `history.jsonl` 的 stat，毫秒级。
- **`list --json`**：多一个字段，同样的读取代价。

**反向**

- **`usage` 命令、`local_snapshot`**：不改；`_candidate_files` 只是多一个调用方。
- **`list -v`**：不调用 `last_used`，输出逐字不变（`test_everyday.py` 的 `test_verbose_is_the_old_table` 覆盖）。
- **演示图**：`make-assets.py` 抓的是真实输出，示例账号有一条刚写入的会话记录，LAST USED 显示 `0m ago`；图片需重新生成。表格变宽约 11 个字符：演示 GIF 放得下；社交预览图按 §5.1.4 自动调小字号（实测 92 个字符的表头会选 18 号，宽约 998 像素）。
- **时钟与文件系统**：mtime 晚于当前时间（时钟回拨、跨机复制）时按 0 分钟显示，不出现负数。

**运行时**：无新进程，无网络。

## 8. 回归测试

**环境**：本机，以及编译机 ubuntu20 的 Python 3.8.10（按验证手册跑两次）。命令：`python3 -m unittest discover -s tests -t tests`。

| 编号 | 用例 | 判据 |
| ---- | ---- | ---- |
| T1 | `_age_cell` 函数级 | 0 秒 → `0m ago`；59 分 → `59m ago`；60 分 → `1h ago`；47 小时 59 分 → `47h ago`；48 小时 → `2d ago`；未来 5 分钟 → `0m ago`；None → `-` |
| T2 | 只有会话文件（mtime 设为 3.5 小时前） | LAST USED 为 `3h ago`；JSON `last_used` 等于 `time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(mtime))` |
| T3 | 只有 `history.jsonl`（mtime 2.5 天前） | `2d ago` |
| T4 | 会话文件 3.5 小时前、`history.jsonl` 10.5 分钟前 | `10m ago`（取最大值） |
| T5 | 只有 `archived_sessions/rollout-*.jsonl`（mtime 3.5 小时前） | `3h ago` |
| T6 | `history.jsonl` 是指向别处的软链（目标 mtime 为现在），会话文件 3.5 小时前；另一账号只有软链的 `history.jsonl` | 前者 `3h ago`，后者 `-`（软链不计入） |
| T7a | `sessions` 软链，会话文件不含额度快照 | USAGE 为 `-`，LAST USED 为 `Nm ago*`，表格后有脚注 |
| T7b | `sessions` 软链但目录为空，也没有 `history.jsonl` | LAST USED 为 `-`，不带 `*`，没有脚注 |
| T7c | `sessions` 软链，会话文件含额度快照 | USAGE、LAST USED 都带 `*`；脚注含 `usage and last used may belong to another account` |
| T8 | 没有任何文件；账号目录不存在 | `-`；JSON `null` |
| T9 | 表头 | `test_everyday.py:237` 按 `split()` 断言为 `["NAME", "LOGIN", "PROXY", "SHARED", "USAGE", "LAST", "USED", "STATUS"]` |
| T10 | `list -v` | 不含 LAST USED，与旧输出逐字相同（现有用例） |
| T11 | 社交预览图（只在 macOS 本机执行，脚本依赖 macOS 系统字体；编译机只跑 unittest） | `make-assets.py` 正常生成，脚本内断言最长一行宽度不超过边框（§5.1.4）；目检两张图 |
| T12 | README | 中英两份的示例表、速查表与命令表都提到最近使用时间，与 `list` 实际输出一致 |
| T13 | 回归 | 现有全部用例通过 |

## 9. 日志 / 观测点

- `list` 的 LAST USED 列与脚注；`list --json` 的 `last_used`。
- 不新增日志：`list` 是只读命令。
