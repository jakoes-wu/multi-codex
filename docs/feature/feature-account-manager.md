# multi-codex：Codex CLI 多账号管理工具

> 2026-09-29 注记：代码已按本方案落地，尚未提交。§4 涉及模块中所有条目都是新增。
>
> - 验证情况：本机 macOS（Python 3.11、3.13）和Linux 测试机 Ubuntu 20.04（Python 3.8.10）上，§8 的回归用例全部通过；shellcheck 两台机器都没有安装，留给 CI。
> - 代码评审共 2 轮，第 2 轮结论为可以合并。
>
> - 第 1 轮独立评审共发现高级问题 3 个（H1–H3）、中级问题 10 个（M1–M10），本版已全部修订。
> - 第 2 轮复审新发现高级问题 1 个（N1）、中级问题 6 个（N2–N7）；第 3 轮复审新发现高级问题 1 个（R1）、中级问题 3 个（R2–R4）。本版均已修订，低级问题一并处理。
> - 第 4 轮复审结论：没有高级或中级问题，达到可实施门槛，可以进入编码。该轮提出的低级问题 L-m 至 L-q 也已修订。
> - 代码评审（第 1 轮）发现高级问题 2 个：`os.walk` 静默跳过读不了的目录导致复制校验漏检；删除目录时错误回调写错导致迁移卡死。均已修复并补回归用例，相关方案文字同步见 §5.1.2、§5.1.5 续跑表第 3 行与“从第 1 步重新开始”、§9。
> - 编码后按实现同步的偏差：新增 `actions.py`（§4）；命令行输出改为英文（§9）；http 代理清除继承的 `ALL_PROXY`（§5.1.3）；状态目录与 `lock` 文件在冲突时也会建出（§5.1.1）；安装脚本的中断测试改用钩子（§8 第 16 条）。
> - 用户已决定一期只支持 macOS 和 Linux，Windows 放到二期，设计要点见 §11。

## 1. 背景

Codex CLI 通过环境变量 `CODEX_HOME` 决定配置、凭据、会话数据库的存放目录；未设置时使用 `~/.codex`。要在同一台机器上使用多个账号，就得给每个账号准备一个独立目录，并在启动时设置对应的 `CODEX_HOME`。实际需要手工维护以下内容：

- 各账号目录；
- 各账号的启动入口，以及入口上的代理环境变量；
- 各账号指向公共资源（`AGENTS.md`、skills 等）的软链；
- 把已有的 `~/.codex` 转成其中一个账号。

本项目把这些操作做成一个命令行工具，准备以开源形式发布到 GitHub。

## 2. 目标 / 非目标

**目标**（对应用户提出的 5 项需求）：

1. 把默认目录 `~/.codex` 迁移为一个具名的独立账号，原位置留兼容软链。
2. 新增账号：创建账号目录、生成独立启动命令，可选链接共享资源。也可以把已有目录登记为账号。
3. 为每个账号单独设置本地代理端口（或完整代理 URL）。可以显式关闭代理，也可以继承启动时 shell 里的代理设置。
4. 一键部署：`install.sh` 安装工具本身；加上 `--config <文件>` 时，装完立即执行 `apply`，一条命令就完成工具安装和全部账号部署。
5. 所有写操作命令都可以重复执行：
   - 已处于目标状态时不做任何修改，返回 0；
   - 中途失败或被中断后，再次执行能接着完成；
   - 遇到不归本工具管理的冲突时，不修改任何内容，返回 3。

**非目标**：

- 一期不支持 Windows（二期见 §11）。WSL 内按 Linux 使用。
- 不负责登录，也不读写凭据内容（`auth.json` 由 Codex 自己的 `codex login` 管理）。
- 不改写 Codex 数据库或 `config.toml` 里记录的绝对路径，旧路径依靠兼容软链继续访问。
- 不提供删除账号数据的命令；`remove` 只注销账号、删除启动命令，不删除账号目录。
- 不修改 shell 启动文件（`.zshrc` / `.bashrc`），只提示用户自行添加 PATH。
- 不自动导入 shell 别名里已有的代理设置，需要用户显式用 `--proxy` 设置。
- 不提供迁移完成后的撤销命令。手工回滚步骤写在 README 里，并说明使用 `--keep-backup` 时备份目录的位置。

## 3. 假设与约束

**运行环境**

- Python ≥ 3.8，只使用标准库，不能用 3.9 以后才有的 API（如 `str.removeprefix`、`list[str]` 注解、`Path.is_relative_to`）。
- 平台：macOS、Linux。
- 调用 Codex 时以 `PATH` 中的 `codex` 为准，工具不负责安装 Codex。

**默认路径**（都可以通过 `init` 的参数修改）

| 项目 | 路径 |
| ---- | ---- |
| 默认目录（迁移源） | `~/.codex` |
| 账号根目录 | `~/.cx` |
| 启动命令目录 | `~/.local/bin` |
| 工具状态目录 | `~/.config/multi-codex/`（设置了 `XDG_CONFIG_HOME` 时以它为准） |
| 工具安装位置 | `~/.local/share/multi-codex` |

工具状态目录里有三个文件：

- `config.json`：唯一的权威配置；
- `lock`：写操作互斥锁（`flock`）；
- `migrate-journal.json`：迁移事务记录，只在迁移进行中存在。

**外部行为依据**

- Codex 官方文档说明 `CODEX_HOME` 默认为 `~/.codex`。依据：<https://learn.chatgpt.com/docs/config-file/environment-variables>。
- 实测（codex-cli 0.155.1）：`CODEX_HOME` 指向不存在的目录时，codex 报 `CODEX_HOME points to "...", but that path does not exist` 后退出。所以账号目录必须先存在，才能启动 Codex。
- Codex 官方文档没有写明支持哪些代理环境变量。macOS 上已有实测：通过 `HTTPS_PROXY` 启动的 Codex，流量确实走了指定端口。另有三点未验证，列入 §10：
  - Codex 是否读取 `ALL_PROXY`；
  - WebSocket 请求是否走代理；
  - Linux 上代理是否同样生效。
- 实测：Python 3.11 的 `shutil.copytree` 遇到 unix socket 时抛出 `[Errno 102] Operation not supported on socket`，并在目标目录留下已复制的部分。真实的 `~/.codex` 下有 `ipc/ipc.sock` 等 socket 文件。
- 实测：`lsof +D ~/.codex` 能查到 Codex 以外的占用者：ChatGPT 浏览器扩展宿主（工作目录和可执行文件在 `~/.codex/plugins/` 下）、VS Code 扩展和 app-server。其中有的进程没有打开任何 sqlite 文件，只检查数据库文件会漏掉它们。
- 实测：macOS 默认的 APFS 文件系统不区分大小写，`~/.CODEX` 与 `~/.codex` 是同一目录。
- 官方环境变量页面还列出了以下变量，全局设置时会破坏账号隔离：
  - `CODEX_SQLITE_HOME`：指定 SQLite 状态的存放位置，`config.toml` 里的 `sqlite_home` 优先于它。全局设置后，所有账号会共用同一组数据库，迁移也不会移动这些数据库；
  - `CODEX_API_KEY`、`CODEX_ACCESS_TOKEN`：全局设置后，所有账号会用同一份凭据。

  本工具不自动清除这些变量，只在 `migrate-default` 和 `list` 中检测到它们时给出警告。启动命令是否应清除它们，列入 §10。

**其它约束**

- 账号名：
  - 正则为 `^[A-Za-z0-9][A-Za-z0-9._@+-]{0,63}$`，必须以字母或数字开头，所以不会被当成命令行选项，也不会以 `.` 开头；
  - 邮箱可以直接作为账号名；
  - 查重不区分大小写，`Work` 与 `work` 视为同一个账号。
- 账号根目录下以 `.` 开头的条目（例如 `.migration`）一律不当作账号，工具也不去碰。工具不扫描根目录，只以 `config.json` 为准。
- 新建的账号目录和根目录权限为 0700，因为目录里会保存 `auth.json`。
- 许可证为 MIT。README 提供英文（`README.md`，GitHub 默认展示）和简体中文（`README.zh-CN.md`）两份，文件顶部互相链接。

## 4. 涉及模块

仓库新建，基线为空，下表所有行都是新增。

| 区域 | 文件 | 改动类型 | 改动点 |
| ---- | ---- | ---- | ---- |
| 命令行入口 | `src/multi_codex/cli.py` | 新增 | argparse 子命令分发、退出码、输出格式 |
| 配置 | `src/multi_codex/config.py` | 新增 | 读写 `config.json`（原子写入）、自动初始化、校验账号名与代理 |
| 互斥锁 | `src/multi_codex/lock.py` | 新增 | 用 `fcntl.flock` 实现写操作互斥 |
| 账号操作 | `src/multi_codex/accounts.py` | 新增 | init / add / proxy / remove / apply / list 的计划与收敛 |
| 启动命令 | `src/multi_codex/launcher.py` | 新增 | 渲染启动命令模板、识别受管标记、扫描孤儿启动命令 |
| 默认目录迁移 | `src/multi_codex/migrate.py` | 新增 | 事务记录、rename / 复制校验、兼容软链、接续与回滚 |
| 共享资源 | `src/multi_codex/shared.py` | 新增 | 按配置建立、核对、删除共享链接，并登记由本工具建立的链接 |
| 平台适配 | `src/multi_codex/platform.py` | 新增 | 默认路径、创建与识别链接、占用检查；平台差异都集中在这里，为二期 Windows 预留 |
| 文件工具 | `src/multi_codex/fsutil.py` | 新增 | 原子写入、文件清单与 SHA-256、跳过特殊文件的复制 |
| 动作与输出 | `src/multi_codex/actions.py` | 新增 | 动作对象（计划与执行分离）、统一的输出格式；由 accounts 与 shared 共用，单独成文件以免循环 import |
| 包入口 | `src/multi_codex/__init__.py`、`__main__.py` | 新增 | 版本号，支持 `python -m multi_codex` |
| 一键安装 | `install.sh` | 新增 | 安装、升级、卸载，以及 `--config` 一键部署 |
| 测试 | `tests/test_*.py`、`tests/helpers.py` | 新增 | unittest；每个用例使用临时 HOME 和假 codex |
| 开源标准件 | `README.md`、`README.zh-CN.md`、`LICENSE`、`CONTRIBUTING.md`、`CODE_OF_CONDUCT.md`、`CHANGELOG.md`、`SECURITY.md`、`.gitignore`、`.editorconfig`、`pyproject.toml` | 新增 | GitHub 常规结构 |
| CI 与模板 | `.github/workflows/ci.yml`、`.github/ISSUE_TEMPLATE/*`、`.github/pull_request_template.md` | 新增 | ubuntu 与 macOS 两个平台跑测试，外加 shellcheck |
| 示例配置 | `examples/config.example.json` | 新增 | 给 `apply` 使用的声明式示例 |

## 5. 方案

### 5.1 实现要点

#### 5.1.1 状态权威源与收敛模型

- `config.json` 是唯一的权威源，启动命令、共享链接等文件都由它推导出来。
- 第一次执行任何写命令时，如果 `config.json` 还不存在，就按默认值自动创建。

每个写命令都按以下顺序执行：

1. **获取互斥锁**：对 `lock` 文件执行 `fcntl.flock(LOCK_EX | LOCK_NB)`。进程退出时由内核自动释放锁，所以不存在“持有者已死但锁还在”的情况，也不受 PID 复用影响。拿到锁后把自己的 PID 写进锁文件，这个 PID 只用于报错时显示。获取失败时返回 1，并说明锁被哪个 PID 持有。

   实现时必须注意两点：
   - 锁文件用 `os.open(O_RDWR | O_CREAT)` 打开，不能带截断标志；拿到锁以后，再原地截断并写入 PID。
   - 锁文件不能用 `atomic_write` 写，也不能删除。`os.replace` 会换掉文件的 inode，两个进程可能会各自锁住不同的文件。
2. **计算新配置**：读取当前配置，按命令参数算出新配置。
3. **生成动作列表**：对比新配置和实际文件状态，逐项生成动作。每个动作是 `create`、`update`、`delete`、`unchanged`、`conflict` 之一。
4. **冲突拦截**：只要有一个 `conflict`，就不做任何写入（包括配置），返回 3。
5. **执行**：先用原子写入保存新配置，再逐个执行文件动作。文件动作中途失败时，配置已经是新的，重跑同一命令或 `apply` 就能收敛。

“有冲突就什么都不写”不包括工具状态目录和 `lock` 文件：加锁发生在生成计划之前，第一次运行任何写命令时都会建出它们。它们只是工具内部记录，不属于用户数据。

`--dry-run` 只执行到第 3 步，把动作列表打印出来。

存在未完成的迁移事务记录时，只有 `migrate-default`（续跑）、`list`、`usage`、`doctor` 和 `--dry-run` 可以执行；`usage` 与 `doctor` 在读取事务记录之前分派，记录损坏时也能运行。但事务记录损坏时，`list` 也返回 1（见 §9）。其它写命令一律返回 1，并提示先完成迁移。这样可以防止迁移中途根目录或账号被改动。

原子写入（`fsutil.atomic_write`）的做法：先写同目录下的临时文件，`fsync` 后再用 `os.replace` 覆盖目标。

**受管判定**（决定哪些东西可以覆盖或删除）

- **启动命令**：第 2 行固定为 `# managed-by: multi-codex account=<名称>`。
  - 同名文件如果没有这个标记，视为冲突。报错要直接说明原因，例如“`codex-hud` 已存在且不归 multi-codex 管理”。
  - 启动命令目录里带受管标记、但对应账号已不在配置中的文件，称为孤儿启动命令，由 `apply` 和 `remove` 删除。
- **共享链接**：见 §5.1.6。
- **账号目录**：已存在就直接登记，不改动目录里的内容。

#### 5.1.2 命令一览

| 命令 | 作用 | 再次执行的结果 |
| ---- | ---- | ---- |
| `init [--root] [--bin-dir] [--shared-dir] [--shared-items]` | 创建或修改全局配置 | 参数相同时输出 unchanged |
| `migrate-default <名称> [--source] [--copy] [--keep-backup] [--proxy] [--skip-process-check] [--accept-relogin]` | 把默认目录迁移成账号 | 已完成时输出 already migrated；上次中断时从中断处继续；凭据存在系统钥匙串时拒绝，见 `docs/bugfix/fix-keyring-migration.md` |
| `add <名称> [--proxy P] [--shared \| --no-shared] [--adopt]` | 新增账号、登记已有目录，或修改账号选项；`--adopt` 见 `feature-adopt-links.md` | 与现状相同时输出 unchanged |
| `proxy <名称> <端口 \| URL \| off \| inherit>` | 设置代理并重新生成启动命令 | 值相同时输出 unchanged |
| `remove <名称>` | 注销账号，删除受管启动命令和本工具建立的共享链接；账号目录保留 | 账号不存在时输出 not registered，同时清理该名字的孤儿启动命令，返回 0 |
| `apply [-f 文件]` | 带 `-f` 时，以该文件为新配置，否则以当前 `config.json` 为新配置；按 §5.1.1 的顺序执行，收敛全部账号并清理孤儿启动命令 | 已收敛时全部输出 unchanged |
| `list [--json]` | 列出账号、目录、代理、启动命令状态和登录身份 | 只读；身份列见 `docs/feature/feature-account-insight.md` |
| `usage [名称 ...] [--live] [--timeout 秒] [--json]` | 显示额度 | 只读、不加锁；见 `feature-account-insight.md` |
| `doctor [--json]` | 体检 | 只读、不加锁；见 `feature-account-insight.md` |
| `completion`、`run`、`path`、`env` | 补全、在账号环境下运行命令、输出目录、每账号环境变量 | 见 `docs/feature/feature-cli-ergonomics.md` |
| `use [名称]`、`restore 名称` | 切换默认账号、撤销迁移 | 见 `docs/feature/feature-default-switch.md` |

各命令的补充规则：

- `add` 不带 `--shared` / `--no-shared` 时，新账号默认不共享，已有账号保持原设置。
- `add` 不带 `--proxy` 时，新账号默认为 `inherit`，已有账号保持原设置。
- `init` 修改 `root` 时，只要已有登记账号就判冲突，工具不会自动搬迁账号目录。
- `init` 修改 `bin_dir` 时，删除旧目录里的受管启动命令，在新目录重新生成。
- 从 `config.json` 里去掉某个账号后执行 `apply`，处理方式与 `remove` 相同。
- `apply -f` 的额外规则：
  - 与其它写命令一样，先生成计划，有冲突时什么都不写，包括 `config.json`；
  - 文件里的 `root` 与当前配置不同、并且当前已有登记账号时，按 `init` 的规则判冲突；
  - 文件里有两个只差大小写的账号名时，返回 1；
  - 文件把已有账号只改了大小写（例如 `work` 改成 `Work`）时判冲突：本工具不支持改名，在区分大小写的文件系统上这样改会新建另一份目录和启动命令；
  - `managed_links` 是工具内部状态，一律忽略文件中的值：按账号名（不区分大小写）沿用当前配置中的值，新账号从空列表开始。
- `init` 修改 `shared.dir` 时，先删除各账号 `managed_links` 里、仍指向旧共享目录的软链，再按新目录重建。
- `apply -f` 导入的文件修改了 `bin_dir` 或 `shared.dir` 时，处理规则与 `init` 相同。

#### 5.1.3 代理

| 取值 | 启动命令中的行为 |
| ---- | ---- |
| `inherit`（默认） | 不改动任何代理变量，沿用启动时 shell 里的值 |
| `off` | 清除以下 8 个变量：`HTTPS_PROXY`、`HTTP_PROXY`、`ALL_PROXY`、`NO_PROXY` 的大写和小写形式 |
| 端口，如 `7901` | 展开为 `http://127.0.0.1:7901`，之后按下面的 URL 规则处理 |
| URL | 设置 `HTTPS_PROXY`、`HTTP_PROXY` 的大写和小写形式；socks 协议再加设 `ALL_PROXY`；非 socks 协议则清除继承来的 `ALL_PROXY`（大小写），因为 curl 等程序会把它用于所有协议，父进程里另一个 `ALL_PROXY` 可能把部分流量带到别的代理；`NO_PROXY` 和 `no_proxy` 各自在原有值后追加 `localhost,127.0.0.1,::1`，不覆盖用户原来的值；原值为空时不留开头的逗号 |

设置为 socks 地址（`socks5://`、`socks5h://`）时照常接受，但在 stderr 给出警告，退出码不变。触发入口为 `add --proxy`、`proxy`、`migrate-default --proxy`，以及 `apply -f` 文件中每个 socks 账号。原因是实测 Codex 拿到 socks 地址时，大部分请求仍按 HTTP CONNECT 发送（见 §10），只有同时支持 HTTP 的端口才能正常工作。

URL 校验规则：

- 协议只允许 `http`、`https`、`socks5`、`socks5h`；
- 必须带主机和端口，端口范围 1–65535；
- 不允许带路径；
- 不允许带用户名和密码，因为启动命令是 0755 权限的明文文件。

校验失败时，命令行参数不合法返回 2，配置文件中的值不合法返回 1。

#### 5.1.4 启动命令 `<bin-dir>/codex-<名称>`

- 用 POSIX sh 编写，权限 0755。模板加渲染的结构方便二期增加 cmd 模板。
- 所有值都用 `shlex.quote` 转义；路径写展开后的绝对路径。
- 执行顺序：
  1. 检查账号目录是否存在。不存在时向 stderr 打印“账号目录不存在：<路径>”并返回 1，不把 codex 那条难懂的报错直接丢给用户。
  2. `export CODEX_HOME=...`。
  3. 按 §5.1.3 处理代理变量。
  4. `exec codex "$@"`。`PATH` 里找不到 `codex` 时返回 127。
- 不会出现自引用：账号名必须以字母或数字开头，不可能生成名为 `codex` 的启动命令，所以启动命令里的 `codex` 不会找回它自己。

#### 5.1.5 默认目录迁移 `migrate-default`

下文记号：S 为源位置（默认 `~/.codex`），T 为目标目录 `<root>/<名称>`，B 为 copy 模式下的备份目录 `<S>.multi-codex-bak.<时间戳>`（与 S 在同一目录，改名是原子操作）。

**事务记录**

迁移开始前，把以下信息原子写入 `migrate-journal.json`：

- S、T、B 的路径；
- 模式（rename 或 copy）；
- 阶段；
- 开始时间；
- 本次命令的全部参数：名称、`--source`、`--copy`、`--keep-backup`、`--proxy`、`--skip-process-check`、`--accept-relogin`，以及是否需要在完成时提示重新登录（`relogin_needed`，见 `docs/bugfix/fix-keyring-migration.md`）。

此后每完成一步就更新一次记录。

续跑时，参数一律以记录为准。命令行传入的名称或 `--source` 与记录不同时，返回 2，并提示先完成记录中的那次迁移。其它参数如果与记录不同，只给出警告，仍按记录执行。事务记录只用来提供**路径、模式和参数**；重跑时**真实进度以文件系统的实际状态为准**，阶段字段只在实际状态有歧义时用来区分。这样即使进程在“动作已完成、记录还没更新”之间被杀，重跑也能判断正确。

| 阶段 | 含义 |
| ---- | ---- |
| `planned` | 预检和占用检查已通过，尚未移动数据 |
| `copying` | 仅 copy 模式：正在复制，T 可能不完整 |
| `moved` | rename 模式：S 已改名为 T；copy 模式：T 已复制完成并校验一致 |
| `parked` | 仅 copy 模式：S 已改名为 B |
| `linked` | S 位置已建好兼容软链 |
| `registered` | 账号已登记，启动命令已生成，只剩清理 |
| `rolled-back` | 回滚已把数据还原到 S，只剩删除 T 的副本 |

模式从 rename 切换到 copy（遇到 EXDEV）之前，先把 `mode=copy` 写进记录，然后再开始复制。

**第 1 步：预检**（没有事务记录时）

| S 的状态 | T 的状态 | 结果 |
| ---- | ---- | ---- |
| 指向 T 的软链，账号已登记 | 存在 | 输出 already migrated，返回 0 |
| 指向 T 的软链，账号未登记 | 存在 | 补登记，生成启动命令 |
| 真实目录 | 不存在（用 `lexists` 判断，断开的软链也算存在） | 继续迁移 |
| 指向别处的软链，或不存在 | 任意 | 冲突 |
| 任意 | 已存在，但不属于以上前两行 | 冲突 |
| 任意 | 与已登记的另一个账号同名（不区分大小写） | 冲突；即使 S 已是指向该账号目录的软链，只要大小写不同，也不输出 already migrated |

此外，预检还要按 §5.1.1 的第 3、4 步，对“登记完成后的新配置”生成完整的动作列表：启动命令、代理、共享链接，都视为账号目录已经就位。只要有冲突，就在移动任何数据之前返回 3。这样可以保证数据移动之后，登记这一步不会再因为冲突卡住。

预检通过后、占用检查之前，再检查凭据存储模式。凭据存在系统钥匙串时（`keyring`，或者 `auto` 且没有 `auth.json`），迁移会丢失登录，因此返回 3；带 `--accept-relogin` 时照常迁移。详见 `docs/bugfix/fix-keyring-migration.md`。

如果环境变量 `CODEX_HOME` 已设置，并且解析后的真实路径与 S 不同，给出警告：直接执行 `codex` 时，实际使用的是 `CODEX_HOME` 指向的目录，而不是迁移源。

**第 2 步：占用检查**（以下统称“占用检查”，迁移过程中会多次执行）

- 把 S 解析成真实路径 R。一个路径“落在 R 之下”指：它等于 R，或以 `R + "/"` 开头。必须按路径段比较，否则 `~/.codex-shared` 这样前缀相同的路径会被误判。
- 用 `lsof -n -P -F pcfn` 列出所有进程。只要某个进程的工作目录（`fcwd`）、可执行文件（`ftxt`）或打开的文件落在 R 之下，就判定为占用。
  - 本工具自身和 lsof 的 PID 要排除。
  - 如果调用本工具的 shell 的工作目录就在 R 下，报错时要单独提示“当前 shell 的工作目录在源目录中，请先 cd 出去”。
- Linux 上没有 `lsof` 时，改为遍历 `/proc/*/cwd`、`/proc/*/exe`、`/proc/*/fd/*`。
- 判定为占用时返回 4，每个进程输出一行 `pid=… command=… usage=… path=…`：
  - `usage` 取 `cwd`（工作目录）、`executable`（可执行文件）、`mapped`（内存映射）、`fd N`（打开的文件）之一；
  - `path` 是落在源目录下的那个路径；
  - 进程名含 codex 的排在前面。
- `lsof` 没有正常输出时，视为“检查失败”：拒绝迁移，并提示可以用 `--skip-process-check` 跳过。“没有正常输出”指：退出码非 0，并且 stdout 里一个进程记录都没有。
  - Linux 普通用户运行 lsof 时，常会在 stderr 输出无权限读取的告警，同时退出码非 0，所以不能只凭 stderr 判定失败。这条规则需要在Linux 测试机上实测，见 §10。
- 什么时候检查：
  1. 首次通过后记录 `planned`；
  2. 移动数据前立即再查一次；
  3. copy 模式在 park 之前还要再查一次（首次运行和续跑都要）。复制和计算哈希可能要几分钟，这期间写进源目录的数据不会出现在目标里；如果这时有占用，就停在 `moved`，返回 4。

**第 3 步：移动数据**

- **rename 模式**：S 与根目录的 `st_dev` 相同，并且没有指定 `--copy` 时使用。
  - 用 `os.rename(S, T)` 完成移动，然后记录 `moved`。
  - 遇到 EXDEV（跨设备）时，先把模式改为 copy 写入记录，再改走 copy 模式。
- **copy 模式**：
  1. 记录 `copying`，然后按文件清单逐个复制到 T。
     - socket、FIFO、设备文件属于运行时产物，跳过，并在日志中列出。
     - 软链按软链本身复制，不复制它指向的内容。
  2. 比对清单与 SHA-256。清单包括普通文件、目录和软链，逐项比较相对路径、类型、大小、哈希、软链目标。
  3. 校验一致时记录 `moved`；不一致时删除 T，S 保持不动，返回 1。
  4. 再做一次占用检查。通过后执行 `os.rename(S, B)`，然后记录 `parked`。**从 S 被改名为 B 的那一刻起，再也不删除 T。**

**第 4 步：建兼容软链**

- 在 S 位置创建指向 T 的绝对路径软链，然后记录 `linked`。
- 创建时如果 S 已存在，按以下两种情况处理：
  - S 已经是指向 T 的软链：视为上次已建成，直接继续；
  - S 是其它东西（例如常驻的 app-server 又写入了 `~/.codex`）：不回滚，报错并说明以下几点：
    - 数据已完整位于 T；
    - 能查到时，列出是哪个进程重建了 S；
    - 处理办法：关闭该进程，把重建出来的 S 移走，然后重跑同一命令，迁移会接着完成。

**第 5 步：登记与清理**

1. 登记账号，生成启动命令，记录 `registered`。
2. copy 模式下，如果没有指定 `--keep-backup`，删除 B。删除失败只打印警告，不影响迁移结果。
3. 删除事务记录。

**重跑时按实际状态续跑**（有事务记录时）

按从上到下的顺序，取第一条匹配的规则。凡是会删除 T 或从头重来的行，都要求 B 不存在：只要 B 存在，原始数据就在 B 里，绝不能丢掉对它的记录。

| 编号 | 实际状态 | 视为 | 续跑动作 |
| ---- | ---- | ---- | ---- |
| 1 | 阶段是 `rolled-back` | 回滚未完成 | 如果 S 不存在而 B 存在，先执行 `os.rename(B, S)`。然后确认 S 是真实目录、B 不存在，再删除 T（T 是软链时只删链接）和事务记录，按“没有事务记录”从第 1 步重新开始。S 不是真实目录时，按第 10 行处理 |
| 2 | S 是指向 T 的软链 | `linked` | 执行第 5 步；已登记则跳过登记，只做清理 |
| 3 | S 不存在，B 存在，T 存在 | `parked` | 执行第 4 步（T 不存在只可能是人工删除，按第 10 行处理，避免在 S 建出断链） |
| 4 | S 是真实目录，B 存在 | 已 park，但 S 被重建 | 按第 4 步“S 是其它东西”的规则报错，不做任何改动 |
| 5 | S 不存在，B 不存在，T 存在，模式为 rename | `moved` | 执行第 4 步 |
| 6 | S 是真实目录，T 不存在，B 不存在 | `planned` | 从第 1 步重新开始 |
| 7 | S 是真实目录，T 存在，B 不存在，模式为 copy，阶段为 `planned` 或 `copying` | 复制中断 | 删除这份不完整的 T，从第 1 步重新开始 |
| 8 | S 是真实目录，T 存在，B 不存在，模式为 copy，阶段为 `moved` | 已复制，尚未 park | 先做占用检查，再重新校验清单与哈希。通过则继续 park；不通过则删除 T，从第 1 步重新开始 |
| 9 | S 是真实目录，T 存在，B 不存在，模式为 rename | S 被重建 | 按第 4 步“S 是其它东西”的规则报错 |
| 10 | 其它组合，例如 S、T、B 都不存在（只会在人工改动后出现） | 无法判断 | 返回 1，列出事务记录内容和 S、T、B 的实际状态，不做自动处理 |

续跑表中“从第 1 步重新开始”的含义：

- 使用事务记录里的参数，重新执行完整预检，包括 R3 要求的登记冲突检查；
- 模式沿用事务记录里的值，不重新按 `st_dev` 判断：一旦用过 copy 模式就继续用 copy，行为只会更保守。事务记录同时保存了命令行的 `--copy`（字段 `copy`）；
- 事务记录保留，在原记录上更新阶段；只有重新预检或占用检查不通过时（此时数据一点都没动），才删除事务记录后停下，免得一条记录挡住其它命令。

**失败回滚**

只针对能捕获到的异常。进程被杀时不会执行回滚，由上面的续跑规则负责收尾。

- rename 模式在第 4 步之前失败：如果 S 为空，执行 `os.rename(T, S)`，然后删除事务记录。
- copy 模式在 park 之前失败：S 没有动过。先确认 B 不存在，再删除 T 和事务记录。如果 B 存在，说明 park 其实已经执行，改按下一条处理。
- copy 模式在 park 之后失败，并且 S 为空时，按以下顺序处理：
  1. 记录 `rolled-back`；
  2. 执行 `os.rename(B, S)`，还原数据；
  3. 删除 T；
  4. 删除事务记录。

  先写记录再动文件，所以无论中断在哪一步，重跑都会匹配到续跑表第 1 行，从中断处接着完成回滚。之后再执行同一命令，能从头正常迁移，不会遇到“目标已存在”的冲突。
- 回滚本身失败时，保留事务记录，输出 S、T、B 的当前状态和建议操作。

#### 5.1.6 共享资源

**配置**

- 全局：`shared.dir` 是共享目录；`shared.items` 是要共享的条目，默认 `AGENTS.md`、`skills`、`rules`、`agents`。
- 每个账号：`shared: true/false`，以及 `managed_links`（本工具在这个账号里建立过的链接名单）。

**开启共享**

逐个处理共享目录中实际存在的条目。共享目录里没有的条目跳过，并给出提示。

| 账号里该条目的状态 | 处理 |
| ---- | ---- |
| 不存在 | 创建软链，把名字加入 `managed_links` |
| 已是指向该共享条目的软链 | 记为 `unchanged`；如果不是本工具建立的，不加入 `managed_links`。带 `--adopt` 时改为接管：加入 `managed_links`，链接不动（`feature-adopt-links.md`） |
| 指向别处的软链、真实文件或真实目录 | 冲突 |

判断“是否指向该共享条目”时，两边都先用 `os.path.realpath` 解析成真实路径再比较，不按字符串比较。

**关闭共享**，或从 `shared.items` 去掉某一项时：只删除 `managed_links` 中记录的、并且仍然指向共享目录的软链。删除只用 `os.unlink` 删链接本身，不碰共享目录里的内容。

#### 5.1.7 与已有 `~/.cx` 目录共存

- 工具只认 `config.json`，不扫描根目录。根目录下已有的目录不会被自动当成账号。
- 登记已有目录的方法是执行 `add <已有目录名>`，不会改动目录里的内容。
- 生成的启动命令名为 `codex-<名称>`，与 shell 里已有的 `codex.<名称>` 别名名字不同，可以并存。
- 登记后开启共享：
  - 已有的正确软链记为 `unchanged`；
  - 账号里的真实目录（例如某个账号自带的 `skills` 目录）判为冲突，整条命令返回 3，不做任何修改。这是预期行为，由用户自己决定如何处理那个目录。

#### 5.1.8 一键安装 `install.sh`

- **依赖**：`python3`（不低于 3.8）、`tar`；远程安装还需要 `curl` 或 `wget`。
- **安装方式**：
  - 本地：在克隆的仓库里执行 `./install.sh`；
  - 远程：`curl -fsSL <raw>/install.sh | sh -s -- [参数]`。
    - 默认下载最新 release tag 的源码包；还没有任何 release 时报错退出，并提示可以设置 `MULTI_CODEX_REF=main` 安装主分支。
    - 用 `MULTI_CODEX_REF` 指定其它 tag 或分支。
    - 用 `MULTI_CODEX_TARBALL` 覆盖整个下载地址，测试时指向本地的 `file://` 包。
- **安装内容**：把 `src/multi_codex` 复制到 `<prefix>/share/multi-codex`，生成 `<prefix>/bin/multi-codex`。`--prefix` 默认为 `~/.local`。
- **重复执行**：比较待安装文件树与已安装文件树的哈希。一致时输出 unchanged；不一致时按以下步骤替换，任何一步失败都能回到旧版本：
  0. 先处理上次中断的残留：安装目录不存在而 `.old` 存在时，把 `.old` 改回正式目录名；两者都存在时，删除 `.old`；
  1. 把新版本解到同级临时目录；
  2. 把旧的安装目录改名为 `.old`；
  3. 把临时目录改名为正式目录名；
  4. 删除 `.old`。
- **参数**：
  - `--config <文件>`：安装后执行 `multi-codex apply -f <文件>`，退出码沿用 apply 的退出码；
  - `--uninstall`：只删除工具本身，配置、账号目录和已生成的启动命令都保留。启动命令不依赖本工具，卸载后仍能使用；
  - `-h` / `--help`：打印全部用法，返回 0。
- `PATH` 里没有 `<prefix>/bin` 时只打印提示，不修改 rc 文件。
- **另一条安装渠道**：`pyproject.toml` 声明了 `multi-codex` 命令入口，可以用 `pipx install git+https://github.com/jakoes-wu/multi-codex` 安装。README 写明两种方式，推荐安装脚本。

### 5.2 接口变更

本项目是新增的对外接口，没有存量兼容问题。对外契约如下。

**命令行**：§5.1.2 中的全部子命令与参数。

**退出码**

| 退出码 | 含义 |
| ---- | ---- |
| 0 | 成功，或已处于目标状态 |
| 1 | 运行错误：IO 错误、配置文件内容不合法、复制校验失败、锁被占用 |
| 2 | 命令行参数不合法，包括账号名和代理值 |
| 3 | 存在冲突，本次没有做任何修改 |
| 4 | 迁移源目录正被占用 |

**配置文件 `config.json`**（`version: 1`）

```json
{
  "version": 1,
  "root": "~/.cx",
  "bin_dir": "~/.local/bin",
  "shared": {"dir": "~/.codex-shared", "items": ["AGENTS.md", "skills", "rules", "agents"]},
  "accounts": {
    "work": {"proxy": "http://127.0.0.1:7901", "shared": true, "managed_links": ["skills"]},
    "personal": {"proxy": "inherit", "shared": false, "managed_links": []}
  }
}
```

- `proxy` 的取值为 `inherit`、`off` 或 URL。命令行里输入的端口号会在写入前展开成 URL。
- `managed_links` 是工具内部状态。用 `apply -f` 导入时，文件中的值一律忽略，按账号名沿用当前配置中的值（见 §5.1.2）。`examples/config.example.json` 中不写这个字段。

**启动命令**：文件名固定为 `codex-<名称>`，第 2 行是带账号名的受管标记。

**事务记录** `migrate-journal.json`：内部格式，不对外承诺稳定。

本项目没有 `docs/reference/*`，不涉及 reference 章节的 sibling 回补检查。

## 6. 备选方案与决策

- **启动入口用 shell 别名**：只在交互式 shell 中生效，还要编辑用户的 rc 文件；独立命令脚本在任何环境里都能用。按用户选择，采用独立命令脚本。
- **迁移前先完整备份**：`~/.codex` 可能有几个 GB。同盘 rename 本身是原子的，所以不做备份；copy 模式把源目录改名为备份，数据自然保留下来，不额外占用空间。
- **中断后从头回滚，还是按记录接着做**：数据一旦移动完成，完整数据就已经在目标目录，接着做风险更小。所以中断后重跑采用接续；回滚只在异常能捕获、并且源位置仍然为空时执行。
- **配置格式用 TOML**：Python 3.8 无法写 TOML，所以采用 JSON。
- **一期同时支持 Windows**：用户手上没有 Windows 设备，仓库也还没推到 GitHub，一期的 Windows 代码无法验证；它新增的风险又集中在迁移路径上。所以放到二期，见 §11。

## 7. 影响分析

- **对 Codex 本身**：只通过 `CODEX_HOME` 和代理环境变量影响 Codex，不修改 Codex 的数据文件。迁移后，旧路径的访问经兼容软链落到新目录，数据库里记录的绝对路径仍然可以访问。
- **直接执行 `codex`（不带账号）**：
  - 没有设置 `CODEX_HOME` 时，迁移后默认目录变成兼容软链，直接执行 `codex` 仍然使用被迁移的那个账号；
  - 设置了 `CODEX_HOME` 时，以它指向的目录为准，迁移时会给出警告。
- **正在运行的 Codex**：占用检查覆盖工作目录、可执行文件和打开的文件，本机实测能查到 IDE 扩展里的 app-server 和浏览器扩展宿主；Codex 桌面端未实测。检查通过后如果又有进程重建了源目录，迁移会停在“数据已移动、软链未建”，并给出明确提示，不会进入不确定状态。
- **用户已有文件**：不受本工具管理的同名启动命令、真实的共享条目、用户自己建的软链，都不会被覆盖或删除。
- **运行时开销**：
  - 没有常驻进程；
  - 启动命令只多一次 `exec`，开销可以忽略；
  - 同盘迁移是一次 rename，耗时与目录大小无关；
  - 跨盘复制期间临时占用一份账号目录大小的额外空间，还要把全部文件读一遍计算 SHA-256；
  - macOS 上 copy 模式不保留扩展属性（xattr），因为 Python 在 macOS 上没有 `os.listxattr`。
- **代理**：只作用于通过对应启动命令启动的进程，不影响系统全局，也不影响其他账号。`inherit` 保持用户原有环境不变；`off` 让该账号在 shell 设置了全局代理时也不走代理。

## 8. 回归测试

**测试环境**

- 所有用例用 unittest 实现，在临时 HOME 中运行。
- 用一个假的 `codex` 替代真实程序，它把收到的参数、`CODEX_HOME` 和全部代理变量写到文件里，供断言检查。
- CI 平台与版本：ubuntu 上跑 Python 3.8 和 3.12；macOS 上跑 3.10 和 3.12。GitHub 的 macOS arm64 runner 能否安装 3.8 未验证，所以 macOS 不跑 3.8。
- 发布前在两台真机上完整跑一遍：开发机（macOS）和 Linux 测试机（Ubuntu 20.04）。

**用例**

1. **幂等**：`init`、`add`、`proxy`、`apply`、`remove` 各连续执行两次。第二次所有动作都是 unchanged，退出码为 0，文件的 mtime 不变。
2. **启动命令**：执行 `codex-<名称> --x "a b"`，检查假 codex 收到的参数、`CODEX_HOME` 和代理变量：
   - 父环境带代理变量时，`off` 让 8 个变量全部消失，`inherit` 原样保留；
   - 设置 URL 时，`NO_PROXY` 在原有值后追加；
   - 账号目录被删除后执行启动命令，返回 1，并提示“账号目录不存在”；
   - 账号目录路径含空格和单引号时，仍能正确启动。
3. **代理解析**：
   - `7901`、`socks5://127.0.0.1:1080`、`off`、`inherit` 都解析正确，其中只有 socks 设置了 `ALL_PROXY`；
   - 以下非法值在命令行中返回 2，写在配置文件里时 `apply` 返回 1：端口 0、端口 70000、`ftp://` 协议、带路径的 URL、带用户名密码的 URL。
4. **冲突**：以下情况都返回 3，并且执行前后的文件清单完全相同：
   - 同名启动命令没有受管标记，包括名为 `codex-hud` 的文件；
   - 共享条目位置是真实目录；
   - 共享条目位置是指向别处的软链；
   - 迁移目标已存在，包括断开的软链；
   - 迁移目标与已登记账号同名，只是大小写不同；
   - 迁移时 `<bin_dir>/codex-<名称>` 已被一个不受管的文件占用：在预检阶段就返回 3，源目录没有移动；
   - 已有登记账号时，用 `init` 修改 `root`。
5. **账号名**：`-x`、`.hidden` 返回 2；`add Work` 之后再 `add work`，判定为同一个账号。
6. **迁移：同盘 rename**：
   - 迁移后清单一致，源位置变成指向目标的软链；
   - 再执行一次，输出 already migrated；
   - 源目录中原有的软链和权限位都保留。
7. **迁移：中断后续跑**：
   - **注入方式**：代码中预留两个测试钩子，都通过环境变量设置。`MULTI_CODEX_TEST_FAIL_AT=<注入点>` 在指定点抛出可捕获的异常，用来触发回滚。`MULTI_CODEX_TEST_CRASH_AT=<注入点>` 如下所述，两者可以同时设置，用来模拟“回滚过程中被杀”。测试在子进程中运行迁移，到达注入点时子进程调用 `os._exit(137)`，模拟进程被杀：不执行回滚，也不执行任何清理。
   - **注入点**：每个动作都设两个，一个在“动作完成、记录未更新”，一个在“记录已更新”。动作包括写 `planned`、复制中途、复制完成、rename 到 T、rename 到 B、建软链、登记、删除 B。
   - 回滚过程也设注入点：写 `rolled-back` 之后、B 改回 S 之后、删除 T 之后。
   - 每个注入点之后，重跑同一命令，最终状态与一次跑完完全相同；回滚中断的情况下，重跑先完成回滚，再从头完成迁移。
   - 在“rename 到 B 之后、写记录之前”被杀，然后由测试代码重建 S：重跑匹配续跑表第 4 行，报错，T、B 和事务记录都不变。
   - 续跑时不带 `--keep-backup`（首次运行带了）：B 仍然保留，并输出参数不一致的警告。
   - 续跑时换了账号名：返回 2。
   - 覆盖续跑表的每一行，其中第 10 行（例如 S、T、B 都不存在）返回 1，并且不做任何改动。
   - 另外单独测试一种没有事务记录的状态：“S 已是指向 T 的软链，但账号未登记”，重跑后补登记。
   - 用模拟的 `os.rename` 抛出 EXDEV：记录中的模式先变为 copy，再开始复制；在复制中途中断后重跑，能够收敛。
8. **迁移：copy 模式**：
   - 源目录里放一个 socket 和一个 FIFO：它们被跳过并在日志中列出，其余内容校验通过；源目录被改名为备份，全部完成后备份被删除；
   - 加上 `--keep-backup`：备份保留；
   - 人为让校验失败：目标副本被删除，源目录完好；
   - 在 `parked` 之后，用可捕获的异常让建软链失败：B 被改名回 S，T 被删除，事务记录被删除；再次执行同一命令，能从头完成迁移。
   - 同上，但让删除 T 这一步也失败：事务记录保留在 `rolled-back`；重跑时先删除 T，再完成迁移。
   - 在复制完成、park 之前让源目录被占用：返回 4，停在 `moved`；解除占用后重跑，重新校验通过，完成迁移。
9. **迁移：占用检查**：
   - 测试子进程分别以两种方式占用源目录：打开其中的 sqlite 文件；把工作目录设在源目录下。两种情况迁移都被拒绝，返回 4，并列出 PID；
   - 进程打开的是前缀相同但属于不同目录的路径（例如 `<源>-shared/x`）：不算占用；
   - 用假的 `lsof` 模拟检查失败：迁移被拒绝；加上 `--skip-process-check` 后放行；
   - Linux 上 `PATH` 里没有 `lsof` 时，改走 `/proc` 路径，同样能查到占用。
10. **迁移：源位置被重建**：在第 3 步完成、第 4 步开始前，由测试代码创建源目录。迁移报错，数据完整留在目标目录，事务记录停在 `moved`；删掉重建出来的目录后重跑，迁移完成。
11. **环境变量警告**：
    - 把 `CODEX_HOME` 设为另一个目录后执行迁移：输出警告，迁移照常完成；
    - 设置 `CODEX_SQLITE_HOME` 或 `CODEX_API_KEY` 后执行 `migrate-default` 和 `list`：都输出警告。
12. **共享资源**：
    - `shared` 在 true、false、true 之间切换：工具建立的软链被正确创建和删除，`managed_links` 同步变化；
    - 用户自己建的、指向同一个共享条目的软链记为 unchanged，关闭共享时不被删除；
    - 从 `shared.items` 去掉一项后，对应的受管软链被删除，共享目录里的内容完好。
13. **登记已有目录**：构造与 §5.1.7 相同的目录布局：根目录下有 `.migration` 和两个已有账号目录，其中一个账号带指向共享目录的软链，另一个带真实的 `skills` 目录。验证：
    - `add` 两个账号后，目录内容不变；
    - `.migration` 没有被当成账号；
    - 对带真实 `skills` 目录的账号开启共享，返回 3。
14. **init / apply / remove**：
    - 修改 `bin_dir` 后，旧目录的受管启动命令被删除，新目录重新生成；
    - `apply -f` 导入的配置少了一个账号：该账号的启动命令被删除，目录保留；
    - `remove` 一个未登记的名字：如果有同名的孤儿启动命令，一并删除，返回 0；
    - `apply -f` 的文件带有冲突：`config.json` 保持不变，返回 3；
    - `apply -f` 的文件改动了 `root`，而当前已有账号：返回 3；
    - `apply -f` 的文件里有只差大小写的账号名：返回 1；
    - `apply -f` 的文件里 `managed_links` 被写成了其它值：导入后仍沿用原来的值，关闭共享时不会删除用户自己建的软链；
    - `init` 修改 `shared.dir`：指向旧目录的受管软链被删除，并按新目录重建；
    - 有未完成的迁移事务记录时，`add` 返回 1，`list` 可以正常执行。
15. **互斥锁**：
    - 持有锁的子进程还在运行时，另一个写命令返回 1，并说明锁被哪个 PID 持有；
    - 该子进程被 SIGKILL 杀掉后，下一个写命令能立即拿到锁；
    - 锁文件里残留一个已被复用的 PID 不影响加锁。
16. **install.sh**：
    - 用 `MULTI_CODEX_TARBALL=file://...` 走远程安装路径；
    - 在临时 prefix 下重复安装两次，第二次输出 unchanged；
    - 带 `--config` 安装后，apply 已执行；
    - 用测试钩子 `MULTI_CODEX_TEST_CRASH_SWAP=1` 让安装在第 2、3 步之间退出（模拟被杀），下次安装先把 `.old` 恢复，再正常完成；
    - 残留 `.old`、安装目录不存在时再次安装：先恢复，再正常完成；
    - 没有 release 且没有设置 `MULTI_CODEX_REF` 时，报错并给出提示；
    - `--uninstall` 之后工具消失，账号目录、配置和已生成的启动命令都还在，启动命令仍能使用；
    - `--help` 返回 0，参数不合法时返回非 0；
    - shellcheck 检查 `install.sh` 和生成的启动命令，都没有告警。

## 9. 日志 / 观测点

- 每个动作输出一行，固定前缀 `[multi-codex]`，格式为 `<动作> <对象> <路径> (<原因>)`。命令行输出统一用英文，便于开源用户检索和提交 issue。例如：
  - `[multi-codex] unchanged launcher ~/.local/bin/codex-work`
  - `[multi-codex] conflict shared-link ~/.cx/work/skills (already exists as a real directory)`
- 错误输出到 stderr，内容包括：
  - 阶段（`phase=`）：迁移为 `precheck`、`busy-check`、`move`、`verify`、`park`、`link`、`register`、`cleanup`、`resume`、`rollback` 之一；收敛引擎为动作对象类型（`config`、`account-dir`、`launcher`、`shared-link`）；
  - 相关路径；
  - 底层异常的类型和信息。
- 迁移失败或中断时，输出以下内容：
  - 源、目标、备份三者的当前状态：存在、不存在，或是软链；
  - 事务记录的位置和所处阶段；
  - 下一步应执行的命令。
- `list` 显示每个账号的状态：
  - 启动命令：`ok`、`missing`、`conflict` 或 `stale`（内容与配置不一致）；
  - 账号目录：`ok` 或 `missing-dir`。
- 续跑表第 4、9、10 行报错时，除了输出当前状态，还要给出放弃这次迁移的手工收尾步骤：
  1. 说明完整数据在哪个目录（T 或 B）；
  2. 如何把它恢复到 S；
  3. 删除事务记录的命令。

  README 中写同样的步骤。
- 事务记录损坏（不是合法 JSON 或缺少字段）时，包括 `list` 在内的所有命令都返回 1，并提示用户先确认完整数据在哪里、恢复到源路径、再删除事务记录；不自动猜测进度。
- 有未完成的事务记录时，任何命令开头都先打印一行提示：“迁移未完成，请重跑 `migrate-default <名称>`”。

## 10. 开放问题

- **代理行为（2026-09-29 已在Linux 测试机实测，结论已写进 README）**：

  测试环境为 Ubuntu 20.04 与 codex-cli 0.159.0。用本工具生成的启动命令，登录态的账号执行 `codex exec`。一个会记录连接的代理同时支持 HTTP CONNECT 和 SOCKS5，并把流量转发给本机已有的代理；同时每 0.1 秒采样一次 codex 进程的全部 TCP 连接。

  | 场景 | 结果 |
  | ---- | ---- |
  | 本工具设置 http 代理 | 16 个连接全部经过代理（目标为 chatgpt.com、ab.chatgpt.com、*.oaiusercontent.com），没有直连；exec 正常回答 |
  | `off`，父进程带着代理变量 | 代理收到 0 个连接，codex 直连，正常回答 |
  | inherit，父进程只设置 `ALL_PROXY=http://…` | Codex 读取了 ALL_PROXY，14 个连接全部经过代理 |
  | `ALL_PROXY` 或本工具设置为 `socks5h://…` | 14 个连接中只有 1 个用 SOCKS5，另外 13 个仍按 HTTP CONNECT 发往该端口 |

  结论：
  - Linux 上代理有效，`ALL_PROXY` 会被读取；
  - codex 0.159.0 的 `responses_websockets` 功能开关状态为 removed，采样期间没有任何绕过代理的连接，所以不存在走直连的 WebSocket；
  - SOCKS 代理只有在端口同时支持 HTTP 时才能正常使用，README 已建议优先使用 HTTP 代理。

  本工具仍然接受 socks 地址，因为 mixed 端口很常见；2026-09-29 用户决定在设置 socks 地址时给出警告（§5.1.3）。
  macOS 上此前的依据是一次手工代理配置核验（在代理软件的连接表中看到 Codex 流量从指定端口进入），没有用本工具生成的启动命令重新测过。
- **Linux 上 lsof 的表现**：普通用户运行 lsof 时的退出码和 stderr，需要在Linux 测试机（Ubuntu 20.04）上实测，确认“检查失败”的判定规则不会误伤。编码阶段完成，不阻塞开工。
- **启动命令是否清除 `CODEX_SQLITE_HOME`、`CODEX_API_KEY`、`CODEX_ACCESS_TOKEN`**：一期只给出警告。是否在启动命令里清除，需要看这三个变量在实际使用中的情况，待用户决定。
- **HTTPS_PROXY 实测记录**：“macOS 上 HTTPS_PROXY 生效”依据的是此前一次手工代理配置核验：在代理软件的连接表中，看到对 `chatgpt.com` 的连接从账号对应的监听端口进入。发布前还要用本工具生成的启动命令重新测一次，并把测试方法写进 README。
- **并发安装**：`install.sh` 开头会清理所有残留的 `.multi-codex-stage.*` 临时目录。两个安装进程同时运行时，可能删掉对方正在使用的临时目录。一期不处理，因为不支持并发安装。
- **GitHub 地址**：已确定为 `https://github.com/jakoes-wu/multi-codex`（2026-09-29 用户提供 owner `jakoes-wu`），原先的 `jakoes-wu` 占位已全部替换。

## 11. 二期：Windows 支持（不在本期编码范围）

**前置条件**

- 仓库已推到 GitHub，可以使用 windows CI runner。
- 已在 Windows 实机上确认 Codex 会读取代理环境变量。

以下是已经确定的设计要点。一期代码把平台差异都收在 `platform.py` 和启动命令模板里，二期只需要新增代码，不需要重构。

**默认路径**

| 项目 | 路径 |
| ---- | ---- |
| 迁移源 | `%USERPROFILE%\.codex` |
| 账号根目录 | `%USERPROFILE%\.cx` |
| 启动命令目录 | `%USERPROFILE%\.local\bin` |
| 工具状态目录 | `%APPDATA%\multi-codex\` |
| 工具安装位置 | `%LOCALAPPDATA%\multi-codex` |

依据：Codex 官方环境变量页面说明，根状态目录默认为 `~/.codex`，该页面没有针对 Windows 单独说明。二期需要在 Windows 实机上核实。

**启动命令 `codex-<名称>.cmd`**

- 第 1 行 `@echo off`，第 2 行是 `rem` 形式的受管标记。
- 用 `setlocal` 加 `set "VAR=值"` 设置变量；Windows 的环境变量名不区分大小写，所以只写大写形式。
- 最后一行执行 `codex %*`，不用 `call`，因为 `call` 会让参数里的 `^` 被重复处理。
- 路径或 URL 中含 `%`、`"`、换行时，在生成阶段直接拒绝，返回 2。

**链接**

- 目录用 junction，通过 `cmd /c mklink /J` 创建，不需要管理员权限。
- 文件用符号链接，需要开启开发者模式或使用管理员权限；没有权限时返回 1 并给出提示，不退而复制文件。
- 相关的 Python 行为（出处：<https://docs.python.org/3/whatsnew/3.8.html>）：
  - Python 3.8 起，`os.readlink` 能读取 junction；
  - `os.path.islink` 对 junction 返回 False；
  - `shutil.rmtree` 删除 junction 时，不会递归删除它指向的内容；
  - 删除 junction 本身用 `os.rmdir`。

**占用检查**

- 用 `tasklist` 查找 Codex 进程，只要存在就拒绝迁移。原因是 Windows 上无法知道某个进程用的是哪个 `CODEX_HOME`，只能从严处理。npm 安装的 Codex 在 Windows 上的实际进程名（是否为 `codex.exe`）未验证，二期先实测再定。
- rename 时遇到 `PermissionError`，同样按“被占用”处理，返回 4。

**安装脚本 `install.ps1`**

- 依次查找 `py -3`、`python`，要求版本 ≥ 3.8。
- 远程安装命令：`irm <raw>/install.ps1 | iex`。这种写法无法传参数，需要传 `-Config` 等参数时，改用 `& ([scriptblock]::Create((irm <raw>/install.ps1))) -Config x.json`。
- 下载 zip 后用 `Expand-Archive` 解压。
- 支持 `-Config`、`-Uninstall`、`-Help`、`-Prefix`，语义与 `install.sh` 一致。
- 用 PSScriptAnalyzer 做静态检查。

**需要新增的测试**

- cmd 转义：参数中含空格、`&`、`^`，账号名含 `@`、`+`，路径含 `%`。
- junction 的创建、识别和删除。
- 缺少符号链接权限时的报错。
- `codex.exe` 占用检查。
- rename 遇到文件被占用。
- `install.ps1` 的重复安装和卸载。
