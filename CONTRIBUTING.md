# Contributing

Thanks for your interest in multi-codex!

## Ground rules

- The tool uses the Python standard library only and must keep working on
  Python 3.8. Avoid newer APIs such as `str.removeprefix`, `list[str]`
  annotations or `Path.is_relative_to`.
- Every write command must stay idempotent: plan first, refuse to change
  anything when there is a conflict, and converge when re-run.
- Never overwrite or delete a file that multi-codex did not create. Launchers
  carry a marker on line 2; shared links are tracked in `managed_links`.
- Platform differences belong in `src/multi_codex/platform.py`.

## Development

```sh
python3 -m unittest discover -s tests -t tests   # run all tests
python3 -m pyflakes src tests                    # optional lint
shellcheck install.sh                            # if installed
```

Tests run the CLI in subprocesses with a temporary `HOME`; they never touch
your real `~/.codex`, `~/.cx` or `~/.local/bin`.

The migration code has test hooks: `MULTI_CODEX_TEST_CRASH_AT=<point>` makes
the process exit abruptly at a point, `MULTI_CODEX_TEST_FAIL_AT=<point>`
raises a catchable error there. Add a crash point for every new step so that
resuming from it is covered.

## Pull requests

1. Open an issue first for larger changes.
2. Add or update tests for the behavior you change.
3. Update both `README.md` and `README.zh-CN.md` when user-facing behavior
   changes, and add an entry to `CHANGELOG.md`.
