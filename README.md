# multi-codex

**English** | [简体中文](README.zh-CN.md)

[![Release](https://img.shields.io/github/v/release/jakoes-wu/multi-codex)](https://github.com/jakoes-wu/multi-codex/releases)
[![CI](https://github.com/jakoes-wu/multi-codex/actions/workflows/ci.yml/badge.svg)](https://github.com/jakoes-wu/multi-codex/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/multi-codex)](https://pypi.org/project/multi-codex/)
![Python](https://img.shields.io/badge/python-3.8%2B-blue)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Use several [Codex CLI](https://github.com/openai/codex) accounts on one machine, at the same time. Each account keeps its own login, settings, history and, if you like, its own proxy. No more logging out and in again.

```sh
codex-work          # Codex, logged in with your work account
codex-personal      # Codex, logged in with your personal account, in another terminal
multi-codex list    # which account is logged in as whom
```

![multi-codex demo: add two accounts and list them](https://raw.githubusercontent.com/jakoes-wu/multi-codex/main/docs/assets/demo.gif)

<sub>The accounts in the demo are examples.</sub>

## How it works

Codex keeps everything (settings, credentials, sessions) in one directory, `CODEX_HOME`, which is `~/.codex` by default. multi-codex gives every account its own directory and a small launcher command, `codex-<name>`, that starts Codex with that directory:

```text
codex-work       -> CODEX_HOME=~/.cx/work       HTTPS_PROXY=http://127.0.0.1:7901
codex-personal   -> CODEX_HOME=~/.cx/personal   (inherits your shell's proxy settings)
codex            -> ~/.codex, which can itself become one of the accounts
```

The launchers are plain shell scripts. They keep working even if you uninstall multi-codex.

## Install

You need macOS or Linux (Windows support is planned; inside WSL, use the Linux instructions), Python 3.8 or newer (no extra packages), `tar`, `curl` or `wget`, and the Codex CLI on your `PATH`.

```sh
curl -fsSL https://raw.githubusercontent.com/jakoes-wu/multi-codex/main/install.sh | sh
multi-codex --version
```

Other ways: `pipx install multi-codex` (from PyPI), or on macOS `brew install jakoes-wu/tap/multi-codex`. Either way, the `codex-<name>` launchers still go to `~/.local/bin`.

The `multi-codex` command and the `codex-<name>` launchers go to `~/.local/bin`. If your shell says `command not found`, that directory is not on your `PATH` yet. The installer, `multi-codex add` and `multi-codex doctor` then print the exact command for your shell (zsh, bash or fish), but never edit your shell profile themselves. In zsh, for example, run this once (bash on macOS uses `~/.bash_profile`, bash on Linux `~/.bashrc`) and open a new terminal:

```sh
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
```

In fish, run `fish_add_path ~/.local/bin` once instead.

Installing from a clone, with pipx or into another directory, and how downloads are verified: see [More installation options](#more-installation-options).

## Quick start

### 1. Create one account per login

```sh
multi-codex add work
multi-codex add personal
```

Each command creates a directory (`~/.cx/work`) and a launcher (`codex-work`). A name starts with a letter or digit and may contain letters, digits and `._@+-`; an e-mail address works too.

After `add`, multi-codex prints the next step: the login command, and a warning if `~/.local/bin` is not on your `PATH` yet. Running `multi-codex` without arguments shows these steps again.

If an account should go through a proxy, give it a local port or a URL, for example `multi-codex add work --proxy 7901` (the same as `http://127.0.0.1:7901`). See [Proxy values](#proxy-values). To change an existing account later, use `multi-codex set`, for example `multi-codex set work --proxy 7902`.

### 2. Log in once per account

```sh
multi-codex login work
multi-codex login personal
```

`multi-codex login NAME` runs `codex login` with the account's environment and works even before `~/.local/bin` is on your `PATH`; `codex-work login` does the same.

### 3. Use the launchers instead of `codex`

```sh
codex-work                 # all arguments are passed to codex
codex-personal resume
```

### 4. Check that everything is right

```sh
multi-codex list      # who is logged in, proxy, sharing, usage and anything that needs fixing
multi-codex usage     # 5-hour and weekly usage; empty until you have used an account (--live asks right away)
multi-codex doctor    # finds problems and prints the command that fixes each one
```

### Already using Codex? Keep your current login

Your existing `~/.codex` can become an account as well, so you do not have to log in again:

```sh
# Close Codex first: terminals, VS Code, the desktop app
multi-codex migrate-default
```

Without a name, the account is named after the e-mail address in `~/.codex/auth.json`; if there is none (API key, not logged in, keyring), pass a name, for example `multi-codex migrate-default main`. This moves `~/.codex` to `~/.cx/<name>`, leaves a link at `~/.codex` and creates `codex-<name>`. Plain `codex`, VS Code, the desktop app and old absolute paths under `~/.codex` keep working as before. Later, `multi-codex use work` makes another account the default. If Codex keeps your login in the system keyring, the command stops and explains why. Details and how to undo it: [Migrating `~/.codex`](#migrating-codex).

## Common tasks

| I want to | Command | Details |
| ---- | ---- | ---- |
| Open VS Code with an account | `multi-codex code work ~/src/project` | [VS Code and the desktop app](#vs-code-and-the-desktop-app-experimental) |
| Open the desktop app with an account (macOS) | `multi-codex app work` | [VS Code and the desktop app](#vs-code-and-the-desktop-app-experimental) |
| Change the account that plain `codex` and the Dock apps use | `multi-codex use work` (after `migrate-default`) | [Default account](#default-account) |
| Always use one account inside a project | In the project directory: `multi-codex bind work`, then `multi-codex run` | [Directory bindings](#directory-bindings) |
| Set or change an account's proxy | `multi-codex set work --proxy 7901` | [Proxy values](#proxy-values) |
| Share `AGENTS.md`, skills and rules between accounts | Put them in `~/.codex-shared`, then run `multi-codex set work --shared` | [Shared resources](#shared-resources) |
| Start a new account with another account's settings | `multi-codex add new --config-from work` | [Copying settings](#copying-settings-from-another-account) |
| Give an account extra environment variables | `multi-codex env work KEY=VALUE` | [Environment variables](#per-account-environment-variables) |
| Set up all accounts on a new machine | `curl -fsSL https://raw.githubusercontent.com/jakoes-wu/multi-codex/main/install.sh \| sh -s -- --config accounts.json` | [Declarative setup](#declarative-setup-with-apply) |
| Get tab completion | `eval "$(multi-codex completion zsh)"` | [Shell completion](#shell-completion) |
| See what a command would change | add `--dry-run` | [Commands](#commands) |
| Remove an account | `multi-codex remove work` (the directory is kept) | [Commands](#commands) |

More questions are answered in the [FAQ](#faq).

## Good to know

- **Safe to re-run.** Every command can be run again. Write commands print only what they change, or `already up to date`; add `-v` to see every item, including unchanged ones.
- **Never overwrites your files.** If a file that multi-codex did not create is in the way, it reports a conflict and changes nothing.
- **Interrupted migrations resume.** Run the same command again and it continues from the actual state on disk.
- **Not a security boundary.** Separate directories keep the accounts' local state apart, but any program running as your user can read every account directory.

## Commands

| Command | What it does |
| ---- | ---- |
| `multi-codex init [--root DIR] [--bin-dir DIR] [--shared-dir DIR] [--shared-items A,B]` | Create or change global settings. |
| `multi-codex migrate-default [NAME] [--source DIR] [--copy] [--keep-backup] [--proxy P] [--skip-process-check] [--accept-relogin]` | Turn the default directory into an account. Without NAME, the e-mail address in its `auth.json` is used. |
| `multi-codex add NAME [--proxy P] [--shared [DIR] \| --no-shared] [--adopt] [--config-from OTHER]` | Add an account, adopt an existing directory, or change its options. `--config-from` copies `config.toml` from another account once. |
| `multi-codex set NAME [--proxy P] [--shared [DIR] \| --no-shared] [--adopt] [--config-from OTHER]` | Change an existing account; same options as `add`, but never creates one. |
| `multi-codex login NAME [-- ARGS]` | Run `codex login` with an account's environment; arguments after `--` go to `codex login`. Does not need `~/.local/bin` on `PATH`. |
| `multi-codex proxy NAME PORT\|URL\|off\|inherit` | Set an account's proxy. |
| `multi-codex remove NAME` | Unregister an account and delete its launcher. **The account directory is kept.** |
| `multi-codex apply [-f FILE]` | Converge everything to the configuration (or to `FILE`). |
| `multi-codex list [-v] [--json]` | Show accounts: who is logged in, proxy, sharing, usage and status. `-v` shows the full table. |
| `multi-codex usage [NAME ...] [--live] [--timeout SEC] [--json]` | Show rate-limit usage. |
| `multi-codex doctor [--json]` | Check the installation, configuration and accounts. Read-only. |
| `multi-codex run [NAME] [-- COMMAND ...]` | Run a command (default: `codex`) with an account's environment. Without NAME, the account bound to the current directory is used. |
| `multi-codex bind [NAME [DIR]]` / `unbind [DIR]` | Bind a directory to an account, list bindings, or remove one. |
| `multi-codex code NAME [PATH] [-- ARGS]` | Open VS Code for an account (experimental). |
| `multi-codex app NAME` | Open the Codex desktop app for an account (macOS, experimental). |
| `multi-codex path NAME` | Print an account's directory. |
| `multi-codex env NAME [KEY=VALUE ...] [--unset KEY] [--clear]` | List or change an account's extra environment variables. |
| `multi-codex use [NAME] [--skip-process-check]` | Show or change the default account (where `~/.codex` points). |
| `multi-codex restore NAME [--skip-process-check] [--accept-relogin]` | Undo `migrate-default`: move the account back to `~/.codex`. |
| `multi-codex completion bash\|zsh\|fish` | Print a shell completion script. |

Every write command accepts `--dry-run`. `init`, `add`, `set`, `proxy`, `remove`, `apply`, `bind`, `unbind` and `env` print only the items they change (or `already up to date`); `-v` / `--verbose` also prints unchanged items. For `list`, `-v` means the full table instead. `list`, `usage` and `doctor` never change anything; with `--json` they print a single JSON object on stdout (with a `"version": 1` field) and keep warnings on stderr. Use `--json` in scripts: the table layout is not guaranteed to stay the same.

### Login and usage

`list` shows one line per account:

```text
default: work
NAME  LOGIN          PROXY                  SHARED  USAGE           STATUS
work  w@example.com  http://127.0.0.1:7901  yes     5h 23%, 7d 41%  ok
home  -              inherit                no      -               not logged in
run `multi-codex doctor` for details
```

LOGIN is read from each account's local `auth.json`: the e-mail address, `api-key`, `-` (not logged in), `keyring` (credentials are in the system keyring and cannot be read from files) or `unreadable`. No token is ever printed, and nothing is sent anywhere. USAGE is the last usage snapshot in the account's local session logs, the same data as `multi-codex usage` (`reset` means the window has reset since; `*` means `sessions` is shared with other accounts, so the numbers may belong to another one). STATUS lists what needs fixing (`missing-dir`, `launcher missing`/`stale`/`conflict`, `not logged in`); `doctor` explains each problem. If two accounts are logged in as the same ChatGPT user and workspace, `list` warns you: they share one quota.

`list -v` prints the full table of earlier versions: the root, launcher and shared directories, and the DIR, LAUNCHER and PLAN columns. PLAN is the plan recorded when the current token was issued; it is updated the next time Codex refreshes the token.

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

### Directory bindings

```sh
cd ~/work/project && multi-codex bind work     # this directory and everything below it use "work"
multi-codex run -- codex resume                # no account name needed here
multi-codex bind                               # list bindings; * marks the one in effect here
multi-codex unbind                             # remove the binding of the current directory
```

`run` without an account name walks up from the current directory and uses the nearest bound directory. Bindings are stored in `config.json` (not in your project), keyed by the real path of the directory (links resolved, the on-disk letter case used on case-insensitive file systems). `apply -f` keeps the current bindings; removing an account (`remove`, `restore`, `apply -f`) also removes its bindings. Older versions of multi-codex drop the `bindings` field on their next write.

### Copying settings from another account

`multi-codex add new --config-from work` copies `config.toml` from `work` into `new` once; afterwards the two files are independent. An existing `config.toml` with different content is a conflict (nothing is written), and so is copying into an account that shares `config.toml`. The copy includes everything in the file, such as `cli_auth_credentials_store` or absolute paths that point into the other account.

### Per-account environment variables

```sh
multi-codex env work OPENAI_BASE_URL=https://example.com/v1 TERM_PROGRAM=vscode
multi-codex env work                      # list
multi-codex env work --unset TERM_PROGRAM
multi-codex env work --clear
```

The variables are stored in `config.json` (`accounts.<name>.env`) and written into the launcher. Values are used literally (no `$VAR` expansion). `CODEX_HOME` and the proxy variables are reserved: use `multi-codex proxy` for proxies. A launcher with environment variables is readable only by you (mode 0700), and `list --json` shows only the variable names; still, the values are stored in plain text, so do not put secrets there that need stronger protection. Older versions of multi-codex ignore the `env` field and drop it on their next write; run `multi-codex env NAME --clear` before downgrading.

### VS Code and the desktop app (experimental)

```sh
multi-codex code work ~/src/project        # a separate VS Code window that uses account "work"
multi-codex app work                       # a separate Codex desktop app instance (macOS)
```

These rely on undocumented behavior (verified with VS Code 1.139.1, the OpenAI extension 26.928.31416 and the Codex desktop app 26.831.11858) and may break after an update.

- `code` runs `code --user-data-dir <root>/.apps/<name>/vscode` with the account's environment (like `run`). Each account gets its own VS Code settings; extensions are shared from `~/.vscode/extensions`. On macOS the `code` command passes the whole environment, including the account's variables, to `open --env`, so the values are briefly visible in the process list.
- `app` starts `/Applications/ChatGPT.app` (bundle id `com.openai.codex`) through `open -n` with `CODEX_HOME` set to the account directory and its own data directory `<root>/.apps/<name>/desktop`; output goes to `desktop.log` there. The desktop app loads your login shell's environment, so it uses the shell's proxy settings, not the account's, and the account's extra environment variables are not passed. Log in to one instance at a time: sign-in uses a fixed local callback port. While an account's instance is running, opening the app normally (Dock, Finder, `open -a`) only brings that instance to the front; to run your default account next to it, start it with `open -n -a /Applications/ChatGPT.app`.

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
multi-codex set work --shared
```

Without a directory, `--shared` uses the shared directory already configured, or `~/.codex-shared` if none is set yet. Put the items you want to share there first: items missing from it are skipped (`skip`), and nothing is linked for them; if none of them is there, multi-codex says so. To choose which items are shared, use `multi-codex init --shared-items AGENTS.md,skills,rules,agents`.

`--shared DIR` (for example `multi-codex set work --shared ~/my-shared`) changes the shared directory for **every** shared account, not only this one: their links move to the new directory, and links to items missing there are removed. Write the account name before `--shared`; `add --shared work` would take `work` as the directory.

multi-codex creates the missing links and remembers which links it created. Turning sharing off removes only those links; links you made yourself are left alone. A real file or directory at a link location is a conflict and is never overwritten.

If you already linked an account to the shared directory by hand, `multi-codex set NAME --shared --adopt` takes those links over without recreating them: from then on, turning sharing off removes them as well. Only links that already point to the matching shared item are adopted.

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

## FAQ

### Which account do VS Code and the desktop app use when I start them from the Dock?

The one in `~/.codex`. Apps started from the Dock or Finder get no `CODEX_HOME` from your terminal; both the OpenAI extension and the Codex desktop app then fall back to `~/.codex` (`process.env.CODEX_HOME ?? ~/.codex` in their code). They do read your login shell's environment, so a `CODEX_HOME` exported in your shell profile would apply, but setting it there is not recommended: it also changes what plain `codex` uses in every terminal.

### How do I change that default account?

Let multi-codex manage `~/.codex`, then switch with `use`:

```sh
# Close Codex everywhere first (VS Code, the desktop app, codex sessions in terminals)
multi-codex migrate-default main        # turn the current ~/.codex into an account named "main"
multi-codex use work                    # ~/.codex now points to account "work"
multi-codex use                         # show the current default account
```

From then on, apps started from the Dock use the account `use` points to. To undo the migration, point `~/.codex` back at it first: `multi-codex use main`, then `multi-codex restore main` (`restore` refuses while `~/.codex` points to another account). `use` and `restore` refuse to run while a process is using the directories involved, so close Codex first; see [Default account](#default-account).

### How do I open VS Code with a particular account?

```sh
multi-codex code work ~/src/project
```

This starts a separate VS Code instance with its own user data directory and the account's environment. A separate data directory is required: with the same one, `code` only hands the request to the VS Code that is already running, whose Codex extension keeps using the environment it started with. The extension has no setting for choosing an account. See [VS Code and the desktop app (experimental)](#vs-code-and-the-desktop-app-experimental).

### How do I open the Codex desktop app with a particular account?

```sh
multi-codex app work
```

To do it by hand, both variables are needed: without `CODEX_ELECTRON_USER_DATA_PATH` the desktop app replaces `CODEX_HOME` with your login shell's value after it starts, and shares its data directory with the default instance.

```sh
D="$HOME/.cx/.apps/work/desktop"; mkdir -p "$D"
open -n --env CODEX_HOME="$HOME/.cx/work" --env CODEX_ELECTRON_USER_DATA_PATH="$D" \
  -a /Applications/ChatGPT.app --args --user-data-dir="$D"
```

### Where do I run `open -n -a /Applications/ChatGPT.app`?

In any terminal window (Terminal, iTerm, Warp, …), from any directory. It is the macOS `open` command, not part of multi-codex. `-n` starts a new instance even if one is running; without `CODEX_HOME` the new instance uses `~/.codex`. You need it to run the default account next to an account instance started with `multi-codex app`, because opening the app normally (Dock, Finder, `open -a`) only brings the running instance to the front.

### `codex login status` says I am logged in, but the desktop app asks me to sign in. Why?

`codex login status` only checks that the credentials file exists; it does not check that the token still works. If a directory has not been used for a while, its token may no longer be accepted, and the app shows the sign-in page. Sign in again for that directory (for an account: `codex-<name> login`). `multi-codex usage --live NAME` asks Codex for live usage and fails if the login is no longer valid.

### How do I upgrade multi-codex?

Run the installer again; configuration, accounts and launchers are not touched:

```sh
curl -fsSL https://raw.githubusercontent.com/jakoes-wu/multi-codex/main/install.sh | sh
multi-codex --version
```

## Exit codes

| Code | Meaning |
| ---- | ---- |
| 0 | Success, or already in the desired state |
| 1 | Runtime error (I/O, invalid configuration file, failed verification, lock held by another command, an unfinished migration or restore blocks the command); `usage`: at least one account failed; `doctor`: at least one check failed |
| 2 | Invalid command-line arguments |
| 3 | Conflict with files multi-codex does not own; `migrate-default` or `restore` refused because credentials are in the system keyring; `use` / `restore` found `~/.codex` in an unexpected state or on another file system. Nothing was changed |
| 4 | The directory to migrate, switch away from or restore is in use |

## More installation options

From a clone:

```sh
git clone https://github.com/jakoes-wu/multi-codex.git
cd multi-codex
./install.sh
```

With pipx: `pipx install multi-codex` (the latest release from PyPI), or `pipx install git+https://github.com/jakoes-wu/multi-codex` for the current `main` branch.

The tool goes to `~/.local/share/multi-codex` and the `multi-codex` command to `~/.local/bin`. Use `--prefix DIR` to install somewhere else. Run `./install.sh --help` for all options.

**Verified downloads.** From v0.5.0 on, every release publishes `multi-codex-<tag>.tar.gz` and `SHA256SUMS`. The remote installer downloads that archive and checks its SHA-256 before installing anything; a mismatch stops the installation. Branches and older releases are installed unverified (the installer says so); set `MULTI_CODEX_REQUIRE_CHECKSUM=1` to refuse them. The checksum is published next to the archive, so it protects against a damaged or altered download, not against a compromised GitHub account.

## Uninstalling

```sh
./install.sh --uninstall                                  # from a clone
curl -fsSL https://raw.githubusercontent.com/jakoes-wu/multi-codex/main/install.sh | sh -s -- --uninstall    # without a clone
```

This removes the tool only. Your configuration, account directories and `codex-<name>` launchers stay; the launchers keep working because they do not depend on multi-codex.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Run the tests with:

```sh
python3 -m unittest discover -s tests -t tests
```

## License

[MIT](LICENSE)
