# Clash 检查拆分与接口自动查找

> 2026-11-05 注记：代码已落地并合并（PR #41，main `4dbbcda`），随 0.14.0 发布；本机与 Windows 测试机实测通过，第 1 轮代码评审的 2 中 7 低已处理（低 3 记为 §7 第 6 条已知局限）。主要落点：配置 `src/multi_codex/config.py` `NetCheck` / `_parse_net_check`；判定 `src/multi_codex/netcheck.py` `_check_clash` / `read_clash_state`；进程枚举 `src/multi_codex/platform.py` `list_process_argv` / `split_ps_command`；查找 `src/multi_codex/clashfind.py` `find_controller`；命令行 `src/multi_codex/cli.py` `_prepare_clash_find` / `_find_clash` / `_apply_netcheck_changes`。
>
> 2026-11-05 注记（原）：方案已定、代码待落地。基线：main `2dece26`（PR #40 合并后），以下 `file:line` 均指该基线。

## 1. 背景

启动前网络检查（`docs/feature/feature-network-check.md`，PR #40）里的 Clash 检查只有一个开关 `--clash on|off`，开启后要求 Clash / mihomo 同时满足“全局模式”和“TUN 已开启”（`src/multi_codex/netcheck.py:341-374`）。用户提出把这两项拆成两个独立参数分别管理，并要求同时提供“自动找到本机 Clash 控制接口”的功能。

现有事实：

- 账号设置 `NetCheck(level, country, clash: bool)`（`src/multi_codex/config.py:425-450`），解析在 `_parse_net_check`（`config.py:529-544`），`clash` 必须是布尔值。
- 命令行只有 `netcheck NAME ... --clash on|off`（`src/multi_codex/cli.py:302-303`），写入在 `_apply_netcheck_changes`（`cli.py:1695-1720`）。
- 控制接口要用户手动执行 `multi-codex clash-api ADDRESS` 设置（`cli.py:421-429`、`cli.py:1723-1738`）。
- mihomo 的模式只有 `global`、`rule`、`direct` 三种（`~/tmp/src-mihomo/v1.19.32/tunnel/mode.go:38-45`）。
- 本机（macOS）Clash Verge 以服务模式运行，内核进程属主为 root，`ps -axww -o command=` 可看到其完整启动参数：`/Library/Application Support/clash-verge-service/cores/verge-mihomo -d ... -f .../runtime/config.yaml -ext-ctl-unix /var/run/clash-verge-service/users/501/verge-mihomo.sock`；`runtime` 目录普通用户无读权限；用户目录 `~/Library/Application Support/io.github.clash-verge-rev.clash-verge-rev/config.yaml` 里写的 unix 套接字路径实际不存在，`external-controller: 127.0.0.1:9097` 也未监听。
- Windows 测试机 Clash Verge 的内核进程命令行为 `"C:\Program Files\Clash Verge\verge-mihomo.exe" -d C:\Users\86176\AppData\Roaming\io.github.clash-verge-rev.clash-verge-rev -f ...\clash-verge.yaml -ext-ctl-pipe \\.\pipe\verge-mihomo`。
- mihomo 的命令行参数 `-ext-ctl`、`-ext-ctl-unix`、`-ext-ctl-pipe`、`-secret`、`-d`、`-f`（`main.go:61-72`）用 Go 标准 `flag` 包解析；unix 套接字相对路径按 `-d` 目录解析（`hub/route/server.go:260` 的 `C.Path.Resolve`）；unix 套接字与命名管道不校验 secret（`hub/route/server.go:290`、`:321`）。

## 2. 目标 / 非目标

目标：

1. Clash 检查拆成两个独立开关：`--clash-tun on|off`（TUN 已开启，且模式不是 direct）与 `--clash-global on|off`（全局模式）；`--clash on|off` 保留为“两项一起开 / 关”的简写；旧配置 `"clash": true` 视为两项都开。
2. 只开全局、不开 TUN 时，设置命令给出提醒（不拒绝）。
3. 新增 `multi-codex clash-api auto`：从本机正在运行的 Clash / mihomo 内核找到可用的控制接口并写入配置；开启 Clash 检查而接口未设置时自动尝试一次。

非目标：

- 运行中持续监视 Clash（待办 0b，另行决定）。
- 启动时（`netgate` / `gate`）自动查找接口：查找要枚举进程，Windows 上约 1 秒，不放进每次启动。
- 读取其它进程的环境变量（`CLASH_OVERRIDE_EXTERNAL_CONTROLLER*`）、TLS 控制接口（`-ext-ctl-tls`）。
- 修改 Clash 的任何设置。

## 3. 假设与约束

- 仍只经 `GET /configs` 读 `mode` 与 `tun.enable`（沿用 `netcheck.py:341-374` 的读法与报错分类）。
- 0.13.0 之后尚未发版，`net_check.clash` 只存在于用户本机由 main 部署的配置里；兼容读取即可，写出一律用新字段。
- 新字段写出后，旧版 multi-codex 读同一份配置时 `_parse_net_check` 不认识新字段（它只 `get` 已知键，`config.py:529-544`），会当作未开启 Clash 检查；同一台机上新旧版本混用不在支持范围内，CHANGELOG 写明。
- 自动查找只采信“实际 `GET /configs` 返回 200 且含 `mode` 与 `tun.enable`”的候选，不采信配置文件里写的地址本身。

## 4. 涉及模块

| 区域 | 行号锚点（main 2dece26） | 改动类型 | 改动点 |
| ---- | ---- | ---- | ---- |
| `config.py` `NetCheck` | 425-450 | 修改 | `clash` 换成 `clash_tun`、`clash_global` 两个布尔；`active`、`copy`、`to_dict` 同步 |
| `config.py` `_parse_net_check` | 529-544 | 修改 | 读新字段；只有旧字段 `clash` 时兼容为两项同值；新旧字段同时出现时以新字段为准 |
| `netcheck.py` `scope_text` | 73-83 | 修改 | 两项都开写 `clash`，只开一项写 `clash tun` / `clash global` |
| `netcheck.py` `run_checks` | 86-95 | 修改 | 任一项开启就加 `clash` 检查项，传入要求 |
| `netcheck.py` `failure_text` | 158-160 | 修改 | 关闭提示里的 `--clash off` 条件改为任一项开启 |
| `netcheck.py` `_check_clash` | 341-374 | 修改 | 拆出 `read_clash_state(address, secret)` 返回 `(mode, tun)`；按两项要求分别判定；未设置接口的提示改为 `clash-api auto` |
| `clashfind.py` | 新文件 | 新增 | 自动查找：候选收集、配置文件读取、逐个验证 |
| `platform.py` | 523 邻近（`_parse_win32_processes` 之后）追加 | 新增 | `list_process_argv()`：列出所有进程的参数列表 |
| `cli.py` netcheck 参数 | 302-303 | 修改 | 新增 `--clash-tun`、`--clash-global`；`--clash` 帮助改为“两项一起” |
| `cli.py` 子命令示例 | 117-128 | 修改 | netcheck、clash-api 示例补新用法 |
| `cli.py` clash-api 参数 | 422-424 | 修改 | `address` 帮助文字增加 `auto` |
| `cli.py` 只读判定 | 620 | 修改 | 只读条件纳入两个新参数 |
| `cli.py` 加锁前 | 631 与 632 之间（`_blocked_by_migration` 之后、`with WriteLock()` 之前） | 新增 | 调 `_prepare_clash_find(args)`，加锁前跑完查找 |
| `cli.py` `dispatch` clash-api | 772-773 | 修改 | `auto` 时使用预先查到的结果 |
| `cli.py` `_apply_netcheck_changes` | 1695-1720 | 修改 | 合并三个参数；冲突检查；全局无 TUN 提醒；接口未设置时用预先查到的结果 |
| `cli.py` `_clash_api_from_args` | 1723-1738 | 修改 | `auto` 分支 |
| `cli.py` netcheck 写后提示 | 845-849 | 不改 | `scope_text` 已覆盖新写法 |
| `tests/test_netcheck.py` | 255-259、363-460、525-622 | 修改 + 新增 | 见 §8 |
| `tests/helpers.py` `CliTestCase` | 89-106 邻近 | 修改 | 默认设新钩子 `MULTI_CODEX_TEST_CLASH_PROCESSES` 为空列表文件（§7 第 7 条） |
| `README.md` / `README.zh-CN.md` | 191-192、365-375 / 191-192、415-425 | 修改 | 命令表与网络检查一节 |
| `CHANGELOG.md` | 18-19 | 修改 | 同步新开关、`clash-api auto`、兼容说明 |
| `docs/feature/feature-network-check.md` | 文件头注记 | 修改 | 加一行注记指向本方案 |

## 5. 方案

### 5.1 实现要点

#### 5.1.1 设置与兼容

`NetCheck.__init__(level="off", country=DEFAULT_COUNTRY, clash_tun=False, clash_global=False)`（替换 `config.py:436-439`）。

- `active`：`level != "off" or clash_tun or clash_global`。
- `to_dict`：`{"level", "country", "clash_tun", "clash_global"}`，不再写 `clash`。
- `_parse_net_check`（替换 `config.py:541-544`）：
  - `clash_tun` / `clash_global` 存在时必须是布尔，否则 `ConfigError`（报错文字仿 `config.py:543`，带字段名）。
  - 两个新字段都不存在、只有 `clash` 时：`clash` 必须是布尔，两项都取它的值。
  - 新字段与 `clash` 同时存在时忽略 `clash`。

#### 5.1.2 检查判定

`_check_clash(config, need_tun, need_global)`（替换 `netcheck.py:341-374`）：

1. 读接口与报错沿用现有逻辑，拆成 `read_clash_state(address, secret) -> Tuple[str, bool]`（mode 小写、tun.enable 是否为 True），失败抛 `CheckFailure`，报错文字与现在相同（含 401、非 200、响应不像 mihomo）。`CheckFailure` 新增可选属性 `kind`（默认 `"other"`），`read_clash_state` 按失败类别设为 `"unreachable"`（连接 / 超时等 `OSError`、`HTTPException`，以及 `_pipe_get` 自己抛出的 `CheckFailure`，如命名管道超时，`netcheck.py:464`、`:469`）、`"auth"`（401）、`"http"`（其它非 200）、`"not-clash"`（响应缺 `mode` / `tun.enable`）；启动检查只用文字，自动查找按 `kind` 分类。接口未设置时报 `Clash controller is not set; run `multi-codex clash-api auto` or `multi-codex clash-api ADDRESS``。
2. 判定顺序：
   - `need_global` 且 mode 不是 `global` → `Clash mode is <mode>, expected global`（沿用现有文字）。
   - `need_tun` 且 tun 关 → `Clash TUN is off`。
   - `need_tun` 且不要全局、mode 为 `direct` → `Clash mode is direct; TUN would pass traffic through without the proxy`。
3. 通过时说明文字统一为 `mode=<mode>, tun on|off`。

`run_checks`（`netcheck.py:94-95`）：`if check.clash_tun or check.clash_global:` 加检查项，项名仍为 `clash`。

`scope_text`（`netcheck.py:81-82`）：两项都开 `clash`；只开 TUN `clash tun`；只开全局 `clash global`。

`failure_text`（`netcheck.py:159`）：任一项开启时关闭提示带 ` --clash off`。

#### 5.1.3 命令行

- `netcheck` 新增 `--clash-tun on|off`、`--clash-global on|off`；`--clash on|off` 帮助文字改为 “both Clash checks: TUN on and global mode”。
- `_apply_netcheck_changes`（`cli.py:1706-1707` 处）：
  - `--clash` 与 `--clash-tun` / `--clash-global` 同时给出 → `UsageError("--clash sets both Clash checks; use it alone or use --clash-tun / --clash-global")`（退出 2）。
  - `--clash` 设两项；另外两个各设一项。
  - 改完后 `clash_global and not clash_tun` → `warn("global mode without TUN only covers traffic sent to the proxy port; a direct connection is not caught. Add --clash-tun on to cover it")`。每次 netcheck 写命令结束时状态仍是这一组合就提醒（不论本次是否改动了它），与 `cli.py:1717` 的接口未设置提醒同一处理方式。
- `cli.py:620` 只读判定加 `args.clash_tun is None and args.clash_global is None`。

#### 5.1.4 自动查找（`clashfind.py`）

对外函数：`find_controller() -> Tuple[Optional[ClashApi], str, List[str]]`，返回找到的设置、读到的状态说明（如 `mode=global, tun on`）和逐个候选的结果说明；是否打印由调用方按 `-v` 决定。

候选收集，按顺序，去重（以规范化后的地址为键；同一地址出现多次时保留第一份带 secret 的，都不带则保留第一份）：

1. **运行中的进程**：`platform.list_process_argv()` 返回每个进程的参数列表。只处理“程序名（参数 0 的文件名，不区分大小写）含 `mihomo` 或 `clash`”或“参数里带 `-ext-ctl`、`-ext-ctl-unix`、`-ext-ctl-pipe` 之一”的进程；`-f`、`-d` 只在这类进程上读取（`tail -f` 之类的无关进程不碰）。
   - 参数按 Go `flag` 的写法读取：`-name value`、`-name=value`、`--name value`、`--name=value`。
   - 主目录 D：`-d` 的值；它缺省或是相对路径时视为未知（mihomo 缺省用自己的默认主目录、相对路径按进程当前目录解析，`main.go:126-129`，本工具都拿不到）。
   - `-ext-ctl-unix P`：P 为绝对路径直接用；相对路径且 D 已知时用 `D/P`（mihomo 按主目录解析，`hub/route/server.go:260` → `constant/path.go:80-84`）；否则跳过。候选 `unix:P`，secret 为空。
   - `-ext-ctl-pipe P` → 候选 `P`，secret 为空。
   - `-ext-ctl H:P` → TCP 候选。
   - 同一进程的所有 TCP 候选（来自 `-ext-ctl` 或它的配置文件）一律先用该进程的 `-secret`，没有时才用它配置文件里的 `secret`（`-secret` 覆盖整个控制接口的 secret，`main.go:71-72`、`:212-213`）。
   - 配置文件：`-f F` 为绝对路径时用 F；`-f` 为相对路径时跳过（`main.go:160-163` 按当前目录解析）；没有 `-f` 而 D 已知时用 `D/config.yaml`（mihomo 默认配置文件名，`constant/path.go:51`）。文件可读时按第 2 步的读取方式取接口地址与 `secret`，其中 unix 相对路径按 D 解析，D 未知则跳过。
2. **已知配置文件**（只列已在本机或测试机上核实存在的位置）：
   - macOS：`~/Library/Application Support/io.github.clash-verge-rev.clash-verge-rev/config.yaml`
   - Windows：`%APPDATA%\io.github.clash-verge-rev.clash-verge-rev\config.yaml`
   - 这一路没有进程参数可依，假定主目录就是配置文件所在目录（Clash Verge 的 `-d` 即这个应用目录，见 §1 测试机命令行）。
   - 读取方式：只读顶层（行首无缩进）的 `external-controller`、`external-controller-unix`、`external-controller-pipe`、`secret` 四个键，值去掉首尾空白、成对引号与 ` #` 之后的注释；不引入 YAML 库。
3. TCP 候选的主机为空或 `0.0.0.0` 时改为 `127.0.0.1`，为 `::` 时改为 `::1`；改写后主机不是回环地址（`127.0.0.0/8`、`::1`、`localhost`）的候选丢弃——自动查找只找本机 Clash，不把读到的 secret 发往别的主机。远端控制接口仍可用 `clash-api ADDRESS` 手动设置。
4. 每个候选先 `normalize_clash_address`，不合本平台的（如 macOS 上的命名管道）丢弃；按“unix 套接字 / 命名管道在前、TCP 在后”（这两种不需要 secret）的顺序逐个 `read_clash_state`，第一个成功的即结果。失败按 `CheckFailure.kind` 记入说明（`unreachable` → 连不上，附原因；`auth` → `needs a secret`；`http` → `HTTP <码>`；`not-clash` → `not a Clash controller`；`other` → 失败原文），继续下一个。保存时 unix 套接字与命名管道的 secret 一律为空（与 `ClashApi` 约定一致，`config.py:453-460`）。

进程列表 `platform.list_process_argv() -> List[List[str]]`（`platform.py:523` 之后追加）：

- 测试钩子：新增专用变量 `MULTI_CODEX_TEST_CLASH_PROCESSES`（不复用 Windows 占用检查的 `MULTI_CODEX_TEST_PROCESS_LIST`，见 §7 第 7 条），指向 JSON 文件时读它（格式同 `platform.py:485-489` 读的进程列表），把每项 `CommandLine` 拆成参数：Windows 用 `winapi.split_command_line`，POSIX 用 `shlex.split`。
- Windows：复用 `_run_process_query()`（`platform.py:504`）与 `_parse_win32_processes()`（`platform.py:523`），`CommandLine` 用 `winapi.split_command_line` 拆分。
- Linux：读 `/proc/<pid>/cmdline`，按 `\0` 拆分；读不了的进程跳过。
- macOS：`ps -axww -o command=`（不加 `-o pid=` 也可）。`ps` 输出不带引号，路径含空格时无法准确拆分，处理方式：按空格切开后，把不以 `-` 开头的连续片段并回前一个值（`-ext-ctl-unix /var/run/a b.sock` 得到 `/var/run/a b.sock`）；参数 0 取第一个以 ` -` 开头的片段之前的整段。拆错的候选会在第 4 步验证时失败，不会写错配置。
- 枚举失败时 `list_process_argv` 抛出异常（Windows 上是 `_run_process_query` / `_parse_win32_processes` 的 `BusyCheckError`，POSIX 上是 `OSError`，如 `ps` 不存在或执行失败）；`find_controller` 捕获这两类，在结果说明里记一句后继续用配置文件候选。

调用入口：

- 位置：`cli.py:630-631` 的 `_blocked_by_migration` 检查通过之后、`cli.py:632` 的 `with WriteLock()` 之前，调用 `_prepare_clash_find(args)`；它返回 `None` 表示继续，返回整数（找不到时的 1）时主流程直接返回该退出码，`UsageError` 照常由 `cli.py:650-652` 转成退出 2。查找的说明（found 行、`-v` 的逐个候选）都写 stderr。它第一句无条件设 `args.found_clash = None`（`_apply_netcheck_changes` 对所有 netcheck 写命令都会读它），再按下面两种情况决定是否查找；其它写命令不查找。
- `clash-api auto`（`args.address.lower() == "auto"`，与 `off` 的判断方式 `cli.py:1725` 一致）：先检查 `--secret-stdin`，同时给出 → `UsageError`，退出 2，不做查找；再调用 `find_controller`，结果存到 `args.found_clash`；找不到时 `error("no working Clash / mihomo controller found; ...")` 返回退出码 1，并列出试过的候选（不带 secret）。找到后 `info("found Clash controller <地址> (mode=..., tun ...)")`，`_clash_api_from_args` 返回该结果，之后与手动设置走同一条写配置路径（`--dry-run` 时照常只列出 `update config`，不写盘）。
- `netcheck` 开启 Clash 检查且未设置接口时：`_prepare_clash_find` 先做 `--clash` 与 `--clash-tun` / `--clash-global` 的互斥检查（冲突抛 `UsageError`，退出 2，与 `auto --secret-stdin` 一样先于查找；`_apply_netcheck_changes` 里保留同一检查，供不经此处的路径使用），再看账号是否已登记（`load_config()` 里找不到该账号就不查找，交给 `dispatch` 照常报未登记）；然后在命令为 `netcheck`、参数里有任何一个 Clash 开关为 `on`、且 `load_config()` 的 `clash_api is None` 时同样调用；`_apply_netcheck_changes` 改完后若 `config.clash_api is None and args.found_clash` → 写入并 `info`；仍找不到则保留现有提醒（`cli.py:1717-1720`），提示文字改为 `clash-api auto`。
- `--dry-run` 时同样查找（只读），打印 found 行，配置变化按现有 dry-run 输出列出，不写盘。
- 查找放在加锁之前、迁移检查之后：Windows 进程枚举最长可等 90 秒（`platform.py:473`），既不长时间占着写锁，迁移未完成时也不白跑一次查找。

#### 5.1.5 文档

- README 两份：命令表 `netcheck` 行加 `--clash-tun on|off`、`--clash-global on|off`；`clash-api` 行加 `auto`；网络检查一节的表格改为三行（`--clash-tun on`、`--clash-global on`、`--clash on` 两项），写明只开全局的局限；示例加 `multi-codex clash-api auto`。
- CHANGELOG `Unreleased`（`CHANGELOG.md:18-19`）同步，并写“`"clash": true` 读作两项都开，下次改动配置时改写为新字段；旧版本读到新字段会当作未开启 Clash 检查”。

### 5.2 接口变更

| 接口 | 变更 | 兼容性 |
| ---- | ---- | ---- |
| `config.json` `accounts.*.net_check.clash_tun` / `clash_global` | 新增 | 旧字段 `clash` 仍可读 |
| `config.json` `accounts.*.net_check.clash` | 废弃（只读不写） | 下次改动了配置的写命令保存时改写为新字段；没有改动时不写盘（`accounts.py:128`），旧字段继续留在文件里，读法不变 |
| `netcheck --clash-tun on\|off`、`--clash-global on\|off` | 新增 | — |
| `netcheck --clash on\|off` | 语义不变（两项一起） | 与新开关同时给出时退出 2 |
| `clash-api auto` | 新增 | 地址 `auto` 以前会被当作主机名 `auto` 而缺端口报错，无人能依赖 |
| `netcheck NAME` 输出的检查范围 | 新增 `clash tun`、`clash global` 两种写法 | 两项都开时仍是 `clash` |
| `clash` 检查项通过时的说明 | 仍为 `mode=<mode>, tun on\|off` | 原来恒为 `mode=global, tun on`，两项都开时文字不变 |
| 失败说明 | 新增 direct 模式拒绝文字；未设置接口的提示改为推荐 `clash-api auto` | 退出码不变（5） |

本仓库没有 `docs/reference/` 目录，不涉及 reference sibling 回补检查。

## 6. 备选方案与决策

- 设置写成列表 `"clash": ["tun", "global"]`：一个字段能表达全部组合，但与旧布尔值同名不同类型，解析分支更绕；选两个布尔字段。
- 只拆开、不保留 `--clash`：用户最常用的仍是两项一起开，保留简写更顺手。
- 启动时自动查找：每次启动多一次进程枚举，Windows 约 1 秒；选“设置时查一次并保存”。

## 7. 影响分析

1. **配置读写**（正向）：`NetCheck` 被 `Account.copy` / `to_dict`（`config.py:84-104`）、`launcher.render` 的 `net_check` 开关（`accounts.py:101`、`:193`、`:342`）、`cli.py:849`、`:1468`、`:1628`、`:1670` 使用，只用到 `active`；`active` 语义（任一检查开启）不变，所以启动命令是否带检查段不变。
2. **旧配置**（反向）：用户本机 `net_check` 写着 `"clash": true` 的账号读作两项都开，行为与现在一致；之后某次写命令实际改动了配置（`accounts.py:128` 比较新旧序列化结果）时才改写成新字段，在那之前旧字段留在文件里，读法不变。
3. **只开 TUN 时放宽了模式**：rule 模式也通过。rule 模式下按规则可能有直连流量，这是用户选择只查 TUN 的结果；`--clash on` 行为不变。
4. **加锁前的查找**：只在 `clash-api auto` 和“开启 Clash 检查且未设置接口”时运行；只读（进程列表、配置文件、`GET /configs`），不改其它命令的耗时。Windows 上每次约 1 秒，最长 90 秒超时。
5. **进程枚举的权限**：macOS `ps` 可看到其它用户进程的参数（本机已核实 root 进程可见）；Linux `/proc/<pid>/cmdline` 对其它用户进程一般可读；读不到的跳过。Windows：测试机的内核以当前用户身份运行（`-d` 在该用户的 AppData 下），`Get-CimInstance Win32_Process` 能读到它的命令行（已实测）；Clash Verge 在 Windows 以服务模式（SYSTEM）运行时普通用户能否读到 `CommandLine` 未核实，读不到时 `CommandLine` 为空，只能靠已知配置文件这一路——测试机应用目录下的 `config.yaml` 含 `external-controller-pipe: \\.\pipe\verge-mihomo`（已实测），可覆盖这种情况。
6. **候选 secret**：从进程参数 `-secret` 或配置文件 `secret` 读到的值只用于验证 TCP 候选，写入配置时随地址一起保存（与 `--secret-stdin` 保存位置相同，`config.json` 权限 0600）；输出与日志（含 `-v` 的逐个候选说明）只写地址，不写 secret。只在第 1 步筛出的 Clash 类进程上读配置文件，且 TCP 候选只限回环地址（§5.1.4 第 3 步），读到的 secret 不会发往别的主机。已知局限：Clash Verge 配置文件里写的回环端口平时可能没有监听（§1 实测 9097 未监听），多用户机器上另一个本机用户若先占住该端口，能收到这个 secret；单用户机器不受影响。secret 含不能放进 HTTP 头的字符（如中文）时，该候选记为失败并继续下一个，不中止查找。
7. **测试钩子不共用**：进程列表的测试钩子用新变量 `MULTI_CODEX_TEST_CLASH_PROCESSES`，只有 `list_process_argv()` 读它；Windows 占用检查的 `MULTI_CODEX_TEST_PROCESS_LIST`（`platform.py:485`，只由 `tests/test_windows.py:53` 设置）不改，所以 Windows CI 上其它测试的占用检查仍跑真实的 PowerShell 查询，行为与现在相同。
   - 测试里开 Clash 检查且未设置接口（如 `tests/test_netcheck.py:557`）会触发查找，不隔离就会读到开发机上真实运行的 Clash（本机 Mac、Windows 测试机都有），把真实地址写进测试配置。处理：`tests/helpers.py` 的 `CliTestCase`（家目录隔离在 `:89-106`）默认把新变量指向一份空列表 `[]` 文件，查找类用例换成自己的列表。

## 8. 回归测试

自动测试（`tests/test_netcheck.py`，本机 macOS + CI 三平台）：

0. 测试基础：`tests/helpers.py` 的 `CliTestCase` 默认设置 `MULTI_CODEX_TEST_CLASH_PROCESSES` 指向空列表文件（见 §7 第 7 条）；`tests/test_netcheck.py:255-257` 的 `config()` 工具改为接受 `clash_tun`、`clash_global`，另留一个参数可写旧字段 `clash`。
1. 配置解析：旧 `clash: true` → 两项都开；`clash_tun` / `clash_global` 非布尔 → 报错；新旧字段同时出现 → 以新字段为准；`to_dict` 不再含 `clash`。现有断言改写：
   - `tests/test_netcheck.py:441`：输入保留旧字段 `"clash": True`，期望改为 `{"level": "strict", "country": "JP", "clash_tun": True, "clash_global": True}`（同时覆盖兼容读取与新字段写出）。
   - `tests/test_netcheck.py:543`：期望改为 `{"level": "strict", "country": "SG", "clash_tun": False, "clash_global": False}`。
   - `tests/test_netcheck.py:450` 的非法值循环加 `{"clash_tun": "yes"}`、`{"clash_global": 1}`。
   - 旧字段不被无谓改写（§7 第 2 条）：手写 `"net_check": {"level": "off", "clash": true}`，跑 `netcheck work --clash on` → 输出 `already up to date`，文件里仍是旧字段；再跑 `netcheck work --clash-tun off` → 文件变为 `clash_tun: false, clash_global: true`，不再有 `clash`。
2. 检查判定（假 mihomo）：
   - 只 TUN：rule + tun 开通过；global + tun 开通过；direct + tun 开拒绝；tun 关拒绝。
   - 只全局：global + tun 关通过；rule 拒绝。
   - 两项：沿用现有 `test_clash_states` 全部断言。
   - 通过说明：只开 TUN、rule + tun 开时 detail 为 `mode=rule, tun on`；只开全局、global + tun 关时为 `mode=global, tun off`。
   - 失败说明：只开一项时 `failure_text` 的关闭提示含 `--clash off`。
3. `scope_text`：`clash`、`clash tun`、`clash global`，以及与 `strict, US` 组合。
4. 命令行：`--clash on` 写入两项；`--clash-tun on` 只写一项；`--clash on --clash-tun off` 退出 2；只开全局时 stderr 含 `without TUN`；`netcheck work`（只读）不写配置。
5. 未设置接口时的失败说明含 `clash-api auto`。
6. 自动查找（假 mihomo + `MULTI_CODEX_TEST_CLASH_PROCESSES`）：
   - 进程参数含 `-ext-ctl <假 mihomo 地址>` → 找到并写入；`-ext-ctl=...` 写法同样找到。
   - POSIX：`-ext-ctl-unix <假 unix 套接字>` 找到；相对路径配合 `-d` 找到。
   - 进程参数指向的 `-f` 配置文件里有 `external-controller` → 找到。
   - 第一个候选连不上、第二个可用 → 选第二个。
   - TCP 候选需要 secret 且进程参数带 `-secret` → 找到并保存 secret；`clash-api` 显示 `secret set`。
   - 无可用候选 → 退出 1，不写配置。
   - `--dry-run` 不写配置；`auto --secret-stdin` 退出 2。
   - `netcheck work --clash on` 且未设置接口、能找到 → 接口被写入；找不到 → 仍写入账号设置并提醒（即现有 `tests/test_netcheck.py:557` 用例，在空进程列表下断言不变，文字改为含 `clash-api auto`）。
   - 只取 Clash 类进程：列表里有 `tail -f <含 external-controller 的文件>` 这样的无关进程时不产生候选。
   - 非回环 TCP 候选（如 `-ext-ctl 10.0.0.1:9090`）被丢弃，不发请求。
   - 相对 `-f`、缺 `-d` 时的相对 unix 路径被跳过。
   - 已知配置文件：进程列表为空，在假家目录的已知位置（macOS `~/Library/Application Support/io.github.clash-verge-rev.clash-verge-rev/config.yaml`，Windows `%APPDATA%\io.github.clash-verge-rev.clash-verge-rev\config.yaml`，Linux 上该路径不在候选内则本条跳过）写入：`external-controller: "127.0.0.1:<假 mihomo 端口>"  # 注释`、一个缩进的同名键（指向不可用端口，应被忽略）、`secret: <值>`（假 mihomo 要求该 secret）→ 找到并保存 secret。POSIX（macOS）另加一条：`external-controller-unix: rel.sock`（相对路径）→ 只断言 `-v` 输出的候选列表里有按配置文件所在目录解析出的 `unix:<该目录>/rel.sock`，不要求真连上（测试家目录下该路径约 144 字节，超过 macOS unix 套接字路径上限 104 字节，起不了假服务端）。
   - 进程带 `-secret`、接口地址只写在它的 `-f` 配置文件里（文件里无 secret 或 secret 不同）→ 用 `-secret` 的值找到。
   - secret 不外泄：带 `-secret` 的查找在 `-v` 下运行，stdout 与 stderr 都不含 secret 原文。
   - 不触发查找：未设置接口时运行 `netcheck work basic`（不带 Clash 开关）与 `proxy work 7901`，进程列表里放一个可用的假 mihomo（`-ext-ctl <假 mihomo 地址>`），命令结束后 `clash-api` 仍显示未设置（证明没有查找）。
7. macOS `ps` 输出拆分（直接测拆分函数）：按 §1 实际的 ps 输出原样构造，程序路径 `/Library/Application Support/clash-verge-service/cores/verge-mihomo` 与 `-d`、`-f` 的值都含空格，拆出的参数 0、`-d`、`-f`、`-ext-ctl-unix` 全部正确。
7a. 真实进程枚举：清掉 `MULTI_CODEX_TEST_CLASH_PROCESSES` 后直接调用 `platform.list_process_argv()`，先用 `sys.executable -c "import time; time.sleep(60)" mc-marker-<uuid>` 起一个子进程，断言 `any(marker in " ".join(argv) for argv in result)`（子串匹配：macOS 按 §5.1.4 的拆分规则会把标记并入 `-c` 的值，Linux / Windows 上它是单独一项；不依赖解释器名的显示方式），测完结束子进程；CI 三个平台各跑一次，覆盖 macOS `ps`、Linux `/proc`、Windows PowerShell 三条真实路径，不依赖 Clash。
7b. Windows 占用检查：`tests/test_windows.py` 等现有用例不受影响，全部通过。
8. 全量 `python3 -m unittest` 通过，`shellcheck` / 现有 CI 检查通过。

实机：

9. 本机 Mac：`multi-codex clash-api off` 后 `multi-codex clash-api auto` 找到 `unix:/var/run/clash-verge-service/users/501/verge-mihomo.sock`；`netcheck jakoes.wu.us@gmail.com` 四项通过；完成后接口设置与改前一致。
10. Windows 测试机（临时目录，假家目录）：`clash-api auto` 找到 `\\.\pipe\verge-mihomo`；只开 TUN 检查通过；临时切 rule 后只开 TUN 仍通过、两项都开被拒绝；切回 global；记录候选是从进程参数还是配置文件找到的，以及内核以普通用户还是服务模式运行；测完清理临时目录并逐项核对。

## 9. 日志 / 观测点

- `clash-api auto` 成功：stderr `[multi-codex] found Clash controller <地址> (mode=global, tun on)`；`-v` 时逐个列出候选及结果（`ok`；连不上时写原因，如 `connection refused`、`timed out`；`needs a secret`；`HTTP <码>`；`not a Clash controller`；`not supported on this platform`；其它写失败原文）。
- 找不到：stderr `multi-codex: no working Clash / mihomo controller found`，下面列出试过的候选；退出码 1。
- 启动检查失败说明：`network check failed for account X (clash tun): clash: Clash mode is direct; ...`。
- 设置提醒：`global mode without TUN only covers traffic sent to the proxy port ...`。
