# Contributing

Thank you for helping. Bug reports, fixes and new tests are welcome.

## Set up

You need Python 3.12 or later and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/steel-experiments/steel_browser_toolset
cd steel_browser_toolset
uv sync
```

## Make a change

1. For a bug, first write a test that fails because of the bug.
2. Make the change. Keep it small, and match the code around it.
3. Run the checks:

   ```bash
   uv run ruff check . && uv run ruff format --check .
   uv run pytest
   ```

4. For a change to `driver.py`, also run the live tests. They create Steel sessions, so they need a Steel API key:

   ```bash
   STEEL_API_KEY=... uv run pytest -m live
   ```

5. Add a line to `CHANGELOG.md` under `Unreleased`.

## Style

- Every code file starts with two `ABOUTME:` comment lines that tell what the file does.
- Write comments and docs in simple, direct English.
- Text that the model reads (error text, results) must not contain local paths, URLs from exceptions or secrets.
  Use fixed phrases.

## Pull requests

Open a pull request against `main`. Tell what changes and why. CI runs lint, unit tests and a package build.
Maintainers run the live tests before a merge.

By contributing, you agree that your contribution is under the [Apache License 2.0](LICENSE).
