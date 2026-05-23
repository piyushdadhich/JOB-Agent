# Quickstart

From a fresh clone to your first scored postings in under 30 minutes.

## Prerequisites

| | |
|---|---|
| Python | 3.12 or newer |
| Git | any recent version |
| RAM | 4 GB minimum (8 GB recommended if running local LLMs) |
| Disk | ~500 MB for the agent + dependencies. Add 1.5 GB if you install the `[skills]` extra. |

```bash
python --version    # must show 3.12 or newer
```

If older, install from [python.org/downloads](https://www.python.org/downloads/).

## Step 1 — Clone

```bash
git clone <your-fork-or-this-repo>.git job-agent
cd job-agent
```

## Step 2 — Install

```bash
python -m venv venv
# Windows: .\venv\Scripts\Activate.ps1
# macOS / Linux: source venv/bin/activate

pip install -e .
```

This installs the runtime and registers the `job-agent` console script. Optional extras:

```bash
pip install -e ".[local]"   # Ollama client for local LLMs
pip install -e ".[api]"     # anthropic + openai + google-genai
pip install -e ".[gmail]"   # Google API client for the email monitor
pip install -e ".[skills]"  # ojd-daps-skills (1.5 GB; spaCy + torch)
pip install -e ".[all]"     # everything above
```

## Step 3 — Start

```bash
job-agent start
```

A native window opens at `http://localhost:8080`. On first run the page redirects to `/setup`.

## Step 4 — Setup wizard (8 steps, ~20 minutes)

| Step | What it does |
|------|--------------|
| **1. Hardware** | Detects CPU / RAM / GPU and recommends a performance tier. Click through if the defaults look right. |
| **2. LLM config** | Pick a provider per task (evaluation / resume / form-fill). Each provider has a Test button that validates your API key before you continue. |
| **3. Profile** | Name, email, phone, optional LinkedIn URL. Writes `default.yaml` plus `default_applicant.yaml` (the latter is gitignored — it holds PII). |
| **4. Career inventory** | The longest step. Three paths: copy/paste an AI prompt into Claude or ChatGPT, upload a .docx resume, or fill the per-role form by hand. The matcher uses this for everything downstream. |
| **5. Target market** | Cities, salary range, sectors, role types. The wizard checks `config/domains/<domain>.yaml` and lists the available role types. |
| **6. Sources** | Country + which job boards to query + LinkedIn search keywords. You can leave most defaults alone. |
| **7. Gmail (optional)** | Upload `credentials.json` from Google Cloud Console and run OAuth. Skip if you don't want recruiter-email parsing. |
| **8. Schedule** | Pick a daily run time and install background services (cron on Unix, Task Scheduler on Windows). |

When you click **Finish setup**, the wizard immediately kicks off your first discovery + evaluation pass. Progress streams to the page.

## Step 5 — First results

When the first run finishes you'll see:

- **Dashboard** — today's evaluation counts + skill-gap hint
- **Shortlist** — STRONG and TOP_TIER postings. Click "Why this score?" to see matched / missed skills
- **Prompts** — per-posting resume + cover-letter prompts queue
- **Apply** — Playwright form-filler control panel
- **Pipeline** — Kanban (Shortlisted / Applied / Interview / Offer / Rejected)
- **Calibration** — guided yes/no on top-10 / bottom-10 samples to nudge your tier thresholds

The pipeline re-runs daily at your configured time. You can also trigger it manually with `job-agent run` or the dashboard's **Run now** button.

---

<details>
<summary><b>Windows-specific notes</b></summary>

- PyWebView opens a native Edge-WebView2 window; the dashboard also serves on `http://localhost:8080` in any browser if you'd rather use Chrome / Firefox.
- The PowerShell activation script is `.\venv\Scripts\Activate.ps1`. If PowerShell refuses to run it, set the execution policy once: `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned`.
- Background scheduling uses Task Scheduler (`schtasks`). Tasks register at user scope — run `job-agent install-service` from a regular (non-admin) PowerShell prompt.

</details>

<details>
<summary><b>macOS notes</b></summary>

- PyWebView uses the system WebKit; no extra dependency needed.
- Background scheduling uses `launchd`. The agent writes a `.plist` to `~/Library/LaunchAgents/`.
- If the install fails on a native build step, run `xcode-select --install` first to get the C toolchain.

</details>

<details>
<summary><b>Linux notes</b></summary>

- PyWebView needs WebKit GTK bindings: `sudo apt-get install gir1.2-webkit2-4.0` (or the GTK equivalent for your distro).
- If the native window won't open, the dashboard still works in your browser at `http://localhost:8080`.
- Background scheduling uses `cron`. The agent writes to your user crontab on `job-agent install-service`.

</details>

<details>
<summary><b>Installing with a local LLM (Ollama)</b></summary>

1. Install Ollama from [ollama.com/download](https://ollama.com/download).
2. Pull a model:
   ```bash
   ollama pull gemma2:9b      # general-purpose, ~5 GB
   ollama pull qwen2.5:7b     # smaller alternative
   ```
3. In the setup wizard's Step 2, pick **local** for any task you want Ollama to handle. The wizard probes for the binary and the model list.
4. Add `pip install -e ".[local]"` if you skipped the extra during initial install.

Local LLMs cost nothing per call but are slower and less accurate than frontier API models. A reasonable starting routing is `evaluation: local` + `resume: anthropic` (or your preferred API).

</details>

<details>
<summary><b>Using cloud LLMs (API keys)</b></summary>

The setup wizard's Step 2 writes API keys to a gitignored `.env`. Keys are also accepted via environment variables:

```bash
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
GEMINI_API_KEY=...
```

Each provider has a **Test** button in the wizard that hits the `/models` endpoint to verify the key before saving.

Cost estimates appear on the **Prompts** tab before any LLM call — you'll know what a batch will cost before you run it.

</details>

---

Stuck? [TROUBLESHOOTING.md](TROUBLESHOOTING.md).
