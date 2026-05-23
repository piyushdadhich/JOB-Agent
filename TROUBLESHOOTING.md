# Troubleshooting

Common issues and fixes, grouped by where in the flow they bite.

<details open>
<summary><b>Installation</b></summary>

**Q: `pip install -e .` fails with "Microsoft Visual C++ 14.0 is required".**
A: A native-extension dependency (`psutil`, `pydantic-core`) needs a C toolchain.
- Windows: install [Microsoft Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/), tick "Desktop development with C++".
- macOS: `xcode-select --install`
- Linux (Debian/Ubuntu): `sudo apt-get install build-essential python3-dev`

**Q: `job-agent` command not found after install.**
A: The console script is in your venv's `Scripts/` (Windows) or `bin/` (Unix). Activate the venv first:
```bash
.\venv\Scripts\Activate.ps1     # Windows PowerShell
source ./venv/bin/activate       # macOS / Linux
```

**Q: PowerShell refuses to run the venv activation script.**
A: Set execution policy once: `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned`.

**Q: Install hangs on `playwright install chromium`.**
A: Playwright downloads ~150 MB. If you're on a slow link or behind a corporate proxy, set `HTTPS_PROXY` and `HTTP_PROXY` env vars before retrying.

</details>

<details>
<summary><b>Setup wizard</b></summary>

**Q: Dashboard won't start — "Port 8080 in use".**
A: Another process owns the port. Either kill it or stop the existing agent:
- Windows: `Get-NetTCPConnection -LocalPort 8080 -State Listen | %{ Stop-Process -Id $_.OwningProcess -Force }`
- macOS / Linux: `lsof -i :8080 && kill <pid>`
- Cross-platform: `job-agent stop`

**Q: PyWebView won't open a native window on Linux.**
A: Install the WebKit GTK bindings: `sudo apt-get install gir1.2-webkit2-4.0`. The dashboard still works in your browser at `http://localhost:8080` regardless.

**Q: "Ollama not detected" in the wizard's Step 2.**
A: Either install Ollama from [ollama.com/download](https://ollama.com/download) and pull a model (`ollama pull gemma2:9b`), or skip local routing and pick an API provider per task.

**Q: API-key Test button always fails.**
A: Three usual causes:
- Wrong prefix (`sk-ant-…` for Anthropic, `sk-…` for OpenAI, no prefix for Gemini)
- Project-scoped key without `models.list` permission — the test hits `/models` to validate
- Corporate proxy MITMing the API call

**Q: "credentials.json invalid" in Step 7.**
A: Google Cloud Console exports two OAuth shapes — Desktop and Web. We need **Desktop** (the flow runs a local redirect server).

**Q: Career inventory parser detects 0 roles.**
A: Your markdown is probably missing the `### Title — Company — Dates` heading pattern. Check the template at `source_materials/<profile>/career_inventory.md.example`.

</details>

<details>
<summary><b>Discovery</b></summary>

**Q: First discovery run finishes with 0 postings.**
A: Likely causes, in order:
- No ATS slugs configured — edit `config/profiles/default.yaml` and add to `greenhouse_api.slugs`, `lever_api.slugs`, etc.
- JobSpy 429/403 — Indeed and Google Jobs throttle hard; retry in 30 minutes or raise `jobspy.rate_limit_seconds` ≥ 8
- LinkedIn keywords too narrow — broaden `linkedin_guest.keywords_traditional`

**Q: Every source fails with "rate limited" on a fresh run.**
A: You're probably starting on an IP that's been hammered recently. Wait 30 minutes; if it persists, lower the per-source rate-limit setting and run with fewer sources at once.

**Q: A source disappeared from the dispatch list.**
A: Source registration lives in `scripts/run_daily.py`. If you upgraded and a source is missing, check the changelog — sources are sometimes split or renamed.

</details>

<details>
<summary><b>Scoring</b></summary>

**Q: Scorer returns SKIP for everything.**
A:
- Your inventory might be too small for the default thresholds. Open the Calibration tab → click **Re-auto**.
- Skill extraction may not be wired. The `[skills]` install extra is 1.5 GB; without it the scorer falls back to a baseline that under-counts. Install with `pip install -e ".[skills]"`.

**Q: All scores cluster in a narrow band (e.g. everything between 4.0 and 5.0).**
A: Your inventory is dense in a few skill clusters. Run the Calibration tab's guided sample — the auto-calibrator widens thresholds to match your judgment.

**Q: "Why this score?" panel shows skills I didn't mention.**
A: The skill extractor uses Lightcast/ESCO taxonomy, which infers related skills from context. If an inferred skill is wrong, downvote it on the panel; the calibrator learns from these signals.

</details>

<details>
<summary><b>Dashboard</b></summary>

**Q: Dashboard tabs are blank after first run.**
A: Run history isn't writing. Check `data/<profile>/run_history.json` exists and has entries; if not, the pipeline ran but didn't persist results — usually a permissions issue on the data directory.

**Q: Pipeline tab loses cards I moved between columns.**
A: Card state is per-profile in SQLite, not browser-side. If columns reset on refresh, your DB is on read-only storage — move `data/` to a writable location.

**Q: Frontend console shows CORS errors against `localhost:8080`.**
A: You're hitting the API from a different port. Either use the bundled SPA (served by FastAPI) or set `VITE_API_BASE` to `http://localhost:8080` and re-run the dev server.

</details>

<details>
<summary><b>LLM</b></summary>

**Q: Daily pipeline failed: "ollama: connect refused".**
A: The Ollama daemon isn't running. Start it (`ollama serve` on Linux, the macOS app, the Windows tray icon) or switch evaluation to an API provider in `llm_routing`.

**Q: Anthropic / OpenAI calls return 401 mid-run.**
A: The key was rotated or hit its credit cap. Open `.env`, replace the key, restart the agent.

**Q: Cloud-evaluator hits its soft-stop and warns daily.**
A: You're past the configured `cloud_evaluator.soft_stop` budget. Either bump it in `default.yaml`, or split evaluation work across providers in `llm_routing`.

**Q: Resume prompts come back truncated.**
A: Token limit. Shorten the career inventory's per-role bullets, or switch the resume task to a model with a larger context window.

</details>

<details>
<summary><b>Gmail</b></summary>

**Q: Email monitor stops working after 7 days.**
A: Google's "Testing" OAuth status expires refresh tokens after 7 days. Either publish the OAuth screen (Google Cloud Console → "Publish app") or re-run `job-agent gmail-auth` weekly.

**Q: Gmail OAuth flow opens a blank tab.**
A: A previous browser session is interfering. Open an incognito window, paste the auth URL, complete consent there.

**Q: Email monitor detects 0 recruiter messages.**
A: The classifier requires explicit recruiter-tagged threads or specific subject patterns. Add the keywords you see in your inbox to `email_monitor.subject_patterns` in `default.yaml`.

</details>

## Where to look

| If you suspect… | Look at |
|-----------------|---------|
| Pipeline run logic | `data/<profile>/run_history.json` |
| 404 sweep / dismiss | `scripts/output/expiry_runs/<date>.jsonl` |
| Per-posting state | `data/<profile>/tracker.db` (open with any SQLite viewer) |
| Frontend errors | DevTools → Console in the PyWebView window (right-click → Inspect) |
| Background scheduler | `job-agent status` |
