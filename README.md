<div align="center">

<img src="data/icon.png" alt="Resuto Logo" width="100"/>

# Resuto

**AI-powered job application automation for LinkedIn**

[![Release](https://img.shields.io/github/v/release/SatyaVardhanM/Resuto?color=blue&label=Latest)](https://github.com/SatyaVardhanM/Resuto/releases/latest)
[![Platform](https://img.shields.io/badge/Platform-Windows-lightgrey)](https://github.com/SatyaVardhanM/Resuto/releases/latest)
[![License](https://img.shields.io/badge/License-Proprietary-red)](LICENSE)

[Download](#download) · [Features](#features) · [Setup](#setup) · [Screenshots](#screenshots)

</div>

---

## What is Resuto?

Resuto automates the most time-consuming part of job hunting — applying on LinkedIn. It scans job listings that match your preferences, generates a **custom-tailored resume** for each position using Claude AI, and submits applications — all while you focus on what matters.

> Built by a software engineer for software engineers. Designed for active job seekers who want quality applications at scale.

---

## Screenshots

<!-- Add screenshots here -->
<!-- ![Dashboard](images/dashboard.png) -->
<!-- ![Resume Generation](images/resume_gen.png) -->
<!-- ![Job Tracker](images/tracker.png) -->

*Screenshots coming soon*

---

## Features

### Smart Resume Generation
- Tailors your resume to every job description using Claude AI
- Highlights your most relevant experience and skills per role
- Generates properly formatted Word documents automatically
- Respects your experience metrics and bullet point budget

### LinkedIn Automation
- Scans and applies to LinkedIn Easy Apply jobs automatically
- Filters by job title, location, experience level, and keywords
- Skips jobs you have already applied to
- Detects and skips roles requiring sponsorship

### Job Tracking
- Tracks every scan and application in a local database
- View application history with match scores
- Clear history and start fresh anytime

### Auto-Updates
- App notifies you when a new version is available
- One-click update — settings and history preserved

---

## Download

<div align="center">

### [⬇ Download Resuto-Setup.exe](https://github.com/SatyaVardhanM/Resuto/releases/latest)

**No admin rights required · Installs to your user folder · Windows 10/11**

</div>

---

## Requirements

| Requirement | Details |
|---|---|
| OS | Windows 10 or later |
| Internet | Required for LinkedIn and AI features |
| LinkedIn Account | Free account works |
| Anthropic API Key | [Get one here](https://console.anthropic.com) — pay per use |

---

## Setup

**1. Install**
Run `Resuto-Setup.exe` — no admin rights needed. A desktop shortcut is created automatically.

**2. Register**
On first launch, enter your name, email, and phone number to request access. You will receive approval within 24 hours.

**3. Configure**
Open Settings and enter:
- Your Anthropic API key
- Path to your resume XML profile
- Work authorization status (CPT, OPT, H1B, Green Card, Citizen)

**4. Set Preferences**
Go to the Preferences tab and configure:
- Job titles to search
- Preferred locations
- Experience level
- Keywords to include or exclude

**5. Run**
Click **Start Bot** and let Resuto handle the rest.

---

## Resume Profile

Your profile is stored as a local XML file — your data never leaves your machine except when sent to the Anthropic API for resume generation.

Use the included `resume_data.example.xml` as a template:

```xml
<profile>
  <personal>
    <name>Your Name</name>
    <email>you@email.com</email>
    <phone>+1 555 0000</phone>
    <location>City, State</location>
  </personal>
  <experience>
    <job>
      <title>Software Engineer</title>
      <company>Company Name</company>
      <duration>2022 - Present</duration>
      <bullets>
        <bullet>Built X that improved Y by Z%</bullet>
      </bullets>
    </job>
  </experience>
  ...
</profile>
```

---

## Privacy

| Data | Where it goes |
|---|---|
| Resume profile | Stays on your machine |
| Job descriptions | Sent to Anthropic API for resume generation only |
| Application history | Local SQLite database on your machine |
| LinkedIn session | Stored locally in your Chrome profile folder |
| Personal info (registration) | Stored securely for access control only |

---

## Tech Stack

- **UI** — Python, CustomTkinter
- **AI** — Anthropic Claude API
- **Automation** — Playwright, Chromium
- **Resume** — python-docx
- **Database** — SQLite

---

## Support

Found a bug or have a feature request?

[Open an Issue](https://github.com/SatyaVardhanM/Resuto/issues)

---

<div align="center">

Built with ❤ by [Satya Vardhan Mudiganti](https://linkedin.com/in/satya-mudiganti)

</div>
