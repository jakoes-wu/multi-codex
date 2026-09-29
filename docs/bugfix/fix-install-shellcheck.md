# install.sh 未通过 shellcheck 导致 Linux CI 失败

> 2026-09-29 注记：已修复，待合并。修复位置：`install.sh:111`。

## 1. 背景

首次推送到 GitHub 后，CI 中两个 Linux 任务（ubuntu-22.04 + Python 3.8、ubuntu-latest + Python 3.12）失败，两个 macOS 任务通过。唯一失败的用例是 `tests/test_install.py` 的 `test_shellcheck`；其余 79 个用例通过，生成的启动命令也通过了 shellcheck。

## 2. 目标 / 非目标

- 目标：`install.sh` 在 CI 的 shellcheck 下没有任何告警，Linux CI 恢复通过。
- 非目标：不改变 `install.sh` 的任何行为。

## 3. 根因分析

- 现象：shellcheck 报 `SC2015 (info): Note that A && B || C is not if-then-else`。
- 错误代码点：`install.sh:111`，写法为 `SCRIPT_DIR="$(cd "$(dirname "$0")" 2>/dev/null && pwd || true)"`。
- 失效路径：CI 的 `test_shellcheck` 要求 shellcheck 退出码为 0，而 shellcheck 对 info 级提示也返回非 0。
- 行为影响：没有。`pwd` 在 `cd` 成功后不会失败，`|| true` 只在 `cd` 失败时生效，结果与意图一致。这是写法问题，不是运行时缺陷。
- 为什么本地没发现：开发机和 Linux 测试机都没有安装 shellcheck，这个用例在两处都被跳过了。事后在开发机装了 shellcheck 0.11.0，但新版也不报这条，可见“能否复现”与 shellcheck 版本有关。

## 4. 复现步骤

1. 安装 **0.8.0 或 0.9.0** 版的 shellcheck（CI runner 上 apt 源的版本：ubuntu-22.04 为 0.8.0-2，ubuntu-latest 为 0.9.0-1）。
2. 在仓库根目录执行 `shellcheck install.sh`，第 111 行报 SC2015，退出码为 1。

shellcheck 0.11.0（macOS Homebrew 版）对同一文件不报告，退出码为 0，所以在本地新版下无法复现，只能以 CI 的结果为准。

## 5. 影响版本

提交 `85cb284`（首次发布）中的 `install.sh`。只影响 CI 与 shellcheck 检查，不影响安装行为。

## 6. 涉及模块

| 区域 | 位置 | 改动类型 | 改动点 |
| ---- | ---- | ---- | ---- |
| 安装脚本 | `install.sh:111` | 修改 | 改为 `SCRIPT_DIR="$(cd … && pwd)" \|\| SCRIPT_DIR=""`，由赋值语句自己的退出状态决定是否回退为空 |

## 7. 方案

### 7.1 实现要点

用赋值语句的退出状态（即命令替换的退出状态）判断 `cd` 是否成功，失败时把 `SCRIPT_DIR` 置空，不再在命令替换内部使用 `&& … ||`。

### 7.2 接口变更

无接口变更。

## 8. 影响分析

只改动“从本地仓库运行时定位脚本所在目录”这一行；远程安装（`curl | sh`）不经过这一行。行为不变。

## 9. 回归测试

- `shellcheck install.sh` 无输出、退出码为 0（本地 0.11.0 修改前后都为 0，不能单独作为判据）；
- `tests/test_install.py` 全部用例通过（本地安装、远程安装、中断恢复、卸载、`--help`）；
- 推送分支后 CI 四个任务全部通过。

## 10. 日志 / 观测点

CI 任务中 `test_shellcheck` 的结果，以及 `shellcheck install.sh` 的退出码。
