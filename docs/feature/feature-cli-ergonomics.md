# 日常操作：shell 补全、run / path、每账号环境变量、共享项对照表（v0.4）

> 2026-10-01 注记：代码已落地（未提交，等待编译机验证）。
> - 补全：`src/multi_codex/completion.py`。
> - `run` / `path` / `env`：`src/multi_codex/cli.py` 中的 `cmd_run`、`cmd_path`、`cmd_env_list`、`_apply_env_changes`。
> - 环境变量的渲染与权限：`src/multi_codex/launcher.py` 中的 `render`、`launcher_mode`、`launcher_file_ok`。
> - 配置：`src/multi_codex/config.py` 中的 `Account.env`、`validate_env_key`。
> - 代理变量列表移到了 `config.PROXY_ENV_VARS`，`launcher` 引用它，避免循环导入。
> - §8 的用例在 `tests/test_ergonomics.py` 中。
>
> 本文其余部分保持方案原文。与 `docs/feature/feature-default-switch.md`（U9）一起落地：本方案中 `use` / `restore` 的补全依赖那份方案。

## 1. 背景

v0.3.0 之后，日常使用还有几处不便：

- 账号名通常是邮箱，在命令行里手打很长，而且没有补全；
- 只能通过 `codex-<名>` 运行 codex 本身。要在某个账号的环境（`CODEX_HOME`、代理）下运行别的命令，只能手工导出环境变量；
- 启动命令只能设置代理，不能为某个账号额外设置环境变量；
- README 只列出了可以共享的条目，没有说明哪些条目不能共享、原因是什么。

本方案对应路线中的 U6、U7、U8、U10。

## 2. 目标 / 非目标

**目标**

1. U6：`multi-codex completion bash|zsh|fish` 输出补全脚本，能补全子命令、选项和已登记的账号名（含 `@`）。
2. U7：`multi-codex run NAME [-- CMD ...]` 在该账号的环境下运行命令（不带 CMD 时运行 `codex`）；`multi-codex path NAME` 输出账号目录。
3. U8：配置 `accounts.<名>.env` 为每个账号设置额外的环境变量，由命令 `multi-codex env` 维护，写进启动命令。
4. U10：README 中英文增加“共享项与独立项”对照表和安全边界说明。

**非目标**

- 不修改用户的 shell 启动文件，只输出脚本，由用户自己加 `eval`。
- `run` 不加写锁、不改动任何文件，也不检查账号是否已登录。
- 环境变量的值不经 shell 求值：不支持 `$HOME` 之类的引用，按字面写入。
- 不支持从文件导入环境变量（例如 `.env`）。
- U10 只改文档，不改变默认的共享条目。

## 3. 假设与约束

- 只依赖 Python 3.8 标准库。`atomic_write` 写完后会显式 `chmod` 到指定权限（`fsutil.py:78`），不受 umask 影响。补全脚本不能依赖 bash-completion 包，因为 macOS 自带的 bash 3.2 没有它。
- 启动命令的现有内容（没有设置环境变量时）必须与 v0.3.0 **逐字节相同**。否则升级后所有启动命令都会被判为 stale，`doctor` 会报 drift，`apply` 会全部重写。
- `config.json` 中，没有环境变量的账号**不写** `env` 字段。否则升级后的第一条写命令会把每个账号的配置都判为 update（`accounts.py:94` 按序列化文本比较配置）。

## 4. 涉及模块

| 区域 | 行号锚点（基线 `493818f`） | 改动类型 | 改动点 |
| ---- | ---- | ---- | ---- |
| 配置模型 | `src/multi_codex/config.py:38-49` `Account` | 修改 | 新增属性 `env: Dict[str, str]`；`copy` 复制它；`to_dict` 只在非空时写 `env`，按键名排序后写入（避免 `apply -f` 文件中键的顺序不同就被判为配置改动） |
| 配置解析 | `src/multi_codex/config.py:175-189` `_parse_account` | 修改 | 解析并校验 `env`，见 §5.1.3 |
| 配置校验 | `src/multi_codex/config.py:192` `validate_name` 之后 | 新增 | `validate_env_key(key)`、常量 `RESERVED_ENV_KEYS` |
| 启动命令 | `src/multi_codex/launcher.py:28-54` `render` | 修改 | 新增参数 `env: Optional[Dict[str, str]] = None` 和 `command_mode: bool = False`，见 §5.1.2、§5.1.3 |
| 启动命令 | `src/multi_codex/launcher.py:17` `LAUNCHER_MODE` 之后 | 新增 | `PRIVATE_LAUNCHER_MODE = 0o700`；`launcher_mode(env)` |
| 收敛引擎 | `src/multi_codex/accounts.py:121-135` `_plan_launcher` | 修改 | 签名增加 `env`；传给 `render`；写入时用 `launcher_mode(env)`；判断是否需要重写的条件见 §5.1.3 |
| 收敛引擎 | `src/multi_codex/accounts.py:72` `_plan_launcher` 调用点 | 修改 | 传入 `account.env` |
| 收敛引擎 | `src/multi_codex/accounts.py:192-202` `launcher_status` | 修改 | 期望内容按 `account.env` 渲染；是否 ok 的判断与 `_plan_launcher` 共用同一个函数 `launcher_file_ok(path, content, env)` |
| 补全 | 新文件 `src/multi_codex/completion.py` | 新增 | 从 argparse 解析器生成三种 shell 的补全脚本，见 §5.1.1 |
| 命令行 | `src/multi_codex/cli.py:96-98` `doctor` 子命令之后 | 新增 | `completion`、`run`、`path`、`env` 四个子命令 |
| 命令行 | `src/multi_codex/cli.py:120-133` `main` 开头 | 修改 | 解析参数之前，如果第一个参数是 `run`，就在第一个 `--` 处把 argv 分成两段，`--` 之后的部分原样作为命令，见 §5.1.2 |
| 命令行 | `src/multi_codex/cli.py:134-137` | 修改 | `completion`、`run`、`path` 与 `usage` 一样，在 `pending_journal_notice` 之前分派 |
| 命令行 | `src/multi_codex/cli.py:141-143` 之间 | 新增 | `env NAME`（不带任何修改参数，带不带 `--dry-run` 都算列出）在这里分派：不加锁，有未完成的迁移时也照常可用 |
| 命令行 | `src/multi_codex/cli.py:178-239` `dispatch` | 修改 | 新增 `env` 的写操作分支（设置、删除、清空），走 `converge` |
| 测试 | 新文件 `tests/test_ergonomics.py` | 新增 | 见 §8 |
| CI | `.github/workflows/ci.yml` 的 Linux 安装步骤 | 修改 | 增装 zsh、fish，让补全脚本的语法检查在 CI 中执行 |
| 文档 | `README.md`、`README.zh-CN.md`：命令表、新增 Shell completion / Running other commands / Per-account environment 三节、Shared resources 一节补对照表；`CHANGELOG.md`；`docs/feature/feature-account-manager.md` §5.1.2 命令表与 §5.1.3 启动命令内容 | 修改 | 见 §5.1.4 |

## 5. 方案

### 5.1 实现要点

#### 5.1.1 U6 补全

命令：`multi-codex completion bash|zsh|fish` 把脚本写到 stdout，退出码 0。shell 参数用 `nargs='?'`：只带 `--list-accounts` 时可以省略；既没有 shell 参数也没有 `--list-accounts` 时报参数错误，退出码 2。用法写进 README：

```sh
eval "$(multi-codex completion bash)"                     # ~/.bashrc
eval "$(multi-codex completion zsh)"                      # ~/.zshrc，放在 compinit 之后
multi-codex completion fish | source                      # ~/.config/fish/config.fish
```

**静态部分在生成时从解析器读出**：遍历 `build_parser()` 的子命令，得到子命令名，以及每个子命令的全部选项字符串（不含 `-h` / `--help`，`completion` 子命令本身的隐藏选项也排除），同时记录哪些选项需要带一个值（`action.nargs != 0`，例如 `--source`、`--proxy`、`--timeout`）。这样以后新增选项时，补全脚本自动同步，不需要另外维护一份列表。

**账号名在补全时动态读取**：隐藏选项 `multi-codex completion --list-accounts` 每行输出一个已登记的账号名。它只读 `config.json`，不输出任何警告；配置不存在或读不了时什么都不输出，退出码 0。

**哪些位置补账号名**：子命令 `add`、`proxy`、`remove`、`migrate-default`、`run`、`path`、`env`、`usage`、`use`、`restore` 之后的第一个非选项参数（`usage` 的每个非选项参数都补）。`proxy` 的第二个参数补 `off`、`inherit`。`run` 的 `--` 之后不补全。“第几个非选项参数”按空白切分后计数：以 `-` 开头的词不算；需要带值的选项，其后紧跟的那个词也不算。三种 shell 用同一套规则。

**bash 中的 `@`**：bash 默认把 `@`（以及 `=`、`:` 等）当作单词分隔符（bash 3.2.57 中 `COMP_WORDBREAKS` 默认是 `"'@><=;|&(:` 加空白），会把 `a@b.com` 拆成几段，所以 `COMP_WORDS` 和 `COMP_CWORD` 都不可靠。做法：

- 子命令、参数位置、当前词，一律从 `${COMP_LINE:0:COMP_POINT}` 按空白切分得到，完全不用 `COMP_WORDS` / `COMP_CWORD`；
- 用 `compgen -W` 匹配当前词；
- 如果 `COMP_WORDBREAKS` 中含有 `@`、且当前词中有 `@`，就去掉每个候选中“当前词最后一个 `@` 及其之前”的部分，因为此时 bash 只会替换最后一个 `@` 之后的那一段。

逻辑与 bash-completion 的 `__ltrim_colon_completions` 相同，但不依赖该包，兼容 macOS 自带的 bash 3.2。

**zsh**：定义函数 `_multi_codex`，用 `compadd` 添加候选，然后执行 `compdef _multi_codex multi-codex`。zsh 不把 `@` 当作分隔符，不需要特殊处理。

**fish**：生成一组 `complete -c multi-codex` 语句：子命令用 `__fish_use_subcommand` 条件，选项用 `__fish_seen_subcommand_from`。账号名与 `proxy` 第二个参数的位置判断，放在生成的辅助函数 `__multi_codex_arg_kind` 中，规则与上面相同（由 `commandline -opc` 取得已输入的词）；它返回 `account`、`proxy-value` 或空，`complete -a '(multi-codex completion --list-accounts)'` 只在返回 `account` 时生效。

#### 5.1.2 U7 run 与 path

- `multi-codex run NAME [-- CMD ...]`：
  - 不交给 argparse 处理 `CMD`：不同 Python 版本的 argparse 对 `REMAINDER` 和 `--` 的处理不一致（实测 3.9 起会把第一个 `--` 吃掉）。`main` 在解析参数之前，如果 `argv[0] == "run"`，就在第一个 `--` 处把 argv 分成两段：前半段交给 argparse（只有 `NAME`），后半段原样作为 `CMD`。`run NAME` 后面有多余的参数、却没有 `--` 时，报参数错误，退出码 2；
  - `CMD` 为空时运行 `codex`，等同于 `codex-<名>`；
  - 账号未登记：报错，退出码 1；
  - 实现：用 `launcher.render(name, 账号目录, proxy, env, command_mode=True)` 生成脚本。`command_mode` 改三处：开头加一行 `unset MULTI_CODEX_RUN_SCRIPT`，去掉 “codex 不在 PATH” 的检查，把 `exec codex "$@"` 换成 `exec "$@"`。这样 `run` 与启动命令设置的环境变量逐字一致，不另写一套 Python 的环境变量计算；
  - 脚本**通过环境变量传给 sh，不放进命令行参数**：脚本中可能含有 U8 设置的密钥，放在 argv 中，其它用户可以用 `ps`、或在 Linux 上读 `/proc/<pid>/cmdline` 看到。具体做法是：
    - 把脚本放进环境变量 `MULTI_CODEX_RUN_SCRIPT`；
    - 执行 `os.execve("/bin/sh", ["sh", "-c", 'eval "$MULTI_CODEX_RUN_SCRIPT"', "sh"] + CMD, 环境)`；
    - `command_mode` 生成的脚本第一行是 `unset MULTI_CODEX_RUN_SCRIPT`，被运行的命令不会继承它。

    进程的环境变量只有同一个用户（和 root）能读，与启动命令文件 0700 的保护范围一致；
  - 不需要启动命令文件存在，也不检查启动命令状态；账号目录不存在时，由脚本中现有的检查报错并以 1 退出；
  - 退出码就是被运行命令的退出码。命令不存在时由 sh 报错，退出码 127；
  - 不加锁，不调用 `pending_journal_notice`（事务记录损坏时也能用）。
- `multi-codex path NAME`：在 stdout 输出 `<root>/<名>` 的绝对路径，加换行，退出码 0。账号未登记时退出码 1。目录不存在时照样输出路径，并在 stderr 给出警告。

#### 5.1.3 U8 每账号环境变量

**配置**：`accounts.<名>.env` 是一个对象，键和值都是字符串。

- 键必须匹配 `^[A-Za-z_][A-Za-z0-9_]*$`；
- 以下键为保留键，拒绝设置：`CODEX_HOME`，以及启动命令中代理逻辑管理的 8 个变量（`launcher.py:19-20` 的 `_PROXY_VARS`）。代理一律用 `multi-codex proxy` 设置，避免两处设置互相覆盖；
- 值必须是字符串，可以为空，不能包含 NUL 字符（exec 时无法传递）。

`config.json` 和 `apply -f` 文件中违反以上规则的，都按 `ConfigError` 报错（退出码 1）；命令行参数违反规则的，退出码 2。

**命令**：

```text
multi-codex env NAME                            # 列出：每行 KEY=VALUE，按键名排序
multi-codex env NAME KEY=VALUE [KEY=VALUE ...]  # 设置（已有的覆盖）
multi-codex env NAME --unset KEY [--unset KEY ...]
multi-codex env NAME --clear                    # 删除该账号的全部环境变量
```

- 设置与删除可以出现在同一条命令里，但同一个键不能既设置又删除（退出码 2）；`--clear` 不能和其它两种操作同时出现（退出码 2）；
- 设置、删除、清空都是写命令：加写锁，支持 `--dry-run`，走 `converge`；有未完成的迁移时拒绝执行；
- 列出是只读操作，不加锁；
- 账号未登记：退出码 1。

**启动命令**：

- 在代理相关行之后、`command -v codex` 检查之前，按键名排序，每个变量写一行：`KEY=<shlex.quote(value)>; export KEY`；
- 没有环境变量时一行都不加，内容与 v0.3.0 逐字节相同；
- 新写入的启动命令：有环境变量时权限为 `0o700`（值里可能有 API key，不能让其它用户读到），没有时仍为 `0o755`；
- **判断启动命令是否需要重写**（`_plan_launcher` 与 `launcher_status` 共用 `launcher_file_ok`）：
  - 内容必须相同，且可执行（`os.access(X_OK)`，与 v0.3.0 相同）；
  - 有环境变量时，还要求组和其他用户没有任何权限（`mode & 0o077 == 0`）；
  - 没有环境变量时**不比较权限位**：用户手工改成 0775 等权限，或者 `bin_dir` 所在的文件系统忽略 chmod 时，都不会被反复判为 stale；
  - 清空环境变量时，删掉的那几行会让内容变化，启动命令会以 0755 重写；
- 第 3 行注释（`# Generated by multi-codex. Do not edit; ...`）保持不变：改它会让所有现有启动命令被判为 stale，违反 §3 的“逐字节相同”约束。

**`list --json`**：不输出环境变量的值（可能是密钥），只在每个账号下新增 `"env_keys": [...]`（排序后的键名）。

#### 5.1.4 U10 共享项与独立项

README 的 Shared resources 一节增加下表（英文版同步翻译）。依据是 openai/codex 源码（基线 `6b4daafd`，路径省略 `codex-rs/` 前缀）；“未核实”的内容不写进 README。

| 条目 | 用途 | 能否共享 | 原因（源码依据） |
| ---- | ---- | ---- | ---- |
| `AGENTS.md` | 全局指令 | 可以（默认共享项） | 每次加载都按路径重新读，Codex 自己不写（`codex-home/src/instructions/mod.rs:41-77`） |
| `agents/` | 自定义 agent 角色 | 可以（默认共享项） | 只读（`agent-roles/src/loader.rs:75-78`） |
| `rules/` | 执行策略，“总是允许”的命令 | 可以（默认共享项） | 追加时加文件锁（`execpolicy/src/amend.rs:147-157`）；共享后，一个账号批准的“总是允许”对所有账号生效 |
| `skills/` | 技能 | 可以（默认共享项） | 启动时如果内置技能指纹不同，会删掉并重写 `skills/.system`（`skills/src/lib.rs:88-99`）；各账号使用同一个 codex 版本时没有影响 |
| `config.toml` | 配置 | 可以，但有风险 | 写入时会跟随软链、原子替换目标文件，软链本身不会被替换（`utils/path-utils/src/lib.rs:69-156`）；但读改写过程没有跨进程锁，两个账号同时改配置时，后写的会覆盖先写的 |
| `history.jsonl` | 输入历史 | 可以 | 写入加独占 advisory 锁、读取加共享锁（`message-history/src/lib.rs:143-189`、`:382-416`）；共享后各账号的输入历史混在一起 |
| `auth.json`、`secrets/`、`.credentials.json`、`.env` | 凭据 | **不能** | 账号凭据；`auth.json` 原地写入，没有锁（`login/src/auth/storage.rs:214-231`） |
| `installation_id` | 安装标识 | 不要共享 | 作为请求头 `x-codex-installation-id` 发出（`core/src/client.rs:161`）；共享后，多个账号看起来来自同一个安装 |
| `state_5.sqlite` 等 `*.sqlite` | 线程、日志、记忆等数据库 | **不能** | `state_5.sqlite` 记录账号 ID（`state/migrations` 中的 `creator_account_id` 等字段）；数据库是软链时，WAL 文件放在哪里尚未核实 |
| `sessions/`、`archived_sessions/`、`session_index.jsonl` | 会话记录 | 不要共享 | `session_index.jsonl` 只有进程内锁（`rollout/src/session_index.rs:20-22`）；会话中还记有创建者账号 |
| `models_cache.json`、`cache/` | 缓存 | 不需要共享 | 内容按账号身份区分，身份不符就当作未命中（`models-manager/src/cache.rs:163-175`） |
| `app-server-control/`、`app-server-daemon/`、`packages/`、`tmp/`、`.tmp/`、`log/`、`shell_snapshots/` | 后台服务、临时文件、日志 | 不要共享 | 运行时状态，按进程或会话使用 |

另加一段安全边界说明：目录隔离只是让各账号的本地状态互不干扰，**不是**操作系统或服务端层面的安全边界——同一个系统用户运行的任何程序，都能读取所有账号目录。

### 5.2 接口变更

| 类型 | 接口 | 兼容性 |
| ---- | ---- | ---- |
| 新增 | `completion bash\|zsh\|fish`；隐藏选项 `completion --list-accounts` | 新增 |
| 新增 | `run NAME [-- CMD ...]`、`path NAME` | 新增 |
| 新增 | `env NAME [KEY=VALUE ...] [--unset KEY ...] [--clear] [--dry-run]` | 新增 |
| 新增 | 配置字段 `accounts.<名>.env` | 旧版本读到这个字段会怎样：`_parse_account` 不校验未知字段，会忽略它；但下一次写配置时会丢掉它。README 写明降级前先 `env NAME --clear` |
| 修改 | 设置了环境变量的启动命令，权限从 0755 改为 0700 | 只影响新设置了环境变量的账号 |
| 新增 | `list --json` 中每个账号的 `env_keys` | 新增字段，`version` 不变 |

仓库中没有 `docs/reference/` 目录，不涉及 reference 章节的同步检查。

## 6. 备选方案与决策

| 问题 | 方案 | 结论 |
| ---- | ---- | ---- |
| `run` 的环境如何计算 | 在 Python 中按启动命令的逻辑重新计算一份环境变量字典 | 否决：两份逻辑以后容易不一致，尤其是 NO_PROXY 追加、`off` 时清除变量这些细节 |
| `run` 的环境如何计算 | 复用 `render` 生成的 shell 脚本，只换掉最后一行 | 采纳 |
| 补全脚本的静态列表 | 在 `completion.py` 中手写子命令和选项列表 | 否决：新增选项时容易忘记同步；改为从解析器读出 |
| 环境变量存放位置 | 每个账号目录下放一个 `profile.env` 文件（muzahidul 的做法） | 否决：配置分散在两处，`apply` 的声明式配置覆盖不到 |

## 7. 影响分析

- **启动命令内容**：没有环境变量时，`render` 的输出与 v0.3.0 相同，§8 第 9 条做逐字节回归。有环境变量时多出若干行，权限改为 0700。`launcher_status` 与 `_plan_launcher` 共用 `render` 和 `launcher_file_ok`，两处的判断保持一致。
- **权限位判断**：没有环境变量时仍只检查“可执行”，与 v0.3.0 相同，用户手工改过的权限不会被判为 stale。有环境变量时，如果用户把启动命令改成组或其他用户可读，就会判为 stale：`apply` 会把它改回 0700；`usage --live` 会拒绝运行该账号，提示先执行 `apply`（`cli.py:380-384`）。这是有意的：带密钥的文件不应该让其他用户读到。
- **配置序列化**：`to_dict` 只在 `env` 非空时写入，没有环境变量的配置序列化结果与 v0.3.0 相同，升级后第一条写命令不会出现多余的 update（§8 第 10 条）。
- **`doctor` 的 drift 检查**：它调用 `accounts.plan`，会自动用上新的 `_plan_launcher`，不需要修改。
- **只读命令的分派**：`completion`、`run`、`path` 放在 `pending_journal_notice` 之前分派，与 `usage`、`doctor` 相同。`env` 的列出模式放在 `cli.py:141-142` 分派 `list` 之后、`cli.py:143` 的迁移拦截之前：不加锁，有未完成的迁移时只打印一条提示，照常列出。
- **运行时**：
  - `run` 只多一次 `/bin/sh` 进程替换，没有额外的进程常驻；
  - 补全时每按一次 Tab，最多运行一次 `multi-codex completion --list-accounts`，只读一个 KB 级的 JSON 文件；
  - 补全脚本在 shell 启动时执行一次 `multi-codex completion <shell>`。
- **安全**：
  - 环境变量的值会以明文出现在 `config.json`（0600）和启动命令（0700）中；
  - `run` 通过环境变量把脚本交给 sh，进程命令行中不出现密钥；被运行的命令看不到 `MULTI_CODEX_RUN_SCRIPT`；
  - `env NAME` 列出时会在终端显示值；
  - `list --json` 只输出键名，不输出值；
  - README 写明：不要把需要更强保护的密钥放在这里。

## 8. 回归测试

新文件 `tests/test_ergonomics.py`，全部在临时 HOME 中运行。

**U6 补全**

1. 三种 shell 的脚本都能生成，退出码 0；脚本中包含 `doctor`、`usage --live` 等从解析器读出的子命令和选项。
2. bash：在 `bash --norc` 中 `eval` 脚本，设置 `COMP_LINE`、`COMP_POINT`，并按 bash 默认分隔规则设置 `COMP_WORDS`、`COMP_CWORD`（实现不应依赖后两者），然后调用补全函数：
   - 第一个词补出子命令；
   - `run jak` 补出 `jakoes.wu@icloud.com`；
   - 当前词为 `run jakoes.wu@ic` 时，候选为 `icloud.com`（只替换 `@` 之后的部分）；
   - `proxy jakoes.wu@icloud.com ` 补出 `off`、`inherit`（账号名中含 `@` 时参数位置仍然正确）；
   - `migrate-default --source /x ` 补出账号名（带值选项的值不计入参数位置）；
   - `usage --l` 补出 `--live`；
   - `run a -- ` 之后没有候选。
3. zsh、fish：本机安装了才跑，用 `zsh -n`、`fish --no-execute` 做语法检查；没装就跳过。CI 的 Linux 任务会安装 zsh 和 fish（`.github/workflows/ci.yml`），保证这两条至少在 CI 中真正执行。
4. `completion --list-accounts`：有配置时每行一个名字；没有配置、配置损坏时 stdout 为空，退出码 0，stderr 为空。`completion`（既没有 shell 参数也没有 `--list-accounts`）：退出码 2。

**U7 run 与 path**

5. `run work -- sh -c 'echo "$CODEX_HOME|$HTTPS_PROXY|$FOO|${MULTI_CODEX_RUN_SCRIPT-unset}"'`：输出账号目录、代理地址、设置的环境变量和 `unset`；退出码透传（`run work -- sh -c 'exit 7'` 返回 7）。
6. `run work`（不带命令）：假 codex 收到空参数，看到的环境与直接运行 `codex-work` 时相同（逐个变量比较，排除 shell 自己维护的 `_`、`SHLVL`、`PWD`、`OLDPWD`）。
7. `run ghost`：退出码 1；`run work -- no-such-cmd`：退出码 127；账号目录被删除后 `run work -- true`：退出码 1；`run work extra`（没有 `--`）：退出码 2；`run work -- -- x`：运行的命令是 `-- x`（只在第一个 `--` 处分割）。
7a. 密钥不进命令行：在进程内 mock `os.execve` 捕获参数，断言 argv 中不含环境变量的值。
8. `path work` 输出正确的绝对路径；`path ghost` 退出码 1。

**U8 环境变量与兼容性**

9. 没有环境变量时，生成的启动命令与 v0.3.0 的 `render` 输出逐字节相同（用测试内保存的 v0.3.0 样本对比），权限为 0755。
10. 用 v0.3.0 格式（没有 `env` 字段）的 `config.json` 运行 `apply`：所有动作都是 unchanged，`config.json` 的字节不变。
11. `env work FOO=bar 'B=a b$c'`：启动命令中多出对应的行，权限变为 0700；假 codex 看到的值分别为 `bar` 和 `a b$c`（没有被 shell 展开）。
12. `env work --unset FOO`、`env work --clear`：对应的行被删除；清空后权限恢复为 0755。
13. 保留键与非法键：`env work CODEX_HOME=x`、`env work HTTPS_PROXY=x`、`env work 1A=x`、`env work A=1 --unset A`、`env work A=1 --clear` 都返回退出码 2，什么都没改。`apply -f` 文件中出现同样的键，退出码 1。
13a. 权限：没有环境变量时，把启动命令手工改成 0775，`list` 仍显示 ok，`apply` 不会重写；有环境变量时改成 0755，`list` 显示 stale，`apply` 改回 0700。
13b. `env` 键的顺序：`apply -f` 文件中同一组环境变量换一种键顺序，`apply` 时配置动作为 unchanged。
14. `env work FOO=bar --dry-run`：什么都不写；有未完成的迁移事务时设置被拒绝（退出码 1），但 `env work`（列出）照常可用，不加锁（另一个进程持有写锁时也能列出）。
15. `list --json` 中出现 `env_keys`，不出现值。
16. `doctor`：设置环境变量后，`drift` 仍为 ok。

**U10**：文档修改，不单独测试。评审时对照 §5.1.4 表中的源码依据，核对 README 的每一行。

**整体回归**：现有 129 个用例全部通过；在编译机上用 Python 3.8 跑全部用例；CI 在 macOS 和 Linux 上通过。

## 9. 日志 / 观测点

- `env` 的写操作照常输出 `[multi-codex] update launcher ...` 与 `update config ...` 动作行。
- 保留键被拒绝时，stderr 输出 `[multi-codex] error: CODEX_HOME is managed by multi-codex ...`，或者 `... use \`multi-codex proxy\` for proxy variables`。
- `run`、`path`、`completion` 不输出动作行；`path` 只在目录不存在时往 stderr 写一条警告。
