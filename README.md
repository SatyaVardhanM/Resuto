<p align="center">
  <img src="https://raw.githubusercontent.com/SatyaVardhanM/Resuto/main/data/icon.png" alt="Resuto" width="120"/>
</p>

<h1 align="center">Resuto</h1>

<p align="center">
  <strong>AI-powered resume tailoring tool — built for the modern job search.</strong>
</p>

Resuto scans LinkedIn job listings, scores them against your profile using Claude AI, and generates a uniquely tailored resume for each matched role. It does **not** auto-apply to jobs. The goal is simple: every application you send goes in with a resume that speaks directly to that specific job description — increasing your chances of getting past ATS systems and landing interviews.

> ⚠️ Resuto increases your chances of getting interview calls. It is not a guarantee of employment.

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

## Why this matters

Generic resumes get filtered by ATS before a human ever reads them. Tailoring manually for every job is exhausting and doesn't scale. Resuto sits in the middle: it does the scanning, matching, and writing — you do the final review and decide what to send.

---

## Features

- **Profile analysis** — Claude reads your XML resume and suggests matching job titles ranked by fit percentage
- **Search modes** — Exact role names / Broader terms / Both / Location only
- **Smart relevance filter** — Two-stage check: fast title pre-filter, then full JD analysis by Claude
- **Application mode** — Auto (continuous scan) or One-at-a-time (review each job before resume is generated)
- **History tab** — Track every scanned, matched, skipped, and failed job with filter and pagination
- **Stats dashboard** — Live donut chart, match scores, session summaries
- **Date & filter controls** — Experience level, job type, workplace type, date posted, Easy Apply toggle
- **Resume preview** — Review the generated resume before downloading
- **Font & theme** — Dark UI, adjustable font size, fully keyboard navigable

---

## Tech stack

| Layer | Technology |
|---|---|
| Desktop UI | Python · CustomTkinter |
| AI / LLM | Anthropic Claude API (Haiku for filtering, Sonnet for resume generation) |
| Browser automation | Playwright (Chromium) |
| Database | SQLite |
| Resume output | python-docx · WeasyPrint (PDF) |
| Packaging | Nuitka (standalone Windows exe) · Inno Setup (installer) |
| CI/CD | GitHub Actions |

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

The prompt engineering behind the generation enforces strict rules: no invented metrics, no fabricated tools, no fake titles. If a skill isn't in your profile, it won't appear in the resume. What changes is the *emphasis and framing* — the same experience described in language the hiring manager for that specific role actually uses.

---

## Screenshots

> *(Coming soon)*

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

## Built to demonstrate

This project was built as a real engineering challenge, not a tutorial project. It covers:

- **Prompt engineering at production level** — multi-stage validation, hallucination prevention, structured output parsing
- **Async browser automation** — Playwright with human-like timing, anti-detection, retry logic, error page detection
- **Desktop application architecture** — mixin-based modular CTk app, clean separation of UI and logic
- **LLM pipeline design** — two-model strategy (cheap model for filtering, powerful model for generation), exponential backoff, 500/timeout retry
- **Data pipeline** — SQLite job tracker with pagination, filter, dedup, and phase tracking
- **CI/CD + packaging** — GitHub Actions, Nuitka standalone compilation, Inno Setup installer, code-signed binary

---

## Disclaimer

Resuto is a resume assistance tool. It does not submit job applications on your behalf. All applications are reviewed and sent manually by the user. Results vary based on your experience, the roles you apply for, and market conditions. No employment outcome is guaranteed.

---

## License

MIT License — see [LICENSE](LICENSE)

---

*Built with Python · Powered by Claude AI · Designed for real job searches*
