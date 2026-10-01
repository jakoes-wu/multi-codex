# 按账号打开 VS Code 与 Codex 桌面端（实验性，v0.5）

> 2026-10-01 注记：代码已落地（未提交，等待编译机验证与人工实机验证）。实现：`src/multi_codex/apps.py`；`cli.py` 的 `cmd_code`、`cmd_app`、`_account_exec_args`。§8 第 1–10 条的用例在 `tests/test_apps.py` 中。
>
> 2026-10-01 人工实机验证（本机 macOS，账号 jakoes.wu@icloud.com，以进程信息为证据）：
> - `code`：新的 VS Code 实例（`--user-data-dir .../.apps/<名>/vscode`）在 4 秒内由扩展宿主拉起 `codex app-server`，它的 `CODEX_HOME` 是该账号目录；这个 codex 二进制来自 `~/.vscode/extensions/openai.chatgpt-26.928.31416`，证明换了用户数据目录后扩展仍然可用（§1.1 中源码与官方文档的矛盾，以源码为准）。
> - `app`：新的桌面端实例（`ChatGPT --user-data-dir=.../.apps/<名>/desktop`）拉起的 `codex app-server` 的 `CODEX_HOME` 是该账号目录，说明合并登录 shell 环境后 `CODEX_HOME` 确实被恢复；单实例锁 `SingletonLock` 在该数据目录中；`desktop.log` 有输出，权限仍为 0600。
> - 补测“与原有实例同时存在”（2026-10-01）：icloud 实例运行时，用 `open -a /Applications/ChatGPT.app`（与点 Dock 图标相同）打开桌面端，**没有**启动默认实例，只是把 icloud 实例切到前台，因为 LaunchServices 把它们视为同一个应用。改用 `open -n -a` 后，默认实例（拉起的 codex 没有设置 `CODEX_HOME`，即使用 `~/.codex`）与 icloud 实例（codex 的 `CODEX_HOME` 为该账号目录）同时运行，各用各的目录。README 已补充这条提醒。
> - 界面核对：桌面端截图停在设置页，看不到账号；截取 VS Code 窗口时触发了 macOS 的屏幕录制权限提示，未继续。随后由用户亲自查看：VS Code 新窗口中 OpenAI 扩展显示的是 jakoes.wu@icloud.com，与进程环境一致；桌面端界面上的账号未核对，以进程环境为准。
>
> 本文其余部分保持方案原文。

## 1. 背景

账号用启动命令 `codex-<名>` 隔离，但 VS Code 中的 OpenAI 扩展和 Codex 桌面端只会使用它们启动时环境里的 `CODEX_HOME`；没有设置时就是 `~/.codex`。路线中的 U12（`~/.claude-shared/playbook/multi-codex-competitors.md` §5.3）要求：按账号打开独立的 VS Code 窗口和桌面端实例。

### 1.1 已核实的事实（本机 macOS，只读调查）

被核实的版本：VS Code 1.139.1、OpenAI 扩展 26.928.31416、Codex 桌面端 26.831.11858。

| 事实 | 依据 |
| ---- | ---- |
| OpenAI 扩展用 `{...process.env, ...代理设置, PATH, RUST_LOG, ...}` 作为环境启动自带的 `codex app-server`，`CODEX_HOME` 原样传下去；只有在没有 `CODEX_HOME` 且处于远程窗口（不含 WSL）时才会改写，从不删除 | 扩展 `out/extension.js`：`u={...process.env,...n,PATH:a,RUST_LOG:"warn",...}`（已亲自核对原文） |
| 扩展没有指定 `CODEX_HOME` 的设置项 | 扩展 `package.json` 的 `contributes.configuration` |
| VS Code 的主 IPC 套接字由用户数据目录推出，不同的 `--user-data-dir` 一定开出新实例；相同时请求转给已运行的实例 | VS Code `main.js` 的 `mainIPCHandle`；官方文档 `docs/configure/command-line.md` 第 145–159 行：“To run VS Code instances with separate environment variables, use the `--user-data-dir` option” |
| 从命令行启动时，VS Code 不读取登录 shell 的环境，扩展宿主得到的是启动时的环境 | `main.js`：`resolveShellEnv(): skipped (VSCODE_CLI is set)` |
| 扩展目录默认是 `~/.vscode/extensions`，与用户数据目录无关；但官方文档说换了用户数据目录要重装扩展 | `main.js` 的 `extensionsPath`；文档第 170 行。**两者矛盾，实际表现未实测** |
| macOS 上 `code` 命令通过 `open -n --env K=V ...` 拉起应用，把**全部**环境变量放进 `open` 的命令行参数 | VS Code `cli.js`：`for(let p in n)... c.push("--env"),c.push(\`${p}=${n[p]}\`)` |
| 桌面端是 `/Applications/ChatGPT.app`，bundle id `com.openai.codex`，改名后的 Electron 应用，自带 `Resources/codex` | `Info.plist`；`Contents/Frameworks/Codex Framework.framework` |
| 桌面端启动后会把登录 shell 的环境合并进 `process.env`，可能覆盖传入的 `CODEX_HOME`；只有设置了 `CODEX_ELECTRON_USER_DATA_PATH` 时才把 `CODEX_HOME` 恢复成启动时的值 | `app.asar`：`Mb=process.env.CODEX_ELECTRON_USER_DATA_PATH?.trim()?process.env.CODEX_HOME:void 0`（已亲自核对原文） |
| 桌面端的用户数据目录取自 `CODEX_ELECTRON_USER_DATA_PATH`，只有设置了它，正式包才申请单实例锁（锁按用户数据目录区分） | `app.asar` 中的 `ee(...)` 与 `wD({isMacOS,isPackaged,hasExplicitUserDataPath})` |
| 桌面端启动后用 `Object.assign(process.env, userEnv)` 把登录 shell 的环境整体合并进来，之后只恢复 `CODEX_HOME`；所以代理变量和同名的其它变量都以登录 shell 的值为准 | `app.asar` 偏移约 3777881：`Object.assign(process.env,s.userEnv),Mb!=null&&(process.env.CODEX_HOME=Mb)` |
| 桌面端内部的 demo 启动器同时传 `CODEX_HOME`、`CODEX_ELECTRON_USER_DATA_PATH` 和 `--user-data-dir` | `app.asar`：`` [`-n`,`--env`,`CODEX_HOME=`+e,`--env`,`CODEX_ELECTRON_USER_DATA_PATH=`+t,...,`--args`,`--user-data-dir=`+t] ``（已亲自核对原文） |
| 竞品 Ducksss/codex-profiles 同样同时传这两个变量；open-profile-manager 只传 `CODEX_HOME` 和 `--user-data-dir` | Ducksss `bin/codex-profile:1620-1669`；opm `Sources/ProfileCore/ProcessLaunch.swift:192-212` |

## 2. 目标 / 非目标

**目标**

1. `multi-codex code NAME [PATH] [-- CODE_ARGS ...]`：以该账号的环境（与 `run` 相同）打开一个独立的 VS Code 实例，用户数据目录按账号区分。
2. `multi-codex app NAME`（仅 macOS）：打开一个独立的 Codex 桌面端实例，使用该账号的 `CODEX_HOME`。

**非目标**

- 不复制或同步 VS Code 的设置、快捷键：每个账号的实例使用自己的用户数据目录，第一次打开时是默认设置。
- 不管理这些实例的生命周期（不关闭、不重启），也不检查实例是否已在运行。
- 桌面端不使用账号的代理设置和账号环境变量：桌面端会用登录 shell 的环境覆盖它们（§1.1），传了也无效；而且通过 `open --env` 传入时，值会出现在 `open` 的命令行参数中。
- 不支持 Linux 上的桌面端（官方没有 Linux 版）；Linux 上的 `code` 只按文档推断可用，未实测。
- 不支持远程窗口（Remote-SSH 等）：扩展在远程窗口中会自行决定 `CODEX_HOME`。

## 3. 假设与约束

- `code` 的环境计算复用 `run` 的机制：`launcher.render(..., command_mode=True)` 生成脚本，通过环境变量 `MULTI_CODEX_RUN_SCRIPT` 交给 `/bin/sh`（`docs/feature/feature-cli-ergonomics.md` §5.1.2）。VS Code 从命令行启动时不读取登录 shell 的环境（§1.1），所以代理的 `inherit` / `off` 与账号环境变量在 VS Code 中的语义与启动命令一致。
- `app` 通过 LaunchServices（`open -n`）启动，与用户从 Finder 打开时相同，隐私权限（TCC）、深链（`codex://`）的归属都按应用本身处理。只传两个不含密钥的变量：`CODEX_HOME`、`CODEX_ELECTRON_USER_DATA_PATH`。
- 每个账号的 GUI 数据目录放在 `<root>/.apps/<名>/`：`vscode/` 为 VS Code 用户数据目录，`desktop/` 为桌面端用户数据目录。账号名必须以字母或数字开头（`config.py:20` 的 `NAME_PATTERN`），所以 `.apps` 不会和任何账号目录重名。目录在第一次使用时以 0700 权限创建。`remove` 不删除这些目录，与账号目录一样保留。
- 两个命令都是实验功能：每次运行都在 stderr 打印一行，写明依赖未公开的行为，以及被验证过的版本。

## 4. 涉及模块

| 区域 | 行号锚点（基线 `8a4e559`） | 改动类型 | 改动点 |
| ---- | ---- | ---- | ---- |
| 新模块 | 新文件 `src/multi_codex/apps.py` | 新增 | `gui_data_dir(config, name, kind)`、`find_code(bin_override)`、`desktop_app_info(path)`，见 §5.1 |
| 命令行 | `src/multi_codex/cli.py:104` 附近 | 新增 | 子命令 `code NAME [PATH] [--bin CODE]`（`--` 之后的参数原样交给 VS Code）、`app NAME [--app PATH]` |
| 命令行 | `src/multi_codex/cli.py:154-163` `_split_run_command` | 修改 | 对 `code` 子命令同样在第一个 `--` 处分割 argv |
| 命令行 | `src/multi_codex/cli.py:185-190` | 修改 | `code`、`app` 与 `run` 一样，在读取迁移事务记录之前分派（不加锁） |
| 命令行 | `src/multi_codex/cli.py:546-565` `cmd_run` | 修改 | 把“生成脚本与环境”拆成 `_account_exec_args(config, account, command)`，返回 `(argv, env)`，`run` 与 `code` 共用；`run` 的 argv（`["sh", "-c", ...]`）与环境保持不变 |
| 补全 | `src/multi_codex/completion.py:16` `ACCOUNT_COMMANDS` | 修改 | 加入 `code`、`app` |
| 补全 | `src/multi_codex/completion.py:154-168`（zsh） | 修改 | 候选为空时调用 `_files`，与 bash 的 `-o default` 一致（`code NAME <TAB>` 能补路径） |
| 测试 | 新文件 `tests/test_apps.py` | 新增 | 见 §8 |
| 文档 | `README.md`、`README.zh-CN.md`：命令表，新增 “VS Code and the desktop app (experimental)” 一节；`CHANGELOG.md`；`docs/feature/feature-account-manager.md` 命令表 | 修改 | |

## 5. 方案

### 5.1 实现要点

#### 5.1.1 `code NAME [PATH] [-- CODE_ARGS ...]`

1. 账号必须已登记，账号目录必须存在，否则退出码 1。
2. 找 VS Code 命令：`--bin` 指定的路径，否则 `shutil.which("code")`。找不到，或者不是可执行文件，都返回退出码 1，并提示安装 `code` 命令（VS Code 中的 “Shell Command: Install 'code' command in PATH”）或用 `--bin` 指定。
3. 用户数据目录 D = `<root>/.apps/<名>/vscode`，不存在时创建（0700）。macOS 上，VS Code 的主套接字是 `D/<版本>-main.sock`；`D` 的长度超过 80 个字符时，在 stderr 警告：套接字路径可能超过系统 104 字节的上限。
4. 以账号环境运行 `<code> --user-data-dir D [PATH] [CODE_ARGS ...]`。与 `run` 一样经 `os.execve` 替换当前进程（`code` 命令本身很快退出，VS Code 窗口在后台运行），退出码就是 `code` 的退出码。
5. stderr 输出实验提示。在 macOS 上再加一行：VS Code 的 `code` 命令会通过 `open --env` 传递当前的全部环境变量，包括账号环境变量，它们在 `open` 运行的一瞬间会出现在进程命令行中（§1.1 的 `cli.js`）。

#### 5.1.2 `app NAME`（仅 macOS）

1. 非 macOS：退出码 1，提示只支持 macOS。
2. 账号必须已登记，账号目录必须存在，否则退出码 1。
3. 应用路径：`--app` 指定，否则 `/Applications/ChatGPT.app`。用标准库 `plistlib` 读取 `Contents/Info.plist`，`CFBundleIdentifier` 必须是 `com.openai.codex`，否则退出码 1，避免误开 ChatGPT Classic 等其它应用。
4. 用户数据目录 D = `<root>/.apps/<名>/desktop`，不存在时创建（0700）。日志 L = `<root>/.apps/<名>/desktop.log`，不存在时创建（0600）。
5. 执行 `open -n --env CODEX_HOME=<账号目录> --env CODEX_ELECTRON_USER_DATA_PATH=D --stdout L --stderr L -a <应用路径> --args --user-data-dir=D`。`open` 固定使用 `/usr/bin/open`（不从 PATH 查找，避免同名命令被误用）；测试用专用环境变量 `MULTI_CODEX_TEST_OPEN` 替换成假命令，沿用现有 `MULTI_CODEX_TEST_*` 的命名。
   - `CODEX_ELECTRON_USER_DATA_PATH` 让桌面端使用 D 作为用户数据目录、按 D 申请单实例锁，并在合并登录 shell 环境之后把 `CODEX_HOME` 恢复为账号目录（§1.1）；
   - `--user-data-dir` 与桌面端自己的 demo 启动器、两个竞品的写法一致。
6. `open` 退出码不为 0 时，`app` 返回 1，并显示 `open` 的 stderr。否则输出 `started Codex desktop for NAME (log <L>)`，返回 0。`open` 只负责交给 LaunchServices，应用随后的启动失败要看 L。
7. 同一个账号已有桌面端实例在运行时，桌面端自己的单实例锁会处理（行为由桌面端决定，未实测）。

### 5.2 接口变更

| 类型 | 接口 | 兼容性 |
| ---- | ---- | ---- |
| 新增 | `code NAME [PATH] [--bin CODE] [-- CODE_ARGS ...]` | 新增，实验性 |
| 新增 | `app NAME [--app PATH]`（仅 macOS） | 新增，实验性 |
| 新增 | 目录 `<root>/.apps/<名>/{vscode,desktop}`、日志 `desktop.log` | 新增 |

仓库中没有 `docs/reference/` 目录，不涉及 reference 章节的同步检查。

## 6. 备选方案与决策

| 问题 | 方案 | 结论 |
| ---- | ---- | ---- |
| 桌面端怎么启动 | 直接执行包内的主程序，环境变量经 `MULTI_CODEX_RUN_SCRIPT` 传入 | 否决（评审第 1 轮）：不经 LaunchServices，隐私权限（TCC）会归到发起的终端名下，`codex://` 深链交给哪个实例也不确定；而且桌面端会用登录 shell 的环境覆盖代理变量，按账号传代理本来就无效 |
| 桌面端怎么启动 | `open -n`，只传 `CODEX_HOME` 和 `CODEX_ELECTRON_USER_DATA_PATH` | 采纳 |
| 桌面端只传 `--user-data-dir`（opm 的做法） | | 否决：按 §1.1，桌面端会把用户数据目录改回默认，`CODEX_HOME` 也可能被登录 shell 的值覆盖 |
| VS Code 直接执行包内主程序 | | 否决：各平台路径不同，`code` 命令是官方支持的入口 |
| GUI 数据目录放在账号目录里（Ducksss 的 `electron-user-data`） | | 否决：会混进 `CODEX_HOME`，影响 README“哪些可以共享”的说明，也会让账号目录的体积大幅增加 |

## 7. 影响分析

- **`run`**：环境计算拆成共用函数，`run` 的 argv 与环境与 v0.4.0 相同（`tests/test_ergonomics.py` 中现有的 `RunPathTest` 回归）。
- **账号目录**：不写入任何新文件；GUI 数据都在 `<root>/.apps/` 下。
- **`doctor` / `list`**：不受影响。`<root>/.apps` 不是账号目录，`plan` 只遍历已登记的账号（`accounts.py:61`），不会把它当成孤儿。
- **用户的 VS Code 与桌面端**：只新开实例，不修改已有实例和它们的数据目录。
- **桌面端的代理**：桌面端使用登录 shell 的代理设置，与账号的 `proxy` 设置无关。README 写明这一点。
- **并行登录**：桌面端的登录回调使用本机固定端口（`app.asar` 中的 `[1455,1457]`），两个实例同时进行登录时可能冲突。README 建议一次只在一个实例中登录。
- **兼容风险**：两者依赖未公开的行为，VS Code 或桌面端升级后可能失效。实验提示中写明被验证过的版本。

## 8. 回归测试

新文件 `tests/test_apps.py`。测试中用假的 `code`、假的 `open` 和假的应用包（目录结构加 Info.plist），不启动真实应用：

1. `code work /p`：假 `code` 收到 `--user-data-dir <root>/.apps/work/vscode /p`，看到的 `CODEX_HOME`、代理变量与 `run work` 相同；数据目录权限为 0700。
2. `code work /p -- --new-window`：假 `code` 收到 `--new-window`。
3. 找不到 `code`：退出码 1；`--bin` 指向不存在或不可执行的文件：退出码 1；指定假 `code` 后成功。
4. 在 macOS 上，stderr 中有 `open --env` 的提示（只在 macOS 上断言）。
5. `app work --app <假应用>`（只在 macOS 上运行，其它平台跳过）：
   - 假 `open` 收到 `-n`、`--env CODEX_HOME=<work 的目录>`、`--env CODEX_ELECTRON_USER_DATA_PATH=<数据目录>`、`-a <假应用>`、`--args --user-data-dir=<数据目录>`；
   - 参数中不包含账号环境变量的值；
   - 日志文件权限为 0600，数据目录为 0700；退出码 0。
6. 假应用的 bundle id 不是 `com.openai.codex`：退出码 1；假 `open` 返回非 0：退出码 1；账号目录不存在：退出码 1，且没有调用 `open`。
7. 在 Linux 上执行 `app`：退出码 1（只在 Linux 上运行，CI 的 Linux 任务覆盖）。
8. `code ghost`、`app ghost`：退出码 1。
9. 补全中出现 `code`、`app`；zsh 下 `code work <TAB>` 退回文件名补全（通过脚本内容断言 `_files` 的调用条件）。
10. `run` 的现有用例全部通过；现有用例全部通过；编译机 Python 3.8 运行全部用例；CI 通过。
11. **人工实机项**（本机，执行前征得用户同意，会启动用户的 VS Code 和桌面端）：
    - `multi-codex code <账号>`：新窗口中 OpenAI 扩展显示的是该账号；扩展是否仍在（§1.1 中源码与文档矛盾）；关闭发起命令的终端后窗口仍然存在；
    - `multi-codex app <账号>`：新实例登录的是该账号，与原来的实例同时存在；日志确实写入 `desktop.log`，文件权限仍为 0600；
    - 结果写回本文档的注记。

## 9. 日志 / 观测点

- stderr：`[multi-codex] experimental: ... (verified with VS Code 1.139.1 / extension 26.928.31416 / Codex desktop 26.831.11858)`。
- `app`：stdout 输出 `started Codex desktop for NAME (log <路径>)`；桌面端自己的输出在 `<root>/.apps/<名>/desktop.log`。
- 失败：`[multi-codex] error: ...`，退出码 1。
