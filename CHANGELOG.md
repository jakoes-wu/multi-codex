# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed

- The busy-process report of `migrate-default` now says how each process uses
  the source (`cwd`, `executable`, `mapped` or `fd N`) and which path it holds.

### Changed

- Setting a SOCKS proxy now prints a warning: Codex sends most requests as HTTP
  CONNECT even with a SOCKS URL, so only ports that also accept HTTP work.

### Added

- `migrate-default`: turn the default `~/.codex` into a named account, with a
  resumable journal, busy-process check, rename or copy-and-verify modes and a
  compatibility link.
- `init`, `add`, `proxy`, `remove`, `apply`, `list` commands; every write
  command is idempotent and supports `--dry-run`.
- Per-account proxy settings (`inherit`, `off`, port or URL).
- Optional shared resources linked from one directory into selected accounts.
- `install.sh` with `--config`, `--prefix` and `--uninstall`.
