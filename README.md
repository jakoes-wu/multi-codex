# multi-codex

**English** | [简体中文](README.zh-CN.md)

Run several [Codex CLI](https://github.com/openai/codex) accounts side by side on one machine.

Codex keeps its configuration, credentials and session databases in the directory named by `CODEX_HOME` (default `~/.codex`). multi-codex gives every account its own directory and its own launcher command, optionally with its own proxy:

```text
codex-work       -> CODEX_HOME=~/.cx/work       HTTPS_PROXY=http://127.0.0.1:7901
codex-personal   -> CODEX_HOME=~/.cx/personal   (inherits your shell's proxy settings)
codex            -> ~/.codex, which can itself become one of the accounts
```

## Features

- **Migrate the default directory** — turn your existing `~/.codex` into a named account and leave a compatibility link behind, so `codex` and old absolute paths keep working.
- **Add accounts** — create an account directory and a `codex-<name>` launcher, or adopt a directory you already have.
- **Per-account proxy** — a local port, a full proxy URL, `off`, or `inherit`.
- **One-step deployment** — `install.sh --config accounts.json` installs the tool and creates every account in the file.
- **Idempotent** — every command can be re-run safely. Unchanged state is reported as `unchanged`; conflicts with files multi-codex does not own are reported without changing anything; an interrupted migration resumes where it stopped.
- **Optional shared resources** — link `AGENTS.md`, `skills`, `rules`, `agents` and so on from one shared directory into selected accounts.
- **See who is logged in and how much quota is left** — `list` shows each account's email and plan; `usage` shows the 5-hour / weekly usage from local session logs, or live with `--live`.
- **Health check** — `doctor` checks the installation, environment and accounts, and tells you which command fixes each problem.
- **Everyday helpers** — shell completion (bash, zsh, fish), `run` any command with an account's environment, per-account environment variables, and `use` / `restore` to switch or undo the default account.

## Requirements

- macOS or Linux (Windows is planned; inside WSL, use the Linux instructions)
- Python 3.8 or newer (standard library only)
- Codex CLI on your `PATH`

## Installation

From a clone:

```sh
git clone https://github.com/jakoes-wu/multi-codex.git
cd multi-codex
./install.sh
```

Or directly:

```sh
curl -fsSL https://raw.githubusercontent.com/jakoes-wu/multi-codex/main/install.sh | sh
```

The tool goes to `~/.local/share/multi-codex` and the `multi-codex` command to `~/.local/bin`. Use `--prefix DIR` to install somewhere else. Make sure `~/.local/bin` is on your `PATH`; the installer only prints a hint and never edits your shell profile.

Alternatively: `pipx install git+https://github.com/jakoes-wu/multi-codex`.

Run `./install.sh --help` for all options.

## Quick start

```sh
# Turn the existing ~/.codex into an account named "main"
multi-codex migrate-default main

# Add a second account that goes through a local proxy on port 7901
multi-codex add work --proxy 7901

# Log in once per account, then use it
codex-work login
codex-work

multi-codex list
```

## Commands

| Command | What it does |
| ---- | ---- |
| `multi-codex init [--root DIR] [--bin-dir DIR] [--shared-dir DIR] [--shared-items A,B]` | Create or change global settings. |
| `multi-codex migrate-default NAME [--source DIR] [--copy] [--keep-backup] [--proxy P] [--skip-process-check] [--accept-relogin]` | Turn the default directory into an account. |
| `multi-codex add NAME [--proxy P] [--shared \| --no-shared] [--adopt]` | Add an account, adopt an existing directory, or change its options. |
| `multi-codex proxy NAME PORT\|URL\|off\|inherit` | Set an account's proxy. |
| `multi-codex remove NAME` | Unregister an account and delete its launcher. **The account directory is kept.** |
| `multi-codex apply [-f FILE]` | Converge everything to the configuration (or to `FILE`). |
| `multi-codex list [--json]` | Show accounts, the state of their launchers, and who is logged in. |
| `multi-codex usage [NAME ...] [--live] [--timeout SEC] [--json]` | Show rate-limit usage. |
| `multi-codex doctor [--json]` | Check the installation, configuration and accounts. Read-only. |
| `multi-codex run NAME [-- COMMAND ...]` | Run a command (default: `codex`) with an account's environment. |
| `multi-codex path NAME` | Print an account's directory. |
| `multi-codex env NAME [KEY=VALUE ...] [--unset KEY] [--clear]` | List or change an account's extra environment variables. |
| `multi-codex use [NAME] [--skip-process-check]` | Show or change the default account (where `~/.codex` points). |
| `multi-codex restore NAME [--skip-process-check] [--accept-relogin]` | Undo `migrate-default`: move the account back to `~/.codex`. |
| `multi-codex completion bash\|zsh\|fish` | Print a shell completion script. |

Every write command accepts `--dry-run`. `list`, `usage` and `doctor` never change anything; with `--json` they print a single JSON object on stdout (with a `"version": 1` field) and keep warnings on stderr. Use `--json` in scripts: the table layout is not guaranteed to stay the same.

### Login and usage

`list` adds two columns, LOGIN and PLAN, read from each account's local `auth.json`. No token is ever printed, and nothing is sent anywhere. LOGIN is the e-mail address, `api-key`, `-` (not logged in), `keyring` (credentials are in the system keyring and cannot be read from files) or `unreadable`. PLAN is the plan recorded when the current token was issued; it is updated the next time Codex refreshes the token. If two accounts are logged in as the same ChatGPT user and workspace, `list` warns you: they share one quota.

`multi-codex usage` reads the most recent rate-limit snapshot from each account's session logs (`sessions/` and `archived_sessions/`). It is offline but can be out of date; a window that has reset since the snapshot is shown as `reset since snapshot`.

`multi-codex usage --live` runs `codex app-server` through the account's launcher (so the account's proxy applies) and asks it for the current usage (`account/rateLimits/read`, Codex 0.48.0 or newer). multi-codex never reads or sends tokens itself. As with any Codex run, Codex may refresh the account's token and write it back to that account's directory. Accounts are queried one after another; `--timeout` limits each one (default 30 seconds).

### Health check

`multi-codex doctor` prints one line per check (`ok`, `warn` or `fail`) and a `fix:` line with the command to run. It checks the `codex` executable and its version, the configuration, unfinished migrations, whether `bin_dir` is on `PATH`, environment variables that break isolation, where `~/.codex` points, whether the files match the configuration (the same plan `apply` would execute), each account's login, and duplicate logins. It does not repair anything and does not use the network. The exit code is 1 if any check fails, otherwise 0.

### Shell completion

```sh
eval "$(multi-codex completion bash)"     # in ~/.bashrc
eval "$(multi-codex completion zsh)"      # in ~/.zshrc, after compinit
multi-codex completion fish | source      # in ~/.config/fish/config.fish
```

Subcommands, options and registered account names (including e-mail addresses) are completed.

### Running other commands

`multi-codex run NAME -- COMMAND ...` runs any command with exactly the environment of `codex-NAME` (`CODEX_HOME`, proxy, extra variables). Without a command it runs `codex`. Everything after the first `--` is passed through unchanged; the exit code is the command's. `multi-codex path NAME` prints the account directory.

### Per-account environment variables

```sh
multi-codex env work OPENAI_BASE_URL=https://example.com/v1 TERM_PROGRAM=vscode
multi-codex env work                      # list
multi-codex env work --unset TERM_PROGRAM
multi-codex env work --clear
```

The variables are stored in `config.json` (`accounts.<name>.env`) and written into the launcher. Values are used literally (no `$VAR` expansion). `CODEX_HOME` and the proxy variables are reserved: use `multi-codex proxy` for proxies. A launcher with environment variables is readable only by you (mode 0700), and `list --json` shows only the variable names; still, the values are stored in plain text, so do not put secrets there that need stronger protection. Older versions of multi-codex ignore the `env` field and drop it on their next write; run `multi-codex env NAME --clear` before downgrading.

### Default account

After `migrate-default`, `~/.codex` is a link to one account, and plain `codex`, the Codex desktop app and IDE extensions use that account. `multi-codex use` shows which one; `multi-codex use NAME` points the link at another account atomically.

A Codex process started without `CODEX_HOME` re-opens files under `~/.codex` while it runs, so switching underneath it would mix the files of two accounts. `use` therefore refuses (exit code 4) while any process has the current default account open — including sessions started with `codex-<name>`, which cannot be told apart. Close Codex (the CLI, the desktop app, IDE extensions and the app-server daemon), switch, then restart them. The check sees only files that are open at that moment, so treat it as a safety net, not a guarantee.

If you `remove` the account that `~/.codex` points to, the link is left pointing at an unregistered directory; `doctor` reports it.

### Default locations

| Item | Default |
| ---- | ---- |
| Account directories | `~/.cx/<name>` |
| Launchers | `~/.local/bin/codex-<name>` |
| Configuration | `~/.config/multi-codex/config.json` (honours `XDG_CONFIG_HOME`) |

Account names may contain letters, digits and `._@+-`, must start with a letter or digit, and are case-insensitive (`Work` and `work` are the same account). An email address works as a name.

### Proxy values

| Value | Effect in the launcher |
| ---- | ---- |
| `inherit` (default) | Leaves proxy variables exactly as they are in your shell. |
| `off` | Unsets `HTTPS_PROXY`, `HTTP_PROXY`, `ALL_PROXY`, `NO_PROXY` and their lowercase forms. |
| `7901` | Same as `http://127.0.0.1:7901`. |
| `http://host:port`, `https://…`, `socks5://…`, `socks5h://…` | Sets `HTTPS_PROXY`/`HTTP_PROXY` (both cases). `ALL_PROXY` is set only for SOCKS proxies; for other proxies an inherited `ALL_PROXY` is removed. `localhost,127.0.0.1,::1` is appended to your existing `NO_PROXY`. |

Proxy URLs must not contain a user name or password: launchers are plain, world-readable files.

> **Prefer HTTP proxies.** Codex's documentation does not list which proxy variables it honours. Tested with codex-cli 0.159.0 on Linux:
>
> - `HTTPS_PROXY` / `HTTP_PROXY` and a lone `ALL_PROXY` are honoured. Every connection (chatgpt.com, ab.chatgpt.com, oaiusercontent.com) went through the proxy, and none bypassed it.
> - `off` works: with proxy variables set in the parent shell, Codex connected directly.
> - With a `socks5h://` URL, most connections were still sent as HTTP `CONNECT` requests to that port. A SOCKS proxy therefore only works when its port also speaks HTTP (for example a "mixed" port); a SOCKS-only port will break most requests. multi-codex prints a warning whenever you set a SOCKS proxy.

### Declarative setup with `apply`

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
# or, on a new machine, in one step:
./install.sh --config accounts.json
```

`apply -f` replaces the configuration with the file. Accounts missing from the file are unregistered (their directories are kept). If anything conflicts, nothing is written at all.

## Migrating `~/.codex`

`multi-codex migrate-default main`:

1. refuses to start when Codex stores the credentials in the system keyring (`cli_auth_credentials_store = "keyring"` in `config.toml` or `/etc/codex/config.toml`, or `"auto"` without an `auth.json`): the keyring entry is tied to the directory path, so you would be logged out after the move. Pass `--accept-relogin` to migrate anyway and log in again afterwards;
2. refuses to start while any process has files, its working directory or its executable inside `~/.codex` (close Codex, IDE extensions and the ChatGPT browser extension host first);
3. renames `~/.codex` to `~/.cx/main` when both are on the same file system, otherwise copies, verifies every file by SHA-256, and parks the original as `~/.codex.multi-codex-bak.<timestamp>`;
4. creates the link `~/.codex -> ~/.cx/main` and registers the account.

Progress is recorded in `~/.config/multi-codex/migrate-journal.json`. If the migration is interrupted, run the same command again and it continues from the actual state on disk. While a migration is unfinished, other write commands refuse to run.

Sockets and FIFOs (runtime files such as `ipc.sock`) are not copied in copy mode. On macOS, copy mode does not preserve extended attributes.

If `CODEX_HOME`, `CODEX_SQLITE_HOME`, `CODEX_API_KEY` or `CODEX_ACCESS_TOKEN` is set in your environment, multi-codex warns you: these variables override or bypass per-account isolation.

### Undoing a migration

```sh
multi-codex restore main
```

`restore` removes the `~/.codex` link, moves `~/.cx/main` back to `~/.codex` and unregisters the account (its launcher is deleted; shared links inside the directory stay and keep working). It checks the same things as `migrate-default`: whether the directory is in use, and whether credentials are in the system keyring (moving the directory back logs you out in that case; `--accept-relogin` proceeds anyway). If it is interrupted, run the same command again. While a restore is unfinished, other write commands refuse to run. If `~/.codex` currently points to another account, run `multi-codex use main` first. The account directory and `~/.codex` must be on the same file system; otherwise restore by hand:

```sh
rm ~/.codex                     # remove the link (only the link)
mv ~/.cx/main ~/.codex          # move the data back
multi-codex remove main         # unregister the account
```

With `--keep-backup` in copy mode, the original directory stays at `~/.codex.multi-codex-bak.<timestamp>`.

If a migration stops with an error and you want to abandon it: the error message says where the complete data is; move it back to `~/.codex` and delete `~/.config/multi-codex/migrate-journal.json`.

## Shared resources

```sh
multi-codex init --shared-dir ~/.codex-shared --shared-items AGENTS.md,skills,rules,agents
multi-codex add work --shared
```

multi-codex creates the missing links and remembers which links it created. Turning sharing off removes only those links; links you made yourself are left alone. A real file or directory at a link location is a conflict and is never overwritten.

If you already linked an account to the shared directory by hand, `multi-codex add NAME --shared --adopt` takes those links over without recreating them: from then on, turning sharing off removes them as well. Only links that already point to the matching shared item are adopted.

### What can be shared

Based on the Codex source code (openai/codex at `6b4daafd`):

| Item | What it is | Share? | Why |
| ---- | ---- | ---- | ---- |
| `AGENTS.md` | Global instructions | Yes (default) | Read fresh on every load; Codex does not write it. |
| `agents/` | Custom agent roles | Yes (default) | Read-only. |
| `rules/` | Exec policy ("always allow" commands) | Yes (default) | Appends are file-locked. An approval given in one account then applies to all sharing accounts. |
| `skills/` | Skills | Yes (default) | On start-up Codex rewrites `skills/.system` when its built-in skills differ; harmless as long as all accounts use the same Codex version. |
| `config.toml` | Settings | With care | Codex writes through the link and replaces the target atomically, so the link survives; but there is no cross-process lock, so two accounts changing settings at the same time can lose one change. |
| `history.jsonl` | Prompt history | Yes | Reads and writes are file-locked; the histories of the accounts are merged. |
| `auth.json`, `secrets/`, `.credentials.json`, `.env` | Credentials | **No** | They are the account. |
| `installation_id` | Installation identifier | No | Sent with requests; sharing makes several accounts look like one installation. |
| `*.sqlite` (`state_5.sqlite`, …) | Threads, logs, memories | **No** | `state_5.sqlite` records account IDs. |
| `sessions/`, `archived_sessions/`, `session_index.jsonl` | Session logs | No | The index has only an in-process lock; sessions record the account that created them. |
| `models_cache.json`, `cache/` | Caches | Not needed | Keyed by the account; a mismatch is a cache miss. |
| `app-server-control/`, `app-server-daemon/`, `packages/`, `tmp/`, `.tmp/`, `log/`, `shell_snapshots/` | Runtime state | No | Per process or per session. |

Separate directories keep the local state of the accounts apart. They are **not** a security boundary: any program running as your user can read every account directory.

## Exit codes

| Code | Meaning |
| ---- | ---- |
| 0 | Success, or already in the desired state |
| 1 | Runtime error (I/O, invalid configuration file, failed verification, lock held by another command, an unfinished migration or restore blocks the command); `usage`: at least one account failed; `doctor`: at least one check failed |
| 2 | Invalid command-line arguments |
| 3 | Conflict with files multi-codex does not own; `migrate-default` or `restore` refused because credentials are in the system keyring; `use` / `restore` found `~/.codex` in an unexpected state or on another file system. Nothing was changed |
| 4 | The directory to migrate, switch away from or restore is in use |

## Uninstalling

```sh
./install.sh --uninstall
```

This removes the tool only. Your configuration, account directories and `codex-<name>` launchers stay; the launchers keep working because they do not depend on multi-codex.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Run the tests with:

```sh
python3 -m unittest discover -s tests -t tests
```

## License

[MIT](LICENSE)
