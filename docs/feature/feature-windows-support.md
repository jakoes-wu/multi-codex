# 原生 Windows 支持

> 2026-11-03 注记：方案起草中，代码待落地。基线 main `44cb4c1`（v0.13.0）。本文所有 `file:line` 锚点均指该基线。
>
> 2026-11-03 第 1 轮评审修订（D1–D18）；第 2 轮评审修订（D19–D28）；第 3 轮评审修订（D29–D39）；第 4 轮评审修订（D40–D48）；第 5 轮评审修订（D49–D55）。

## 1. 背景

multi-codex 目前只在 macOS 与 Linux 上运行，启动时由平台闸门直接拒绝其它平台：

- `src/multi_codex/cli.py:520-522`：`platform.is_supported()` 为假时报 `this platform is not supported yet (macOS and Linux only)` 并返回 1；
- `src/multi_codex/platform.py:20-21`：`is_supported()` 只认 `darwin` 与 `linux`。

依赖 POSIX 的点集中在以下几类（逐项见 §4）：

- 启动命令是 `#!/bin/sh` 脚本（`launcher.py:54-100`、`router.py:41-103`），`run` / `code` 用 `os.execve("/bin/sh")`（`cli.py:1381-1420`）；
- 共享与迁移用 `os.symlink`（`platform.py:52-54`、`fsutil.py:215`、`shared.py:115-131`）；
- 文件锁用 `fcntl.flock`（`lock.py:8`、`:32`、`:45`）；
- 占用检查用 `lsof` 或 `/proc`（`platform.py:85-185`）；
- `usage --live` 用进程组与 `os.killpg`（`usage.py:242-243`、`:340-355`）；
- 系统级配置路径写死 `/etc/codex/config.toml`（`identity.py:28`）。

同一作者的 multi-claude 已合并原生 Windows 支持（multi-claude 仓库 `docs/feature/feature-windows-support.md`，PR #56，提交 `beda802`）。multi-codex 的模块划分与它几乎一一对应，本方案沿用其做法，只记录两者的差异与 multi-codex 特有的点。

`docs/feature/feature-account-manager.md:619-677` §11 记有一份早期的 Windows 二期设想（install.ps1、tasklist、`.cmd` 里写死 `set` 变量等），本方案取代它。

用户已拍板（2026-11-03）：

1. Windows 只支持 pipx 安装，不写 install.ps1；
2. `migrate-default` / `use` / `restore` 在 Windows 上一起支持；只要有任何 Codex 进程在运行就拒绝。

## 2. 目标 / 非目标

**目标**

1. 在原生 Windows（Python 3.8+，pipx 安装，PowerShell 或 cmd）上，`add` / `list` / `login` / `run` / `code` / `codex-<名>` 启动命令 / `codex-auto` / `bind` / `share` / `usage`（含 `--live`）/ `sessions` / `doctor` / `completion` 可用，行为与 macOS·Linux 一致；
2. `migrate-default` / `use` / `restore` 在 Windows 上可用，有 Codex 进程运行时拒绝；
3. macOS·Linux 上所有输出字符串、生成文件与行为逐字节不变；CI 增加 Windows 行。

**非目标**

- 不支持 WSL 以外的 Cygwin / MSYS2 / Git Bash 作为运行环境（它们下 `sys.platform` 为 `cygwin` / `msys` 或仍是 `win32`；`win32` 时按原生 Windows 处理，不专门适配它们的 shell）；
- 不写 install.ps1，不做 winget / scoop 包；
- `app` 子命令在 Windows 上保持“仅 macOS”（`cli.py:1468` 现状不变）；
- 不改 Codex 自身行为，不处理 Codex 在 Windows 上的已知问题。

## 3. 假设与约束

- **判断平台**：`IS_WINDOWS = sys.platform == "win32"`，放在 `platform.py`，其它模块从这里引用，不各自判断。
- **Codex 在 Windows 上的进程形态**（已静态核实）：npm 版 `@openai/codex@0.166.0` 为 `codex.cmd`（由 `cmd.exe` 解释）→ `node.exe <npm 全局目录>\node_modules\@openai\codex\bin\codex.js` → `vendor\<triple>\bin\codex.exe`（`$(npm root -g)/@openai/codex/bin/codex.js:89-93`、`:236-249`：Windows 上不 execve，而是 spawn 子进程；`:285-290` node 等子进程退出后才以其退出码退出）。
- **直接用 node 运行 codex.js 与经 codex.cmd 等价**（已静态核实）：codex.js 定位 codex.exe 只依赖自身位置——`:13` `__dirname`、`:15` 包根、`:81-86` 先 `require.resolve` 平台包、否则 `__dirname/../vendor`；`:176` 的包管理器识别看 `process.argv[1]` 的上级目录与包根，`:226-233` 的环境只来自 `process.env`。所以 `node <同一个 codex.js> <参数>` 与 `codex.cmd <参数>` 得到同一个 codex.exe、同一份环境。codex.cmd 优先用同目录下的 `node.exe`、否则用 PATH 上的 `node`，是 npm cmd-shim 的固定写法，本机无 Windows 版 shim 可读，列为实机项 R9。
- **钥匙串模式在 Windows 上同样受 junction 影响**（已静态核实）：凭据键为 `cli|sha256(canonicalize(codex_home))[:16]`（上游 `codex-rs/login/src/auth/storage.rs:310-321`）；Rust 的 `canonicalize` 在 Windows 上会解析 junction 并返回 `\\?\` 前缀路径，迁移前后键不同，登录会丢。沿用 `migrate.py:236-269` 的预检拒绝，不新增逻辑。
- **系统级配置路径**（已静态核实）：Windows 为 `%ProgramData%\OpenAI\Codex\config.toml`（上游 `f2b94ef` 的 `codex-rs/config/src/loader/mod.rs:84` 默认 `C:\ProgramData`、`:808-818` `windows_codex_system_dir`、`:836-840` `windows_system_config_toml_file`：取 `FOLDERID_ProgramData`，失败时用 `C:\ProgramData`）。本工具用环境变量 `ProgramData` 近似 `FOLDERID_ProgramData`（两者在默认安装下相同，被组策略重定向时可能不同）；上游另有注册表策略 `HKLM\SOFTWARE\Policies\OpenAI\Codex`（`mod.rs:820-834` 读 `RequirementsPath`，指向 requirements.toml），它能否影响凭据存储方式列为开放问题（§10）。
- **链接**：目录用 junction（普通用户可建，无需开发者模式）；文件用符号链接，需要开发者模式或管理员。默认共享清单含文件 `AGENTS.md`（`config.py:18`），所以 Windows 上任何会新建文件链接的写命令都先预检能否建文件链接，不能就什么都不改直接退出（§5.1.9）。
- **CODEX_HOME 查找**：上游 `codex-rs/utils/home-dir/src/lib.rs:13-20` 在 Windows 上同样读环境变量 `CODEX_HOME`，未设时取 `%USERPROFILE%\.codex`。
- **默认路径**：`~/.codex`、`~/.cx`、`~/.local/bin` 在 Windows 上展开为 `%USERPROFILE%\...`（`platform.py:24-33` 已用 `expanduser`，不改）。`~/.local/bin` 正是 pipx 在 Windows 上的默认 bin 目录。
- 状态目录：Windows 用 `%APPDATA%\multi-codex`（与 multi-claude 一致），POSIX 保持 XDG（`platform.py:45-49`）。
- 不引入第三方依赖；Windows API 只经 `ctypes` / `_winapi` / `msvcrt`。

## 4. 涉及模块

新增文件：

| 区域 | 行号锚点（基线） | 改动类型 | 改动点 |
| ---- | ---- | ---- | ---- |
| `src/multi_codex/_entry.py` | 新文件 | 新增 | `.cmd` 启动命令的入口：追加包上级目录到 `sys.path` 末尾、校验包一致、`runpy.run_module` |
| `src/multi_codex/winapi.py` | 新文件 | 新增 | `ctypes` 封装：OpenProcess / GetExitCodeProcess、CommandLineToArgvW、GetShortPathNameW |
| `tests/test_windows.py` | 新文件 | 新增 | Windows 专属测试（CI Windows 行只跑它） |
| `.github/workflows/ci.yml` | 矩阵段 | 修改 | 增加 windows-latest × 3.8 / 3.12 |

修改既有文件：

| 区域 | 行号锚点（基线） | 改动类型 | 改动点 |
| ---- | ---- | ---- | ---- |
| `platform.py` 平台判断 | `:20-21` | 修改 | `is_supported` 加 `win32`；新增 `IS_WINDOWS` |
| `platform.py` 状态目录 | `:45-49` | 修改 | Windows 取 `%APPDATA%\multi-codex` |
| `platform.py` 链接 | `:52-54` | 修改 | Windows 目录用 junction、文件用 symlink；新增 `is_link` / `is_junction` / `remove_link` / `read_link`、`LinkPermissionError` |
| `platform.py` 占用检查 | `:85-185` | 修改 | 新增 Windows 分支 `_find_busy_windows`；POSIX 分支不动 |
| `platform.py` 新增工具 | `:54` 之后追加 | 新增 | `which`、`system_program`（含子目录表）、`is_sharing_violation`、`retry_on_sharing_violation`、`can_create_file_link`、`in_use_message`、`copy_link`、测试注入辅助 `test_hook_point`；注入点 `file-link-denied`、`busy-check-powershell`，以及 `MULTI_CODEX_TEST_SHARING_AT`、`MULTI_CODEX_TEST_PROCESS_LIST`（5.1.10） |
| `lock.py` | `:8`、`:30`、`:32`、`:45` | 修改 | `fcntl` 改为按平台导入；新增 `lock_fd` / `unlock_fd` |
| `launcher.py` 文件检查 | `:37-52` | 修改 | Windows 只比较内容，不查可执行位与权限位 |
| `launcher.py` 渲染 | `:100` 之后追加 | 新增 | `render_windows`、`cmd_unsafe_reason`、`account_env`、`resolve_command`（Windows 专用；POSIX 的 `render` `:54-100` / `_proxy_lines` `:102-124` 不动） |
| `launcher.py` 路径与标记 | `:17` 之后追加、`:29-30`、`:127-155` | 修改 | 新增 `WIN_MARKER_PREFIX`；Windows 启动命令名加 `.cmd`；`managed_account` 按扩展名选标记 |
| `router.py` | `:22` 之后追加、`:26-39`、`:41-103` | 修改 | 新增 `WIN_ROUTER_MARKER`；Windows 渲染 `codex-auto.cmd`；`is_router` 按扩展名选标记 |
| `accounts.py` | `:178-237`（含 `:181`、`:217`、`:230`）、`:249`、`:300-312` | 修改 | 计划 / 写入 / 删除启动命令按平台选文件名与内容；`_plan_launcher`（`:178-193`）、`_plan_router`（`:196-226`）、`launcher_status`（`:300-312`）在 Windows 上先调 `cmd_unsafe_reason` |
| `accounts.py` execute | `:252-275`（写配置 `save_config` 之前） | 新增 | Windows 文件链接预检 |
| `cli.py` 平台闸门与提示 | `:520-522` | 修改 | 提示文字改为包含 Windows 的版本（只在不支持的平台出现，见 §5.2） |
| `cli.py` main 入口 | `:502-505`（插在 `:504` 之前） | 新增 | 仅 Windows：第一个参数为 `launch` 时直接交给 `cmd_launch`，不进 argparse、不进补全；POSIX 不拦截 |
| `cli.py` 写命令拦截 | `:593-605` | 修改 | `_blocked_by_migration` 增加 use 中断记录检查 |
| `cli.py` run / code / login | `:1381-1459` | 修改 | Windows 分支：Python 构造环境 + `subprocess.call` |
| `cli.py` 续聊命令 | `:1574`、`:1693-1719` | 修改 | Windows 给 PowerShell 写法 |
| `cli.py` 共享条目名 | `:638` | 修改 | Windows 额外拒绝 `\` 与 `:` |
| `usage.py` | `:195`、`:219`、`:242-243`、`:304-332`、`:340-355` | 修改 | Windows：`CREATE_NEW_PROCESS_GROUP \| CREATE_NO_WINDOW` 启动；`close` 改调两个平台无关的结束动作；`:195`、`:219` 的 `islink` 改 `is_link`；新增 `from . import platform`（`:24` 之后）；注入点 `job-assign-failed` |
| `switch.py` use | `:92-145` | 修改 | Windows：写 use 中断记录 → `remove_link` → 建链接 → 删记录；注入点 `use-remove`、`use-unlinked`、`use-link` |
| `switch.py` 新增 | `:145` 之后追加 | 新增 | `use_journal_path` / `load_use_journal` / `recover_use_journal` |
| `switch.py` restore | `:233-296` | 修改 | `unlink` 改 `remove_link`；rename 经 `retry_on_sharing_violation`；处理 use 中断记录（5.1.8） |
| `migrate.py` | `:299-316`、`:445-470`、`:542`、`:558`、`:600`、`:633` | 修改 | rename 经重试包装；Windows 上共享冲突（`is_sharing_violation`）按占用处理返回 4，POSIX 不变；docstring 行号更新为 `:310-321` |
| `fsutil.py` | `:41-53`、`:79`、`:124`、`:137-141`、`:166-174`、`:209-216` | 修改 | `entry_kind` 把 junction 判为链接；`os.replace` 加重试；删除走 `remove_link`、复制链接走 `copy_link`；三处 `os.walk` 剪掉 junction；在函数体内导入 `platform`，避免循环导入 |
| `shared.py` | `:115-131` | 修改 | 建 / 换 / 删链接走 `platform` 辅助；新增 `file_links_needed` / `file_link_message` |
| `identity.py` | `:28`、`:66-67` | 修改 | Windows 系统级配置取 `%ProgramData%\OpenAI\Codex\config.toml` |
| `doctor.py` | `:67-68`、`:109-113`、`:186` | 修改 | `shutil.which` 改 `platform.which`；`islink` 改 `platform.is_link` |
| `apps.py` | `:54-58` | 修改 | `find_code` 用 `platform.which`；Windows 不要求 X_OK |
| `config.py` | `:46-49`、`:279`、`:330-337` | 修改 | Windows 上环境变量键不区分大小写地拒绝保留键；共享名额外拒绝 `\` |
| `shellpath.py` | `:12-52` | 修改 | Windows 给 PowerShell 写法与 `pipx ensurepath` |
| `completion.py` | `:19`、`:106`、`:167`、`:246` | 修改 | 新增 `powershell` |
| `pyproject.toml` | `:13-18` | 修改 | classifiers 加 `Operating System :: Microsoft :: Windows` |
| `tests/helpers.py` | `:21`、`:60-61`、`:96` | 修改 | 隔离 USERPROFILE / APPDATA / LOCALAPPDATA；Windows 上假 codex 用 Python 脚本并外包 `.cmd`，POSIX 保留原 sh 版 |
| `tests/data/posix-launchers-44cb4c1.json` | 新文件 | 新增 | §8.2-3 的期望文本：用基线 44cb4c1 生成的 POSIX `codex-<名称>` / `codex-auto` 内容，生成方法见 `tests/test_windows.py` 的 `RENDER_CASES` 注释 |
| `README.md` / `README.zh-CN.md` | 安装与限制节 | 修改 | 补 Windows 安装与限制（Ctrl+C 批处理提示、`.cmd` 目标的参数限制、文件链接需开发者模式、续聊命令需粘贴到 PowerShell 运行） |
| `tests/test_windows.py` | 新文件 | 新增 | §8.1 用例 |
| `.github/workflows/ci.yml` | 矩阵 | 修改 | 增加 windows-latest 行（5.1.10） |
| `docs/feature/feature-account-manager.md` | `:619-677` | 修改 | §11 改为一句指向本文 |
| `CHANGELOG.md` | 顶部 | 修改 | Unreleased 记一条 |

## 5. 方案

### 5.1 实现要点

#### 5.1.1 平台层 `platform.py`

- `IS_WINDOWS = sys.platform == "win32"`；`is_supported()` 返回 `sys.platform in ("darwin", "linux", "win32")`。
- `system_program(name)`：照搬 multi-claude `beda802` `platform.py:277-287` 的子目录表并追加 `taskkill.exe`（位于 System32 根下） `_SYSTEM_PROGRAMS = {"cmd.exe": "", "powershell.exe": os.path.join("WindowsPowerShell", "v1.0"), "taskkill.exe": ""}`，返回 `os.path.join(<SystemRoot>, "System32", _SYSTEM_PROGRAMS[name], name)`，`<SystemRoot>` 依次取 `SystemRoot`、`SYSTEMROOT` 环境变量，都没有时为 `C:\Windows`。Windows PowerShell 装在 `System32\WindowsPowerShell\v1.0\` 下，不在 `System32` 根下，直接拼 `System32\powershell.exe` 会找不到文件、占用检查每次失败。程序内部调用的 `cmd.exe`、`powershell.exe`、`taskkill.exe` 一律走它，不经 PATH 查找，防止当前目录或 PATH 里的同名程序被调用。
- `which(name, path=None)`：`path` 省略时取 `os.environ["PATH"]`；只按 `path` 各项与 PATHEXT 查找，不查当前目录（Python 3.8 的 `shutil.which` 在 Windows 上先查当前目录）。POSIX 上直接返回 `shutil.which(name, path=path)`，行为不变。
- 链接（只在 `IS_WINDOWS` 分支生效，POSIX 分支原样调用 `os.symlink` / `os.unlink` / `os.readlink` / `os.path.islink`）：
  - `create_link(target, link)`：target 是目录时建 junction（`_winapi.CreateJunction`；不存在时兜底 `system_program("cmd.exe") /c mklink /J`）；是文件时 `os.symlink`，捕获 `winerror == 1314` 抛 `LinkPermissionError`，提示“请在 Windows 设置 → 开发者选项里开启开发者模式，或用管理员身份运行”；
  - `is_junction(path)`：3.12 起用 `os.path.isjunction`，之前看 `os.lstat(path).st_reparse_tag == stat.IO_REPARSE_TAG_MOUNT_POINT`；
  - `is_link(path)`：`os.path.islink(path) or is_junction(path)`；
  - `remove_link(path)`：junction 或目录符号链接用 `os.rmdir`（只删链接本身，不碰目标目录里的内容），其它用 `os.unlink`；照搬 multi-claude `beda802` `platform.py:190-199`；
  - `read_link(path)`：`os.readlink` 后去掉 `\\?\` 前缀；
  - `copy_link(source, link)`：把链接原样复制（复制目录树用），照搬 multi-claude `beda802` `platform.py:155` 起：POSIX 为 `os.symlink(os.readlink(source), link)`（与基线 `fsutil.py:215` 相同）；Windows 上目标原文照抄（`read_link`，不按当前目录解释相对路径、不检查目标是否存在），源是 junction 就建 junction，否则建符号链接并保留源的目录 / 文件类型（目录符号链接仍建目录符号链接）。悬空链接照样建。文件符号链接无权限时抛 `LinkPermissionError`。
- `retry_on_sharing_violation(point, fn, *args)`：Windows 上捕获 `is_sharing_violation` 为真的 `PermissionError`，按 50 / 100 / 200 / 400 / 800 ms 退避重试，最后一次仍失败就抛出；POSIX 上直接调用一次。用于 `os.replace` / `os.rename`（杀毒软件、索引服务会短暂占用文件）。`point` 是测试注入点名（5.1.10 的 `MULTI_CODEX_TEST_SHARING_AT`），各调用点取值：`atomic-replace`（`fsutil.py:79`）、`migrate-move`（`migrate.py:466`）、`migrate-park`（`:542`）、`migrate-rollback`（`:600`）、`migrate-rollback-restore`（`:633`）、`restore-rename`（`switch.py:277`）。
- `can_create_file_link()`：POSIX 恒为真；Windows 上在临时目录里试建一个文件链接再删掉，`LinkPermissionError` 时为假（照搬 multi-claude `beda802` `platform.py:380-396`）；`test_hook_point("file-link-denied")` 为真时直接返回假（5.1.10）。
- `is_sharing_violation(exc)`：`IS_WINDOWS` 且 `getattr(exc, "winerror", None) in (5, 32)` 时为真（照搬 multi-claude `beda802` `platform.py:375-377`）；POSIX 上恒为假。`retry_on_sharing_violation` 与下文 5.1.8 的“改报占用”都只按它判断。
- `in_use_message(path, exc)`：winerror 用 `getattr(exc, "winerror", None)` 读取；改名遇占用时的提示，`<path> is in use by another program (winerror <n>); close all Codex sessions, IDEs and terminals opened in it, then retry`。
- 状态目录（`:45-49`）：Windows 返回 `os.path.join(os.environ["APPDATA"], "multi-codex")`，APPDATA 缺失时退回 `~\AppData\Roaming\multi-codex`。
- 占用检查（`:85-98` 的 `find_busy_processes`）：Windows 分支调用 `_find_busy_windows()`：
  - 运行 `system_program("powershell.exe") -NoProfile -NonInteractive -Command "Get-CimInstance Win32_Process | Select-Object ProcessId,Name,CommandLine,ExecutablePath | ConvertTo-Json -Compress"`，超时 90 秒，`creationflags=CREATE_NO_WINDOW`；命令前缀 `try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {};`（没有控制台时设置会失败，故包 try，照搬 multi-claude `platform.py:819-822`），输出按 `decode("utf-8", "replace")` 解码并去掉开头 BOM，`ConvertTo-Json` 只有一个进程时输出单个对象而非数组，解析后统一包成列表（照搬 multi-claude `beda802` `platform.py:819-844`）；失败或超时抛 `BusyCheckError`（与 POSIX 无 lsof 时同一通道，`migrate.py:299-316` 已处理）；
  - 判为占用的条件：`Name` 为 `codex.exe`（不区分大小写）；或 `Name` 为 `node.exe` 且命令行拆分（CommandLineToArgvW）后的任一参数含 `@openai\codex`（不区分大小写，同时接受 `/`）；
  - 不按路径判断：Windows 上进程打开的文件列表需要句柄枚举，代价与权限都不可控；用户已定“有任何 Codex 进程在运行就拒绝”，所以直接按进程判；
  - 跳过本进程 PID；返回与 POSIX 相同的结构（`pid`、`command`、`usage`、`path`），`usage` 固定为 `"running"`，`path` 为 `ExecutablePath` 或空串，使 `migrate.py:299-316` 的打印格式不变。
  - 测试注入（5.1.10）：设了 `MULTI_CODEX_TEST_PROCESS_LIST=<JSON 文件>` 时不运行 PowerShell，改读该文件（格式同上面 `ConvertTo-Json` 的输出）；`test_hook_point("busy-check-powershell")` 为真时按“PowerShell 非 0 退出”抛 `BusyCheckError`；两者同时设置时先判注入点。
  - 判活 `_pid_alive(pid)`（若有调用方需要）：OpenProcess(`PROCESS_QUERY_LIMITED_INFORMATION`) + GetExitCodeProcess == `STILL_ACTIVE`；**绝不**在 Windows 上调用 `os.kill(pid, 0)`（Windows 的 `os.kill` 会直接结束进程）。

#### 5.1.2 文件锁 `lock.py`

- `:8` 顶层 `import fcntl` 改为按平台导入（Windows 导入 `msvcrt`）。
- 新增 `lock_fd(fd)`（非阻塞，失败抛 `BlockingIOError`）/ `unlock_fd(fd)`：
  - POSIX：原样 `fcntl.flock(fd, LOCK_EX | LOCK_NB)` / `LOCK_UN`；
  - Windows：`msvcrt.locking` 锁的是“当前文件位置起的 N 字节”，所以加锁与解锁都先记下当前位置、`os.lseek(fd, LOCK_OFFSET, SEEK_SET)`、调用 `msvcrt.locking(fd, LK_NBLCK 或 LK_UNLCK, 1)`，在 `finally` 里 `lseek` 回原位置（参照 multi-claude `beda802` 的 `lock_fd`）。`LOCK_OFFSET = 0x7FFFFFFF`：锁住远在文件末尾之后的 1 字节，不影响读写 PID 内容。
- `:32` 的抢锁失败分支：Windows 上 `lock_fd` 已把位置恢复到 0，`os.read(fd, 64)` 读到的是 PID；为防将来改动，读之前仍显式 `os.lseek(fd, 0, SEEK_SET)`（POSIX 上无副作用）。
- `:30` 的 `os.open` 在 Windows 上加 `os.O_BINARY`。
- `:45` 改调 `unlock_fd`。`:37-39` 的 `ftruncate` / `lseek(0)` / `write` 不变：锁区在 `0x7FFFFFFF`，截断文件不影响它。

#### 5.1.3 启动命令 `launcher.py`

- POSIX 的 `render`（`:54-100`）与 `_proxy_lines`（`:102-124`）一字不改。
- 新增 `account_env(name, account_dir, proxy, env, base=None) -> dict`（仅 Windows 调用），逐条对应 `render` 脚本的语义：
  1. 从 `base`（默认 `os.environ`）复制；Windows 的 `os.environ` 键已是大写，复制后按大写键操作；
  2. `CODEX_HOME = account_dir`（对应 `:76-77`）；
  3. 删除 `ISOLATION_BREAKING_ENV`（`platform.py:17`：`CODEX_SQLITE_HOME`、`CODEX_API_KEY`、`CODEX_ACCESS_TOKEN`），对应 `:80`；
  4. 代理，对应 `_proxy_lines`：
     - `inherit`：不动（`:103-105`）；
     - `none`：删除 `HTTPS_PROXY`、`HTTP_PROXY`、`ALL_PROXY`、`NO_PROXY` 的所有大小写写法（`:106-108`）；
     - 指定代理：**先读出 `NO_PROXY` 原值**（Windows 上不区分大小写，取到的就是继承值；`base` 是普通 dict 且同时含多种大小写写法时以大写 `NO_PROXY` 为准，没有大写键时取按键名排序的第一个），再删除四个变量的所有大小写写法，然后写大写 `HTTPS_PROXY`、`HTTP_PROXY` = 代理；socks 代理写 `ALL_PROXY`，http 代理不写（`:115-119`）；`NO_PROXY` = 原值非空时 `原值,` + `_NO_PROXY_HOSTS`，否则只写 `_NO_PROXY_HOSTS`（`:121-123`）。Windows 进程环境不区分大小写，sh 版对小写 `no_proxy` 的单独追加在这里合并为一次；
  5. 最后应用账号 `env`（`:82-84`）：Windows 上键按大写去重——按 `sorted(env)` 顺序写入大写键，同一大写键出现多次时后写的覆盖先写的，与 sh 版按排序依次 export 的结果一致。
  - 正确性靠对照测试保证（§8.2 第 2 条）：在 POSIX 上用 `sh` 实跑 `render(..., command_mode=True)` 打印环境，与 `account_env` 在同一输入下的结果比较（比较时把 sh 结果的键转大写、合并 `no_proxy`）。
- 新增 `resolve_command(argv, env) -> list`（仅 Windows）：把要运行的命令解析成可直接交给 `CreateProcess` 的参数表：
  1. `full = platform.which(argv[0], path=env["PATH"])`，找不到返回 `None`（调用方报错，见 5.1.5）；
  2. `full` 是 `codex.cmd`（文件名不区分大小写）且 `<full 所在目录>\node_modules\@openai\codex\bin\codex.js` 存在时，返回 `[node, codex.js] + argv[1:]`；`node` 取 `<full 所在目录>\node.exe`（存在时），否则 `platform.which("node", path=env["PATH"])`。这样不经 `cmd.exe`，参数原样传给 node（`subprocess.list2cmdline` 只按 MSVC 规则引号，`node` 按同一规则拆分）；
  3. 找不到 node 或 codex.js 时返回 `[full] + argv[1:]`，由 Python 交给 `cmd.exe` 解释；此时参数里的 `& | > < ^ %` 可能被 cmd 改写或执行，写进 README 限制；
  4. 其它 `.cmd` / `.bat` 目标（如 `run <名> -- some.cmd`、`code` 找到的 `code.cmd`）同第 3 条，写进 README 限制。
- `render_windows(name, python, entry) -> str`（对应 multi-claude `beda802` `launcher.py:140`，本仓库不写账号目录行），内容（`\n` 拼接；`atomic_write` 以文本模式写，落盘为 CRLF，`read_text` 通用换行读回比较一致，见 `fsutil.py:64-86`、`:103-109`）：

  ```bat
  @echo off
  rem managed-by: multi-codex account=<名>
  "<python>" -I "<entry>" launch <名> -- %*
  exit /b %ERRORLEVEL%
  ```

  - `python`、`entry` 由新增的 `windows_command_paths()` 给出：原始值为 `sys.executable`（pipx venv 里的 python.exe）与 `<包目录>\_entry.py`（`<包目录>` 取 `os.path.dirname(multi_codex.__file__)`）；各自依次 ① 改写成 `%LOCALAPPDATA%` / `%APPDATA%` / `%USERPROFILE%` 开头的写法（取最长匹配，剩余部分须为 ASCII，照搬 beda802 `_cmd_path`）；② 仍含非 ASCII 时取 GetShortPathNameW 8.3 短名；
  - `.cmd` 不含 CODEX_HOME、代理、账号环境变量，所以不含密钥；账号设置改动后不必重写 `.cmd`，`launch` 每次运行时读配置；
  - `%*` 是 cmd 原样转发的命令行文本：用户在 cmd 里给 `codex-<名>` 传含 `& | > < ^ %` 的参数时，cmd 先解释一遍，写进 README 限制（PowerShell 调用时由 PowerShell 引号，不受影响的部分以实机为准，R2 一并记录）；
- 新增 `cmd_unsafe_reason(values, commands) -> Optional[str]`（照搬 beda802 `launcher.py:121`）：`values=[name]`（只出现在 `rem` 行）、`commands=[python, entry]`（cmd 真正执行的路径）。先去掉 `commands` 开头的 `%变量%`（`_without_var_prefix`），任一值含 `CMD_UNSAFE_CHARS`（`"`、`%`、`!`、换行）时返回 `path <值> contains <字符>, which cannot be written safely into a .cmd launcher`；`commands` 仍含非 ASCII 时返回 `path <值> has non-ASCII characters and no 8.3 short name, so cmd.exe cannot run it from a .cmd launcher; reinstall multi-codex with pipx under a path without them`（cmd 按控制台代码页读 `.cmd`，非 ASCII 会被读错）；否则返回 `None`。调用方（只在 Windows 上，生成内容之前调用）：
  - `_plan_launcher`（`accounts.py:178-193`，替换 `:181` 的 `render` 之前）：非 `None` 时返回 `Action(CONFLICT, "launcher", path, reason)`，`execute` 按现有冲突通道汇总，返回 3；
  - `_plan_router`（`:196-226`，`:217` `router.render` 之前）：非 `None` 时同样返回 `Action(CONFLICT, "launcher", path, reason)`；
  - `launcher_status`（`:300-312`，`:305-306` 的 missing 判断之后）：非 `None` 时返回 `"conflict"`，`list` / `doctor` 显示为冲突。因判断在 missing 之后，`.cmd` 缺失且路径不安全时 `list` 显示 missing、`apply` 返回 3；§8.1 对应用例按此断言，README 限制里写明“路径含 cmd 特殊字符时，补建启动命令会报冲突”。
- `launcher_path`（`:29-30`）：Windows 返回 `<bin_dir>\codex-<名>.cmd`。
- `managed_account`（`:127` 起，比较在 `:138-140`）：按扩展名选标记——`.cmd`（不区分大小写）认新增常量 `WIN_MARKER_PREFIX = "rem managed-by: multi-codex account="`（`:17` 之后追加），其它认 `MARKER_PREFIX`（`:17`）；POSIX 文件名没有扩展名，行为不变；`scan_managed`（`:143` 起）在 Windows 上只扫 `codex-*.cmd`。
- `launcher_file_ok`（`:37-52`）：Windows 上只比较内容（`read_text(path) == content`）。理由：Windows 上 `stat.S_IMODE(st_mode) & 0o077` 恒不为 0（Python 按只读属性合成权限位，组与其他用户位总是置上），不跳过的话有 env 的账号会被永远判成需要重写；`.cmd` 不含密钥，不需要权限位保护。
- `_entry.py`：
  1. `sys.path.append(os.path.dirname(<本文件目录>))`（追加到末尾，`-I` 已排除当前目录与用户 site）；
  2. `import multi_codex`，比较 `os.path.realpath(os.path.dirname(multi_codex.__file__))` 与本文件目录的 realpath，不同则报错退出 1（防止 PATH 上另一份 multi-codex 被加载）；
  3. `runpy.run_module("multi_codex", run_name="__main__", alter_sys=True)`。

#### 5.1.4 `codex-auto` `router.py`

- Windows 生成 `codex-auto.cmd`，完整内容（`python`、`entry` 与 5.1.3 相同，同样先经 `cmd_unsafe_reason`）：

  ```bat
  @echo off
  rem managed-by: multi-codex router
  "<python>" -I "<entry>" launch -- %*
  exit /b %ERRORLEVEL%
  ```

- 第 2 行是新增常量 `WIN_ROUTER_MARKER = "rem managed-by: multi-codex router"`（`router.py:22` 之后追加），即 `ROUTER_MARKER`（`:22`）把开头的 `# ` 换成 `rem `。与 `:19-21` 注释的约束相同，它不以 `WIN_MARKER_PREFIX` 开头，`managed_account` 对它返回 `None`，孤儿清理不会把它当账号启动命令删除。
- `is_router`（`:30-38`）按扩展名选比较方式：`.cmd`（不区分大小写）要求第 2 行完全等于 `WIN_ROUTER_MARKER`，其它沿用 `:38` 的 `ROUTER_MARKER` 比较；`router_path`（`:26` 起）Windows 返回 `codex-auto.cmd`。
- `launch` 不带名称时与 sh 版 `render`（`:41-103`）逐分支对照：

| 分支 | sh 版（基线） | Windows `launch` |
| ---- | ---- | ---- |
| 取当前目录 | `/bin/pwd -P`（`:55`） | `binding.normalize_dir(os.getcwd())`（`binding.py:33-47`） |
| 选绑定 | apply 时把绑定写死进脚本，最长前缀（`:60-80`） | 运行时读配置，`binding.resolve`（`binding.py:50-59`）取最近祖先 |
| 绑定指向未登记账号 | stderr `multi-codex: account <名> bound to <目录> is not registered; run: multi-codex unbind <目录>`，退出 1（`:64-67`、`:82-85`） | 同文案，退出 1 |
| 命中且账号启动命令缺失 | stderr `multi-codex: launcher <路径> is missing; run multi-codex apply`，退出 1（`:86-90`） | 不检查 `.cmd`：直接按账号环境运行（等同 `launch <名>`）。有意不同，见下 |
| 命中 | `exec <启动命令>` | 等同 `launch <名>` |
| 未命中 | stderr `multi-codex: no account is bound to <目录> or its parents; running codex (the default account)`（`:93-94`） | 同文案 |
| 未命中且无 codex | stderr `multi-codex: codex not found in PATH`，退出 127（`:95-98`） | 同文案，退出 127 |
| 未命中时的环境 | 原样继承（`:99`） | 原样继承，不设 CODEX_HOME |

- 有意的差异：sh 版在 apply 时把绑定写死，绑定改了要重新 apply；Windows 的 `.cmd` 内容固定，`launch` 每次读配置，所以绑定一改即生效，也不存在“账号启动命令缺失”这一种状态。`apply` 在 Windows 上仍会生成 / 更新 `codex-auto.cmd`（内容只和 python 路径有关）。

#### 5.1.5 隐藏子命令 `launch` 与 run / code / login（`cli.py`）

- `launch [<名>] -- <参数...>`：只在 Windows 上存在。`main`（`cli.py:502`）开头、`:504` 的 `_split_run_command` 之前插入 `if platform.IS_WINDOWS and argv[:1] == ["launch"]: return cmd_launch(argv[1:])`（照搬 multi-claude `beda802` `cli.py:605-623`）。不经 argparse，用户参数里的 `-h`、`--version` 不会被当成本工具的选项；不注册子解析器，`completion.py:25-29` 遍历子解析器生成补全时自然不含它，帮助里也没有。`cmd_launch` 自己解析参数：第一个参数不是 `--` 时为名称，接下来必须是 `--`，否则 stderr `multi-codex: usage: multi-codex launch [NAME] -- ARGS...`，退出 2。POSIX 上不拦截，`multi-codex launch` 照旧经 `:508` `_unknown_command` 报未知命令。无名称 → 5.1.4 的路由；有名称 → 下面的账号分支。
- 有名称时与 sh 版启动命令（`launcher.py:54-100`）逐分支对照：
  - 账号未登记：与 `cmd_run` 的现有报错相同，退出 1；
  - 账号目录不存在：stderr `multi-codex: account directory does not exist: <目录>`，退出 1（对应 `:70-75`；`run` 的 command_mode 同样先查）；
  - 找不到 codex：stderr `multi-codex: codex not found in PATH`，退出 127（对应 `:92-96`）；
  - 以上文案用 `sys.stderr.write` 直接输出，与 sh 版逐字一致，不经 `error()` 的格式。
- Windows 上 `run` / `code` / `login` / `launch` 不经 `/bin/sh`：
  1. `env = launcher.account_env(...)`；
  2. `argv = launcher.resolve_command(command, env)`；`None` 时：命令是 `codex` 报上面的 `codex not found in PATH`，其它命令报 `multi-codex: <命令> not found in PATH`，都退出 127；
  3. 忽略 SIGINT（Ctrl+C 由子进程自己处理），`return subprocess.call(argv, env=env)`。
  - 密钥只在环境里，不出现在命令行，与 `cli.py:1381-1391` 的约束一致。
- POSIX 分支（`:1381-1420`、`:1436-1459`）一字不改，含 `:1449` 的 darwin 分支。
- `code`：Windows 上 `apps.find_code()` 找到的通常是 `code.cmd`，经 `resolve_command` 第 3、4 条交给 cmd，参数限制写进 README。
- `app`（`:1462-1500`）：保持 `:1468` 的“仅 macOS”报错。

#### 5.1.6 续聊与默认启动命令（`cli.py:1574`、`:1693-1719`）

- `_resume_command`（`cli.py:1693` 起，拼接在 `:1698` 之后）整体改为按平台生成，不只是替换 `:1574` 的常量：POSIX 分支原样保留，Windows 分支生成下面的 PowerShell 写法。续聊命令只按 PowerShell 写法给出，README 限制写明“需粘贴到 PowerShell 运行”。
- 已登记账号：Windows 上 `_resume_command` 输出 PowerShell 写法 `Set-Location '<目录>'; codex-<名> resume <id>`；单引号内的 `'` 写成 `''`。`Set-Location` 改变当前会话目录，与 POSIX 版 `cd ... &&` 一致。
- 未登记的默认目录：要与 `cli.py:1572-1575` 对等，清除 `CODEX_HOME` 与 `ISOLATION_BREAKING_ENV` 三个变量，且只影响子进程、不改用户当前 PowerShell 会话。PowerShell 的 `$env:X=$null` 会改当前会话，不用；改为让 cmd 在它自己的进程里清变量：

  ```powershell
  Set-Location '<目录>'; cmd /d /c 'set CODEX_HOME=& set CODEX_SQLITE_HOME=& set CODEX_API_KEY=& set CODEX_ACCESS_TOKEN=& codex resume <id>'
  ```

  会话目录未知时（`cli.py:1699-1700` 只返回后半段），已登记账号输出 `codex-<名> resume <id>`，默认目录输出去掉 `Set-Location '<目录>'; ` 前缀的 `cmd /d /c '...'` 一段。

  - `set X=&` 在 cmd 里删除变量 X，只作用于这个 cmd 进程及其子进程；整串不含双引号，避开 Windows PowerShell 5.1 向外部程序传带引号参数的已知问题；`<id>` 是 UUID，变量名固定，没有需要转义的字符；
  - 变量名从 `ISOLATION_BREAKING_ENV` 生成，与 `_DEFAULT_RESUME_LAUNCHER` 同源；
  - 实机复核为 R10。
- `_tilde_path`（`:1705-1719`）在 Windows 上不缩写成 `~`（PowerShell 的 `~` 只在部分 cmdlet 里展开），输出完整路径。

#### 5.1.7 实时额度 `usage.py`

- 选择：**不改 `live_snapshot` 的参数，`cli.py:1339-1340` 不动**。Windows 上 `_AppServer` 仍运行该账号的启动命令 `codex-<名>.cmd app-server`：`.cmd` 经 `launch` 走 5.1.5 的账号环境与 `resolve_command`，与 POSIX “走启动命令沿用 CODEX_HOME 和代理”（`usage.py:371` docstring）同一思路；参数只有固定的 `app-server`，经 cmd 不会被改写。找不到 codex 时 `launch` 以 127 退出，stdout 关闭，走现有的“app-server 提前退出”错误路径；启动命令文件缺失时 `Popen` 抛 `OSError`，走 `:379` 的 `LiveError`。理由：最小改动——改参数要同时改 `cli.py:1339` 与 `live_snapshot`，并在 usage 里重复一遍账号环境构造。
- `:242-243`：Windows 上 `Popen([launcher, "app-server"], ..., creationflags=CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW)`；POSIX 原样 `start_new_session=True`。
- 进程树：`cmd.exe`（解释 `.cmd`）→ pipx venv 的 `Scripts\python.exe`（venv 转发器，再拉起基础解释器的 `python.exe`）→ `python.exe`（launch）→ `node.exe`（codex.js）→ `codex.exe`。`resolve_command` 退回 `codex.cmd` 时再多一层 `cmd.exe`。
- Job Object 兜底（`winapi.py` 新增）：`Popen` 返回后立即 `CreateJobObjectW` + `SetInformationJobObject`（`JOBOBJECT_EXTENDED_LIMIT_INFORMATION`，`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`）+ `AssignProcessToJobObject(job, proc._handle)`，之后起的全部后代自动进入同一 job；`close` 末尾（`finally` 内）`CloseHandle(job)`，系统结束 job 内所有仍在的进程。
  - `Popen` 与 `Assign` 之间的极短窗口里起的孙进程不在 job 内，可能漏；由 app-server 自带的 45 秒看门狗兜底（`usage.py:230-234` docstring）；
  - `Assign` 失败（例如父进程已在不允许嵌套的 job 中，Windows 8 之前）时降级为只用 `taskkill`，记录一条 DEBUG 日志 `job object unavailable: winerror=<n>`，不报错。
  - `taskkill /T /F` 仍作为先行的结束动作保留（下文 `_terminate_group` / `_kill_group`）。
- `close`（`:304-332`）当前直接引用 `signal.SIGTERM`（`:314`）与 `signal.SIGKILL`（`:320`），Windows 的 `signal` 模块没有 `SIGKILL`，执行到即 `AttributeError`。改为调用两个平台无关的动作：
  - `_terminate_group()`：POSIX `os.killpg(pid, SIGTERM)`；Windows 运行 `system_program("taskkill.exe") /PID <pid> /T /F`；
  - `_kill_group()`：POSIX `os.killpg(pid, SIGKILL)`；Windows 上若 `self.proc.poll()` 已有退出码就跳过（链上每层都等子进程退出才退出：cmd、`subprocess.call`、codex.js `:285-290`，组长退出即整棵树已退出），否则再运行一次 `taskkill /T /F`；
  - `_group_alive()`：POSIX 原样（`:346-355`）；Windows 为 `self.proc.poll() is None`；
  - `taskkill` 调用：`timeout=10`、`creationflags=CREATE_NO_WINDOW`、输出丢弃；返回码 128（进程已不存在）忽略，其它非 0 也只忽略不抛，`close` 不能因此挂住或报错；超时同样忽略；
  - `signal.SIGTERM` / `SIGKILL` 只在 POSIX 分支里引用，`import signal`（`:16`）保留。
- `close` 的等待与超时结构不变，只把 `:314`、`:320` 两处信号调用换成上面两个函数。

#### 5.1.8 切换与迁移

- `use`（`switch.py:92-145`）在 Windows 上：`os.replace` 不能覆盖已存在的 junction，改为先删再建。基线只在 `~/.codex` 是链接（`KIND_LINK`）时才有旧目标 `current`（`:104-105`）；不存在（`KIND_MISSING`）时直接建链（`:127-135`）。所以按 `kind` 分两路：
  - `KIND_MISSING`：直接 `create_link(target, link)`，不写记录；成功后若存在旧的 `use-journal.json`（上次中断留下的）一并删除；失败返回 1，`~/.codex` 仍不存在。
  - `KIND_LINK`：旧目标只在内存变量 `current` 里，删除后进程被杀会丢，所以先落盘，再先删后建：
  1. 现有检查与 `busy_check`（`:117`，Windows 走 5.1.1）；
  2. `atomic_write(use_journal_path(), json)`，`use_journal_path()` = `<状态目录>\use-journal.json`，内容 `{"link": <~/.codex>, "old_target": current, "new_target": target}`；
  3. 注入点 `use-remove`（`migrate._test_hook`）后 `remove_link(link)`：失败（`OSError`，如 junction 被占用；或注入的 `migrate.InjectedFailure`）时删除记录、返回 1，报错 `cannot remove ~/.codex (winerror <n>); nothing was changed`，`~/.codex` 仍指向旧目标；成功后经注入点 `use-unlinked`（测试用 CRASH_AT 模拟此刻进程被杀）、`use-link`（测试用 FAIL_AT 模拟建链失败），再 `create_link(target, link)`；
  4. 建链失败（`OSError` 或注入的 `migrate.InjectedFailure`）：`create_link(old_target, link)` 恢复，恢复成功删记录、返回 1；恢复也失败保留记录、返回 1，并打印手工恢复命令 `mklink /J "<link>" "<old_target>"`；
  5. 建链成功后删除记录。
  - POSIX 仍走 `:131-135` 的临时链接 + `os.replace`，不写记录。
- 中断后的恢复 `recover_use_journal(dry_run)`，仅在 use 记录存在时生效。先看有没有 restore 记录（`load_restore_journal()`）：
  - **有 restore 记录**：以 restore 为准。`~/.codex` 由 restore 的状态机负责（状态 B 正是“`~/.codex` 缺失、账号目录还在”，`switch.py:173-178`），use 记录直接删除，不动 `~/.codex`；之后 `use` 按现有流程继续，`restore` 按现有状态续跑；
  - **没有 restore 记录**时按 `~/.codex` 的现状处理：
    - `use <任意名>`（拿到写锁后先调用）：`~/.codex` 不存在 → 视为“已删未建”，按上面的 `KIND_MISSING` 一路继续本次 use（新链接指向本次请求的账号），成功后删记录；`~/.codex` 是链接 → 中断发生在建链之后，直接删记录，继续本次 use；`~/.codex` 是真实目录（中断期间 Codex 自己重建了它）→ 报错 `~/.codex was recreated as a directory while switching from <old_target>; move it away, then rerun multi-codex use <名>`，退出 1，保留记录；
    - `restore <名>`：`~/.codex` 不存在时先 `create_link(old_target, link)`，成功后删 use 记录，再按现有流程继续；建链失败返回 1、保留记录；是链接时删记录继续；是真实目录时不动记录，交给现有 `_restore_state` 判断；
  - `--dry-run`（`use` / `restore` 都有，`cli.py:380`、`:389`）：只打印 `(dry-run) would recover interrupted switch: ~/.codex -> <old_target> (was being replaced by <new_target>)`，不建链、不删记录、不改任何文件。
- `doctor`：记录存在时报一条 `interrupted switch: ~/.codex -> <old_target> was being replaced by <new_target>; run multi-codex use <名>`，`<名>` 取账号目录 realpath 等于 `new_target` realpath 的已登记账号，找不到时写 `NAME`；doctor 只报告，不删记录。
  - `cli._blocked_by_migration`（`:593-605`）增加第三项检查：记录存在且 `~/.codex` 不存在时，`migrate-default` 被拦下（它会把缺失的 `~/.codex` 判为源不存在），提示先运行 `multi-codex use <名>`；其它写命令不涉及 `~/.codex`，不拦。`--dry-run` 不拦，与现有两项一致。
- `restore`（`:233-296`）：`:272` 的 `os.unlink(link)` 改 `platform.remove_link`；`:277` 的 `os.rename` 经 `retry_on_sharing_violation`；重试后仍失败时，`is_sharing_violation(exc)` 为真（只在 Windows）报 `in_use_message`，否则保持 `switch.py:278-280` 的基线报错（POSIX 不变，§8.2 第 6 条对照）。
- `migrate-default`（`migrate.py`）的 `os.rename` 逐点处理（全部先经 `retry_on_sharing_violation`；以下“改报占用、退出 4”只在 `is_sharing_violation(exc)` 为真时做，即只在 Windows 上；POSIX 上与其它 `OSError` 一样按基线路径原样 `raise`，`:466` 仍落到 `:467-469` 的 `raise`，进 `_rollback` 后退出 1 并保留事务记录，与现状一致）：
  - `:466`（rename 模式移动，第 3 步）：rename 失败数据没动，与 `:449-455` 的占用检查失败同样处理——`_delete_journal()` 后 `raise MigrationStop(EXIT_BUSY, in_use_message(source, exc))`，退出 4，不回滚；
  - `:542`（copy 模式 park，S 改名为 B）：S 没动，T 是副本；先 `remove_path(target)` 删副本、`_delete_journal()`，再 `raise MigrationStop(EXIT_BUSY, in_use_message(...))`，退出 4；
  - `:600`（`_rollback` 内）：保持现状——异常落到 `:617-621`，保留事务记录、返回 1；
  - `:633`（`_finish_rollback` 内）有两条进入路径：从 `_rollback`（`:612`）直接调用时，异常同样落到 `:617-621`；从续跑路径进入时，异常先经 `_run` 的 `except`（`:380-382`）打印一次并转 `_rollback`，`_rollback` 按 `PHASE_ROLLED_BACK` 状态再调 `_finish_rollback`（`:612`），第二次失败才落 `:617-621`。所以这条路径会打印两条报错，最终都是保留事务记录、返回 1；
  - 以上回滚中的失败只在报错文字里补 `in_use_message` 的占用原因与 winerror，用户关掉占用程序后重跑同一命令续做；
  - `:558` 的建链走 `platform.create_link`（目录 → junction）；
  - 钥匙串预检 `_check_credentials_store`（`:236-269`）不改逻辑，只把 docstring 里的上游行号从 243-257 更新为 `storage.rs:310-321`。
  - `migrate.py` 不建共享链接（无截断 `grep -n shared src/multi_codex/migrate.py` 为 0），所以不需要 multi-claude 那样的迁移前文件链接预检。

#### 5.1.9 其它模块

- `fsutil.py`：
  - 导入：`platform.py:13` 顶层 `from .fsutil import expand, is_under`，fsutil 顶层再导入 platform 会循环导入；`entry_kind`、`atomic_write`、`remove_path`、`build_manifest`、`copy_tree` 用到 platform 时在函数体内 `from . import platform`；
  - `entry_kind`（`:41-53`）：Windows 上 `os.lstat` 对 junction 不跟随，但 `S_ISLNK` 为假、`S_ISDIR` 为真（junction 不是符号链接）；改为 `S_ISLNK` 或 `platform.is_junction` 时返回 `KIND_LINK`。`entry_kind` 的全部调用方（accounts、cli、switch、launcher、router、migrate、doctor、shared、identity，见 §7.2 枚举）由此统一把 junction 当链接，不再逐处修改；
  - `atomic_write` 的 `os.replace`（`:79`）经 `retry_on_sharing_violation`；`:78` 的 `chmod` 在 Windows 上无害，保留；
  - `remove_path`（`:112-124`）对链接走 `remove_link`（`:124`）；
  - 三处 `os.walk(..., followlinks=False)`：Python 3.8–3.13 的 `os.walk` 都会进入 junction（判断用 `islink`，3.13 新增的 `is_junction` 判断只用于内部的 `_walk_symlinks_as_files`），所以**所有 Python 版本**在 Windows 上都手动剔除。剔除时机：
    - `_grant_owner_access`（`:137-141`）：直接从 `dirnames` 移除，不给 junction 指向的目录改权限；
    - `build_manifest`（`:166-174`）：先把 junction 按 `KIND_LINK` 记进清单（目标取 `platform.read_link`），再从 `dirnames` 移除；
    - `copy_tree`（`:209-216`）：先用 `platform.copy_link` 在目标侧原样复制，再从 `dirnames` 移除；
  - `:172` 的 `os.readlink` 改 `platform.read_link`；`:215` 整行 `os.symlink(os.readlink(full), target)` 改 `platform.copy_link(full, target)`。不用 `create_link`：它按目标是否为目录选链接类型、按当前目录解释相对目标，会把相对或悬空链接复制成错的类型或错的目标。
- `shared.py:115-131`：三个辅助函数改调 `platform.create_link` / `remove_link`。
- 文件链接预检（照搬 multi-claude `beda802` 方案 C4）：
  - `shared.file_links_needed(actions)`：动作里 `kind == "shared-link"`、状态为 CREATE / UPDATE、`reason` 以 `-> ` 开头且指向的不是目录的路径（multi-codex 的 `plan_shared` 以同样的 `"-> " + source` 写 reason，`shared.py:55`、`:69`）；
  - `shared.file_link_message(what, items)`：``cannot create <what> on Windows: <开发者模式提示>; or exclude <items> with `multi-codex set NAME --shared-exclude ITEM`; nothing was changed``（选项名见 `cli.py:419`）；
  - `accounts.execute`（`:252-275`）在冲突检查与 dry-run 返回之后、`save_config` 之前：Windows 上 `file_links_needed` 非空且 `can_create_file_link()` 为假时，打印 `file_link_message`、返回 1，配置与文件都不改。
- `identity.py:66-67`：`_system_config_path()` 在 Windows 上返回 `os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), "OpenAI", "Codex", "config.toml")`；测试覆盖变量 `SYSTEM_CONFIG_ENV` 优先级不变。POSIX 仍返回 `:28` 的常量。`ProgramData` 环境变量是 `FOLDERID_ProgramData` 的近似（§3）。
- `doctor.py:67-68`：`shutil.which("codex")` 改 `platform.which("codex")`；`:186` 的 `os.path.islink` 改 `platform.is_link`。
- `apps.py:54-58`：`find_code` 用 `platform.which("code")`，Windows 不再额外要求 X_OK。
- `config.py`：
  - `validate_env_key`（`:330-337`）：Windows 上把键转大写后再与 `RESERVED_ENV_KEYS`（`:49`，即 `CODEX_HOME` 加 `PROXY_ENV_VARS` `:46-47`）比较；POSIX 仍区分大小写；
  - 共享条目名（`:279`，`cli.py:638` 同理）：Windows 额外拒绝 `\`、`:`。
- `shellpath.py:12-52`：Windows 提示 `pipx ensurepath`，或 PowerShell `[Environment]::SetEnvironmentVariable("Path", "<bin>;" + [Environment]::GetEnvironmentVariable("Path","User"), "User")`。
- `completion.py`：`SHELLS`（`:19`）加 `powershell`；脚本只含 ASCII，用 `Register-ArgumentCompleter -Native -CommandName multi-codex`；安装提示为 `multi-codex completion powershell | Out-String | Add-Content $PROFILE`（不用 `>>`，Windows PowerShell 5.1 的 `>>` 写 UTF-16）。
- `pyproject.toml:13-18`：classifiers 加 `Operating System :: Microsoft :: Windows`。

#### 5.1.10 测试与 CI

- `tests/helpers.py`：
  - `:60-61` 同时隔离 `HOME`、`USERPROFILE`、`APPDATA`、`LOCALAPPDATA`、`ProgramData`（后者只在测试里覆盖，生产逻辑读真实值）；
  - Windows 上假 codex 用 Python 脚本，外包一层 `codex.cmd`（`"<python>" "<脚本>" %*`）；POSIX 保留 `:21` 原有的 sh 版，现有测试的行为不变；
  - `:96` 的 `run_launcher` 在 Windows 上运行 `.cmd`；
  - Windows 测试的 `PATH` 设为 `<fakebin>;%SystemRoot%\System32`，保留 `SystemRoot`（否则 `system_program` 与 Python 自身的 DLL 加载会失败）。
- 测试注入（沿用 `migrate.py:45-55` 的 `_test_hook` 环境变量机制，生产环境不设这些变量时什么也不做）：
  - `switch.py` 的新注入点直接调 `migrate._test_hook`（`switch.py:133` 已这样用）：`use-remove`、`use-unlinked`、`use-link`；
  - `platform.py` 不能导入 `migrate`（`migrate.py:21` 导入 `platform`，会循环导入），新增 `test_hook_point(point) -> bool`：`MULTI_CODEX_TEST_FAIL_AT` 等于 `point` 时为真，由调用方决定怎样模拟失败。用于 `file-link-denied`（`can_create_file_link` 返回假）、`busy-check-powershell`（抛 `BusyCheckError`）、`job-assign-failed`（`usage.py` 按 Assign 失败降级）；
  - `MULTI_CODEX_TEST_SHARING_AT=<点>:<次数>`：`retry_on_sharing_violation` 的 `point` 相同时，前 `<次数>` 次不执行 `fn`，直接抛 `PermissionError(errno.EACCES, "injected sharing violation", <路径>, 32)`（Windows 上第 4 个参数成为 `winerror`，`is_sharing_violation` 为真；写成 `PermissionError("msg")` 时 `winerror` 为 None，不会被识别）；只在 `IS_WINDOWS` 时生效；
  - `MULTI_CODEX_TEST_PROCESS_LIST=<JSON 文件>`：见 5.1.1。§8.1 第 17–20 条（以及其它会在 Windows 上触发占用检查的端到端用例：`use` 在 `~/.codex` 是链接时 `switch.py:117`、`restore` `switch.py:240`、`migrate-default` `migrate.py:456` 与 copy 模式 `:531`）一律把它设为内容为 `[]` 的文件：占用检查分支照常执行、结果为“无占用”，但不运行 PowerShell。不用 `--skip-process-check`，它会写进事务记录的 `skip_process_check`、改变被测路径。真实 PowerShell 只在 §8.1 第 16 条进程内跑一次（WMI 冷启动在 CI 上可能超过 30 秒，`run_cli` 子进程超时只有 120 秒，`tests/helpers.py:83-89`）；
  - 端到端用例用 `tests/helpers.py:83-89` 的 `run_cli`（子进程）设置这些变量；只测单个函数的用例（`cmd_unsafe_reason`、`account_env`、`close` 的 taskkill 计数）写成进程内单测，用 `unittest.mock`。
- 现有依赖 POSIX 的测试文件（helpers、test_apps、test_binding、test_accounts、test_config_copy、test_everyday、test_ergonomics、test_onboarding_commands、test_onboarding、test_sessions、test_switch、test_migrate、test_router）不改断言；CI Windows 行只跑 `tests/test_windows.py`，避免一次性改写全部测试。
- `.github/workflows/ci.yml`：增加 `windows-latest` × `3.8` / `3.12`，job 级 `defaults.run.shell: bash`；Windows 行加 `actions/setup-node`（固定一个 LTS 版本）；Windows 行命令为 `python -m unittest discover -s tests -t tests -p test_windows.py -v`（仓库无 `tests/__init__.py`，测试用 `from helpers import`，必须以 `tests` 为顶层目录发现）。

### 5.2 接口变更

| 接口 | 变更 | 兼容性 |
| ---- | ---- | ---- |
| 平台支持 | 新增 `win32` | macOS·Linux 不变 |
| 不支持平台的报错文案（`cli.py:521`） | 改为 `this platform is not supported yet (macOS, Linux and Windows only)` | 只在其它平台出现，macOS·Linux 看不到 |
| 启动命令文件 | Windows 为 `codex-<名>.cmd`（标记行 `rem managed-by: multi-codex account=<名>`）与 `codex-auto.cmd`（标记行 `rem managed-by: multi-codex router`） | POSIX 文件名与内容不变 |
| 启动命令路径冲突 | Windows 上 python 或入口路径无法安全写进 `.cmd` 时，写命令返回 3，`list` 显示 conflict | POSIX 不触发 |
| 隐藏子命令 `launch` | 仅 Windows 新增，在 argparse 之前拦截，不进帮助与补全 | 只被 `.cmd` 调用；POSIX 上不存在，`multi-codex launch` 仍报 unknown command |
| `completion` | 新增 `powershell` | 现有 zsh / bash / fish 输出不变 |
| `sessions` / 续聊输出 | Windows 给 PowerShell 写法 | POSIX 输出不变 |
| 状态目录 | Windows 为 `%APPDATA%\multi-codex` | POSIX 不变 |
| `env` 键校验 | Windows 上不区分大小写地拒绝保留键 | POSIX 不变 |
| 共享条目名 | Windows 额外拒绝 `\`、`:` | POSIX 不变 |
| 文件链接预检 | Windows 上不能建文件链接时，会新建文件链接的写命令打印 `cannot create ... nothing was changed`，退出 1 | POSIX 不触发 |
| 默认目录续聊命令 | Windows 为 `Set-Location '<目录>'; cmd /d /c 'set CODEX_HOME=& ... & codex resume <id>'`，只影响子进程 | POSIX 不变 |
| use 中断记录 | Windows 新增 `<状态目录>\use-journal.json`；记录存在且 `~/.codex` 缺失时拦下 `migrate-default` | POSIX 不写该文件 |
| `launch` 报错文案 | 与 sh 版启动命令逐字一致（5.1.4、5.1.5） | 不变 |
| 测试注入环境变量 | 新增 `MULTI_CODEX_TEST_SHARING_AT`、`MULTI_CODEX_TEST_PROCESS_LIST` 与若干 FAIL_AT / CRASH_AT 注入点 | 仅测试使用，不设置时行为不变 |
| 退出码 | 不新增；Windows 上路径冲突复用 3，占用复用 4 | 不变 |

reference sibling 回补检查：N/A——本方案 §涉及模块表不含 `docs/reference/*`（仓库无该目录）。

## 6. 备选方案与决策

- **`.cmd` 里写死 `set CODEX_HOME=...` 与代理变量**（`feature-account-manager.md` §11 的旧设想）：否决。cmd 的 `set` 对 `%`、`!`、`^`、`&` 需要转义，转义规则与延迟展开相关，难以保证正确；且会把账号 env 中的密钥写进普通用户可读的文件，权限位在 Windows 上不起作用。改为 `.cmd` 只转发，环境由 Python 构造。
- **用 PowerShell 脚本（`.ps1`）作启动命令**：否决。默认执行策略会拦截，cmd 中无法直接运行，启动开销约 300 ms。
- **pipx 的 console_scripts 入口（`codex-<名>.exe`）**：否决。console_scripts 是安装期固定的，账号是运行期增删的。
- **use 用 `MoveFileEx(MOVEFILE_REPLACE_EXISTING)` 原子替换 junction**：否决。该 API 不能替换目录；替代办法（`ReplaceFile`、事务 NTFS）要么不适用于目录、要么已废弃。用户已定“有 Codex 运行就拒绝”，非原子窗口内没有读者。
- **占用检查按句柄枚举打开 `~/.codex` 的进程**：否决。需要 `NtQuerySystemInformation` 句柄枚举，跨用户进程无权限，耗时不可控；按进程名判断与用户决定一致。

## 7. 影响分析

### 7.1 正向推（改了 A，谁受影响）

- `platform.create_link` / `remove_link`：调用方为 `shared.py:115-131`、`fsutil.py:124`、`switch.py:131-138`、`:272`、`migrate.py:558`；`platform.copy_link` 的调用方为 `fsutil.py:215`。POSIX 分支函数体与基线等价（直接调用 `os.symlink` / `os.unlink` / `os.symlink(os.readlink(...))`），调用方行为不变。
- `lock.lock_fd`：调用方为 `lock.py` 自身的上下文管理器，所有写配置的命令都经过它。POSIX 仍是 `flock`，阻塞语义不变。
- `launcher.render` / `_proxy_lines` 不改，POSIX 启动命令内容不变；Windows 的 `account_env` 是独立实现，偏差由 §8.2 第 2 条的 sh 实跑对照测试发现。
- `accounts.execute` 文件链接预检：所有经 `execute` 的写命令（add / set / share / apply 等）在 Windows 上多一次临时目录试建链接，只在有文件链接要建时执行。
- `_blocked_by_migration` 第三项检查：只读一个状态文件，POSIX 上该文件不会存在，行为不变。
- `launcher_file_ok` 的 Windows 分支：`_plan_launcher`（`accounts.py:178-193`）与 `launcher_status`（`:300-312`）共用，两处判断仍相同。
- `cli.py` 新增 `launch` 拦截：只在 Windows 且第一个参数为 `launch` 时进入（`:504` 之前），不经 `_split_run_command`、`_resolve_command_alias` 与 argparse；POSIX 不拦截，命令集合、帮助与补全不变；若用户曾用 `launch` 作为账号名，`codex-launch` 不受影响（子命令与账号名不在同一命名空间）。
- `cmd_unsafe_reason`：只在 Windows 上被 `_plan_launcher`、`_plan_router`、`launcher_status` 调用，POSIX 计划与 `list` 状态不变。
- 测试注入：生产代码只在注入点读环境变量，未设置时不改变控制流；`platform.test_hook_point` 不导入 migrate，不引入循环导入。
- `identity._system_config_path`：调用方为 `credentials_store`（`:91`），进而被 `migrate._check_credentials_store`（`:236-269`）与 `list` 的凭据存储列使用。Windows 上读取 ProgramData 下的配置，不存在时按“未设置”处理（与 POSIX 文件不存在时一致）。

### 7.2 反向推（没改的功能是否被波及）

- **`os.walk` 进入 junction**：无截断 `grep -rn "os.walk" src/multi_codex` 共 3 处，均在 `fsutil.py`（`:137`、`:164`、`:207`），全部按 5.1.9 处理。
- **`os.path.islink` / `S_ISLNK` 对 junction 返回假**：无截断枚举 `islink` 共 3 处（`doctor.py:186`、`usage.py:195`、`usage.py:219`），`S_ISLNK` 1 处（`fsutil.py:47`），全部改走 `platform.is_link` / 5.1.9 的 `entry_kind`。`entry_kind` 及 `lexists` / `lstat` 相关调用点共 46 处（无截断枚举命令 `grep -rn 'entry_kind(\|lexists\|lstat' src/multi_codex | wc -l`，分布在 accounts、cli、switch、launcher、router、migrate、doctor、shared、identity、fsutil）不逐处改：它们经 `entry_kind` 判断，改一处即覆盖；`os.path.lexists` 对 junction 为真，语义不变。
- **`os.unlink` 删 junction 失败**：无截断枚举 `os.unlink` 共 10 处。可能作用于目录链接的 5 处改走 `remove_link`：`shared.py:123`、`:130`，`switch.py:138`、`:272`，`fsutil.py:124`。其余 5 处只作用于普通文件，不改：`fsutil.py:82`（临时文件）、`accounts.py:230`、`:249`（启动命令）、`switch.py:296`、`migrate.py:95`（事务记录）。`os.rename` / `os.replace` 共 7 处（`switch.py:135`、`:277`，`fsutil.py:79`，`migrate.py:466`、`:542`、`:600`、`:633`），除 `switch.py:135` 改为 5.1.8 的先删再建外，全部经 `retry_on_sharing_violation`。
- **`os.kill` / `killpg`**：无截断枚举只有 `usage.py:342`、`:349` 两处 `os.killpg`，按 5.1.7 替换；全仓无 `os.kill`。`shutil.which` 共 3 处：`platform.py:91`（lsof，只在 POSIX 分支）、`apps.py:55`、`doctor.py:68`，后两处改 `platform.which`。
- **sessions 的 `_usable_cwd`（`sessions.py:222-225`）**：用 `os.path.isdir`，Windows 上可用，不改。
- **binding 的 `normalize_dir`（`binding.py:33-47`）**：`realpath` 在 Windows 上会解析 junction 并统一盘符大小写（3.8 起），绑定比较一致；盘符大小写列为实机项 R6。

### 7.3 运行时

- **进程**：Windows 上每次 `codex-<名>` 依次经过 `cmd.exe` → pipx venv 的 `Scripts\python.exe` 转发器 → 基础解释器 `python.exe`（launch）→ `node.exe` → `codex.exe`；`resolve_command` 能直接运行 codex.js 时不经过 `codex.cmd`。启动开销（两次 Python 进程创建 + 读配置）由实机项 R7 量化。
- **占用检查**：PowerShell `Get-CimInstance` 热启动约 1–3 秒，WMI 冷启动可能要二三十秒（multi-claude 在 CI 上实测首次超过 30 秒，`beda802` `platform.py:824` 注释，故超时取 90 秒），只在 `use` / `restore` / `migrate-default` 运行，不在日常启动路径上。
- **usage --live**：每个账号一个 `codex app-server` 子进程，与 POSIX 相同；结束改为 `taskkill`，多一次进程创建。
- **磁盘**：每个账号一个约 150 字节的 `.cmd`。
- **部署形态**：
  - pipx 安装：`sys.executable` 在 `%USERPROFILE%\pipx\venvs\multi-codex\Scripts\python.exe`；pipx 升级重建 venv 时路径不变；
  - pip `--user` 或系统 Python 安装：`.cmd` 写入的 python 路径可能失效（Python 升级、卸载），`launcher_file_ok` 比较内容时会发现不一致并在 `list` / `doctor` 显示 stale，`add` / `doctor --fix` 重写。
  - Windows 用户目录含中文：按 5.1.3 改写或短名，仍不行就冲突返回 3，提示原因。
- **对外语义**：POSIX 上所有字符串不变；Windows 上续聊命令、补全、PATH 提示为新写法，老的 POSIX 消费方不受影响。

### 7.4 与 multi-claude 方案的差异

1. multi-codex 没有 statusline / hook 模块，multi-claude 中相关改动不做；
2. multi-claude 有 `env.account_env`，multi-codex 没有，在 `launcher.py` 单独新增（POSIX 代码不动），靠对照测试保证与 sh 版一致；
3. 启动 npm 版 Codex 时绕过 `codex.cmd` 直接运行 `node codex.js`，避免参数经 cmd 解释（5.1.3 `resolve_command`）；
4. `use` 先删再建需要落盘的中断记录（5.1.8），multi-claude 无对应；
5. 占用检查的进程判据不同：`codex.exe`，或命令行含 `@openai\codex` 的 `node.exe`；
6. `usage --live` 的子进程管理是 multi-codex 独有：`CREATE_NEW_PROCESS_GROUP` 启动，`taskkill /T /F` 先行结束，Job Object（`KILL_ON_JOB_CLOSE`）兜底；
7. `use` 的原子替换改为先删再建（multi-claude 的切换实现不同，以本仓库 `switch.py:131-135` 为准）；
8. 系统级配置路径（`identity.py`）与钥匙串预检是 Codex 特有；
9. `launcher_file_ok` 的权限位检查是 multi-codex 特有，Windows 上必须跳过；
10. `app` 子命令在 Windows 上保持仅 macOS；
11. 续聊命令（`sessions`）的 PowerShell 写法是 multi-codex 特有；
12. `copy_tree` 复制链接照搬 multi-claude 的 `copy_link`（保留链接类型与目标原文），multi-codex 基线里这一处是 `fsutil.py:215` 的 `os.symlink(os.readlink(...))`。

## 8. 回归测试

### 8.1 Windows CI 自动（`tests/test_windows.py`，windows-latest × 3.8 / 3.12）

测试环境：`PATH` = `<fakebin>;%SystemRoot%\System32`，保留 `SystemRoot`，隔离 `USERPROFILE` / `APPDATA` / `LOCALAPPDATA` / `ProgramData`。

1. `is_supported()` 为真；`IS_WINDOWS` 为真。
2. `render_windows` 输出四行、标记行可被 `managed_account` 识别；`scan_managed` 只列 `.cmd`。`codex-auto.cmd` 第 2 行等于 `WIN_ROUTER_MARKER`：`is_router` 为真、`managed_account` 为 `None`，有绑定变化的 `apply` 不把它当孤儿删除。
3. `cmd_unsafe_reason` 与路径改写（进程内单测，`unittest.mock` 替换短名函数与 `sys.executable`）：`%USERPROFILE%` 改写命中；改写不了且短名函数返回原路径时返回非 ASCII 原因；路径含 `"`、`%`、`!` 时返回不安全字符原因。同一替换下 `_plan_launcher` 与 `_plan_router` 给出 CONFLICT 且原因相同、`converge` 返回 3、`launcher_status` 返回 `conflict`。
4. `_entry.py`：包目录不一致时退出 1；一致时运行 `--version` 成功。
5. 直接运行的目标原样传参（用真实 node.exe）：CI 的 Windows 行先用 `actions/setup-node` 装固定的 LTS 版本（不依赖镜像自带）；测试把 `shutil.which("node")` 找到的 `node.exe` 复制进 fakebin，并在 fakebin 下按 npm 布局放 `codex.cmd` 与 `node_modules\@openai\codex\bin\codex.js`。codex.js 是真 JS：以 JSON 打印 `process.argv.slice(2)` 与 `CODEX_HOME`、代理变量、`ISOLATION_BREAKING_ENV` 中的变量。用子进程分别运行 `multi-codex launch <名> -- <参数>` 与生成的 `codex-<名>.cmd`（后者只传不含 cmd 特殊字符的参数），参数含空格、`&`、`|`、`^`、`%PATH%`、`"`、结尾反斜杠；断言 launch 路径下 argv 与输入逐字相同、`CODEX_HOME` 与代理变量正确、隔离变量已清除、退出码透传。找不到 node 时该用例 skip 并打印原因。codex.js 再 spawn codex.exe 这一跳（libuv 引号 + Rust 拆分）不在 CI 内，并入 R9。
6. `.cmd` 目标的限制：删掉 fakebin 里的 codex.js，`resolve_command` 退回 `[codex.cmd, ...]`；不含特殊字符的参数原样到达。含特殊字符的情况不断言具体结果，只断言 README 限制节存在对应说明（防止限制说明被删）。
7. `launch` 拦截：`launch <名> -- --help` 把 `--help` 交给 codex（不打印本工具帮助）；`launch <名>` 缺 `--` 时退出 2；`multi-codex -h` 与补全里没有 `launch`。账号分支文案：账号目录不存在 → stderr 为 `multi-codex: account directory does not exist: <目录>`、退出 1；无 codex → `multi-codex: codex not found in PATH`、退出 127；与 POSIX 下 sh 启动命令的输出逐字比较（比较文本固定在测试数据里）。
8. `codex-auto.cmd`：绑定命中；绑定指向未登记账号（文案与退出 1）；未命中（stderr `no account is bound to ...` 文案、不设 CODEX_HOME）；未命中且无 codex（退出 127）。文案与 sh 版逐字比较。命中后改绑定，不 apply 立即生效。
9. `account_env` 代理：`inherit` / 指定 http / 指定 socks / `none` 四种设置下，四个变量的大小写变体全部按规则处理；继承的 `no_proxy`（小写）原值被保留并追加本地地址。进程内单测：`base` 是同时含 `NO_PROXY=a`、`no_proxy=b` 的普通 dict 时，结果只有大写 `NO_PROXY`，以 `a` 为原值。
10. 账号 env 键按大写去重：`{"Foo": "1", "FOO": "2"}` 结果只有 `FOO`，值与 sh 版按排序 export 的结果一致。
11. `validate_env_key("codex_home")` 被拒；`validate_env_key("Http_Proxy")` 被拒。
12. junction：`create_link` 对目录建 junction，`is_link` 真、`entry_kind` 为 `KIND_LINK`、`read_link` 无 `\\?\` 前缀、`remove_link` 后目标目录仍在。文件链接无权限时抛 `LinkPermissionError` 且提示含开发者模式；有权限时成功。两种结果都接受，按 `can_create_file_link()` 的实际值断言。
13. 文件链接预检：共享清单含 `AGENTS.md`，设 `MULTI_CODEX_TEST_FAIL_AT=file-link-denied`，`add <名> --shared` 退出 1、输出 `nothing was changed`，配置文件、账号目录、启动命令都没有新增或改动。
14. `share` 与目录遍历：共享目录经 junction 生效；`build_manifest` 把 junction 记为链接、不进入；`copy_tree` 在目标侧把 junction 复制为 junction，相对目录符号链接（有权限时）复制为目录符号链接且目标文本不变，悬空链接原样复制；`_grant_owner_access` 不改 junction 指向目录的权限。
15. 锁：两个进程争锁，后到者得到 `LockBusyError` 且 `holder_pid` 为持锁方 PID；持锁方释放后可再次获得。
16. 占用检查。前提：`use` / `restore` 用例中 `~/.codex` 已是指向某已登记账号的 junction（restore 的账号即该账号）；`migrate-default` 用例中 `~/.codex` 是真实目录。设 `MULTI_CODEX_TEST_PROCESS_LIST` 指向一份 JSON，含 `codex.exe`、含 `@openai\codex` 的 `node.exe`、无关的 `node.exe` 各一条：三个命令都返回 4，输出含前两条的 `pid=`、不含第三条。设 `MULTI_CODEX_TEST_FAIL_AT=busy-check-powershell` 时按现有 `BusyCheckError` 通道报错。另在进程内直接调用一次真实的 `_find_busy_windows()`（运行 CI 机上的 PowerShell），断言返回列表、不抛错。
17. `use`：`~/.codex` 原本不存在时 `use <名>` 成功、建成 junction、不写记录；预置一份旧 `use-journal.json` 时成功后它被删除。两个账号之间来回切换，`~/.codex` 始终是 junction 指向正确目标、结束后无 `use-journal.json`；设 `MULTI_CODEX_TEST_FAIL_AT=use-link` → 恢复为旧链接、返回 1、记录已删；设 `MULTI_CODEX_TEST_FAIL_AT=use-remove` → 返回 1、记录已删、`~/.codex` 仍指向旧目标。
18. `use` 建链前被杀：设 `MULTI_CODEX_TEST_CRASH_AT=use-unlinked`，`use` 退出 137 → `~/.codex` 缺失、记录存在；此时 `use <另一账号> --dry-run` 只打印 `(dry-run) would recover ...`，记录与 `~/.codex` 都不变；此时 `migrate-default` 被拦下；`doctor` 报 interrupted switch；再运行 `use <另一账号>` 按 `KIND_MISSING` 一路成功、记录删除。另测中断期间 `~/.codex` 被重建为真实目录时 `use` 退出 1、记录保留；`restore` 在 `~/.codex` 缺失（且无 restore 记录）时先恢复旧链接、删 use 记录再继续。另测 restore 中断在状态 B 且同时有 use 记录：设 `MULTI_CODEX_TEST_CRASH_AT=restore-unlinked` 运行 `restore <名>`（退出 137，`~/.codex` 缺失、restore 记录存在），再放一份 use 记录；重跑 `restore <名>` → use 记录被删、没有建回旧链接，restore 完成（数据回到 `~/.codex`，restore 记录删除）。
19. `migrate-default`：全流程成功。设 `MULTI_CODEX_TEST_SHARING_AT=migrate-move:3`（前 3 次占用、第 4 次成功）→ 迁移成功。以下“一直失败”均设次数 99：一直失败时：rename 模式 `migrate-move`（`:466`）→ 退出 4、无事务记录、`~/.codex` 原样；copy 模式 `migrate-park`（`:542`）→ 退出 4、副本已删、无事务记录；回滚中 `migrate-rollback`（`:600`）/ `migrate-rollback-restore`（`:633`）失败（回滚分别由 rename 模式的 `MULTI_CODEX_TEST_FAIL_AT=rename-target`（`migrate.py:475`，改名已完成；异常经 `_run` 的 `except`（`:380-382`）进 `_rollback`，命中 `:598-600` 的改回；`verify` 注入点只在 copy 路径 `:493` 调用，rename 模式在 `:478` 已返回，用不上）与 copy 模式的 `MULTI_CODEX_TEST_FAIL_AT=journal-parked` 触发，后者现有测试已在用，`tests/test_migrate.py:170`）→ 退出 1、事务记录保留、报错含 winerror（从续跑路径进入 `:633` 时接受两条报错），去掉该环境变量后重跑同一命令，退出 0 且 `assert_migrated`（rename 模式记录仍为 planned、源缺失目标在，走续跑表 `migrate.py:418-421` 继续迁移；copy 模式记录为 rolled-back，走 `:393-399` 先做完回滚再重新迁移，另断言输出含 `previous rollback finished`；与现有 `tests/test_migrate.py:433-437` 口径一致）。
20. `restore` 全流程；设 `MULTI_CODEX_TEST_SHARING_AT=restore-rename:99` 时报错含 `is in use by another program`。
21. `usage --live`。子进程用例：假 codex 的 `app-server` 再起一个孙进程且不响应 stdin 关闭；运行 `usage --live` 后两者都已退出（用 `_pid_alive` 判），全程无 `AttributeError`；Job Object 正常与设 `MULTI_CODEX_TEST_FAIL_AT=job-assign-failed` 两种情况各跑一次。进程内单测（`unittest.mock` 包装 `subprocess.run` 计数 taskkill）：假 app-server 关 stdin 即退出时，正常退出路径不调用 taskkill；不响应 stdin 时 `_terminate_group` 调一次，组长随之退出后 `_kill_group` 不再调用。
22. `identity._system_config_path()` 在 `ProgramData` 指向临时目录时返回该目录下 `OpenAI\Codex\config.toml`。
23. `sessions` 续聊命令：已登记账号为 `Set-Location '...'; codex-<名> resume <id>`，路径含 `'` 时转义为 `''`；默认目录为 `cmd /d /c 'set CODEX_HOME=& set CODEX_SQLITE_HOME=& set CODEX_API_KEY=& set CODEX_ACCESS_TOKEN=& codex resume <id>'`，并实际运行它（假 codex 打印环境）确认四个变量都不存在、调用后当前进程环境未变。
24. `completion powershell` 输出只含 ASCII；`launch` 不在补全里。
25. `doctor`：`codex` 只在当前目录（不在 PATH）时报未找到。
26. `app` 返回“仅 macOS”错误。

### 8.2 macOS·Linux 回归（现有 CI 矩阵：ubuntu 3.8 / 3.12、macos 3.10 / 3.12）

1. 现有全部测试 `python -m unittest discover -s tests -t tests -v` 通过，断言不改。
2. `account_env` 对照测试（POSIX 上运行）：同一组输入（无代理 / inherit / http / socks / none，父环境分三种：两者都没有、只含 `NO_PROXY`、只含 `no_proxy`（两者都有时 sh 版分别追加、Windows 合并为一个，不可比，由 §8.1 第 9 条单测覆盖），带 env、含大小写冲突的 env 键）下，用 `sh` 实跑 `render(..., command_mode=True)` 后接 `env` 打印环境，与 `account_env(..., base=同一父环境)` 比较：sh 结果先剔除 shell 自带的 `PWD`、`OLDPWD`、`SHLVL`、`_`，再把键转大写、`no_proxy` 并入 `NO_PROXY`，之后两者相同。
3. 启动命令快照：在基线 `44cb4c1` 与新代码下，对同一组账号设置生成 `codex-<名>` 与 `codex-auto`，逐字节一致（基线输出固定为测试数据）。
4. 帮助、补全（zsh / bash / fish）、`sessions` 输出与基线一致；`launch` 不出现在帮助与补全中。
5. 锁（含抢锁失败时读出 PID）、占用检查（lsof 与 `/proc` 两条路径）、`use` 原子替换且不写 `use-journal.json`、`usage --live` 进程组结束行为不变。
6. `migrate-default` 的 `:466` / `:542` 在 POSIX 上遇 `PermissionError` 时：`retry_on_sharing_violation` 在 POSIX 上只调用一次、不重试，异常按基线路径处理（与现状一致）。触发方式：进程内单测，`unittest.mock.patch("os.rename", side_effect=PermissionError(errno.EACCES, "denied"))` 后调用迁移入口，分两条断言，都要求退出码为 1（不是 4）、报错不含 `is in use by another program`，且同一断言在基线 `44cb4c1` 上也成立：`:466`（rename 模式，源目录仍在）落到 `_rollback` 的 else 分支（`migrate.py:615-616`），事务记录保留；`:542`（copy 模式 park）落到 `migrate.py:602-606`，副本与事务记录都已删除、源目录未动。restore 的 `:277` 同法 mock，断言报错仍为基线的 `move the new ~/.codex away`。
7. shellcheck 对生成的 sh 启动命令仍无告警（现有测试）。

### 8.3 Windows 实机（测试机 `192.168.0.74`）

| 编号 | 内容 | 判据 |
| ---- | ---- | ---- |
| R1 | Codex 是否读取 `HTTP_PROXY` / `HTTPS_PROXY` | 给账号设一个本机抓包代理，`codex-<名>` 发起请求，代理侧看到连接 |
| R2 | Ctrl+C 时 cmd 的“终止批处理操作吗(Y/N)” | multi-claude 实机已确认会出现，README 直接写为限制；本项只复核现象一致，并记录 PowerShell 调用 `codex-<名>` 时含 `&` 参数的表现 |
| R3 | 凭据管理器（keyring）模式 | 账号设 `cli_auth_credentials_store = "keyring"` 登录后 `migrate-default` 被预检拒绝；`file` 模式迁移后无需重新登录 |
| R4 | VS Code 插件与桌面版的进程名 | 打开 VS Code Codex 插件、Codex 桌面版时 `use` 返回 4；记录实际进程名，不在判据内的补进判据 |
| R5 | Defender 占用文件时的重试次数 | 在开启实时保护的机器上连续 `migrate-default` / `restore` 10 次，无 PermissionError 漏网 |
| R6 | binding 中盘符大小写 | 在 `c:\x` 与 `C:\x` 下分别 `bind`、`codex-auto`，命中同一绑定 |
| R7 | 启动开销 | 在 pipx 安装下（含 venv `Scripts\python.exe` 转发器一层）`Measure-Command { codex-<名> --version }` 与直接 `codex --version` 相差 ≤ 300 ms |
| R8 | 中文用户目录 | 用户目录含中文时 `add` 生成的 `.cmd` 可正常运行，或按冲突返回 3 并提示 |
| R9 | npm 的 `codex.cmd` 与 `resolve_command` 一致 | 读实机 `%APPDATA%\npm\codex.cmd` 确认其 node 选择规则（同目录 `node.exe` 优先）与 codex.js 相对位置；`codex-<名> --version` 进程树为 `node.exe codex.js` 而非 `cmd.exe codex.cmd`；运行 `codex-<名> exec "a&b c"` 类用例（经 `multi-codex launch`），确认参数经 codex.js spawn codex.exe 后原样到达 |
| R10 | 默认目录续聊命令 | 在 Windows PowerShell 5.1 与 PowerShell 7 中各运行一次，Codex 落到 `~\.codex`，运行后 `$env:CODEX_HOME` 等仍为运行前的值 |
| R11 | `CREATE_NO_WINDOW` 下是否弹窗 | 在 PowerShell 与从资源管理器打开的 cmd 中运行 `usage --live`，进程树各层（cmd、python 转发器、python、node、codex.exe）都不弹出控制台窗口 |

## 9. 日志 / 观测点

- 占用检查：沿用 `migrate.py:299-316` 的 `pid= command= usage= path=` 输出；PowerShell 失败时报错带 `phase=busy-check`、超时秒数或退出码。
- 链接：`LinkPermissionError` 报错带 `path=` 与开发者模式提示；junction 兜底到 `mklink /J` 时 `info` 一行 `(fallback) mklink /J`。文件链接预检失败带 `phase=shared-link` 与 `nothing was changed`。
- 重试：`retry_on_sharing_violation` 不逐次打印（多数命令没有 `--verbose`，`cli.py:247` 等只对部分命令注册）；最终失败的错误信息为 `in_use_message`，含路径与 winerror。
- `use` 中断记录：建链失败恢复成功 / 失败分别打印 `phase=use restore-old-link ok|failed path= target=`，失败时附 `mklink /J` 手工恢复命令；`recover_use_journal` 处理记录时 `info` 一行 `recovered interrupted switch: <old_target> -> <new_target>`；`doctor` 一行 `interrupted switch: ...`；被拦下的 `migrate-default` 提示先运行 `multi-codex use <名>`。
- `usage --live`：`taskkill` 超时或非 0 返回码不报错，只记一条 DEBUG 日志 `taskkill rc=<n>`（`logging.getLogger("multi_codex")`，默认不输出）；不写进 `LiveError` 的详情，因为 `close` 在查询结果（含错误）生成之后才执行。Job Object 创建或关联失败同样只记 DEBUG 日志 `job object unavailable: winerror=<n>`。
- 启动命令冲突：返回 3 时的原因为 `non-ascii path` / `unsupported character` 之一，带路径。
- `doctor`：Windows 上增加一行显示 `bin_dir` 是否在用户 PATH、启动命令里 python 路径是否存在。

## 10. 开放问题

1. R1 若 Codex 不读代理环境变量，Windows 上的代理设置是否改为提示“不生效”，还是另找配置项——等实机结果再定，当前按“读取”实现。
2. R4 若 VS Code 插件使用自带的 `codex.exe` 且进程名不同，判据需要扩充——当前按 `codex.exe` 与 `node.exe @openai\codex` 实现。
3. 上游注册表策略 `HKLM\SOFTWARE\Policies\OpenAI\Codex`（`config/src/loader/mod.rs:820-834`，读出 requirements.toml 路径）能否指定凭据存储方式：若能，`identity.credentials_store` 在 Windows 上漏读这一层，`migrate-default` 的钥匙串预检可能放过应拒绝的情况。当前不读注册表，编码前读上游 requirements 加载代码确认它是否含 `cli_auth_credentials_store`。
