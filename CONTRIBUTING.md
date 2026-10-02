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

## Releasing

1. Open a release PR that bumps `__version__` in `src/multi_codex/__init__.py`
   and moves the `Unreleased` section of `CHANGELOG.md` under the new version.
2. After it is merged and CI on `main` passes, publish the release with your
   own credentials: `gh release create vX.Y.Z --target <commit> --notes-file ...`.
3. The `Release assets` workflow then uploads `multi-codex-vX.Y.Z.tar.gz` and
   `SHA256SUMS`. It fails if `__version__` does not match the tag.
4. Confirm both assets are on the release page and that
   `curl -fsSL .../install.sh | sh` prints `verified sha256` before announcing
   the release. Until the assets are uploaded, installs are unverified.
5. The `Publish to PyPI` workflow builds the sdist and wheel and uploads them
   through PyPI Trusted Publishing (environment `pypi`). To upload an earlier
   tag, run it by hand with `gh workflow run pypi.yml -f tag=vX.Y.Z`.
6. Update the Homebrew formula in
   [jakoes-wu/homebrew-tap](https://github.com/jakoes-wu/homebrew-tap):
   set `url` to the new `multi-codex-vX.Y.Z.tar.gz` asset and `sha256` to the
   value in the release's `SHA256SUMS`, then check it with
   `brew install --build-from-source jakoes-wu/tap/multi-codex && brew test multi-codex`.

## Pull requests

1. Open an issue first for larger changes.
2. Add or update tests for the behavior you change.
3. Update both `README.md` and `README.zh-CN.md` when user-facing behavior
   changes, and add an entry to `CHANGELOG.md`.
