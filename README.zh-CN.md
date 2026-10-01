# multi-codex

[English](README.md) | **简体中文**

在同一台机器上同时使用多个 [Codex CLI](https://github.com/openai/codex) 账号。每个账号有自己的登录、配置和历史记录，还可以有自己的代理；不用再反复退出、重新登录。

```sh
codex-work          # 用工作账号登录的 Codex
codex-personal      # 另开一个终端，用个人账号登录的 Codex
multi-codex list    # 查看每个账号登录的是谁
```

## 工作原理

Codex 把所有东西（配置、凭据、会话）放在一个目录里，这个目录由 `CODEX_HOME` 指定，默认是 `~/.codex`。multi-codex 为每个账号准备一个独立目录，再生成一个小小的启动命令 `codex-<名称>`，用这个目录启动 Codex：

```text
codex-work       -> CODEX_HOME=~/.cx/work       HTTPS_PROXY=http://127.0.0.1:7901
codex-personal   -> CODEX_HOME=~/.cx/personal   （沿用 shell 里的代理设置）
codex            -> ~/.codex，它本身也可以迁移成其中一个账号
```

启动命令是普通的 shell 脚本，即使卸载了 multi-codex 也照常可用。

## 安装

需要 macOS 或 Linux（Windows 请在 WSL 中使用）、Python 3.8 及以上（不需要额外的包），并且 `PATH` 中能找到 Codex CLI。

```sh
curl -fsSL https://raw.githubusercontent.com/jakoes-wu/multi-codex/main/install.sh | sh
multi-codex --version
```

`multi-codex` 命令装在 `~/.local/bin`。如果 shell 提示 `command not found`，说明这个目录还不在 `PATH` 中；安装脚本只给出提示，不会修改你的 shell 配置文件。把下面这行加到 `~/.zshrc` 或 `~/.bashrc`，再打开一个新终端：

```sh
export PATH="$HOME/.local/bin:$PATH"
```

从克隆的仓库安装、用 pipx 安装、装到其它目录，以及下载包如何校验，见“更多安装方式”一节。

## 快速开始

### 1. 每个登录建一个账号

```sh
multi-codex add work
multi-codex add personal
```

每条命令会创建一个目录（`~/.cx/work`）和一个启动命令（`codex-work`）。名称可以包含字母、数字和 `._@+-`，也可以直接用邮箱。

某个账号需要走代理时，给它一个本地端口或代理 URL，例如 `multi-codex add work --proxy 7901`（等同于 `http://127.0.0.1:7901`），见“代理取值”一节。

### 2. 每个账号登录一次

```sh
codex-work login
codex-personal login
```

### 3. 用启动命令代替 `codex`

```sh
codex-work                 # 所有参数都原样传给 codex
codex-personal resume
```

### 4. 检查是否一切正常

```sh
multi-codex list      # 账号、启动命令的状态、登录的邮箱和套餐
multi-codex usage     # 每个账号的 5 小时和每周额度用量
multi-codex doctor    # 找出问题，并给出修复每个问题的命令
```

### 已经在用 Codex？保留现在的登录

现有的 `~/.codex` 也可以变成一个账号，不用重新登录：

```sh
# 先关掉 Codex：终端里的会话、VS Code、桌面端
multi-codex migrate-default main
```

这条命令把 `~/.codex` 移到 `~/.cx/main`，在 `~/.codex` 留一个软链，并创建 `codex-main`。直接运行的 `codex`、VS Code 和桌面端都照常可用。以后用 `multi-codex use work` 就能把默认账号换成别的账号。如果 Codex 把登录信息存在系统钥匙串里，命令会停下并说明原因。细节和撤销方法见“迁移 `~/.codex`”一节。

## 常用操作速查

| 我想 | 命令 | 详见 |
| ---- | ---- | ---- |
| 用某个账号打开 VS Code | `multi-codex code work ~/src/project` | 按账号打开 VS Code 与桌面端 |
| 用某个账号打开桌面端（macOS） | `multi-codex app work` | 按账号打开 VS Code 与桌面端 |
| 换掉直接运行 `codex` 和从 Dock 启动时用的账号 | `multi-codex use work` | 默认账号 |
| 在某个项目里固定使用一个账号 | 在项目目录执行 `multi-codex bind work`，之后用 `multi-codex run` | 目录绑定账号 |
| 设置或修改账号的代理 | `multi-codex proxy work 7901` | 代理取值 |
| 在账号之间共享 `AGENTS.md`、skills、rules | 先 `multi-codex init --shared-dir ~/.codex-shared`，再 `multi-codex add work --shared` | 共享资源 |
| 新账号沿用另一个账号的配置 | `multi-codex add new --config-from work` | 从另一个账号复制配置 |
| 给账号加额外的环境变量 | `multi-codex env work KEY=VALUE` | 每个账号的环境变量 |
| 在新机器上一次建好所有账号 | `./install.sh --config accounts.json` | 用 `apply` 声明式部署 |
| 开启 Tab 补全 | `eval "$(multi-codex completion zsh)"` | shell 补全 |
| 先看看命令会改什么 | 加 `--dry-run` | 命令 |
| 删除账号 | `multi-codex remove work`（账号目录会保留） | 命令 |

更多问题见“常见问题”一节。

## 使用前须知

- **可以放心重复执行**：每条命令都能重跑，已经处于目标状态时输出 `unchanged`。
- **不会覆盖你的文件**：遇到不是 multi-codex 创建的文件挡路时，只报告冲突，不做任何修改。
- **迁移中断可以续跑**：重跑同一条命令，会按磁盘上的实际状态接着做。
- **不是安全边界**：分开的目录只是让各账号的本地状态互不干扰，以你的用户身份运行的任何程序都能读取所有账号目录。

## 命令

| 命令 | 作用 |
| ---- | ---- |
| `multi-codex init [--root DIR] [--bin-dir DIR] [--shared-dir DIR] [--shared-items A,B]` | 创建或修改全局设置 |
| `multi-codex migrate-default 名称 [--source DIR] [--copy] [--keep-backup] [--proxy P] [--skip-process-check] [--accept-relogin]` | 把默认目录迁移成账号 |
| `multi-codex add 名称 [--proxy P] [--shared \| --no-shared] [--adopt] [--config-from 其它账号]` | 新增账号、登记已有目录，或修改账号选项；`--config-from` 从另一个账号复制一次 `config.toml` |
| `multi-codex proxy 名称 端口\|URL\|off\|inherit` | 设置账号的代理 |
| `multi-codex remove 名称` | 注销账号，删除它的启动命令。**账号目录会保留** |
| `multi-codex apply [-f 文件]` | 按配置（或指定文件）收敛全部账号 |
| `multi-codex list [--json]` | 列出账号、启动命令的状态和登录身份 |
| `multi-codex usage [名称 ...] [--live] [--timeout 秒] [--json]` | 显示额度用量 |
| `multi-codex doctor [--json]` | 检查安装、配置和各账号，只读 |
| `multi-codex run [名称] [-- 命令 ...]` | 在账号的环境下运行命令（默认运行 `codex`）；省略名称时，使用当前目录绑定的账号 |
| `multi-codex bind [名称 [目录]]` / `unbind [目录]` | 把目录绑定到账号、列出绑定，或解除绑定 |
| `multi-codex code 名称 [路径] [-- 参数]` | 按账号打开 VS Code（实验功能） |
| `multi-codex app 名称` | 按账号打开 Codex 桌面端（仅 macOS，实验功能） |
| `multi-codex path 名称` | 输出账号目录 |
| `multi-codex env 名称 [KEY=VALUE ...] [--unset KEY] [--clear]` | 列出或修改账号的额外环境变量 |
| `multi-codex use [名称] [--skip-process-check]` | 显示或切换默认账号（`~/.codex` 指向的账号） |
| `multi-codex restore 名称 [--skip-process-check] [--accept-relogin]` | 撤销 `migrate-default`：把账号移回 `~/.codex` |
| `multi-codex completion bash\|zsh\|fish` | 输出 shell 补全脚本 |

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

### shell 补全

```sh
eval "$(multi-codex completion bash)"     # 写进 ~/.bashrc
eval "$(multi-codex completion zsh)"      # 写进 ~/.zshrc，放在 compinit 之后
multi-codex completion fish | source      # 写进 ~/.config/fish/config.fish
```

可以补全子命令、选项和已登记的账号名（包括邮箱形式的名字）。

### 运行其它命令

`multi-codex run 名称 -- 命令 ...` 以与 `codex-名称` 完全相同的环境运行任意命令：`CODEX_HOME`、代理、额外的环境变量都一样。不带命令时运行 `codex`。第一个 `--` 之后的内容原样传给命令，退出码就是该命令的退出码。

`multi-codex path 名称` 输出账号目录。

### 目录绑定账号

```sh
cd ~/work/project && multi-codex bind work     # 这个目录及其所有子目录都使用 work
multi-codex run -- codex resume                # 在这里不用写账号名
multi-codex bind                               # 列出绑定，* 标出对当前目录生效的那一条
multi-codex unbind                             # 解除当前目录的绑定
```

- **查找规则**：`run` 不带账号名时，从当前目录开始逐级向上，使用最近一个已绑定的目录。
- **存放位置**：绑定保存在 `config.json` 中，不会往项目目录写任何文件。
- **路径匹配**：以目录的真实路径为准，即解析软链后的路径；在不区分大小写的文件系统上，使用磁盘上的真实大小写。
- **与其它命令的关系**：`apply -f` 保留现有的绑定；注销账号时（`remove`、`restore`、`apply -f`），它的绑定会一并删除。
- **降级**：旧版本的 multi-codex 会在下一次写配置时丢掉 `bindings` 字段。

### 从另一个账号复制配置

`multi-codex add new --config-from work` 把 `work` 的 `config.toml` 复制一次到 `new`，之后两份文件各自独立。

以下情况视为冲突，什么都不写：
- `new` 已有内容不同的 `config.toml`；
- `new` 共享了 `config.toml`。

复制的是整个文件，包括 `cli_auth_credentials_store` 设置，以及指向原账号的绝对路径。

### 每个账号的环境变量

```sh
multi-codex env work OPENAI_BASE_URL=https://example.com/v1 TERM_PROGRAM=vscode
multi-codex env work                      # 列出
multi-codex env work --unset TERM_PROGRAM
multi-codex env work --clear
```

- **存放位置**：变量保存在 `config.json` 的 `accounts.<名称>.env` 中，并写进启动命令。
- **按字面使用**：值不会展开 `$VAR`。
- **保留变量**：`CODEX_HOME` 和代理变量不能在这里设置，代理请用 `multi-codex proxy`。
- **保护范围**：设置了环境变量的启动命令只有你自己能读（权限 0700），`list --json` 也只显示变量名。但值仍以明文保存，需要更强保护的密钥不要放在这里。
- **降级前先清空**：旧版本的 multi-codex 会忽略 `env` 字段，并在下一次写配置时丢掉它；降级前请先执行 `multi-codex env 名称 --clear`。

### 按账号打开 VS Code 与桌面端（实验功能）

```sh
multi-codex code work ~/src/project        # 打开一个使用 work 账号的独立 VS Code 窗口
multi-codex app work                       # 打开一个独立的 Codex 桌面端实例（macOS）
```

这两个命令依赖 VS Code 和桌面端没有公开的行为，验证过的版本是 VS Code 1.139.1、OpenAI 扩展 26.928.31416、Codex 桌面端 26.831.11858。它们升级后可能失效。

`code`：
- 以账号的环境（与 `run` 相同）运行 `code --user-data-dir <root>/.apps/<名称>/vscode`。
- 每个账号有自己的 VS Code 设置，扩展则共用 `~/.vscode/extensions`。
- 在 macOS 上，`code` 命令会把全部环境变量（包括账号的环境变量）交给 `open --env`，这些值会在进程列表里短暂可见。

`app`：
- 通过 `open -n` 启动 `/Applications/ChatGPT.app`（bundle id `com.openai.codex`），`CODEX_HOME` 设为账号目录，使用独立的数据目录 `<root>/.apps/<名称>/desktop`，输出写到同目录下的 `desktop.log`。
- 桌面端会加载登录 shell 的环境，所以它使用 shell 的代理设置，不使用账号的代理；账号的额外环境变量也不会传过去。
- 请一次只在一个实例中登录：登录回调使用本机固定的端口。
- 某个账号的实例运行时，从 Dock、Finder 或用 `open -a` 正常打开桌面端，只会把那个实例切到前台，不会启动默认账号的实例。要同时使用默认账号，请用 `open -n -a /Applications/ChatGPT.app` 启动。

### 默认账号

迁移之后，`~/.codex` 是指向某个账号的软链。直接运行的 `codex`、Codex 桌面端和 IDE 扩展都使用这个账号。`multi-codex use` 显示当前是哪个账号，`multi-codex use 名称` 把软链原子地改指向另一个账号。

**切换前要关闭 Codex**：
- 没有设置 `CODEX_HOME` 的 Codex 进程，运行期间会按 `~/.codex` 重新打开文件。如果在它运行时切换，两个账号的文件会混在一起。
- 因此只要有进程正在使用当前的默认账号，`use` 就拒绝切换（退出码 4）。用 `codex-<名称>` 启动的会话无法与之区分，也会被拦下。
- 请先关闭 CLI、桌面端、IDE 扩展和 app-server daemon，再切换，然后重新打开它们。
- 这项检查只能看到检查那一刻打开着的文件，所以只是一道保险，不能保证完全没有问题。

如果对 `~/.codex` 当前指向的账号执行了 `remove`，软链会指向一个已注销的目录，`doctor` 会报告这种情况。

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

### 撤销迁移

```sh
multi-codex restore main
```

`restore` 依次执行：
1. 删除 `~/.codex` 软链；
2. 把 `~/.cx/main` 移回 `~/.codex`；
3. 注销账号，删除它的启动命令。目录里的共享软链会保留，照常可用。

它与 `migrate-default` 做同样的检查：
- 目录是否正被占用；
- 凭据是否存在系统钥匙串里。如果是，移回后会丢失登录；加 `--accept-relogin` 可以照常执行。

其它注意事项：
- **中断后**：重跑同一命令即可继续。restore 没完成之前，其它写命令会被拒绝。
- **`~/.codex` 指向的不是这个账号时**：先执行 `multi-codex use main`。
- **账号目录与 `~/.codex` 不在同一个文件系统时**：需要手工撤销：

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

### 哪些可以共享

依据 Codex 源码（openai/codex，版本 `6b4daafd`）：

| 条目 | 用途 | 能否共享 | 原因 |
| ---- | ---- | ---- | ---- |
| `AGENTS.md` | 全局指令 | 可以（默认共享项） | 每次加载都重新读取，Codex 不写它 |
| `agents/` | 自定义 agent 角色 | 可以（默认共享项） | 只读 |
| `rules/` | 执行策略（“总是允许”的命令） | 可以（默认共享项） | 追加时加文件锁。共享后，一个账号批准的“总是允许”对所有账号生效 |
| `skills/` | 技能 | 可以（默认共享项） | 启动时内置技能有变化，Codex 会重写 `skills/.system`；各账号使用同一个 Codex 版本就没有影响 |
| `config.toml` | 配置 | 可以，但要小心 | Codex 写入时会跟随软链、原子替换目标文件，软链不会被破坏；但没有跨进程锁，两个账号同时改配置时，可能丢掉其中一次修改 |
| `history.jsonl` | 输入历史 | 可以 | 读写都加文件锁；各账号的输入历史会合在一起 |
| `auth.json`、`secrets/`、`.credentials.json`、`.env` | 凭据 | **不能** | 它们就是账号本身 |
| `installation_id` | 安装标识 | 不要共享 | 会随请求发出；共享后多个账号看起来像同一个安装 |
| `*.sqlite`（`state_5.sqlite` 等） | 线程、日志、记忆数据库 | **不能** | `state_5.sqlite` 记录了账号 ID |
| `sessions/`、`archived_sessions/`、`session_index.jsonl` | 会话记录 | 不要共享 | 索引只有进程内的锁；会话中记有创建者账号 |
| `models_cache.json`、`cache/` | 缓存 | 不需要 | 按账号区分，账号不符就当作未命中 |
| `app-server-control/`、`app-server-daemon/`、`packages/`、`tmp/`、`.tmp/`、`log/`、`shell_snapshots/` | 运行时状态 | 不要共享 | 按进程或按会话使用 |

分开的目录只是让各账号的本地状态互不干扰，**不是**安全边界：以你的用户身份运行的任何程序，都能读取所有账号目录。

## 常见问题

### 从 Dock 启动 VS Code 和桌面端时，用的是哪个账号？

用的是 `~/.codex` 里的账号。原因：
- 从 Dock 或 Finder 启动的应用拿不到终端里设置的 `CODEX_HOME`；
- 拿不到时，OpenAI 扩展和 Codex 桌面端都退回到 `~/.codex`，它们的代码里写的是 `process.env.CODEX_HOME ?? ~/.codex`。

它们会读取登录 shell 的环境，所以在 shell 配置文件里导出 `CODEX_HOME` 也能生效。但不推荐这样做：终端里直接运行的 `codex` 也会跟着换账号。

### 怎样切换这个默认账号？

让 multi-codex 接管 `~/.codex`，然后用 `use` 切换：

```sh
# 先关掉所有在用 Codex 的程序（VS Code、桌面端、终端里的 codex 会话）
multi-codex migrate-default main        # 把现在的 ~/.codex 变成名为 main 的账号
multi-codex use work                    # 让 ~/.codex 指向 work 账号
multi-codex use                         # 查看当前默认账号
```

之后从 Dock 启动的应用，用的就是 `use` 指向的账号。

要撤销迁移，先让 `~/.codex` 指回原账号：先执行 `multi-codex use main`，再执行 `multi-codex restore main`。`~/.codex` 指向其它账号时，`restore` 会拒绝执行。

有进程正在使用相关目录时，`use` 和 `restore` 都会拒绝执行，所以要先关掉 Codex，详见“默认账号”一节。

### 怎样用指定的账号打开 VS Code？

```sh
multi-codex code work ~/src/project
```

它会以该账号的环境，启动一个使用独立用户数据目录的 VS Code 实例。

用户数据目录必须独立：如果用同一个，`code` 只会把请求交给已经在运行的 VS Code，而那个窗口里的 Codex 扩展仍然使用它启动时的环境。

OpenAI 扩展本身没有选择账号的设置项。详见“按账号打开 VS Code 与桌面端（实验功能）”一节。

### 怎样用指定的账号打开 Codex 桌面端？

```sh
multi-codex app work
```

如果要手工执行，下面两个变量都要设置。缺少 `CODEX_ELECTRON_USER_DATA_PATH` 时，会出现两个问题：
- 桌面端启动后会用登录 shell 中的值替换 `CODEX_HOME`；
- 它会和默认实例共用数据目录。

```sh
D="$HOME/.cx/.apps/work/desktop"; mkdir -p "$D"
open -n --env CODEX_HOME="$HOME/.cx/work" --env CODEX_ELECTRON_USER_DATA_PATH="$D" \
  -a /Applications/ChatGPT.app --args --user-data-dir="$D"
```

### `open -n -a /Applications/ChatGPT.app` 在哪里执行？

在任意终端窗口（Terminal、iTerm、Warp 等）中执行即可，与当前目录无关。它是 macOS 自带的 `open` 命令，不属于 multi-codex。

- `-n` 表示即使已经有实例在运行，也再启动一个新实例；
- 不设置 `CODEX_HOME` 时，新实例使用 `~/.codex`。

用 `multi-codex app` 打开了某个账号的实例后，如果还想同时使用默认账号，就需要这条命令：此时从 Dock、Finder 或用 `open -a` 正常打开，只会把正在运行的那个实例切到前台。

### `codex login status` 显示已登录，桌面端却要求登录，为什么？

`codex login status` 只检查凭据文件是否存在，不检查令牌是否仍然有效。一个目录长时间没有使用，令牌可能已经不被接受，桌面端就会显示登录页。

解决办法：给这个目录重新登录，账号目录执行 `codex-<名称> login`。

`multi-codex usage --live 名称` 会向 Codex 查询实时额度，登录失效时会报错，可以用来检查登录是否仍然有效。

### 怎样升级 multi-codex？

重新执行安装命令即可，配置、账号和启动命令都不受影响：

```sh
curl -fsSL https://raw.githubusercontent.com/jakoes-wu/multi-codex/main/install.sh | sh
multi-codex --version
```

## 退出码

| 退出码 | 含义 |
| ---- | ---- |
| 0 | 成功，或已处于目标状态 |
| 1 | 运行错误（IO 错误、配置文件不合法、校验失败、锁被其它命令占用、未完成的迁移或 restore 拦下了命令）；`usage` 中至少一个账号失败；`doctor` 中至少一项检查为 fail |
| 2 | 命令行参数不合法 |
| 3 | 与不归 multi-codex 管理的文件冲突；`migrate-default` 或 `restore` 因凭据存在系统钥匙串而拒绝；`use` / `restore` 发现 `~/.codex` 状态不对或不在同一个文件系统。没有做任何修改 |
| 4 | 要迁移、要切走或要移回的目录正被占用 |

## 更多安装方式

从克隆的仓库安装：

```sh
git clone https://github.com/jakoes-wu/multi-codex.git
cd multi-codex
./install.sh
```

用 pipx 安装：`pipx install git+https://github.com/jakoes-wu/multi-codex`。

工具本身装到 `~/.local/share/multi-codex`，`multi-codex` 命令装到 `~/.local/bin`；要换位置，用 `--prefix DIR` 指定。全部安装选项见 `./install.sh --help`。

**下载校验**：从 v0.5.0 起，每个 release 都附带 `multi-codex-<tag>.tar.gz` 和 `SHA256SUMS`。远程安装会下载这个包，先校验 SHA-256，不一致就停止安装。安装分支或更早的版本时没有校验，安装脚本会明确提示；设置 `MULTI_CODEX_REQUIRE_CHECKSUM=1` 可以拒绝这种安装。校验和与安装包放在同一个 release 里，只能发现下载过程中的损坏或篡改，不能防范 GitHub 账号本身被攻破。

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
