# multi-codex

[English](README.md) | **简体中文**

在同一台机器上同时使用多个 [Codex CLI](https://github.com/openai/codex) 账号。

Codex 把配置、凭据和会话数据库存放在 `CODEX_HOME` 指定的目录里（默认 `~/.codex`）。multi-codex 为每个账号准备独立的目录和独立的启动命令，每个账号还可以单独设置代理：

```text
codex-work       -> CODEX_HOME=~/.cx/work       HTTPS_PROXY=http://127.0.0.1:7901
codex-personal   -> CODEX_HOME=~/.cx/personal   （沿用 shell 里的代理设置）
codex            -> ~/.codex，它本身也可以迁移成其中一个账号
```

## 功能

- **迁移默认目录**：把现有的 `~/.codex` 变成一个具名账号，原位置留兼容软链。直接执行 `codex`、以及历史记录里的旧绝对路径都照常可用。
- **新增账号**：创建账号目录和 `codex-<名称>` 启动命令；也可以把已有目录登记为账号。
- **每个账号单独设置代理**：可以是本地端口、完整代理 URL、`off` 或 `inherit`。
- **一键部署**：`install.sh --config accounts.json` 一条命令装好工具，并建好文件里描述的全部账号。
- **幂等**：
  - 每条命令都可以重复执行；已处于目标状态时输出 `unchanged`；
  - 遇到不归本工具管理的文件时报告冲突，不做任何修改；
  - 迁移被中断后，重跑会从中断处继续。
- **可选的共享资源**：把 `AGENTS.md`、`skills`、`rules`、`agents` 等从同一个共享目录软链到指定账号。
- **看得到登录身份和剩余额度**：`list` 显示每个账号登录的邮箱和套餐；`usage` 从本地会话记录显示 5 小时和每周额度，加 `--live` 时查询实时额度。
- **体检**：`doctor` 检查安装、环境和各账号，并给出修复每个问题的命令。

## 环境要求

- macOS 或 Linux（Windows 支持在计划中；WSL 内按 Linux 使用）
- Python 3.8 及以上（只用标准库）
- `PATH` 中能找到 Codex CLI

## 安装

在克隆的仓库里安装：

```sh
git clone https://github.com/jakoes-wu/multi-codex.git
cd multi-codex
./install.sh
```

或直接远程安装：

```sh
curl -fsSL https://raw.githubusercontent.com/jakoes-wu/multi-codex/main/install.sh | sh
```

- 工具本身装到 `~/.local/share/multi-codex`，`multi-codex` 命令装到 `~/.local/bin`；要换位置，用 `--prefix DIR` 指定。
- 请确认 `~/.local/bin` 在 `PATH` 中。安装脚本只给出提示，不会修改你的 shell 配置文件。
- 也可以用 pipx 安装：`pipx install git+https://github.com/jakoes-wu/multi-codex`。
- 全部安装选项见 `./install.sh --help`。

## 快速开始

```sh
# 把现有的 ~/.codex 迁移成名为 main 的账号
multi-codex migrate-default main

# 新增一个账号，经本地 7901 端口的代理访问
multi-codex add work --proxy 7901

# 每个账号登录一次，然后就可以使用
codex-work login
codex-work

multi-codex list
```

## 命令

| 命令 | 作用 |
| ---- | ---- |
| `multi-codex init [--root DIR] [--bin-dir DIR] [--shared-dir DIR] [--shared-items A,B]` | 创建或修改全局设置 |
| `multi-codex migrate-default 名称 [--source DIR] [--copy] [--keep-backup] [--proxy P] [--skip-process-check] [--accept-relogin]` | 把默认目录迁移成账号 |
| `multi-codex add 名称 [--proxy P] [--shared \| --no-shared] [--adopt]` | 新增账号、登记已有目录，或修改账号选项 |
| `multi-codex proxy 名称 端口\|URL\|off\|inherit` | 设置账号的代理 |
| `multi-codex remove 名称` | 注销账号，删除它的启动命令。**账号目录会保留** |
| `multi-codex apply [-f 文件]` | 按配置（或指定文件）收敛全部账号 |
| `multi-codex list [--json]` | 列出账号、启动命令的状态和登录身份 |
| `multi-codex usage [名称 ...] [--live] [--timeout 秒] [--json]` | 显示额度用量 |
| `multi-codex doctor [--json]` | 检查安装、配置和各账号，只读 |

所有写命令都支持 `--dry-run`。`list`、`usage`、`doctor` 不会修改任何东西；加 `--json` 时，stdout 上只输出一个 JSON 对象（带 `"version": 1` 字段），警告仍写到 stderr。脚本请使用 `--json`：表格格式不保证稳定。

### 登录身份与额度

`list` 增加了 LOGIN 和 PLAN 两列，内容读自各账号本地的 `auth.json`。不会输出任何令牌，也不会联网。LOGIN 列可能是：
- 邮箱；
- `api-key`；
- `-`：未登录；
- `keyring`：凭据存在系统钥匙串里，无法从文件读取；
- `unreadable`：凭据文件读不了。

PLAN 是当前令牌签发时的套餐，Codex 下次刷新令牌后才会更新。两个账号登录的是同一个 ChatGPT 用户和工作区时，`list` 会给出警告，因为它们共用一份额度。

`multi-codex usage` 从各账号的会话记录（`sessions/` 和 `archived_sessions/`）中读取最近一次的额度快照。这种方式不联网，但数据可能已经过时；快照之后已经重置的窗口显示为 `reset since snapshot`。

`multi-codex usage --live` 通过该账号的启动命令运行 `codex app-server`，所以会使用该账号的代理；然后向它查询当前额度（`account/rateLimits/read`，需要 Codex 0.48.0 或更高版本）。multi-codex 自己不读取、也不发送令牌。与平常运行 Codex 一样，Codex 可能刷新该账号的令牌并写回该账号的目录。多个账号依次查询，`--timeout` 限制每个账号的总耗时（默认 30 秒）。

### 体检

`multi-codex doctor` 每项检查输出一行（`ok`、`warn` 或 `fail`），有问题时再加一行 `fix:`，给出要执行的命令。检查内容：
- `codex` 可执行文件及其版本；
- 配置文件；
- 未完成的迁移；
- `bin_dir` 是否在 `PATH` 中；
- 会破坏隔离的环境变量；
- `~/.codex` 指向哪里；
- 实际文件与配置是否一致（与 `apply` 会执行的计划相同）；
- 各账号的登录状态；
- 重复登录。

它不修复任何东西，也不联网。有任一项为 fail 时退出码为 1，否则为 0。

### 默认位置

| 项目 | 默认值 |
| ---- | ---- |
| 账号目录 | `~/.cx/<名称>` |
| 启动命令 | `~/.local/bin/codex-<名称>` |
| 配置文件 | `~/.config/multi-codex/config.json`（设置了 `XDG_CONFIG_HOME` 时以它为准） |

账号名规则：
- 只能包含字母、数字和 `._@+-`，且必须以字母或数字开头；
- 不区分大小写，`Work` 和 `work` 是同一个账号；
- 邮箱可以直接用作账号名。

### 代理取值

| 取值 | 启动命令中的效果 |
| ---- | ---- |
| `inherit`（默认） | 代理变量保持 shell 里的原样 |
| `off` | 清除 `HTTPS_PROXY`、`HTTP_PROXY`、`ALL_PROXY`、`NO_PROXY` 及其小写形式 |
| `7901` | 等同于 `http://127.0.0.1:7901` |
| `http://主机:端口`、`https://…`、`socks5://…`、`socks5h://…` | 设置 `HTTPS_PROXY` 和 `HTTP_PROXY`（大小写两种形式）；只有 SOCKS 代理才设置 `ALL_PROXY`，其它代理会清除从 shell 继承来的 `ALL_PROXY`；在原有的 `NO_PROXY` 后追加 `localhost,127.0.0.1,::1` |

代理 URL 不能带用户名和密码：启动命令是所有用户都能读取的明文文件。

> **建议使用 HTTP 代理。** Codex 官方文档没有列出它支持哪些代理变量。以下是在 Linux 上用 codex-cli 0.159.0 实测的结果：
>
> - `HTTPS_PROXY`、`HTTP_PROXY`，以及单独设置的 `ALL_PROXY` 都会生效。所有连接（chatgpt.com、ab.chatgpt.com、oaiusercontent.com）都经过了代理，没有绕过代理的连接。
> - `off` 有效：父 shell 设置了代理变量时，Codex 仍然直连。
> - 使用 `socks5h://` 地址时，大部分连接仍然按 HTTP `CONNECT` 发往该端口。所以只有同时支持 HTTP 的端口（例如 mixed 端口）才能用 SOCKS 代理；只支持 SOCKS 的端口会导致大部分请求失败。设置 SOCKS 代理时，multi-codex 会给出警告。

### 用 `apply` 声明式部署

```json
{
  "version": 1,
  "root": "~/.cx",
  "bin_dir": "~/.local/bin",
  "shared": {"dir": "~/.codex-shared", "items": ["AGENTS.md", "skills"]},
  "accounts": {
    "work": {"proxy": "http://127.0.0.1:7901", "shared": true},
    "personal": {"proxy": "inherit"}
  }
}
```

```sh
multi-codex apply -f accounts.json
# 在新机器上一步完成：
./install.sh --config accounts.json
```

`apply -f` 的行为：
- 用该文件替换当前配置；
- 文件中没有的账号会被注销，账号目录保留；
- 只要有任何冲突，就什么都不写。

## 迁移 `~/.codex`

执行 `multi-codex migrate-default main` 时，工具按以下步骤进行：

1. **凭据存储检查**：以下情况拒绝开始：
   - Codex 把凭据存在系统钥匙串里，即 `config.toml` 或 `/etc/codex/config.toml` 中设置了 `cli_auth_credentials_store = "keyring"`；
   - 设置为 `"auto"`，且目录中没有 `auth.json`。

   钥匙串条目和目录路径绑定，迁移后会丢失登录。确实要迁移时加 `--accept-relogin`，迁移完成后重新登录即可。
2. **占用检查**：只要有进程在 `~/.codex` 里打开了文件、把工作目录设在其中，或者可执行文件位于其中，就拒绝开始。请先关闭 Codex、IDE 扩展和 ChatGPT 浏览器扩展宿主。
3. **移动数据**：
   - `~/.codex` 和 `~/.cx` 在同一个文件系统上时，直接把 `~/.codex` 改名为 `~/.cx/main`；
   - 不在同一个文件系统上时，先复制，再逐个文件用 SHA-256 校验，然后把原目录改名为 `~/.codex.multi-codex-bak.<时间戳>` 暂存。
4. **建链接并登记**：创建软链 `~/.codex -> ~/.cx/main`，并登记账号。

**中断与续跑**

- 迁移进度记录在 `~/.config/multi-codex/migrate-journal.json`。
- 迁移中断后，重跑同一条命令即可，工具会按磁盘上的实际状态接着做。
- 迁移未完成期间，其它写命令会拒绝执行。

**copy 模式的限制**

- socket、FIFO 这类运行时文件（例如 `ipc.sock`）不会被复制。
- macOS 上 copy 模式不保留扩展属性（xattr）。

**环境变量提醒**：如果设置了 `CODEX_HOME`、`CODEX_SQLITE_HOME`、`CODEX_API_KEY` 或 `CODEX_ACCESS_TOKEN`，工具会给出警告，因为这些变量会覆盖或绕过账号之间的隔离。

### 手工撤销迁移

```sh
rm ~/.codex                     # 删除软链（只删链接本身）
mv ~/.cx/main ~/.codex          # 把数据移回原位
multi-codex remove main         # 注销账号
```

在 copy 模式下使用了 `--keep-backup` 时，原目录保留在 `~/.codex.multi-codex-bak.<时间戳>`。

如果迁移报错停下，而你想放弃这次迁移：
1. 按报错信息找到完整数据所在的目录；
2. 把它移回 `~/.codex`；
3. 删除 `~/.config/multi-codex/migrate-journal.json`。

## 共享资源

```sh
multi-codex init --shared-dir ~/.codex-shared --shared-items AGENTS.md,skills,rules,agents
multi-codex add work --shared
```

- multi-codex 只创建缺少的链接，并记住哪些链接是它自己建的。
- 关闭共享时，只删除它建的那些链接，你自己建的链接不受影响。
- 如果你以前手工把某个账号软链到了共享目录，可以用 `multi-codex add 名称 --shared --adopt` 让工具接管这些链接：链接本身不重建，但之后关闭共享时也会被删除。只接管已经指向对应共享条目的链接。
- 链接位置上如果已经是真实的文件或目录，视为冲突，绝不覆盖。

## 退出码

| 退出码 | 含义 |
| ---- | ---- |
| 0 | 成功，或已处于目标状态 |
| 1 | 运行错误（IO 错误、配置文件不合法、校验失败、锁被其它命令占用）；`usage` 中至少一个账号失败；`doctor` 中至少一项检查为 fail |
| 2 | 命令行参数不合法 |
| 3 | 与不归 multi-codex 管理的文件冲突，或因凭据存在系统钥匙串而拒绝迁移；没有做任何修改 |
| 4 | 迁移源目录正被占用 |

## 卸载

```sh
./install.sh --uninstall
```

卸载只删除工具本身，配置、账号目录和 `codex-<名称>` 启动命令都会保留。启动命令不依赖 multi-codex，卸载后仍能继续使用。

## 参与贡献

见 [CONTRIBUTING.md](CONTRIBUTING.md)。运行测试：

```sh
python3 -m unittest discover -s tests -t tests
```

## 许可证

[MIT](LICENSE)
