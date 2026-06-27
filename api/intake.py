import anthropic
# api/intake.py — Resume intake and profile generation
import sys as _sys, os as _os
if not getattr(_sys, "frozen", False):
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

"""
Resume intake tool.

What this does:
  1. Reads an existing resume (PDF or DOCX) from a file path you paste.
  2. Sends it to Claude, which generates targeted questions based on
     what is in the resume and what is missing.
  3. Asks those questions one by one, waiting for your answer.
  4. Sends the resume + all answers to Claude, which generates:
       - resume_data.xml  (the base resume file the bot uses)
       - my_profile.py    (the profile script the bot uses)

API resilience:
  If the API connection is lost or fails at any point, the tool shows
  a warning and PAUSES. It does NOT exit. You press Enter to retry.
  Your answers so far are never lost.

Run this once before using the main bot.
"""

import os
import sys
import time
import json
try:
    from core.logger import log, log_warn, log_error
except Exception:
    def log(m,*a): pass
    def log_warn(m,*a): pass
    def log_error(m,*a,**k): pass
# anthropic, prompts and config are imported inside functions
# to avoid circular import errors and top-level ImportError

# MODEL and client are created lazily inside functions
def _get_model():
    from core.config import AI_MODEL
    return AI_MODEL

def _get_client():
    import anthropic
    return anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))


# -- API resilience -----------------------------------------------
def call_api(messages: list, system: str = "", max_tokens: int = 4000) -> str:
    """
    Calls the Claude API and returns the response text.

    On any API failure:
      - Shows a clear warning with the error.
      - Pauses and waits for the user to press Enter to retry.
      - Never exits -- the user stays in control.
    On an auth error (bad key):
      - Shows a specific message about the key being wrong.
      - Still pauses and retries -- user can fix the key and continue.
    """
    while True:
        try:
            kwargs = {
                "model":      _get_model(),
                "max_tokens": max_tokens,
                "messages":   messages,
            }
            if system:
                kwargs["system"] = system

            response = _get_client().messages.create(**kwargs)
            return response.content[0].text

        except anthropic.AuthenticationError:
            print("\n" + "=" * 60)
            print("  [WARN]   API KEY ERROR")
            print("  The Claude API key was rejected.")
            print("  Check that ANTHROPIC_API_KEY is set correctly.")
            print("  Your progress so far is saved.")
            print("=" * 60)
            _safe_input("  Press Enter to retry, or Ctrl+C to quit: ", "")

        except anthropic.RateLimitError:
            print("\n" + "=" * 60)
            print("  [WARN]   RATE LIMIT -- too many requests.")
            print("  Waiting 30 seconds then retrying automatically...")
            print("=" * 60)
            for i in range(30, 0, -5):
                print(f"  Retrying in {i}s...", end="\r")
                time.sleep(5)
            print()

        except Exception as e:
            print("\n" + "=" * 60)
            print("  [WARN]   API CONNECTION ISSUE")
            print(f"  Error: {str(e)[:120]}")
            print("  Your progress so far is saved.")
            print("=" * 60)
            _safe_input("  Press Enter to retry, or Ctrl+C to quit: ", "")


def _is_gui_mode() -> bool:
    """Returns True when running as a GUI subprocess (stdin not interactive)."""
    import sys
    try:
        return not sys.stdin.isatty()
    except Exception:
        return True   # assume GUI if we can't tell


def _safe_input(prompt: str, default: str = "") -> str:
    """input() that returns default instead of crashing in GUI/subprocess mode."""
    if _is_gui_mode():
        return default
    try:
        return input(prompt)
    except EOFError:
        return default


# -- File reading -------------------------------------------------
# Sentinel prefix used to distinguish error strings from empty extractions
_PDF_ERROR_PREFIX = "PDF_ERROR:"


def read_pdf(path: str) -> str:
    """
    Extracts text from a PDF file.
    Returns extracted text, or a string starting with _PDF_ERROR_PREFIX
    describing the specific problem so the UI can show a clear message.
    """
    import os as _os

    # 1. File exists?
    if not _os.path.exists(path):
        return f"{_PDF_ERROR_PREFIX}File not found: {path}"

    # 2. Minimum size check (< 100 bytes = empty/corrupt)
    if _os.path.getsize(path) < 100:
        return f"{_PDF_ERROR_PREFIX}File is too small to be a valid PDF."

    # 3. PDF magic bytes check — first 4 bytes must be %PDF
    try:
        with open(path, "rb") as _f:
            header = _f.read(4)
        if header != b"%PDF":
            # ext = _os.path.splitext(path)[1].lower()
            return (f"{_PDF_ERROR_PREFIX}This doesn't look like a PDF file "
                    f"(got {header!r}). "
                    "Please select a valid .pdf file.")
    except Exception as e:
        return f"{_PDF_ERROR_PREFIX}Could not open file: {e}"

    # 4. Extract text
    try:
        import pypdf
        reader = pypdf.PdfReader(path)

        # Password protected?
        if reader.is_encrypted:
            return (f"{_PDF_ERROR_PREFIX}This PDF is password-protected. "
                    "Please remove the password and try again.")

        # Zero pages?
        if len(reader.pages) == 0:
            return f"{_PDF_ERROR_PREFIX}This PDF has no pages."

        text = []
        for page in reader.pages:
            try:
                page_text = page.extract_text()
                if page_text:
                    text.append(page_text)
            except Exception:
                continue   # skip unreadable pages, try rest

        result = "\n".join(text).strip()

        # Scanned/image-only PDF?
        if not result:
            return (f"{_PDF_ERROR_PREFIX}No text found in this PDF. "
                    "It may be a scanned document (image-only). "
                    "Please convert to a text-based PDF or DOCX and try again.")

        # Garbled text check (too many non-ASCII chars = likely encoding issue)
        non_ascii = sum(1 for c in result if ord(c) > 127)
        if non_ascii > len(result) * 0.4:
            return (f"{_PDF_ERROR_PREFIX}PDF text appears garbled (encoding issue). "
                    "Try saving as PDF/A or converting to DOCX.")

        return result

    except ImportError:
        return f"{_PDF_ERROR_PREFIX}pypdf not installed. Run: pip install pypdf"
    except Exception as e:
        err = str(e).lower()
        if "password" in err or "encrypted" in err:
            return (f"{_PDF_ERROR_PREFIX}PDF is password-protected. "
                    "Remove the password and try again.")
        if "eo" in err or "invalid" in err or "corrupt" in err:
            return f"{_PDF_ERROR_PREFIX}PDF appears corrupted or truncated: {e}"
        return f"{_PDF_ERROR_PREFIX}Could not read PDF: {e}"



def preprocess_resume_text(text: str) -> str:
    """
    Normalize resume text before sending to Claude.
    Handles IT consultant / platform-specialist resume patterns
    that differ from standard SWE resumes.
    """
    import re
    lines = text.split("\n")
    cleaned = []

    for line in lines:
        stripped = line.strip()

        # ── Numbered skill categories: "1. Cloud Infrastructure: AWS, GCP"
        # Keep as-is — the prompt now understands these
        # Just ensure the number prefix is clean
        m = re.match(r"^\d+\.\s+(.+)", stripped)
        if m:
            cleaned.append(m.group(1))
            continue

        # ── Environment lines: tag them clearly for Claude
        if re.match(r"^Environment\s*:", stripped, re.IGNORECASE):
            cleaned.append("\n[TECH STACK FOR ABOVE ROLE]: " + stripped)
            continue

        cleaned.append(line)

    return "\n".join(cleaned)


def read_docx(path: str) -> str:
    """Extracts text from a DOCX file."""
    try:
        import docx
        doc  = docx.Document(path)
        text = [para.text for para in doc.paragraphs if para.text.strip()]
        return "\n".join(text).strip()
    except Exception as e:
        print(f"  [ERR] Could not read DOCX: {e}")
        return ""


def read_resume_file(path: str) -> str:
    """
    Reads a resume from a PDF or DOCX file path.
    Returns the extracted text, or empty string on failure.
    """
    path = path.strip().strip('"').strip("'")

    if not os.path.exists(path):
        print(f"  [ERR] File not found: {path}")
        return ""

    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        raw = read_pdf(path)
    elif ext in (".docx", ".doc"):
        raw = read_docx(path)
    else:
        print(f"  [ERR] Unsupported file type: {ext}")
        print("     Only .pdf and .docx are supported.")
        return ""
    # Normalize resume text for any template/format
    if raw and not raw.startswith("PDF_ERROR:"):
        raw = preprocess_resume_text(raw)
    return raw


# -- Step 1: Generate questions ------------------------------------
def generate_questions(resume_text: str) -> list:
    """
    Sends the resume to Claude and gets back a list of targeted
    questions to fill in the gaps.

    Claude decides how many questions are needed and what they cover,
    based on what is already in the resume and what is missing.

    Returns a list of question dicts:
        [{"id": 1, "question": "...", "why": "..."}, ...]
    """
    print("\n  [AI] Analysing your resume and generating questions...")

    from api.prompts import get_prompt, PROMPT_INTAKE_QUESTIONS
    system = get_prompt(PROMPT_INTAKE_QUESTIONS)

    prompt = """Here is the resume text:

---
{resume_text}
---

Generate the questions now. Return ONLY the JSON array."""

    raw = call_api(
        messages=[{"role": "user", "content": prompt}],
        system=system,
        max_tokens=2000,
    )

    # Parse JSON -- strip any accidental markdown fences
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = "\n".join(cleaned.split("\n")[1:])
    if cleaned.endswith("```"):
        cleaned = "\n".join(cleaned.split("\n")[:-1])
    cleaned = cleaned.strip()

    try:
        questions = json.loads(cleaned)
        if isinstance(questions, list) and len(questions) > 0:
            return questions
        print("  [WARN]  Claude returned an unexpected format. Retrying...")
        return generate_questions(resume_text)
    except json.JSONDecodeError:
        print("  [WARN]  Could not parse questions. Retrying...")
        return generate_questions(resume_text)


# -- Step 2: Ask questions one by one -----------------------------
def ask_questions(questions: list) -> list:
    """
    Asks the user each question one by one.
    Collects all answers into a list of (question, answer) pairs.
    The user can type 'skip' to skip a question.
    Progress is never lost -- answers so far are kept even if interrupted.
    """
    total   = len(questions)
    answers = []

    print(f"\n{'='*60}")
    print(f"  [>>] {total} question(s) to answer")
    print("  Type your answer and press Enter.")
    print("  Type 'skip' to skip a question.")
    print(f"{'='*60}\n")

    for q in questions:
        qid      = q.get("id", "?")
        question = q.get("question", "")
        why      = q.get("why", "")

        print(f"  Question {qid} of {total}")
        if why:
            print(f"  (Why: {why})")
        print(f"  ? {question}")

        answer = _safe_input("  Your answer: ", "").strip()

        if answer.lower() == "skip":
            answer = "[skipped]"
            print("  <-  Skipped.\n")
        else:
            print()

        answers.append({
            "question": question,
            "answer":   answer,
        })

    return answers


# -- Step 3: Generate XML ------------------------------------------
def generate_xml(resume_text: str, qa_pairs: list) -> str:
    """
    Sends the resume + all Q&A answers to Claude.
    Claude generates a complete resume_data.xml in the exact format
    the bot expects.
    Returns the XML string.
    """
    print("\n  [AI] Generating resume_data.xml...")

    _ = "\n".join(
        f"Q: {pair['question']}\nA: {pair['answer']}"
        for pair in qa_pairs
    )

    from api.prompts import get_prompt, PROMPT_INTAKE_XML
    system = get_prompt(PROMPT_INTAKE_XML)

    prompt = """Here is the resume:

---
{resume_text}
---

Here are the answers to the intake questions:

---
{qa_text}
---

Generate the complete resume_data.xml now."""

    raw = call_api(
        messages=[{"role": "user", "content": prompt}],
        system=system,
        max_tokens=4000,
    )

    # Strip accidental markdown fences
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = "\n".join(cleaned.split("\n")[1:])
    if cleaned.endswith("```"):
        cleaned = "\n".join(cleaned.split("\n")[:-1])

    return cleaned.strip()


# -- Step 4: Validate XML ------------------------------------------
def validate_xml(xml_string: str) -> bool:
    """
    Checks that the generated XML is valid and has the required sections.
    Returns True if valid, False otherwise.
    """
    try:
        import xml.etree.ElementTree as ET
        root = ET.fromstring(xml_string)
        required = ["personal", "summary", "skills", "experience", "education"]
        missing  = [s for s in required if root.find(s) is None]
        if missing:
            print(f"  [WARN]  XML is missing sections: {missing}")
            return False
        return True
    except Exception as e:
        print(f"  [WARN]  XML validation error: {e}")
        return False


# -- Step 5: Generate my_profile.py note --------------------------
def enhance_experience(xml_path: str) -> str:
    """
    Optionally rewrites the work experience bullets in the XML to be
    more punchy and achievement-focused.

    For each job in the profile:
      1. Shows the existing bullets.
      2. Asks 4 targeted questions to surface numbers and outcomes.
      3. Calls Claude to rewrite the bullets using the real answers.
      4. Saves the updated XML back to disk.

    Returns the updated XML string, or the original if the user
    declined or if anything went wrong.
    """
    import xml.etree.ElementTree as ET
    from api.prompts import get_prompt, PROMPT_EXPERIENCE_ENHANCE

    print(f"\n{'='*60}")
    print("  [*] Experience Enhancement")
    print(f"{'='*60}")
    print("  For each job, I'll ask a few quick questions to pull out")
    print("  numbers and outcomes, then rewrite the bullets to be")
    print("  punchy and achievement-focused.")
    print("  (Type 'skip' on any question you can't answer.)\n")

    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
    except Exception as e:
        print(f"  [WARN]  Could not read XML for enhancement: {e}")
        return open(xml_path, encoding="utf-8").read()

    jobs = root.findall("experience/job")
    if not jobs:
        print("  (i)  No jobs found in the XML -- nothing to enhance.")
        return open(xml_path, encoding="utf-8").read()

    enhance_prompt = get_prompt(PROMPT_EXPERIENCE_ENHANCE)
    any_enhanced = False

    for i, job in enumerate(jobs, 1):
        title    = (job.findtext("title",    "") or "").strip()
        company  = (job.findtext("company",  "") or "").strip()
        duration = (job.findtext("duration", "") or "").strip()
        bullets_el = job.find("bullets")
        if bullets_el is None:
            continue

        existing = [b.text.strip() for b in bullets_el.findall("bullet")
                    if b.text and b.text.strip()]
        if not existing:
            continue

        print(f"  {'-'*56}")
        print(f"  [{i}/{len(jobs)}] {title} @ {company} ({duration})")
        print(f"  {'-'*56}")
        print("  Current bullets:")
        for b in existing:
            print(f"    * {b[:90]}{'...' if len(b) > 90 else ''}")
        print()

        # Open-ended questions -- non-leading so user gives real answers
        # that Claude uses without inflation or assumption.
        questions = [
            (
                f"For this role at {company}: do you have any real numbers? "
                "For example -- records processed, users served, team size, "
                "time saved in hours, error rate, requests per day. "
                "Only share a number if you actually know it."
            ),
            (
                "What specific thing did you build, fix, or deliver at "
                f"{company} that you are most proud of? Describe it in "
                "your own words."
            ),
            (
                "Did anything you built or changed have a measurable result? "
                "If yes, what was the result exactly? If you are not sure of "
                "a number, just describe what changed -- do not guess."
            ),
            (
                "Were any tools, libraries, or languages part of this work "
                "that are not already mentioned in the bullets above? "
                "Name only the ones you actually used."
            ),
        ]

        answers = []
        for q in questions:
            print(f"  ? {q}")
            ans = _safe_input("     Your answer: ", "").strip()
            if not ans or ans.lower() in ("skip", "n/a", "none", "no"):
                ans = "[no additional info]"
                print("  <-  Skipped.\n")
            else:
                print()
            answers.append({"question": q, "answer": ans})

        qa_text = "\n".join(
            f"Q: {a['question']}\nA: {a['answer']}"
            for a in answers
        )

        # Inline reminder so honesty rules hold even if the stored
        # prompt was edited -- sent with every request
        honesty_reminder = (
            "\n\nCRITICAL -- apply before anything else:\n"
            "- Use ONLY facts from the existing bullets or Q&A answers.\n"
            "- [no additional info] means nothing new -- do not fill in.\n"
            "- If no number was stated by the user, write no number.\n"
            "  Not 'significantly', not 'substantially', not any invented\n"
            "  quantity or percentage.\n"
            "- Do not upgrade a vague answer into a specific-sounding claim.\n"
            "- Technologies in bullets must appear in existing bullets or\n"
            "  be explicitly named in a Q&A answer."
        )

        print(f"  [AI] Rewriting bullets for {company}...")
        try:
            response = call_api(
                messages=[{
                    "role": "user",
                    "content": (
                        f"{enhance_prompt}{honesty_reminder}\n\n"
                        f"ROLE: {title} at {company} ({duration})\n\n"
                        "EXISTING BULLETS:\n"
                        + "\n".join(f"- {b}" for b in existing)
                        + f"\n\nENHANCEMENT Q&A:\n{qa_text}\n\n"
                        "Rewrite the bullets now. "
                        "Return ONLY a JSON array of strings."
                    )
                }],
                max_tokens=1000,
            )

            # Strip markdown fences if present
            cleaned = response.strip()
            if "```" in cleaned:
                parts = cleaned.split("```")
                cleaned = parts[1] if len(parts) > 1 else parts[0]
                if cleaned.startswith("json"):
                    cleaned = cleaned[4:]

            new_bullets = json.loads(cleaned.strip())

            if not isinstance(new_bullets, list) or not new_bullets:
                raise ValueError("Claude returned an empty or invalid list")

            # Update the XML in memory
            for child in list(bullets_el):
                bullets_el.remove(child)
            for bullet_text in new_bullets:
                el = ET.SubElement(bullets_el, "bullet")
                el.text = bullet_text.strip()

            print(f"  [OK] Enhanced -- {len(new_bullets)} bullets rewritten.")
            print("  Preview:")
            for b in new_bullets[:3]:
                print(f"    * {b[:90]}{'...' if len(b) > 90 else ''}")
            if len(new_bullets) > 3:
                print(f"    ... and {len(new_bullets) - 3} more")
            print()
            any_enhanced = True

        except Exception as e:
            print(f"  [WARN]  Could not enhance {company} bullets: {e}")
            print("     Keeping the original bullets for this job.\n")

    if any_enhanced:
        # Pretty-print the XML with indentation
        ET.indent(tree, space="    ")
        tree.write(xml_path, encoding="unicode", xml_declaration=True)
        print(f"  [OK] Enhanced resume saved to: {xml_path}")
        return open(xml_path, encoding="utf-8").read()
    else:
        print("  (i)  No changes made.")
        return open(xml_path, encoding="utf-8").read()




# -- Main flow -----------------------------------------------------
def main():
    print("\n" + "=" * 60)
    print("  [FILE] Resume Intake Tool")
    print("  This will build your resume_data.xml for the job bot.")
    print("=" * 60)

    # -- Check API key --------------------------------------------
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("\n  [ERR] ANTHROPIC_API_KEY is not set.")
        print("     Set it first:  set ANTHROPIC_API_KEY=your-key-here")
        print("     Then run this script again.")
        sys.exit(1)

    print("\n  [KEY] Checking API key...")
    call_api(
        messages=[{"role": "user", "content": "ping"}],
        system="Reply with just the word: ok",
        max_tokens=5,
    )
    print("  [OK] API key works.\n")

    # -- Get the resume file path ---------------------------------
    while True:
        print("  Paste the full path to your resume file.")
        print("  Supported formats: .pdf  .docx")
        print("  Example: C:\\Users\\YourName\\Desktop\\my_resume.pdf\n")
        path = _safe_input("  File path: ", "").strip()

        if not path:
            print("  [WARN]  No path entered. Try again.\n")
            continue

        resume_text = read_resume_file(path)
        if resume_text:
            print(f"\n  [OK] Resume read ({len(resume_text)} characters).")
            break
        else:
            print("  [WARN]  Could not read that file. Check the path and try again.\n")

    # -- Generate questions ---------------------------------------
    questions = generate_questions(resume_text)
    print(f"  [OK] Generated {len(questions)} question(s).\n")

    # -- Ask questions one by one ---------------------------------
    qa_pairs = ask_questions(questions)

    # -- Generate XML ---------------------------------------------
    xml_string = ""
    attempts   = 0
    while attempts < 3:
        xml_string = generate_xml(resume_text, qa_pairs)
        if validate_xml(xml_string):
            break
        attempts += 1
        print(f"  [LOOP] XML validation failed -- regenerating (attempt {attempts}/3)...")

    if not xml_string or not validate_xml(xml_string):
        print("\n  [ERR] Could not generate valid XML after 3 attempts.")
        print("     Saving raw output to resume_data_raw.txt for review.")
        with open("resume_data_raw.txt", "w", encoding="utf-8") as f:
            f.write(xml_string)
        return

    # -- Save XML -------------------------------------------------
    import sys as _sys
    if getattr(_sys, "frozen", False):
        _base = os.path.dirname(_sys.executable)
    else:
        import sys as _sys5
        _base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if not os.path.isdir(os.path.join(_base, "api")):
            _base = os.path.dirname(os.path.abspath(_sys5.executable))
    xml_path = os.path.join(_base, "data", "resume_data.xml")
    os.makedirs(os.path.dirname(xml_path), exist_ok=True)

    # Back up existing resume_data.xml if it exists
    if os.path.exists(xml_path):
        backup = xml_path.replace(".xml", "_backup.xml")
        os.rename(xml_path, backup)
        print("\n  [PKG] Existing resume_data.xml backed up to resume_data_backup.xml")

    with open(xml_path, "w", encoding="utf-8") as f:
        f.write(xml_string)

    print(f"  [OK] resume_data.xml saved to: {xml_path}")

    # -- Offer experience enhancement -----------------------------
    print(f"\n{'='*60}")
    print("  [*] Would you like to enhance your work experience bullets?")
    print("     This makes them more punchy and achievement-focused")
    print("     by asking a few quick questions per job.")
    print(f"{'='*60}")
    enhance_choice = _safe_input("  Enhance experience bullets? [y / n]: ", "n").strip().lower()
    if enhance_choice in ("y", "yes"):
        xml_string = enhance_experience(xml_path)
    else:
        print("  [OK] Keeping the bullets as generated.\n")

    # -- Offer per-job experience highlighting --------------------
    print(f"\n{'='*60}")
    print("  [>>] Job-specific experience highlighting")
    print(f"{'='*60}")
    print("  When generating a tailored resume for each job, should")
    print("  the app reorder your experience bullets to put the most")
    print("  relevant ones first for that specific role?")
    print("  (Bullet text stays exactly the same -- only the order and")
    print("  selection changes per job. Zero content is invented.)")
    highlight_choice = _safe_input("  Enable job-specific highlighting? [y / n]: ", "n").strip().lower()

    experience_highlight = highlight_choice in ("y", "yes")
    print(
        f"  [OK] Job-specific highlighting {'enabled' if experience_highlight else 'disabled'}.\n"
    )

    # Update the XML meta section with the user's choice
    try:
        import xml.etree.ElementTree as ET
        tree = ET.parse(xml_path)
        root = tree.getroot()
        meta = root.find("meta")
        if meta is None:
            meta = ET.SubElement(root, "meta")
        hl_el = meta.find("experience_highlight")
        if hl_el is None:
            hl_el = ET.SubElement(meta, "experience_highlight")
        hl_el.text = "true" if experience_highlight else "false"
        ET.indent(tree, space="    ")
        tree.write(xml_path, encoding="unicode", xml_declaration=True)
        xml_string = open(xml_path, encoding="utf-8").read()
    except Exception as e:
        print(f"  [WARN]  Could not save highlighting preference: {e}")

    # -- Generate personalised prompts from the new profile -------
    # This is automatic -- the user does not need to do anything.
    # Reads the XML we just built and generates prompts specific to
    # this person's background. Stored in prompts.db, never in code.
    print(f"\n{'='*60}")
    print("  [AI] Setting up your personalised AI prompts...")
    print("     This takes about 30 seconds...")
    print(f"{'='*60}\n")
    try:
        import xml.etree.ElementTree as ET
        from core.profile import load_profile_from_xml
        from api.prompts import setup_all_prompts

        profile = load_profile_from_xml(xml_path)
        prompt_result = setup_all_prompts(profile, _get_client(), overwrite=False)
        print("\n  [OK] Prompts ready -- "
              f"{prompt_result['created']} generated, "
              f"{prompt_result['skipped']} already existed.")
    except Exception as e:
        print(f"\n  [WARN]  Could not generate prompts: {e}")
        print("     Run python migrate_prompts.py to set them up separately.")

    # -- Extract name for the preview -----------------------------
    try:
        import xml.etree.ElementTree as ET
        root = ET.fromstring(xml_string)
        name = root.findtext("personal/name", "Unknown").strip()
    except Exception:
        name = "Unknown"

    # -- Profile note ---------------------------------------------
    # Build a short profile summary note
    try:
        _skills = []
        _root2  = ET.fromstring(xml_string)
        for _s in _root2.findall(".//skill")[:5]:
            _skills.append(_s.text or "")
        note = (
            f"  Name:   {name}\n"
            f"  Skills: {', '.join(s for s in _skills if s)}\n"
            f"  Saved:  {xml_path}"
        )
    except Exception:
        note = f"  Profile saved to: {xml_path}"
    print("\n" + "=" * 60)
    print("  [OK] All done!")
    print(note)
    print("=" * 60)

    # -- Show a preview of what was built -------------------------
    print("\n  [>>] Quick preview of what was captured:\n")
    try:
        import xml.etree.ElementTree as ET
        root = ET.fromstring(xml_string)

        name     = root.findtext("personal/name", "").strip()
        email    = root.findtext("personal/email", "").strip()
        location = root.findtext("personal/location", "").strip()
        jobs     = root.findall("experience/job")
        projects = root.findall("projects/project")
        degrees  = root.findall("education/degree")

        print(f"  Name     : {name}")
        print(f"  Email    : {email}")
        print(f"  Location : {location}")
        print(f"  Jobs     : {len(jobs)}")
        print(f"  Projects : {len(projects)}")
        print(f"  Degrees  : {len(degrees)}")
        print()

        for job in jobs:
            title   = job.findtext("title", "").strip()
            company = job.findtext("company", "").strip()
            dates   = job.findtext("duration", "").strip()
            print(f"  [JOB] {title} @ {company} ({dates})")

        print()
        for proj in projects:
            pname = proj.findtext("name", "").strip()
            print(f"  [FIX] {pname}")

    except Exception:
        pass

    print("\n  You can now run the main bot with run.bat.")
    print(f"  It will use the resume data from: {xml_path}\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n  [>>] Stopped by user. Your progress was not saved to disk.")
        print("     Run the script again to start over.\n")