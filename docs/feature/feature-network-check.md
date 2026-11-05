# 启动前网络检查

> 2026-11-04 注记：方案起草中，代码待落地。基线 main `384cb04`（PR #39 Windows 支持合并后）。本文所有 `file:line` 锚点均指该基线；上游 Codex 锚点指 `~/tmp/codex-upstream/src` 的 `f2b94ef`，mihomo 锚点指 `~/tmp/src-mihomo/v1.19.32`。
>
> 2026-11-04 第 4 轮评审收敛（0 高 0 中），D27–D32 低级已修订。
>
> 2026-11-05 注记：代码已落地（分支 `feature/network-check`，未提交），第 1 轮代码评审的 2 中 8 低已修；macOS 全量测试通过，Windows 待 CI 与测试机实测。主要落点：检查逻辑 `src/multi_codex/netcheck.py`（`run_checks` / `gate` / `_check_clash`），POSIX 启动命令检查段 `src/multi_codex/launcher.py` `_net_check_lines`，入口拦截 `src/multi_codex/cli.py` `_net_gate`（`cmd_run` / `cmd_code` / `cmd_launch`）与 `_live_usage`，配置 `src/multi_codex/config.py` `NetCheck` / `ClashApi`。实现与本文的差异：跳过变量常量放在 `config.py` 的 `NETCHECK_SKIP_ENV`，cli 只在需要时导入 `netcheck`，未开启账号的启动不加载联网模块；未开启检查的账号给 `--country` 时提示“不会保存”（§5.1.7 照存一句以 §5.1.1 为准）。
>
> 2026-11-04 第 3 轮评审修订（D20–D26）：通过行补用例、检查范围文字规则、跳过变量统一 strip、PowerShell 跳过写法改 try/finally、`net_check` 只能按关键字传、usage 目录不存在时不检查、行号。
>
> 2026-11-04 第 2 轮评审修订（D13–D19）：cmd 跳过写法、目录检查先于网络检查、`quiet` 只注入开启检查的账号、提示里的账号名、测试证书扩展、补用例与局限。
>
> 2026-11-04 第 1 轮评审修订（D1–D12）：POSIX 检查版启动命令改为“在 `/` 下的子进程里跑检查、通过后执行原 sh 内容”，不再整体交给 Python 启动（D1、D2、D5、D12）；`usage --live` 跳过语义、TLS 测试、macOS 实机项等随之补齐。

## 1. 背景

用户要求在启动 Codex 之前，按账号检查网络是否满足条件，不满足就拒绝启动。用户已拍板（2026-11-05）：

1. 按账号设置，三档：关闭（默认）；基础——账号代理端口能连通才放行；加强——端口通，且不走代理直接访问 `https://chatgpt.com/cdn-cgi/trace` 返回的 `loc`、经代理访问同一地址返回的 `loc` 都等于配置的国家（可配置，默认 `US`）。
2. 另加一个独立的 Clash 检测，默认关闭：Clash / mihomo 处于全局模式且 TUN 已开启才放行，通过 mihomo 的外部控制接口读取。
3. macOS / Linux 与 Windows 一起支持。

已核实的现状与局限：

- **Codex 不严格遵守代理设置**。上游 `codex-rs/features/src/lib.rs:1635-1638` 的 `system_proxy_fallback` 默认开启；`codex-rs/http-client/src/outbound_proxy.rs:3-4` 写明开启时先用系统代理 / PAC，环境变量代理其次，最后兜底直连。2026-11-05 Windows 测试机实测：`HTTPS_PROXY` 指向无人监听的端口，`usage --live` 与 `exec` 照样成功（当时 Clash TUN 开着）。所以本功能只能在启动那一刻拦截一次，不能保证 Codex 运行中不直连，也不能保证 Codex 真的走了账号代理。
- **桌面版与 VS Code 插件**不经过账号启动命令，不受本功能约束（`multi-codex code` 打开 VS Code 之前会检查一次，之后插件的联网同样不受约束）。
- **启动命令现状**：
  - POSIX：`launcher.render`（`src/multi_codex/launcher.py:66-111`）生成 `/bin/sh` 脚本：查账号目录（`:83-87`，失败退出 1）→ 设环境 → 查 codex（`:104-107`，失败退出 127）→ `exec codex "$@"`（`:108`），不经 Python；`tests/test_windows.py:649-700` 的快照测试要求它与 `tests/data/posix-launchers-44cb4c1.json` 逐字节一致。
  - Windows：`.cmd` 只调用隐藏子命令 `launch`（`launcher.py:269-282`），经 `_entry.py` 以 `-I` 进 Python（`cli.py:508` 拦截 → `cmd_launch` `cli.py:1522-1553`）。
- **会联网的入口**（逐一核到调用链；除 `app`（`cli.py:1583`，非目标）外，启动 Codex 的调用点全集为 `cli.py:1445`、`:1487`、`:1507` 与 `usage.py:256`、`:260`）：

| 入口 | 调用链（基线） |
| ---- | ---- |
| `codex-<名>`（POSIX） | sh 脚本直接 `exec codex`（`launcher.py:103-110`） |
| `codex-<名>.cmd`（Windows） | `cli.py:508` → `cmd_launch` `:1522` → `_run_account_windows` `:1553` → `_call_windows` `:1519` |
| `codex-auto`（POSIX） | 路由脚本 `exec "$target" "$@"` 即账号启动命令（`router.py:106`） |
| `codex-auto.cmd`（Windows） | `cmd_launch` 无名字分支按绑定选账号（`cli.py:1537-1549`），再走 `_run_account_windows` |
| `run` | `cli.py:548-549` → `cmd_run` `:1418-1446`：Windows `:1441`，POSIX `os.execve("/bin/sh")` `:1445` |
| `login` | `cli.py:550-552` → `cmd_run(name, ["codex","login",…])` |
| `code` | `cli.py:553-554` → `cmd_code` `:1462-1488`：Windows `:1483`，POSIX `os.execve` `:1487` |
| `usage --live` | `cmd_usage` `cli.py:1355-1364` → `usage.live_snapshot` `usage.py:443` → `_AppServer` 以账号启动命令运行 `app-server`（`usage.py:247-262`） |

## 2. 目标 / 非目标

**目标**

1. 账号可设网络检查档位（关闭 / 基础 / 加强 + 国家）与 Clash 检测开关；开启后，上表所有入口在启动 Codex 之前检查，未通过就拒绝启动、说明哪一项没通过、给出本次跳过的办法，退出码 5（`usage --live` 例外：失败记为该账号的失败结果，汇总退出 1，见 5.1.6）。
2. 未开启检查的账号行为、启动命令内容、启动速度逐字节不变（POSIX 快照测试继续成立）。
3. macOS / Linux / Windows 三平台可用，不引入第三方依赖。

**非目标**

- 不保证 Codex 运行期间不直连、不保证 Codex 走账号代理（见 §1 局限）；要“绝不直连”需在系统防火墙层限制 Codex 进程，不在本工具范围内。
- 不覆盖 Codex 桌面版、VS Code 插件自行启动的 Codex、`multi-codex app`（用户 2026-11-04 拍板不检查），以及不经本工具的 `codex` 直接启动。
- 不做周期性 / 运行中检查，不缓存检查结果。
- 不修改 Clash 的任何设置（只读 `GET /configs`）。
- 不处理 install.sh / Homebrew 包装用 `python -m` 运行、用户在项目目录里直接敲 `multi-codex run …` 时当前目录进模块搜索路径的既有问题（基线已存在，与本功能无关；本方案新增的启动路径已规避，见 §5.1.3）。

## 3. 假设与约束

- **查询地址**：`https://chatgpt.com/cdn-cgi/trace`，响应为 `key=value` 逐行文本，取 `loc=` 一行（Cloudflare 的通用诊断地址，格式未在 OpenAI 文档中承诺，属外部依赖；实机 R3 / M2 核对）。
- **国家代码**：两位字母，存储时转大写；比较时大小写不敏感。
- **加强档的代理请求**只支持 `http://` 与 `socks5://` / `socks5h://` 代理；`https://` 代理（到代理本身走 TLS）需要 TLS 套 TLS，标准库实现成本高，加强档遇到它判失败并说明（基础档只连端口，不受影响）。用户 2026-11-04 已同意。
- **基础 / 加强档需要账号代理是 URL**：账号代理为 `inherit` / `off` 时没有可检查的端口，设置时拒绝；之后若被 `proxy` / `set` / `apply -f` 改成 `inherit` / `off`，启动时判失败并说明（不在配置解析里拒绝，免得一条 `proxy` 命令把整份配置变成不可读）。
- **检查的是账号代理设置**：账号 env 里若另写了 `HTTPS_PROXY` 等变量，它在启动命令里排在代理行之后、会覆盖账号代理（`launcher.py:97-99`），此时被检查的端口不是 Codex 实际使用的端口；本方案不解析 env，README 写明这一局限。
- **mihomo 外部控制接口契约**（已核 mihomo v1.19.32 源码）：
  - `GET /configs` 返回 `executor.GetGeneral()` 的 JSON（`hub/route/configs.go:25-27`、`:133-136`）；
  - `mode` 字段为小写 `global` / `rule` / `direct`（`tunnel/mode.go` 的 `MarshalText` → `String()`）；
  - TUN 状态在 `tun.enable`（`config/config.go:48-50` `General` 内嵌 `Inbound`，`:79` `Tun LC.Tun json:"tun"`；`listener/config/tun.go:13` `Enable … json:"enable"`）；TUN 启动失败时两个 defer 后进先出，先把 `Enable` 置 false 再写入 `LastTunConf`（`listener/listener.go:501-512`），`GetTunConf` 在没有 TUN 监听器时返回它（`:77-82`），所以 `enable` 反映的是实际是否在运行；
  - TCP 接口设了 secret 时要求 `Authorization: Bearer <secret>`，否则 401（`hub/route/server.go:120-121`、`:336-363`）；
  - unix 套接字（`external-controller-unix`）与 Windows 命名管道（`external-controller-pipe`，必须以 `\\.\pipe\` 开头）不鉴权（`server.go:251-296` 与 `:299-334` 均以 `router(…, "", …)` 启动）。
  - **未验证**：Clash Verge Rev 实际开放的是哪种接口、地址与 secret 在哪里能看到（GUI 产品行为，不在 mihomo 源码里）——Windows 测试机实机核对（R5）；`cors.Apply` 对不带 `Origin` 头的本地请求是否有影响未读实现，由假 mihomo 之外的实机 R5 兜住。
- **POSIX 检查版启动命令需要 `multi-codex` 在 PATH 上**（论证见 §6 决策 2）：三种安装方式（pipx、install.sh、Homebrew）的命令目录本来就要在 PATH 上才能正常使用本工具；不在时检查版启动命令报 127 并说明，未开启检查的账号不受影响。
- 不引入第三方依赖：HTTP 用 `http.client` + `ssl`，SOCKS5 握手自行实现（约 30 行），命名管道用内置 `open()`。

## 4. 涉及模块

新增文件：

| 区域 | 行号锚点（基线） | 改动类型 | 改动点 |
| ---- | ---- | ---- | ---- |
| `src/multi_codex/netcheck.py` | 新文件 | 新增 | 检查项实现：端口、出口国家（直连 / 经代理）、Clash 状态；结果结构；`gate()`、跳过变量 |
| `tests/test_netcheck.py` | 新文件 | 新增 | §8.1 用例（假代理、假 SOCKS5、假 trace、假 mihomo），三平台都跑 |
| `tests/data/netcheck-cert.pem`、`netcheck-key.pem` | 新文件 | 新增 | 预生成的 `127.0.0.1` / `localhost` 自签证书（有效期 100 年；扩展含 `basicConstraints=critical,CA:TRUE`、`keyUsage`、SKI/AKI、SAN 含 `IP:127.0.0.1` 与 `DNS:localhost`，满足 Python 3.13 默认的 `VERIFY_X509_STRICT`），只供 §8.1 的 TLS 用例 |

修改既有文件：

| 区域 | 行号锚点（基线） | 改动类型 | 改动点 |
| ---- | ---- | ---- | ---- |
| `config.py` 账号模型 | `:57-100`（`__init__` `:72`、`copy` `:83`、`to_dict` `:87`） | 修改 | 新增 `net_check` 字段（`NetCheck`），只在开启时序列化 |
| `config.py` 全局模型 | `:103-162`（`__init__` `:104`、`copy` `:146`、`to_dict` `:150`） | 修改 | 新增 `clash_api` 字段，只在设置时序列化 |
| `config.py` 解析 | `:243-251`、`:285-314` | 修改 | 解析并校验 `net_check`、`clash_api` |
| `config.py` 新增 | `:401` 之后追加 | 新增 | `NetCheck` 类、`normalize_country`、`normalize_clash_address` |
| `launcher.py` render | `:66-111`（插入点在 `:87` 之后） | 修改 | 新增关键字参数 `net_check=False`，为真时在账号目录检查之后插入检查段；为假时输出与基线逐字节相同 |
| `accounts.py` 计划 | `:98`、`:178-200`（`render` 调用 `:189`） | 修改 | `_plan_launcher` 增加参数 `checked`，POSIX 上传给 `render(net_check=…)` |
| `accounts.py` 状态 | `:321-338`（`render` 调用 `:337`） | 修改 | `launcher_status` 在 POSIX 上按账号是否开启检查生成期望内容 |
| `accounts.py` 退出码 | `:23-27` | 新增 | `EXIT_NETCHECK = 5` |
| `cli.py` main 入口 | `:505-516` | 新增 | 隐藏子命令 `netgate NAME` 在 argparse 之前拦截（两平台都接受，只由 POSIX 检查版启动命令调用） |
| `cli.py` 命令分组与参数 | `:39-70`、`:230-400` | 新增 | `netcheck`、`clash-api` 两个子命令 |
| `cli.py` 分派 | `:556-575`、`:630-760` | 新增 | `netcheck NAME` / `clash-api` 无参只读分派；写操作进 `dispatch` |
| `cli.py` run / code | `:549`、`:552`、`:1436-1440`、`:1466-1468` | 修改 | `cmd_run` 增加 `subcommand` 参数（`main` 两处调用点传入）；找到账号后、启动前调用 `_net_gate` |
| `cli.py` launch（Windows） | `:1549-1553` | 修改 | 找到账号后先 `_net_gate`；POSIX 行为不变（仍只在 Windows 拦截 `launch`） |
| `cli.py` usage --live | `:1345-1364` | 修改 | 循环前读一次跳过变量；开启检查的账号先 `run_checks`，失败转成该账号的失败结果；子进程带静默跳过变量 |
| `cli.py` apply -f | `:1048-1074` | 修改 | `clash_api` 按本机状态沿用当前值（同 `bindings` `:1069-1070`） |
| `usage.py` | `:247-262`、`:443-453` | 修改 | `_AppServer` / `live_snapshot` 增加可选 `env` 参数 |
| `completion.py` | `:16-17` | 修改 | `netcheck` 加入 `ACCOUNT_COMMANDS` |
| `.github/workflows/ci.yml` | `:23`、`:59-61` | 修改 | Windows 新增一步 `-p test_netcheck.py`（`-p` 只接受一个模式）；`:23` 注释同步 |
| `README.md` / `README.zh-CN.md` | 命令节、退出码表（`README.md:509-517`）、Windows 限制节 | 修改 | 新命令说明、局限、退出码 5 |
| `CHANGELOG.md` | 顶部 | 修改 | Unreleased 记一条 |

## 5. 方案

### 5.1 实现要点

#### 5.1.1 配置（`config.py`）

账号字段（`Account.to_dict` `:87-100` 末尾追加，只在开启时写出，未开启账号的序列化结果逐字节不变——原因同 `:89-91` 的 env 注释）：

```json
"net_check": {"level": "strict", "country": "US", "clash": true}
```

- `NetCheck(level="off", country="US", clash=False)`；`level ∈ {"off","basic","strict"}`；`active = level != "off" or clash`。`Account.copy`（`:83-85`）带上它，`rename`（`cli.py:844` 走 `copy` 后改名）不丢设置；`Account` 的其它构造点（`cli.py:678`、`migrate.py:288`）用默认值。
- 解析（`_parse_account` `:285-314`）：字段缺省为默认值；`level` 非法、`country` 不匹配 `^[A-Za-z]{2}$`、`clash` 不是布尔值 → `ConfigError`；`country` 存大写。`active` 为假时 `to_dict` 不写该字段（`country` 随之丢弃，下次开启时回到默认 `US`，`netcheck` 命令开启时会显示国家）。

全局字段（`Config.to_dict` `:150-162` 末尾追加，只在设置时写出）：

```json
"clash_api": {"address": "http://127.0.0.1:9097", "secret": "…"}
```

- `address` 三种写法，`normalize_clash_address` 统一：`HOST:PORT` 或 `http://HOST:PORT` → `http://HOST:PORT`；`unix:/绝对路径`（仅 POSIX）；`\\.\pipe\名称` 或 `pipe:\\.\pipe\名称`（仅 Windows）→ `\\.\pipe\名称`。`https://` 不支持。
- `secret` 可省；配置文件以 0600 写入（`config.py:193-194`），与账号 env 里的密钥同等保护。
- 它是本机状态：`apply -f` 沿用当前值（`cli.py:1069-1070` 之后同法处理），文件里写了也忽略并 warn。

#### 5.1.2 检查实现（新文件 `netcheck.py`）

常量：`TRACE_HOST = "chatgpt.com"`、`TRACE_PATH = "/cdn-cgi/trace"`、`PORT_TIMEOUT_SEC = 3`、`TRACE_TIMEOUT_SEC = 8`、`CLASH_TIMEOUT_SEC = 3`、`SKIP_ENV = "MULTI_CODEX_SKIP_NETCHECK"`。

结果结构：`CheckResult(item, ok, detail, elapsed_ms)`，`item ∈ {"clash","proxy-port","direct-country","proxy-country"}`。

检查项与顺序（`run_checks(config, account, stop_on_failure)`）：

1. **clash**（`clash=True` 时）：按 `clash_api.address` 发 `GET /configs`（HTTP/1.0、`Connection: close`，有 secret 时带 `Authorization: Bearer`）：
   - TCP：`http.client.HTTPConnection(host, port, timeout)`；
   - unix：`HTTPConnection` 子类，`connect()` 改为 `socket.AF_UNIX` 连接该路径；
   - 命名管道：`open(path, "r+b", buffering=0)` 写入请求，读到 EOF（空字节或 `BrokenPipeError` 都按读完处理），用 `http.client.HTTPResponse` 包一层假 socket（`makefile` 返回 `BytesIO`）解析；读在守护线程里做，主线程 `join(CLASH_TIMEOUT_SEC)` 兜住超时（管道读没有超时参数）。
   - 判定：连不上 / 401（“secret 缺失或错误”）/ 非 200 / JSON 无 `mode` 或 `tun.enable` → 失败；`mode != "global"` → 失败（说明当前模式）；`tun.enable` 不是 true → 失败。
   - 未设置 `clash_api` → 失败，提示 `multi-codex clash-api ADDRESS`。
2. **proxy-port**（`level ∈ {basic,strict}`）：账号代理不是 URL → 失败（“account proxy is inherit/off; set a proxy first”）。否则用 `urllib.parse.urlsplit` 取 `hostname`（IPv6 写法 `[::1]` 由它去掉方括号，`config.py:390-392` 存的是带方括号形式）与 `port`，`socket.create_connection((host, port), PORT_TIMEOUT_SEC)` 后立即关闭。
3. **direct-country**（`strict`）：`http.client.HTTPSConnection(TRACE_HOST, 443, timeout, context=ssl.create_default_context())`。`http.client` 不读环境变量代理（只有 `urllib` 读），所以这里是真正的“不走代理”；系统开着 TUN 时流量仍会被 TUN 接管，这正是用户要的“直连出口”。
4. **proxy-country**（`strict`）：
   - `http://` 代理：`HTTPSConnection(proxy_host, proxy_port, …)` + `set_tunnel(TRACE_HOST, 443)`（`CONNECT`；TLS 按隧道主机名握手，CONNECT 失败抛 `OSError("Tunnel connection failed: …")`，Python 3.11 `http/client.py:1470-1471`、`:943` 已核，3.8 由 CI 3.8 行的 TLS 用例覆盖）；
   - `socks5://` / `socks5h://`：`HTTPSConnection` 子类，`connect()` 先连代理、做 SOCKS5 无认证握手（`05 01 00` → `05 00`）、`CONNECT` 以域名形式（ATYP=3）请求 `chatgpt.com:443`，成功（`REP=00`）后 `context.wrap_socket(sock, server_hostname=TRACE_HOST)`；两种 scheme 都让代理解析域名（只影响 DNS 在哪里解析，不影响出口 IP，`loc` 判定不变）；
   - `https://` 代理：直接失败，说明加强档不支持（§3）。
5. 请求统一 `GET /cdn-cgi/trace`，头 `Host`、`User-Agent: multi-codex/<版本>`、`Connection: close`；状态码非 200 → 失败；响应体（上限 64 KiB）里找不到 `loc=` → 失败；`loc` 不等于配置国家 → 失败，说明实际值。

错误分类（按编码规范 §11.3，写进 `detail`）：DNS 失败（`socket.gaierror`）、连接被拒（`ConnectionRefusedError`）、超时（`socket.timeout`）、TLS 失败（`ssl.SSLError`，证书校验失败时附“Python 找不到系统 CA 证书”提示）、代理 CONNECT 被拒、SOCKS 握手失败（含 REP 码）、HTTP 非 200、响应缺 `loc`。不重试：检查本身就是一次性的放行判断，重试只会拖慢启动。

`gate(config, account, hint) -> Optional[str]`：

- `account.net_check.active` 为假 → `None`（不做任何事，不读 `SKIP_ENV`）。
- `skip_mode()`（读 `SKIP_ENV` 并 `strip()`）为 `"1"` → 向 stderr 打 `multi-codex: network check skipped for this run (MULTI_CODEX_SKIP_NETCHECK=1)`；为 `"quiet"`（`usage --live` 内部注入，见 5.1.6）→ 不打印；两种情况都从 `os.environ` 删掉该变量（Python 内启动 Codex 的路径不让 Codex 继承），返回 `None`。比较前先 `strip()`（cmd 的 `set X=1 &&` 会把空格算进值）；其它值视为未设置。
- 否则 `run_checks(stop_on_failure=True)`，全部通过 → 向 stderr 打一行 `multi-codex: network check passed for account <名> (<检查范围>; <总毫秒> ms)` 后返回 `None`（用户 2026-11-04 拍板：通过时打印一行；`usage --live` 不调用 `gate`、不打印）；有失败 → 返回多行说明：

```text
multi-codex: network check failed for account work (strict, US): proxy-country: loc is JP, expected US (via http://127.0.0.1:7905, 412 ms)
  passed: proxy-port, direct-country
  skip once: MULTI_CODEX_SKIP_NETCHECK=1 codex-work
  turn off:  multi-codex netcheck work off
```

  `<检查范围>` 规则（通过行与失败行共用）：basic 写 `basic`；strict 写 `strict, <国家>`；开了 Clash 再加 `, clash`；只开 Clash（档位 off）时只写 `clash`。

  Windows 上“skip once”给两行：`PowerShell: try { $env:MULTI_CODEX_SKIP_NETCHECK=1; codex-work } finally { Remove-Item Env:MULTI_CODEX_SKIP_NETCHECK }`（`finally` 保证 Ctrl+C 中断后变量也被删除） 与 `cmd: cmd /c "set MULTI_CODEX_SKIP_NETCHECK=1&& codex-work"`（在子 cmd 里设变量，命令结束即消失，不残留到当前会话；`1&&` 之间不留空格）。`hint` 是调用方给的命令（`codex-work` / `multi-codex run work` / `multi-codex login work` / `multi-codex code work`）。
- `KeyboardInterrupt`：打印 `multi-codex: network check interrupted`，调用方返回 130。

#### 5.1.3 POSIX 启动命令（`launcher.py`、`accounts.py`）

`render` 在 `command_mode` 之后增加只能按关键字传的参数 `*, net_check: bool = False`（快照测试按位置传 `command_mode`，不能插在它前面）。为真时在账号目录检查（`launcher.py:83-87`）之后、`CODEX_HOME=` 之前插入：

```sh
if ! command -v multi-codex >/dev/null 2>&1; then
  echo 'multi-codex: multi-codex not found in PATH; it runs the network check of this account' >&2
  exit 127
fi
(cd / && exec multi-codex netgate 'work') || exit $?
unset MULTI_CODEX_SKIP_NETCHECK
```

- 为假时不插入任何行，输出与基线逐字节相同，快照测试不变。其余行（环境设置、codex 存在性检查、`exec codex "$@"`）原样保留，所以检查通过后的环境、报错顺序（目录 → 检查 → codex）、退出码都与未开启检查时相同。Python 内的入口（5.1.5）用同样的顺序：账号目录不存在时 `_net_gate` 不检查、直接返回 `None`，由原有路径报目录错误并退出 1。
- **`cd /` 的原因**：install.sh 与 Homebrew 的包装以 `python -m multi_codex` 运行（`install.sh:267`、`homebrew-tap/Formula/multi-codex.rb:17`），`-m` 把当前目录放进模块搜索路径最前面；在用户项目目录里运行时，项目里的 `json.py`、`ssl.py` 会顶替标准库被执行（Windows 改用 `-I` 也是这个原因，`launcher.py:269-273`）。子 shell 先切到 `/` 再运行检查，Codex 本身仍在用户目录启动。`netgate` 只需要账号名，不依赖当前目录。
- **`unset MULTI_CODEX_SKIP_NETCHECK`**：检查在子进程里，删不掉父 shell 的变量，由 sh 在通过后删除，Codex 及其子进程不会继承“跳过”。
- 账号名经 `shlex.quote`；`command_mode=True`（`run` / `code` 用）时不插入检查段——那两条路径在 Python 里调用 `_net_gate`（5.1.5）。
- 权限与 env 判定不变：`_plan_launcher` / `launcher_status` 仍按 `launcher_mode(env)` / `launcher_file_ok(path, content, env)` 处理（检查段不含密钥，env 段照旧在脚本里）。
- `_plan_launcher`（`accounts.py:178-200`）加参数 `checked: bool = False`，POSIX 分支 `:189` 改为 `launcher.render(name, directory, proxy, env, net_check=checked)`；调用点 `:98` 传 `account.net_check.active`。`launcher_status` `:337` 同样传 `account.net_check.active if account else False`。Windows 分支不变。
- 档位在 basic / strict 之间切换、改国家、开启状态下改 Clash 开关不改变启动命令内容；只有“开启 ↔ 关闭”会重写启动命令，由 `netcheck` 命令经 `converge` 完成。

`netgate NAME`（`cli.py` main `:505-516`，与 `launch` 同样在 argparse 之前拦截，不进帮助与补全）：读配置、找账号；账号不存在 → 报错退出 1；`_net_gate(config, account, "codex-" + name)` 失败返回 5，通过返回 0；配置错误按 `main` 现有方式返回 1。两平台都接受（Windows 上无调用方，不特意拒绝）。

#### 5.1.4 Windows `launch`（`cli.py`）

`cmd_launch`（`:1522-1553`）在 `:1549-1552` 找到账号之后、`:1553` 之前调用 `_net_gate(config, account, "codex-" + account.name)`（无名字分支经绑定选到的账号同样经过这里，即 `codex-auto.cmd`）。未绑定、跑默认 codex 的分支（`:1543`）不是账号，不检查。`main` 的 `launch` 拦截仍只在 Windows（`:508` 不变）。

#### 5.1.5 `_net_gate`（`cli.py`，新增于 `_run_account_windows` `:1512` 之前）

```python
def _net_gate(config, account, hint) -> Optional[int]:
    """启动前网络检查；未开启、账号目录不存在（交给原有路径报错）或通过返回 None，失败打印原因并返回 EXIT_NETCHECK，中断返回 130。"""
```

| 入口 | 位置 | hint |
| ---- | ---- | ---- |
| `run` / `login` | `cmd_run` `:1436-1439` 找到账号之后、`:1440` 平台分支之前 | `multi-codex run NAME` / `multi-codex login NAME` |
| `code` | `cmd_code` `:1466-1468`（`_registered_with_dir` 成功之后） | `multi-codex code NAME` |
| `codex-<名>.cmd` / `codex-auto.cmd` | 5.1.4 | `codex-NAME` |
| `codex-<名>` / `codex-auto`（POSIX） | `netgate`（5.1.3） | `codex-NAME` |

`cmd_run` 增加参数 `subcommand`（`main` `:549` 传 `"run"`，`:552` 传 `"login"`）；`hint` 在 `cmd_run` 确定账号名（含按目录绑定选出的名字，`cli.py:1424-1435`）之后才拼成 `multi-codex <subcommand> <名>`，避免省略名字时出现 `None`。

#### 5.1.6 `usage --live`（`cli.py`、`usage.py`）

- `cmd_usage` 在账号循环（`:1352`）之前读一次跳过状态（与 `gate` 共用 `netcheck.skip_mode()`：读 `SKIP_ENV`、`strip()` 后返回 `"1"` / `"quiet"` / `None`），不删除、不调用 `gate()`（`gate` 会删变量，逐账号循环里只有第一个账号能被跳过）。
- 循环内 `elif args.live:` 分支（`:1356`）在 `launcher_status` 判定之后：账号开启检查、未跳过、且账号目录存在时（目录不存在就不检查，让子进程照原样报目录错误，与 5.1.3 的“目录 → 检查”顺序一致），调用 `netcheck.run_checks(stop_on_failure=True)`，失败 → `UsageResult(..., False, "network check failed: <项>: <详情>", ...)`，不启动 app-server。
- 只对开启了检查的账号，启动 app-server 时传 `env = dict(os.environ, MULTI_CODEX_SKIP_NETCHECK="quiet")`（未开启的账号启动命令里没有 `unset`，Windows `gate` 也不删变量，注入会被 Codex 继承，所以不注入、`env=None`）：子进程里的 `netgate`（POSIX）/ `launch`（Windows）静默跳过，不重复检查，也不把提示写进 stderr 临时文件（`usage.py:326-337` 会把它拼进报错）。用户设的 `=1` 被 `quiet` 覆盖，效果相同。
- `usage.py`：`_AppServer.__init__`（`:247`）与 `live_snapshot`（`:443`）加 `env: Optional[dict] = None`，原样传给两处 `subprocess.Popen`（`:256`、`:260`）；不传时与基线相同。
- 退出码沿用 `usage` 的约定：有账号失败即 1（`cli.py:1375`），不用 5（`usage` 是多账号汇总，5 只用于“拒绝启动单个 Codex”）。

#### 5.1.7 命令（`cli.py`）

`netcheck`（分组 Everyday，摘要 `set or test the pre-launch network check of an account`）：

```text
multi-codex netcheck NAME                         # 显示设置并立即检查一次（不停在第一个失败，逐项列出）
multi-codex netcheck NAME off|basic|strict [--country CC] [--clash on|off]
multi-codex netcheck NAME [--country CC] [--clash on|off]   # 只改给出的项，档位不变
```

- 只读形态（只有 NAME）：不加锁，分派位置同 `env` 只读形态（`cli.py:570`）；未开启时打印 `network check is off for NAME` 退出 0；开启时逐项打印 `ok|fail <项> <详情> (<毫秒> ms)`，全部通过退出 0，否则 5。
- 写形态：进 `dispatch`，修改 `account.net_check` 后照常 `converge`（POSIX 上开启 / 关闭会重写启动命令），支持 `--dry-run` / `-v`。
- 设置时校验（按改完后的结果）：档位为 `basic` / `strict` 时要求账号代理是 URL，否则 `UsageError`（退出 2）；`strict` 且代理为 `https://` 同样拒绝；Clash 开启而 `clash_api` 未设置 → 只 warn（允许先设账号再设接口），启动时会失败并提示。
- `--country` 在非 strict 档位也照存（切到 strict 时生效）。

`clash-api`（分组 Advanced，摘要 `set the Clash / mihomo controller used by the network check`）：

```text
multi-codex clash-api                              # 显示（secret 只显示 set / not set）
multi-codex clash-api ADDRESS [--secret-stdin]     # 设置；--secret-stdin 从标准输入读一行作为 secret
multi-codex clash-api off                          # 清除
```

secret 不接受命令行参数（其它用户能用 ps 看到，同 `launcher.py:29` 的考虑）。写操作进 `dispatch` + `converge`（只改配置）。

#### 5.1.8 测试注入

- `MULTI_CODEX_TEST_TRACE=<host>:<port>`：把 trace 目标换成本地假服务器（直连与经代理两条路径都用它）；仍走 TLS。
- `MULTI_CODEX_TEST_CA=<pem 路径>`：设了时 `ssl.create_default_context(cafile=…)` 改用它，配合 `tests/data/netcheck-cert.pem`（`127.0.0.1`/`localhost` 自签，证书即 CA）。
- 只在 `netcheck.py` 读取，命名与 `platform.py:242-310` 的 `MULTI_CODEX_TEST_*` 一致。

### 5.2 接口变更

| 接口 | 类型 | 说明 | 兼容性 |
| ---- | ---- | ---- | ---- |
| `config.json` 账号 `net_check` | 新增字段 | 见 5.1.1 | 未开启时不写；旧版读到会忽略，但旧版写回时会丢掉它 |
| `config.json` 顶层 `clash_api` | 新增字段 | 见 5.1.1 | 同上 |
| `multi-codex netcheck` | 新命令 | 见 5.1.7 | — |
| `multi-codex clash-api` | 新命令 | 见 5.1.7 | — |
| 隐藏子命令 `netgate NAME` | 新增 | 只由 POSIX 检查版启动命令调用 | 不进帮助与补全 |
| POSIX 启动命令内容 | 新增形态 | 开启检查的账号多出检查段 | 未开启账号逐字节不变 |
| `launcher.render(..., net_check=False)` | 内部函数签名 | 新增关键字参数 | 默认值下输出不变 |
| 退出码 5 | 新增 | 网络检查未通过，未启动任何程序 | 与 `accounts.py:23-27` 的 0–4 不冲突；`run` / 启动命令原本透传 Codex 的退出码，Codex 自身若返回 5 无法单凭退出码区分，以 stderr 的 `network check failed` 为准（README 写明） |
| 环境变量 `MULTI_CODEX_SKIP_NETCHECK` | 新增 | `1`：本次跳过并提示；`quiet`：内部用，静默跳过 | — |
| `usage.live_snapshot(..., env=None)` | 内部函数签名 | 新增可选参数 | 不传时行为不变 |

**reference sibling 回补检查**：N/A——本方案 §4 不涉及 `docs/reference/*`（本仓库无该目录，命令说明在 README）。

## 6. 备选方案与决策

1. **POSIX 上所有账号一律改为先经 Python**：否决。每次启动多一次解释器启动；所有已有启动命令内容改变，升级后全部变成 stale、`usage --live` 拒绝运行直到 `apply`（`README.md:507`），违反目标 2。采纳“只有开启了检查的账号才改”。
2. **检查版启动命令怎么调到检查**：
   - (a) 整体交给 `multi-codex launch`，由 Python 设环境再 `execve`（第 1 版方案）：否决。install.sh / Homebrew 的 `-m` 会在用户项目目录里加载同名模块（第 1 轮 D2）；环境语义需要在 Python 里重算一遍，与 sh 版的报错顺序、PATH 计算不一致（D5）。
   - (b) 写死 Python 与 `_entry.py` 的绝对路径（照搬 Windows `.cmd`）：否决。Homebrew 把包装在 `Cellar/multi-codex/<版本>/libexec`（配方 `libexec.install`），`brew upgrade` 清理旧版后启动命令指向不存在的文件。
   - (c) 采纳：sh 里在 `/` 下运行 `multi-codex netgate NAME`，通过后继续执行原 sh 内容。不含版本路径、升级后无需重写；Python 不在用户目录启动；Codex 的环境与原启动命令逐行相同。代价是 PATH 上若是更旧的 multi-codex（没有 `netgate`）会报 unknown command 并退出 2——启动命令随之拒绝启动，README 说明“升级后重开终端”。
3. **在 sh 脚本里用 `nc` / `curl` 做检查**：否决。依赖外部命令且各发行版参数不同，Windows 不可用，与 Python 实现重复一套逻辑。
4. **Clash 接口设置放在每个账号上**：否决。外部控制接口整台机器一个，放全局避免多处重复 secret；是否开启 Clash 检测仍按账号（用户 2026-11-04 已同意）。
5. **加强档用 `urllib` + `ProxyHandler`**：否决。`urllib` 不支持 SOCKS 代理；直连请求还需显式屏蔽环境变量代理。统一用 `http.client`，直连天然不读环境变量。

## 7. 影响分析

### 7.1 正向推

- `config.py` 新字段 → `load_config` / `save_config` / `apply -f` / `list --json`：`list --json` 按字段手工拼（`cli.py:1138-1150`），不带出 `net_check`，输出不变；配置序列化只在开启后变化。
- `render` 新参数 → 调用点全集 `accounts.py:189`、`:337`、`cli.py:1411`；前两处按账号传值，`cli.py:1411`（`command_mode=True`）不传，输出不变。
- `_plan_launcher` 新参数 → 唯一调用点 `accounts.py:98`；`rename` / `remove` / 孤儿清理走 `_plan_launcher_delete`（`:254`），按第 2 行标记识别，检查版同样能删。
- `launcher_status` → `list` 的启动命令状态、`usage --live` 的前置判定（`cli.py:1357-1361`）、`doctor` 的“文件是否与配置一致”。三者对检查版账号按新期望判断；带 env 的账号权限判定与原来相同（D1）。手改 `config.json` 开启检查后三者报 stale，与改代理后的表现相同。
- `codex-auto`（POSIX）→ `exec "$target" "$@"` 即账号启动命令，检查自动生效；`router.render` 不变。
- `usage --live` → 开启检查的账号多一次检查（见 7.3），子进程静默跳过；未开启账号不变。

### 7.2 反向推

- **快照测试**：`render` 默认参数下输出不变，`router.render` 未改，`tests/test_windows.py:687-700` 继续成立。
- **Windows `.cmd`**：内容只由 python / entry 路径决定（`launcher.py:269-282`），本方案不改；检查在 `cmd_launch` 内发生。
- **`_account_exec_args`**（`cli.py:1405-1415`）：不改；`run` / `code` 的检查在它之前完成。
- **跳过变量的继承**：POSIX 启动命令由 sh `unset`；Python 内启动的路径由 `gate` 删除；`usage --live` 只给开启检查的账号注入 `quiet`，由其子进程启动命令（POSIX `unset` / Windows `gate`）删除。
- **锁**：`netgate` / `launch` / `run` / `netcheck` 只读形态不加写锁；检查期间不持有任何锁。
- **当前目录**：POSIX 检查在 `/` 下运行，Codex 仍在用户目录启动；`netgate` 内不使用相对路径（配置路径来自 `platform.state_dir()`）。

### 7.3 运行时

- **启动耗时**（仅开启检查的账号）：POSIX 多一次 `multi-codex` 冷启动（实测值见 §9）；基础档 1 次本地 TCP 连接（毫秒级，超时 3 s）；加强档 2 次 HTTPS 请求（正常数百毫秒，单次读写超时 8 s）；Clash 1 次本地请求（3 s）。失败时约 22 s 内拒绝——不含 DNS 解析耗时（`getaddrinfo` 不受 socket 超时约束），对方逐字节慢速返回时也可能更久；不另加总时限线程（用户可 Ctrl+C）。 `usage --live` 的检查耗时不计入 `--timeout`（该时限只管 app-server，`cli.py:308`）。
- **网络**：加强档每次启动向 `chatgpt.com` 发 2 个约 1 KB 的请求；不发送任何账号信息。
- **进程**：POSIX 检查子进程结束后，sh `exec codex`，不留 Python 进程；Windows 维持 Python 父进程等待 codex（基线既有形态）。
- **部署形态**：`multi-codex` 不在 PATH → 检查版启动命令报 127 并说明；未开启的账号不受影响。Clash 未运行 / 接口地址错 → Clash 检测失败，提示看 `clash-api`。

## 8. 回归测试

### 8.1 自动（`tests/test_netcheck.py`，CI 三平台）

POSIX 用例在临时目录放一个假 `multi-codex` 命令（做法同 `tests/test_ergonomics.py:59-61`，以 `python -m multi_codex` 转发到源码）并放进 PATH；Windows 用例走 `.cmd`（已有 `tests/helpers.py` 的假 codex）。

1. 配置：`net_check` / `clash_api` 往返；非法 level / country / clash 报 `ConfigError`；未开启账号序列化与基线逐字节相同；`rename` 后设置保留、旧名的检查版启动命令被删除；`remove` 删除检查版启动命令。
2. `netcheck` 写形态：开启 → POSIX 启动命令为 `render(net_check=True)`（检查段逐字比对）、关闭 → 回到基线内容；basic / strict 间切换不重写；只给 `--country` 时档位不变；proxy 为 inherit/off 时 basic 报退出 2；strict + `https://` 代理报退出 2；`--clash on` 未设接口只 warn；**带 env 的账号开启检查后 `list` 与 `doctor` 显示 ok、权限仍为 0700**（D1）；手改 `config.json` 开启检查后 `doctor` 报 stale。
3. 端口：本地监听端口 → 通过；关闭的端口 → 失败、详情含 `refused`；代理为 `http://[::1]:<端口>` 时能连（IPv6 不可用的 CI 机器跳过）。
4. 出口国家（`MULTI_CODEX_TEST_TRACE` + `MULTI_CODEX_TEST_CA` 指向本地 TLS 假 trace 服务）：直连 `loc=US` 通过；`loc=JP` 失败并显示 JP；缺 `loc` 失败；HTTP 500 失败；不设 `MULTI_CODEX_TEST_CA` 时证书校验失败、分类为 TLS。
5. 经 http 代理（测试内的假 CONNECT 代理）与经 SOCKS5 代理（假 SOCKS5 服务）各取到 `loc`，TLS 走完整握手；代理拒绝 CONNECT（403）/ SOCKS REP≠0 时失败并分类。
6. Clash（假 mihomo，TCP）：`global`+`tun.enable=true` 通过；`rule` 失败；`tun.enable=false` 失败；带 secret 时校验 `Authorization: Bearer`，错 secret 返回 401 → 失败提示 secret；未设 `clash_api` 失败。POSIX 上另测 unix 套接字形态。Windows 命名管道在 CI 中不造服务端（`multiprocessing.connection` 的收发带长度前缀，不适合承载原始 HTTP），由实机 R5 覆盖。
7. 门禁：账号开启 basic、端口不通时 `run` / `login` / `code`（假 code）/ 启动命令（POSIX `codex-<名>` 与 `codex-auto`，Windows `.cmd`）均退出 5、stderr 含 `network check failed`、假 codex 未被调用；设 `MULTI_CODEX_SKIP_NETCHECK=1` 后放行，且假 codex 看到的环境里没有该变量。
8. 通过时：假 codex 收到的参数与环境（CODEX_HOME、代理变量、账号 env）与未开启检查时完全一致（POSIX 对比两次运行的 `env` 输出，去掉 `PWD` 等 shell 变量）；当前目录为用户目录；`run`、`login`、`code`、POSIX `codex-<名>` 与 `codex-auto`、Windows `.cmd` 通过时 stderr 恰好含一行 `network check passed for account <名>`；检查范围文字按 5.1.2 规则（basic、strict、只开 Clash 各一例）。
9. POSIX：在含恶意 `json.py`（导入即写标记文件）的目录里运行检查版启动命令，标记文件不出现（D2）；账号目录不存在时退出 1、codex 不在 PATH 时退出 127，提示与未开启检查时逐字相同（D5）；PATH 上没有 `multi-codex` 时退出 127 并给出说明；账号目录不存在且端口不通时，启动命令、`run`、Windows `.cmd` 都退出 1（不是 5）。
10. `usage --live`：检查失败的账号显示 `network check failed` 且假 app-server 未启动；检查通过时只检查一次（假 trace 服务计数为 2）；两个开启检查的账号 + `MULTI_CODEX_SKIP_NETCHECK=1` 时计数为 0（D3）；报错文本里不出现“skipped”提示（D8）；检查通过时 `usage` 的输出与报错里都不出现 `passed`；账号目录不存在时报目录错误而不是 `network check failed`；`MULTI_CODEX_SKIP_NETCHECK="1 "`（带尾随空格）同样跳过。
11. 未开启账号：现有测试全量通过；`posix-launchers-44cb4c1.json` 快照测试通过。

### 8.2 现有回归

- macOS 本机与 CI（ubuntu 3.8/3.12、macos 3.10/3.12）全量 `python -m unittest discover -s tests -t tests`；Windows 行跑 `test_windows.py` 与 `test_netcheck.py`；pyflakes 通过。

### 8.3 实机

macOS 本机（用临时 `HOME` 与临时配置，不动现有账号）：

- M1：从源码 `pipx install` 到临时 `PIPX_HOME` 与 install.sh `--prefix <临时目录>` 两种安装各开一个 basic 账号：端口通放行、不通退出 5。
- M2：strict 档用本机代理实测直连与经代理 `loc`（真实 TLS、macOS 证书）；改国家后拒绝。
- M3：开启检查的账号 `time codex-<名> --version` 与未开启账号对比，记录开销。

Windows 测试机（`192.168.0.74`，临时目录隔离）：

- R1：`.cmd` 启动命令开启 basic，代理设 7897 → 放行；设一个无人监听端口 → 退出 5、提示正确；PowerShell 与 cmd 两种跳过写法都放行，执行后当前会话里 `MULTI_CODEX_SKIP_NETCHECK` 不存在。
- R2：strict + 7897 → 直连与经代理 `loc` 实测值，与配置国家一致时放行、改国家后拒绝；Windows Python 的证书校验正常。
- R3：`chatgpt.com/cdn-cgi/trace` 在测试机直连 / 经代理的实际响应格式（含 `loc=`）。
- R4：7905 端口用途核对（HTTP、SOCKS5 还是其它），据此测一次 socks5 形态。
- R5：Clash Verge 的外部控制接口：找出实际开启的形态（TCP 端口 / 命名管道）、地址与 secret 位置；全局 + TUN 放行，切到规则模式或关 TUN 后拒绝；命名管道读到 EOF 的行为。
- R6：`usage --live`、`login`、`run` 在开启检查时的表现与 §8.1-7/10 一致。
- 测完删除临时目录、计划任务、凭据管理器里的测试条目，逐项核对无残留。

## 9. 日志 / 观测点

- 失败：stderr 固定前缀 `multi-codex: network check failed for account <名> (<检查范围>)`，含失败项名、错误分类、目标（代理 URL 或 Clash 地址，secret 不出现）、耗时；随后列出已通过项、本次跳过与关闭的命令。
- 跳过：`multi-codex: network check skipped for this run (MULTI_CODEX_SKIP_NETCHECK=1)`（`quiet` 不打印；`usage --live` 跳过时整段静默、不提示）。
- 中断：`multi-codex: network check interrupted`，退出 130。
- 诊断：`multi-codex netcheck NAME` 逐项输出 `ok|fail <项> <详情> (<毫秒> ms)`，退出 0 / 5。
- 启动开销：M3 实测结果写进 PR 描述。
- 通过：stderr 一行 `multi-codex: network check passed for account <名> (…; <毫秒> ms)`（`usage --live` 不打印）。

## 10. 开放问题

用户 2026-11-04 已拍板：

- `multi-codex app`（macOS 桌面版）不加检查（“不检查”）。
- 加强档遇到 `https://` 代理直接判失败；Clash 接口地址与 secret 放全局、Clash 检测开关按账号（“都同意”）。
- 检查通过时打印一行（“打印一行”），见 5.1.2。

仍开放：

- Q5：Clash Verge Rev 的外部控制接口默认形态与 secret（未验证，R5 实机核对）；若需从 Clash Verge 配置自动读取地址，另行设计。
