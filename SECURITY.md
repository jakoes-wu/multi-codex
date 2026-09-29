# Security Policy

## Supported versions

Only the latest release receives security fixes.

## Reporting a vulnerability

Please report vulnerabilities privately through
[GitHub Security Advisories](https://github.com/jakoes-wu/multi-codex/security/advisories/new)
instead of opening a public issue. Include the version, your platform and the
steps to reproduce. You should receive a reply within a week.

## Scope notes

- multi-codex never reads or writes Codex credentials (`auth.json`).
- Generated launchers are world-readable; proxy URLs with credentials are
  rejected for that reason.
- New account directories are created with mode `0700`.
