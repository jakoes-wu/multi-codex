# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

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

[Unreleased]: https://github.com/jakoes-wu/multi-codex/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/jakoes-wu/multi-codex/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/jakoes-wu/multi-codex/releases/tag/v0.1.0
