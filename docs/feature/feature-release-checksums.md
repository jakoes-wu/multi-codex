# 发布包附 SHA-256 校验和，install.sh 下载后校验（v0.5）

> 2026-10-01 注记：代码已落地（未提交，等待编译机验证）。实现：`.github/workflows/release.yml`；`install.sh` 的下载与校验段。§8 第 1–7 条的用例在 `tests/test_install.py` 的 `ChecksumTest` 中。
> - §8 第 8 条已于 2026-10-01 发布 v0.5.0 后实际核对：发布工作流一次通过（含版本自检）；release 页面有 `multi-codex-v0.5.0.tar.gz`（196738 字节）和 `SHA256SUMS`；`SHA256SUMS`、本地 `shasum -a 256` 与 Python `hashlib` 三者一致（`d01a3835…`）；在临时目录用默认安装命令安装，输出 `verified sha256` 并装出 0.5.0；`MULTI_CODEX_REF=v0.4.0`（没有附件）时走 codeload，提示 `is not verified` 并照常安装。
>
> 本文其余部分保持方案原文。

## 1. 背景

`install.sh` 的远程安装从 `https://codeload.github.com/<repo>/tar.gz/<ref>` 下载 GitHub 自动生成的源码包（`install.sh:123-137`），下载后没有任何完整性校验。路线中的 U14 要求：release 附带校验和，`install.sh` 下载后先校验。开源项目 xjoker/codex-switch 在自更新时会校验 SHA-256。

GitHub 自动生成的源码包不保证字节稳定，不能拿来预先计算校验和。所以发布时要另外生成一个固定的包，作为 release 附件上传。

## 2. 目标 / 非目标

**目标**

1. 每次发布 release 时，由 GitHub Actions 生成 `multi-codex-<tag>.tar.gz` 和 `SHA256SUMS`，作为 release 附件上传。
2. `install.sh` 安装某个 release 时，优先下载这个附件，按 `SHA256SUMS` 校验，不一致就停止安装，退出码 1。
3. 没有附件时（v0.4.0 及更早的 release，或安装的是分支），退回原来的下载方式，并明确提示“没有校验”；设置 `MULTI_CODEX_REQUIRE_CHECKSUM=1` 时改为直接失败。

**非目标**

- 不做签名或构建来源证明（attestation）。校验和与包放在同一个 release 里，只能发现下载过程中的损坏和替换，不能防范 GitHub 账号本身被攻破。README 如实说明。
- 不为已发布的 v0.1.0–v0.4.0 补传附件。
- 不改变本地安装（从克隆目录运行 `./install.sh`）的行为。

## 3. 假设与约束

- `install.sh` 是 POSIX sh，但在下载之前已经确认 `python3 >= 3.8` 可用（`install.sh:93-99`），所以解析 release JSON、计算 SHA-256 都用 python3 完成，不依赖 `sha256sum` / `shasum` 是否存在，也避免用 sed 解析 JSON。
- 附件的下载地址从 release JSON 的 `assets[].browser_download_url` 读取，不在脚本里拼写 GitHub 的 URL。测试中可以用 `MULTI_CODEX_API=file://...` 提供假的 release JSON 和 `file://` 附件，完全离线（现有测试已是这种做法，`tests/test_install.py:60-67`）。
- 附件包的结构与 codeload 源码包一样，都是“顶层一个目录，里面是仓库内容”；顶层目录名不同（附件为 `multi-codex-v0.5.0`，codeload 为 `multi-codex-0.5.0`），但 `install.sh` 现有的 `find ... -path '*/src/multi_codex'`（`install.sh:138`）与顶层目录名无关，无需修改。
- `curl ... | sh` 时，sh 从标准输入读取脚本本身。新增的每次 python3 调用都必须用 `-c` 或 heredoc（参照 `tree_hash`，`install.sh:145-162`），不能让 python3 读标准输入；tag 名、文件名、期望的校验和等值一律经 argv 传给 python3，不拼进代码字符串。现有用例 `test_piped_install_ignores_clone_in_current_directory`（`tests/test_install.py:99-108`）覆盖这条路径。
- 脚本使用 `set -eu`：形如 `X="$(python3 ...)"` 的赋值失败时会静默退出，所以每次 python3 调用后都要 `|| die "<说明>"`。

## 4. 涉及模块

| 区域 | 行号锚点（基线 `8a4e559`） | 改动类型 | 改动点 |
| ---- | ---- | ---- | ---- |
| 发布流程 | 新文件 `.github/workflows/release.yml` | 新增 | 触发条件为 `release: published`；见 §5.1.1 |
| 安装脚本 | `install.sh:39-53` 帮助文本 | 修改 | 说明新增的环境变量 `MULTI_CODEX_SHA256`、`MULTI_CODEX_REQUIRE_CHECKSUM`、`MULTI_CODEX_CODELOAD` |
| 安装脚本 | `install.sh:123-135` | 修改 | 选择下载来源并校验，见 §5.1.2 |
| 测试 | `tests/test_install.py` | 新增 | 见 §8 |
| 文档 | `README.md`、`README.zh-CN.md` 的 Installation 一节；`CHANGELOG.md`；`CONTRIBUTING.md`（新增“发布”一节） | 修改 | |

## 5. 方案

### 5.1 实现要点

#### 5.1.1 发布流程

`.github/workflows/release.yml`：

- 触发：`on: release: types: [published]`；权限：`contents: write`。`release` 事件只在用户凭据（不是 `GITHUB_TOKEN`）创建 release 时触发；本项目一直用 `gh release create` 手工发布，满足这个条件。
- 环境：`GH_TOKEN: ${{ github.token }}`（GitHub Actions 中的 `gh` 需要它），`TAG: ${{ github.event.release.tag_name }}`。脚本中只引用 `$TAG`，不在 `run` 中内联 `${{ }}` 表达式，避免脚本注入。
- 步骤：
  1. `actions/checkout@v4`，`ref` 为 `${{ github.event.release.tag_name }}`；
  2. `git archive --format=tar.gz --prefix="multi-codex-${TAG}/" -o "multi-codex-${TAG}.tar.gz" HEAD`（checkout 的 ref 已经是这个 tag，用 `HEAD` 不依赖本地是否取到 tag 引用）；
  3. `sha256sum "multi-codex-${TAG}.tar.gz" > SHA256SUMS`；
  4. 自检：解包后确认其中有 `src/multi_codex/__init__.py`，并且其中的 `__version__` 等于去掉前缀 `v` 的 tag，不一致就让工作流失败（防止忘记改版本号）；
  5. `gh release upload "${TAG}" "multi-codex-${TAG}.tar.gz" SHA256SUMS --clobber`。
- `SHA256SUMS` 的格式就是 `sha256sum` 的标准输出：`<64 位十六进制>  <文件名>`。

#### 5.1.2 install.sh 的下载与校验

按以下顺序决定下载什么、怎么校验：

| 情况 | 下载 | 校验 |
| ---- | ---- | ---- |
| 设置了 `MULTI_CODEX_TARBALL` | 该地址 | 设置了 `MULTI_CODEX_SHA256` 时按它校验；否则不校验（同时设置了 `MULTI_CODEX_REQUIRE_CHECKSUM=1` 时报错退出） |
| 没有设置 `MULTI_CODEX_REF` | 读取 `releases/latest` 的 JSON | 见下一行 |
| release JSON 中有 `multi-codex-<tag>.tar.gz` 和 `SHA256SUMS` 两个附件 | 附件包 | 从 `SHA256SUMS` 中取出该文件名对应的值来校验 |
| release JSON 中没有这两个附件 | codeload 源码包 | 不校验 |
| 设置了 `MULTI_CODEX_REF`，且形如版本 tag（完整匹配 `v<数字>.<数字>.<数字>`，后面可以有 `-` 或 `+` 开头的后缀，由 python3 用正则判断） | 读取 `releases/tags/<REF>`：读到了就按上面两行处理；**读不到就报错退出**（网络错误、API 限流或 tag 不存在都不悄悄降级） | 见上两行 |
| 设置了 `MULTI_CODEX_REF`，且不形如版本 tag（例如 `main`、`v2-dev`） | codeload 源码包，不读 API | 不校验 |

- `SHA256SUMS` 每行的格式为 `<值>  <文件名>`，也接受二进制模式的 `<值> *<文件名>`。
- 校验用 python3 计算下载文件的 SHA-256：一致时输出 `verified sha256 <值>`；不一致时报错 `checksum mismatch for <文件>: expected <值>, got <值>`，退出码 1，不安装任何东西；`SHA256SUMS` 中找不到该文件名时，同样报错退出。
- 没有校验时输出 `note: <来源> is not verified (no checksum published)`；设置了 `MULTI_CODEX_REQUIRE_CHECKSUM=1` 时，这种情况改为报错退出，退出码 1。
- 不指定 REF 且读不到 `releases/latest` 时，报错信息保留现有的 `MULTI_CODEX_REF` 提示（`install.sh:127-128`；`test_no_release_without_ref` 依赖它）。
- 读取 release JSON 用 python3 的 `json` 模块，替换现在用 sed 提取 `tag_name` 的写法（`install.sh:129`）。
- codeload 的基础地址改为可由环境变量 `MULTI_CODEX_CODELOAD` 覆盖（默认 `https://codeload.github.com`），与现有的 `MULTI_CODEX_API` 一样，主要供测试离线使用。
- 校验在解包之前完成：校验失败时不会解包，也不会触碰已安装的版本。

### 5.2 接口变更

| 类型 | 接口 | 兼容性 |
| ---- | ---- | ---- |
| 新增 | release 附件 `multi-codex-<tag>.tar.gz`、`SHA256SUMS`（v0.5.0 起） | 新增 |
| 新增 | `install.sh` 的环境变量 `MULTI_CODEX_SHA256`、`MULTI_CODEX_REQUIRE_CHECKSUM`、`MULTI_CODEX_CODELOAD` | 新增，不设置时行为见 §5.1.2 |
| 修改 | 默认的远程安装从 codeload 源码包改为下载 release 附件（release 有附件时） | 安装出来的内容相同 |

仓库中没有 `docs/reference/` 目录，不涉及 reference 章节的同步检查。

## 6. 备选方案与决策

| 方案 | 结论 |
| ---- | ---- |
| 对 codeload 源码包预先算校验和 | 否决：GitHub 不保证这个包字节稳定 |
| 用 `gh attestation verify` 校验构建来源（xjoker 的做法） | 否决：要求用户装 gh 并登录，安装脚本应只依赖 curl/wget 和 python3 |
| 在 install.sh 里用 `sha256sum` / `shasum` | 否决：macOS 没有 `sha256sum`，各系统的命令不同；python3 已是必需依赖 |

## 7. 影响分析

- **默认安装路径**：v0.5.0 起，`curl ... | sh` 下载的是 release 附件，内容由 `git archive` 生成，与 codeload 源码包相同（都来自同一个 tag），安装结果相同。
- **旧 release 与分支**：仍走 codeload，多一行“没有校验”的提示；只有设置了 `MULTI_CODEX_REQUIRE_CHECKSUM=1` 才会失败。
- **发布步骤**：发布 release 后，工作流需要约 1 分钟上传附件。在这之前，或者上传失败时，用默认命令安装会走“没有附件”的分支（不校验）。`CONTRIBUTING.md` 目前没有发版步骤，新增一节写明：发布后先确认附件已上传，再宣布新版本。
- **离线测试**：全部依赖 `file://`，CI 中不访问 GitHub。

## 8. 回归测试

在 `tests/test_install.py` 中新增，均使用 `file://` 地址：

1. 假 release JSON 中带两个附件、校验和正确：安装成功，输出 `verified sha256`。
2. 校验和不正确：退出码 1，输出 `checksum mismatch`，已安装的版本（预先装好一份）没有任何变化。
3. `SHA256SUMS` 中没有该文件名：退出码 1。
4. release JSON 中没有附件：安装成功，输出 `is not verified`；同时设置 `MULTI_CODEX_REQUIRE_CHECKSUM=1`：退出码 1。
5. `MULTI_CODEX_REF=v2-dev`（`MULTI_CODEX_CODELOAD` 指向 `file://` 目录）：不读 API，走 codeload，输出 `is not verified`。`MULTI_CODEX_REF=<tag>`：读取 `releases/tags/<tag>` 成功时校验附件；读取失败时退出码 1。`MULTI_CODEX_REF=main`：不读 API，走 codeload（测试中把 `MULTI_CODEX_CODELOAD` 指向 `file://` 目录），输出 `is not verified`。
5a. `SHA256SUMS` 使用 `*文件名` 格式时同样能校验。
5b. 经管道安装（`cat install.sh | sh`）时，下载、校验、安装都正常（现有 `test_piped_install` 的路径加上附件）。
6. `MULTI_CODEX_TARBALL` 加正确或错误的 `MULTI_CODEX_SHA256`：分别成功、失败；不加 `MULTI_CODEX_SHA256` 但设置 `MULTI_CODEX_REQUIRE_CHECKSUM=1`：失败。
7. 现有的安装测试全部通过（本地安装、重复安装、中断恢复、卸载、shellcheck）。
8. 工作流本身无法在 PR 的 CI 中运行。v0.5.0 发布后实际检查：release 页面出现两个附件，`SHA256SUMS` 与本地 `sha256sum` 一致；在临时目录用默认安装命令安装，输出 `verified sha256`。

## 9. 日志 / 观测点

- 安装输出：`[multi-codex-install] downloading <URL>`、`verified sha256 <值>`，或者 `note: ... is not verified (no checksum published)`。
- 失败：`[multi-codex-install] error: checksum mismatch for <文件>: expected <值>, got <值>`，退出码 1。
- 发布工作流：GitHub Actions 的 release 运行日志，以及 release 页面的附件列表。
