# Contributing

We welcome contributions — bug fixes, new ATS sources, scoring improvements, doc tweaks. Smaller PRs land fastest; larger architectural changes should open an issue first so we can talk about scope.

## Quick setup

```bash
git clone <your-fork>.git job-agent
cd job-agent
python -m venv venv && source venv/bin/activate    # .\venv\Scripts\Activate.ps1 on Windows
pip install -e ".[dev]"
playwright install chromium
pytest --timeout=30 -q
```

`.[dev]` adds pytest + pytest-asyncio + pytest-timeout to the base install. A full test run is ~12 minutes (1,500+ tests).

## Adding a new ATS source

This is the most common contribution. The pattern:

1. **Create a module** under `engine/discovery/<your_source>.py` implementing the `Source` protocol:
   ```python
   class YourSource:
       def discover(self, profile_config: dict) -> Iterator[Posting]:
           ...
   ```
2. **Wire it in** `scripts/run_daily.py`'s source dispatch.
3. **Add config keys** to `config/profiles/default.yaml.example` (slugs, rate limits, any source-specific knobs).
4. **Write a smoke test** under `tests/test_<your_source>_source.py`. Mock the HTTP layer; don't hit the real API in CI.

If the source has a form-filler counterpart, add it under `engine/applicant/handlers/<ats>.py` and register the URL pattern in `handlers/base.py`.

## Reporting bugs

Open an issue with:
- What you ran (`job-agent <command>` or which dashboard tab)
- What you expected
- What happened (logs from `data/<profile>/run_history.json` help)
- Your OS + Python version

## PR process

1. Fork → branch from `github-release` (`feature/<short-name>` naming)
2. Make your change + add tests
3. Run `pytest --timeout=30 -q` locally — green tests are a merge prerequisite
4. Open a PR against `github-release`
5. A maintainer reviews; expect one round of comments

## Code style

| Language | Tools |
|----------|-------|
| Python | `black` (default config), `isort` (profile=black), type hints on public functions |
| JavaScript / JSX | `prettier` (default config) |
| Comments | Only when the WHY is non-obvious. Well-named identifiers carry intent. |

## No PII in tracked files

Test fixtures and example configs use placeholders — `Your Name`, `your_city`, `Acme Corp`. Real identity info goes in gitignored `default_applicant.yaml`, `source_materials/`, and `data/`. The repo runs a PII grep before each release; PRs that smuggle in real names or cities will be rejected.

## License of contributions

By submitting code you agree it's licensed under the same [Sustainable Use License](LICENSE) as the rest of the project.
