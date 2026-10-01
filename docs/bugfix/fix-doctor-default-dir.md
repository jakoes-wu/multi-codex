# doctor 的 default-dir 检查没有展开 `~`，总是报告“不存在”

> 2026-10-01 注记：方案已定，代码已落地（未提交，等待编译机验证），随 v0.4 一起发布。由 v0.4 方案评审发现（`docs/feature/feature-default-switch.md` 依赖这项检查）。修复位置：`src/multi_codex/doctor.py` 的 `_check_default_dir`；§9 的用例为 `tests/test_insight.py` 的 `DoctorTest.test_default_dir_states`，已确认修复前会失败。

## 1. 背景

`multi-codex doctor` 的 `default-dir` 一项，用来报告 `~/.codex` 是普通目录、指向哪个账号的软链，还是不存在。

## 2. 目标 / 非目标

**目标**：`default-dir` 按展开后的真实路径检查 `~/.codex`，正确报告它的状态。

**非目标**：不改变这项检查的判定规则与输出格式；不改动 `platform.default_source()`，其它调用方都已自行展开。

## 3. 根因分析

- **现象**：`~/.codex` 是指向已登记账号的软链时，`doctor` 仍然输出 `ok  default-dir  ~/.codex does not exist`。
- **触发条件**：任何情况。只有当前工作目录下恰好有一个名为 `~` 的目录时，结果才会不同。
- **失效路径**：`platform.default_source()` 返回字面字符串 `"~/.codex"`（`src/multi_codex/platform.py:23-24`）。`doctor` 把它直接交给 `entry_kind()` 做 `lstat`，`~` 不会被展开，实际检查的是“当前目录下的 `~/.codex`”。
- **错误代码点**：`src/multi_codex/doctor.py:116`，`source = platform.default_source()` 缺少 `expand()`。对照 `src/multi_codex/migrate.py:130`，那里写的是 `expand(source or platform.default_source())`。
- **为什么测试没发现**：`tests/test_insight.py` 的 `DoctorTest` 只覆盖了 `~/.codex` 不存在的情况；而开发机上 `~/.codex` 恰好也不存在，实机运行时的输出看起来是对的。

## 4. 复现步骤

```sh
export HOME=$(mktemp -d)
multi-codex add a
ln -s "$HOME/.cx/a" "$HOME/.codex"
multi-codex doctor | grep default-dir     # 实际：~/.codex does not exist；应为：links to account a
```

## 5. 影响版本

multi-codex 0.3.0，所有平台。只影响 `doctor` 这一项的输出，以及 `doctor --json` 中对应的 `checks[]` 条目，不影响其它命令。

## 6. 涉及模块

| 区域 | 行号锚点（基线 `493818f`） | 改动类型 | 改动点 |
| ---- | ---- | ---- | ---- |
| 体检 | `src/multi_codex/doctor.py:116` | 修改 | `source = expand(platform.default_source())`；输出中仍显示 `~/.codex` 这种短写法，方便阅读 |
| 测试 | `tests/test_insight.py` `DoctorTest` | 新增 | 见 §9 |
| 文档 | `CHANGELOG.md` 的 Fixed 一节 | 修改 | 记录这次修复 |

## 7. 方案

### 7.1 实现要点

用 `fsutil.expand()` 展开后再做 `entry_kind` 和 `realpath` 判断；消息中的路径继续使用 `platform.default_source()` 的短写法。

### 7.2 接口变更

无接口变更：输出格式不变，只是结果变得正确。

## 8. 影响分析

只影响 `_check_default_dir` 这一个函数。修复后，`~/.codex` 是普通目录、软链或其它类型时，会如实报告为 ok 或 warn（规则见 `docs/feature/feature-account-insight.md` §5.1.3）。之前一直被误报为 ok 的“指向未登记位置”的情况，现在会变成 warn；这正是这项检查本来的用途。

## 9. 回归测试

在 `DoctorTest` 中增加 4 种 `~/.codex` 状态，分别断言：
1. 指向已登记账号的软链：ok，说明中写出账号名；
2. 指向未登记位置的软链：warn；
3. 普通目录：ok，说明“not migrated”；
4. 不存在：ok。

测试中的 CLI 以 `cwd=self.tmp` 运行（`tests/helpers.py:84-89`），临时目录下没有名为 `~` 的目录，所以修复前第 1、2、3 种都会失败，可以证明这组用例确实能发现这个问题。

## 10. 日志 / 观测点

`doctor` 输出中 `default-dir` 这一行，以及 `doctor --json` 中 `id == "default-dir"` 的条目。
