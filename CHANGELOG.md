# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- `bind NAME [DIR]` / `unbind [DIR]`: bind a directory to an account;
  `run` without an account name uses the nearest bound directory.
- `add NAME --config-from OTHER`: copy `config.toml` from another account once.
- `code NAME [PATH]` and `app NAME` (macOS): open VS Code or the Codex desktop
  app for an account. Experimental.
- Releases publish `multi-codex-<tag>.tar.gz` and `SHA256SUMS`; `install.sh`
  verifies the download (`MULTI_CODEX_SHA256`, `MULTI_CODEX_REQUIRE_CHECKSUM`).
- `doctor` reports stale directory bindings.

### Changed

- `install.sh` stops when reading the release of an explicit version tag
  (`MULTI_CODEX_REF=vX.Y.Z`) fails, instead of installing it unverified.
- zsh completion falls back to file names where no other candidates apply.

## [0.4.0] - 2026-10-01

### Added

- `completion bash|zsh|fish`: shell completion for subcommands, options and
  account names (e-mail addresses included).
- `run NAME [-- COMMAND ...]`: run any command (default `codex`) with exactly
  the environment of the account's launcher; `path NAME` prints the account
  directory.
- Per-account environment variables: `env NAME KEY=VALUE`, `--unset`,
  `--clear` (`accounts.<name>.env` in the configuration). Launchers with
  variables are written with mode 0700; `list --json` shows only the names.
- `use [NAME]`: show or atomically change the default account (`~/.codex`),
  refusing while the current default account is in use.
- `restore NAME`: undo `migrate-default`, resumable after an interruption;
  other write commands are blocked until it finishes.
- `list` shows the default account (`default:` line, `default_account` in
  `--json`).
- README: which items of an account directory can be shared, and why.

### Fixed

- `doctor` did not expand `~` when checking `~/.codex`, so the `default-dir`
  check always reported that it did not exist.

## [0.3.0] - 2026-10-01

### Added

- `list` shows each account's login (e-mail, `api-key`, `-`, `keyring` or
  `unreadable`) and plan, read locally from `auth.json` without printing any
  token, and warns when two accounts are logged in as the same ChatGPT user
  and workspace.
- `usage`: show rate-limit usage per account from the latest snapshot in the
  local session logs, or live with `--live` (through `codex app-server` and
  the account's launcher; Codex 0.48.0 or newer).
- `doctor`: read-only health check of the installation, environment,
  configuration drift and accounts, with a suggested fix for each problem.
- `--json` for `list`, `usage` and `doctor`.

### Fixed

- `migrate-default` no longer migrates silently when Codex keeps the
  credentials in the system keyring (`cli_auth_credentials_store = "keyring"`,
  or `"auto"` without an `auth.json`, in the account's or the system-wide
  `config.toml`): the keyring entry is tied to the directory path, so the
  account would be logged out. It now refuses with exit code 3;
  `--accept-relogin` migrates anyway and reminds you to log in again.

## [0.2.0] - 2026-09-30

### Added

- `add --adopt`: take over existing links that already point to the shared
  items, so that turning sharing off later removes them too.

## [0.1.0] - 2026-09-29

First release (macOS and Linux).

### Added

- `migrate-default`: turn the default `~/.codex` into a named account, with a
  resumable journal, rename or copy-and-verify modes and a compatibility link.
  The busy-process check reports how each process uses the source (`cwd`,
  `executable`, `mapped` or `fd N`) and which path it holds.
- `init`, `add`, `proxy`, `remove`, `apply`, `list` commands; every write
  command is idempotent and supports `--dry-run`.
- Per-account proxy settings (`inherit`, `off`, port or URL). Setting a SOCKS
  proxy prints a warning: Codex sends most requests as HTTP CONNECT even with a
  SOCKS URL, so only ports that also accept HTTP work.
- Optional shared resources linked from one directory into selected accounts.
- `install.sh` with `--config`, `--prefix` and `--uninstall`.

[Unreleased]: https://github.com/jakoes-wu/multi-codex/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/jakoes-wu/multi-codex/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/jakoes-wu/multi-codex/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/jakoes-wu/multi-codex/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/jakoes-wu/multi-codex/releases/tag/v0.1.0
