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
| `multi-codex migrate-default NAME [--source DIR] [--copy] [--keep-backup] [--proxy P] [--skip-process-check]` | Turn the default directory into an account. |
| `multi-codex add NAME [--proxy P] [--shared \| --no-shared]` | Add an account, adopt an existing directory, or change its options. |
| `multi-codex proxy NAME PORT\|URL\|off\|inherit` | Set an account's proxy. |
| `multi-codex remove NAME` | Unregister an account and delete its launcher. **The account directory is kept.** |
| `multi-codex apply [-f FILE]` | Converge everything to the configuration (or to `FILE`). |
| `multi-codex list` | Show accounts and the state of their launchers. |

Every write command accepts `--dry-run`.

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
> - With a `socks5h://` URL, most connections were still sent as HTTP `CONNECT` requests to that port. A SOCKS proxy therefore only works when its port also speaks HTTP (for example a "mixed" port); a SOCKS-only port will break most requests.

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

1. refuses to start while any process has files, its working directory or its executable inside `~/.codex` (close Codex, IDE extensions and the ChatGPT browser extension host first);
2. renames `~/.codex` to `~/.cx/main` when both are on the same file system, otherwise copies, verifies every file by SHA-256, and parks the original as `~/.codex.multi-codex-bak.<timestamp>`;
3. creates the link `~/.codex -> ~/.cx/main` and registers the account.

Progress is recorded in `~/.config/multi-codex/migrate-journal.json`. If the migration is interrupted, run the same command again and it continues from the actual state on disk. While a migration is unfinished, other write commands refuse to run.

Sockets and FIFOs (runtime files such as `ipc.sock`) are not copied in copy mode. On macOS, copy mode does not preserve extended attributes.

If `CODEX_HOME`, `CODEX_SQLITE_HOME`, `CODEX_API_KEY` or `CODEX_ACCESS_TOKEN` is set in your environment, multi-codex warns you: these variables override or bypass per-account isolation.

### Undoing a migration by hand

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

## Exit codes

| Code | Meaning |
| ---- | ---- |
| 0 | Success, or already in the desired state |
| 1 | Runtime error (I/O, invalid configuration file, failed verification, lock held by another command) |
| 2 | Invalid command-line arguments |
| 3 | Conflict with files multi-codex does not own; nothing was changed |
| 4 | The migration source is in use |

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
