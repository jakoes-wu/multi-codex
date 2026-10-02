# 分发与展示：PyPI 发布、Homebrew 配方、README 演示图与徽章

> 2026-10-02 注记：已落地。
> - PR #20（main `cf61548`）：`pypi.yml`、README 徽章与演示图、`scripts/make-assets.py`、`.gitattributes`、pyproject 元数据；Homebrew 配方在 https://github.com/jakoes-wu/homebrew-tap （本机 install / test / audit 通过）。
> - 用户登记 Trusted Publisher 后，手动触发 `pypi.yml` 补传 v0.7.0 成功（run 37008405060），从 PyPI 官方源安装后 `multi-codex --version` 为 0.7.0；随后另起 PR 给 README 加 PyPI 徽章与 `pipx install multi-codex`。

## 1. 背景

基线 main `1e27962`（v0.7.0）。当前只能用 `install.sh`（`curl | sh` 或克隆后运行）或 `pipx install git+https://…` 安装；PyPI 上没有 `multi-codex` 包（`https://pypi.org/pypi/multi-codex/json` 返回 404），也没有 Homebrew 配方。README 只有文字说明，没有演示图和徽章；仓库没有设置 topics，也没有自定义社交预览图。

用户 2026-10-02 要求：把这些能在仓库内完成的事做掉，需要用户本人账号操作的部分写成操作步骤。

## 2. 目标 / 非目标

**目标**

1. 发布 release 时，GitHub Actions 自动构建 sdist 与 wheel，并通过 PyPI Trusted Publishing 上传；也能手动对已有 tag 补发。
2. 提供 Homebrew 配方（放在独立的 tap 仓库 `jakoes-wu/homebrew-tap`），安装 release 附件中的源码包。
3. README 顶部加徽章与演示 GIF；生成 1280×640 的社交预览图，供用户在仓库设置中上传。

**非目标**

- 不改任何命令行为，不改 `install.sh`。
- 不在 release 工作流里自动更新 tap 仓库（需要跨仓库写权限的令牌，由用户手动更新配方，步骤写进操作手册）。
- 不提交到 homebrew-core。
- PyPI 账号注册、Trusted Publisher 配置、社交预览图上传由用户完成（GitHub API 不支持上传社交预览图）。

## 3. 假设与约束

- PyPI 侧必须先由用户为 `jakoes-wu/multi-codex` 的工作流 `pypi.yml`、环境 `pypi` 添加 pending publisher；未配置前该工作流的上传步骤会失败，构建与检查步骤不受影响。
- 演示 GIF 用 PIL 按真实命令输出逐帧绘制（本机没有 vhs / asciinema）；输出来自临时 HOME 中实际运行的 `multi-codex`，登录身份用 `example.com` 邮箱构造的假 `auth.json`，GIF 下方注明“示例账号”。
- README 中的图片用 `raw.githubusercontent.com` 的绝对地址，PyPI 项目页才能显示。
- 图片不进入 release 源码包：`.gitattributes` 中 `docs/assets export-ignore`，`install.sh` 下载的包不因图片变大。

## 4. 涉及模块

| 区域 | 行号锚点（基线 `1e27962`） | 改动类型 | 改动点 |
| ---- | ---- | ---- | ---- |
| 打包元数据 | `pyproject.toml:6-19` `[project]` | 修改 | 补 `keywords`、各 Python 版本 classifier、`Topic :: Utilities`；`[project.urls]` 补 Changelog、Issues |
| PyPI 工作流 | 新文件 `.github/workflows/pypi.yml` | 新增 | 见 §5.1.1 |
| 演示素材生成 | 新文件 `scripts/make-assets.py` | 新增 | 生成 `docs/assets/demo.gif` 与 `docs/assets/social-preview.png`，见 §5.1.3 |
| 素材 | 新文件 `docs/assets/demo.gif`、`docs/assets/social-preview.png` | 新增 | 由脚本生成 |
| 源码包排除 | 新文件 `.gitattributes` | 新增 | `docs/assets export-ignore` |
| README | `README.md:1-3`、`README.zh-CN.md:1-3` 标题与语言切换行之后 | 修改 | 加徽章行；简介代码块之后加 GIF |
| README | `README.md`、`README.zh-CN.md` 的 Install 节与 More installation options 节 | 修改 | 加 `brew install jakoes-wu/tap/multi-codex`（PyPI 一行待首次上传后补） |
| CONTRIBUTING | `CONTRIBUTING.md` 的 Releasing 节 | 修改 | 补 PyPI 自动发布与 tap 配方更新步骤 |
| CHANGELOG | `CHANGELOG.md` `[Unreleased]` | 修改 | Added：PyPI 包、Homebrew 配方 |
| tap 仓库 | 新仓库 `jakoes-wu/homebrew-tap` 的 `Formula/multi-codex.rb` | 新增 | 见 §5.1.2；不在本仓库 |

## 5. 方案

### 5.1 实现要点

#### 5.1.1 PyPI 工作流 `pypi.yml`

- 触发：`release: types: [published]`，以及 `workflow_dispatch`（输入 `tag`，用于给已发布的 v0.7.0 补发）。
- `build` 作业：checkout 对应 tag；`python -m pip install build twine`；`python -m build`；`twine check --strict dist/*`；核对 wheel 文件名中的版本与 tag 一致（与 `release.yml:30-36` 的版本检查同一思路）；上传 `dist/` 为 artifact。
- `publish` 作业：`needs: build`，`environment: pypi`，`permissions: id-token: write`，下载 artifact 后用 `pypa/gh-action-pypi-publish@release/v1` 上传。不使用 API token。
- tag 只经环境变量引用，不在 `run` 中内联 `${{ }}`（沿用 `release.yml:19-20` 的做法）。

#### 5.1.2 Homebrew 配方

`Formula/multi-codex.rb`：

- `url` 指向 release 附件 `multi-codex-v<版本>.tar.gz`，`sha256` 取 release 的 `SHA256SUMS`。
- `depends_on "python@3.13"`；`libexec.install "src/multi_codex"`；`bin` 下写一个包装脚本：`PYTHONPATH=#{libexec}` 后执行 `python3.13 -m multi_codex "$@"`。
- `test do`：`assert_match version.to_s, shell_output("#{bin}/multi-codex --version")`。
- 本机验证：`brew tap-new` 一个本地临时 tap，放入配方后 `brew install --build-from-source`、`brew test`、`brew audit --strict --new`，最后卸载并删除临时 tap，避免与 `~/.local/bin/multi-codex` 并存。

#### 5.1.3 演示素材

`scripts/make-assets.py`（头部注释写清前置条件，支持 `-h`）：

1. 在临时目录中创建 HOME、假 `codex`、两个示例账号的假 `auth.json`（`work@example.com`、`me@example.com`），依次实际运行 `multi-codex add work --proxy 7901`、`add personal`、`list`，抓取真实输出。
2. 用 PIL 按终端样式逐帧绘制：命令逐字出现，输出整段出现；字体用 macOS 的 Menlo（找不到时报错退出，不回落到其它字体）。
3. 输出 `docs/assets/demo.gif`（≤ 1 MB）和 `docs/assets/social-preview.png`（1280×640，含项目名、一句话说明和一段 `list` 输出）。
4. 临时目录在结束时删除。

#### 5.1.4 README

- 徽章：Release（`img.shields.io/github/v/release/...`）、CI（工作流徽章）、Python 版本、License。PyPI 徽章与 `pipx install multi-codex` 一行等首次上传 PyPI 成功后再加（另起小 PR），避免 README 出现装不上的命令。
- GIF 放在简介代码块之后，下方一行小字说明是示例账号。
- 安装：首推命令仍为 `curl | sh`；补 Homebrew 一行（tap 仓库建好并在本机验证通过后才写入）。

### 5.2 接口变更

- 新增分发渠道：PyPI 包 `multi-codex`（入口点沿用 `pyproject.toml` 的 `multi-codex = "multi_codex.cli:main"`）、Homebrew 配方。
- 新增工作流 `pypi.yml`、环境 `pypi`。
- 命令行、配置文件、`install.sh` 均无变化。不涉及 `docs/reference/*`。

## 6. 备选方案与决策

- **用 API token 上传 PyPI**：否决。令牌长期有效、需要存成仓库 secret；Trusted Publishing 只在该工作流运行时临时签发凭据。
- **配方放进本仓库 `Formula/`**：否决。`brew tap` 约定仓库名为 `homebrew-<名>`，放在本仓库里用户需要写完整的 URL 才能 tap。
- **装 vhs 录真实终端**：否决。需要 ttyd、ffmpeg 等依赖；用真实输出逐帧绘制可复现、不依赖外部工具。

## 7. 影响分析

1. **release 源码包**：`.gitattributes` 的 `export-ignore` 只排除 `docs/assets/`，`release.yml` 的 `git archive` 仍包含 `src/`，版本检查不受影响；`install.sh` 从源码包中只读取 `src/multi_codex`。
2. **CI**：`ci.yml` 不变；新增的 `pypi.yml` 只在 release 或手动触发时运行。
3. **pyproject 元数据**：只增加元数据字段，不改构建后端、包发现与入口点；`pipx install git+…` 的行为不变。
4. **README**：图片使用绝对地址，GitHub 与 PyPI 都能显示；页内锚点不变。

## 8. 回归测试

1. 本机在干净 venv 中 `python -m build`，`twine check --strict dist/*` 通过；在新 venv 中安装 wheel，`multi-codex --version` 输出当前版本，`multi-codex -h` 正常。
2. `git archive HEAD | tar -t` 不含 `docs/assets/`，含 `src/multi_codex/__init__.py`。
3. Homebrew：本地临时 tap 中 `brew install`、`brew test` 通过，`brew audit --strict --new` 无错误；卸载后 `command -v multi-codex` 仍指向 `~/.local/bin`。
4. `scripts/make-assets.py -h` 退出码 0；运行后生成两张图，GIF ≤ 1 MB，PNG 为 1280×640。
5. 全量 `python3 -m unittest discover -s tests -t tests` 通过；PR 的 CI 通过。
6. 合并后手动触发 `pypi.yml`（tag v0.7.0）：用户配置 Trusted Publisher 前，`build` 作业通过、`publish` 作业失败属预期；配置后重跑应成功，`pipx install multi-codex` 可装。
7. 本次新增的仓库文本与提交信息按维护者约定的用词清单自查通过。

## 9. 日志 / 观测点

- `pypi.yml` 的 `build` 作业输出 `twine check` 结果与版本核对结果；`publish` 作业失败时日志会提示 Trusted Publisher 未配置。
- 本机验证结果记录在 PR 描述中。
