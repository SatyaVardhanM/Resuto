<p align="center">
  <img src="https://raw.githubusercontent.com/SatyaVardhanM/Resuto/main/data/icon.png" alt="Resuto" width="120"/>
</p>

<h1 align="center">Resuto</h1>

<p align="center">
  <strong>AI-powered resume tailoring tool — built for the modern job search.</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white"/>
  <img src="https://img.shields.io/badge/Platform-Windows-informational?logo=windows"/>
  <img src="https://img.shields.io/badge/AI-Claude%20API-blueviolet?logo=anthropic"/>
  <img src="https://img.shields.io/badge/License-MIT-green"/>
  <img src="https://img.shields.io/badge/CI-GitHub%20Actions-black?logo=githubactions"/>
</p>

<p align="center">
  <a href="https://linkedin.com/in/satya-mudiganti">
    <img src="https://img.shields.io/badge/Built%20by-Satyavardhan%20Mudiganti-0077B5?logo=linkedin"/>
  </a>
</p>

---

Resuto scans LinkedIn job listings, scores them against your profile using Claude AI, and generates a uniquely tailored resume for each matched role. It does **not** auto-apply to jobs. Every application you send goes in with a resume that speaks directly to that specific job description — increasing your chances of getting past ATS systems and landing interviews.

> ⚠️ Resuto increases your chances of getting interview calls. It is not a guarantee of employment.

---

## Why I built this

I have 3.5 years of enterprise .NET engineering experience and was applying to jobs the same way everyone else does — sending the same generic resume to every role and getting filtered out by ATS before a human ever read it. Tailoring manually for 20+ applications a week wasn't sustainable.

So I built Resuto. It does the scanning, matching, and rewriting. I do the final review and decide what to send.

It's also a project that let me build something I actually use — combining browser automation, LLM prompt engineering, desktop UI, a CI/CD pipeline, and packaged installer in one codebase.

---

## What it does

**Phase 1 — Smart job scanning**
- Searches LinkedIn with your chosen roles, location, and filters
- Scores each job for relevance against your full profile using Claude AI
- Queues only the jobs that genuinely match your background
- Skips irrelevant roles before spending any API cost on them

**Phase 2 — Tailored resume generation**
- Generates a unique, tailored resume for each matched job
- Rewrites your bullet points to highlight skills the JD actually asks for
- Preserves your factual experience — no fabrication, no hallucination
- Exports a ready-to-send PDF and DOCX for every queued role

**You review. You apply.**
Resuto hands you a folder of polished, job-specific resumes. What you do with them is up to you.

---

## Built to demonstrate

This is a real engineering project, not a tutorial. It covers:

- **Prompt engineering at production level** — multi-stage validation, hallucination prevention, structured XML output parsing, two-model strategy (Haiku for fast filtering, Sonnet for generation)
- **Async browser automation** — Playwright with human-like timing, anti-detection headers, retry logic on LinkedIn loading errors, pagination handling
- **Desktop application architecture** — 5,500+ line CTk app split into mixin-based feature modules with clean separation of UI and logic
- **LLM pipeline design** — relevance scoring → pre-filter → full JD analysis → resume generation → output validation, with exponential backoff on API failures
- **Data pipeline** — SQLite job tracker with pagination, multi-filter history view, dedup, and phase-level status tracking
- **CI/CD + packaging** — GitHub Actions workflow, Nuitka standalone Windows compilation, Inno Setup installer, code-signed binary, automated secret injection

---

## Tech stack

| Layer | Technology |
|---|---|
| Desktop UI | Python · CustomTkinter |
| AI / LLM | Anthropic Claude API (Haiku + Sonnet) |
| Browser automation | Playwright (Chromium) |
| Database | SQLite |
| Resume output | python-docx · WeasyPrint (PDF) |
| Packaging | Nuitka (standalone exe) · Inno Setup (installer) |
| CI/CD | GitHub Actions |

---

## Features

- **Profile analysis** — Claude reads your XML resume and suggests matching job titles ranked by fit percentage
- **Search modes** — Exact role names / Broader terms / Both / Location only
- **Smart relevance filter** — Two-stage check: fast title pre-filter then full JD analysis by Claude
- **Application mode** — Auto (continuous scan) or One-at-a-time (review each job before resume is generated)
- **History tab** — Track every scanned, matched, skipped, and failed job with filter and pagination
- **Stats dashboard** — Live donut chart, match scores, session summaries
- **Filter controls** — Experience level, job type, workplace type, date posted, Easy Apply toggle
- **Resume preview** — Review the generated resume before downloading
- **Dark UI** — Adjustable font size, fully keyboard navigable

---

## How resume tailoring works

```
Your XML profile
       │
       ▼
   Claude reads JD
       │
       ▼
  Identifies required skills → maps to your actual experience
       │
       ▼
  Rewrites bullets with JD keywords — using ONLY facts from your profile
       │
       ▼
  Validates output — checks for hallucinations, enforces honesty rules
       │
       ▼
  Exports DOCX + PDF named after the role and company
```

Strict prompt rules: no invented metrics, no fabricated tools, no fake titles. If a skill isn't in your profile, it won't appear in the resume. What changes is the *emphasis and framing* — the same experience described in language the hiring manager for that specific role actually uses.

---

## Quick start

```bash
# Clone the repo
git clone https://github.com/SatyaVardhanM/Resuto.git
cd Resuto

# Install dependencies
pip install -r requirements.txt

# Install Playwright browsers
playwright install chromium

# Run
python frontend/app.py
```

You'll need an [Anthropic API key](https://console.anthropic.com/) entered on first launch.

---

## Project structure

```
resuto/
├── frontend/           Desktop UI (CustomTkinter)
│   ├── app.py          Main app controller
│   ├── constants.py    Theme, colors, fonts
│   ├── bot_runner.py   Subprocess manager
│   └── views/          Tab modules (run, history, settings, stats, auth)
├── backend/            Automation engine
│   ├── orchestrator.py Job scanning + resume pipeline coordinator
│   ├── scraper.py      LinkedIn job discovery
│   └── browser.py      Chromium session management
├── api/                AI integration layer
│   ├── resume_gen.py   Claude resume generation + validation
│   ├── filter.py       Claude relevance scoring
│   ├── prompts.py      Prompt management
│   └── intake.py       Profile intake + XML parsing
├── core/               Shared utilities
│   ├── config.py       App-wide settings
│   ├── profile.py      Resume XML parser
│   └── settings.py     User preferences
└── db/                 SQLite tracker
    └── tracker.py      Job history, status, metrics
```

---

## Screenshots

> *(Coming soon — app UI, history tab, resume output)*

---

## Disclaimer

Resuto is a resume assistance tool. It does not submit job applications on your behalf. All applications are reviewed and sent manually by the user. Results vary based on your experience, the roles you apply for, and market conditions. No employment outcome is guaranteed.

---

## License

MIT License — see [LICENSE](LICENSE)

---

## Author

**Satyavardhan Mudiganti**
Software Engineer · 3.5 years enterprise .NET · MS in Computer Information Systems (AI concentration)

[![LinkedIn](https://img.shields.io/badge/LinkedIn-satya--mudiganti-0077B5?logo=linkedin)](https://linkedin.com/in/satya-mudiganti)
[![GitHub](https://img.shields.io/badge/GitHub-SatyaVardhanM-181717?logo=github)](https://github.com/SatyaVardhanM)

---

*Built with Python · Powered by Claude AI · Designed for real job searches*
