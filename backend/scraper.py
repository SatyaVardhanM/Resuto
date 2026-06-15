# job_scraper.py
import sys as _sys, os as _os
if not getattr(_sys, "frozen", False):
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

from playwright.async_api import async_playwright
import asyncio
import random

DATE_MAP = {
    "any":   "",
    "month": "f_TPR=r2592000",
    "week":  "f_TPR=r604800",
    "24hr":  "f_TPR=r86400",
}
EXPERIENCE_MAP = {
    "internship": "1", "entry": "2", "associate": "3",
    "mid_senior": "4", "director": "5", "executive": "6",
}
JOB_TYPE_MAP = {
    "full_time": "F", "part_time": "P", "contract": "C",
    "temporary": "T", "volunteer": "V", "internship": "I",
}
WORKPLACE_MAP = {"on_site": "1", "remote": "2", "hybrid": "3"}
JOBS_PER_PAGE = 25

ADJACENT_ROLES = [
    "software engineer", "software developer", "backend engineer",
    "backend developer", "frontend engineer", "frontend developer",
    "full stack", "fullstack", "web developer", "web engineer",
    "application developer", "dotnet", ".net developer", "c# developer",
    "python developer", "python engineer", "machine learning", "ml engineer",
    "ai engineer", "deep learning", "data scientist", "data engineer",
    "computer vision", "nlp engineer", "research engineer", "ai developer",
    "cloud engineer", "devops engineer", "azure developer",
    "platform engineer", "engineer", "developer",
]

CARD_SELECTORS = [
    ".job-card-container",
    ".jobs-search-results__list-item",
    "li[data-occludable-job-id]",
    ".scaffold-layout__list-item",
]


# ── Generic keyword expansion engine ─────────────────────────────
# Generates related search terms from any role the user types.
# Works for any tech stack, any seniority, any domain.
# No hardcoded roles — all rules are structural/linguistic.

_SENIORITY = [
    "senior", "sr.", "sr ", "junior", "jr.", "jr ", "lead", "staff",
    "principal", "entry level", "entry-level", "associate", "mid-level",
    "mid level", "experienced", "ii", "iii", "iv", "i "
]

_TITLE_SYNONYMS = [
    # (set of equivalent words) — swapped to generate variants
    {"developer", "engineer", "programmer", "specialist"},
    {"developer", "engineer", "architect"},
    {"analyst", "specialist", "consultant"},
    {"manager", "lead", "head"},
]

_ROLE_BROADENERS = {
    # specific → broader fallback
    "full stack":   "software",
    "fullstack":    "software",
    "backend":      "software",
    "front end":    "software",
    "frontend":     "software",
    "front-end":    "software",
}


def expand_keyword(keyword: str) -> list:
    """
    Generate a ranked list of related search terms from any keyword.
    Generic — works for any role, any tech stack, any user.

    Strategy (in order of specificity):
      1. Strip seniority modifier       "Senior .NET Developer" → ".NET Developer"
      2. Swap title word synonym        ".NET Developer" → ".NET Engineer"
      3. Broaden role descriptor        "Backend Python Dev" → "Software Developer"
      4. Add Remote variant             ".NET Developer" → ".NET Developer Remote"
      5. Strip tech prefix (last resort)"Python Backend Developer" → "Software Developer"
    """
    import re
    original = keyword.strip()
    results  = []
    seen     = {original.lower()}

    def _add(term):
        t = term.strip()
        if t and t.lower() not in seen and len(t) > 3:
            seen.add(t.lower())
            results.append(t)

    lower = original.lower()

    # ── Step 1: Strip seniority ───────────────────────────────────
    stripped = lower
    for sen in _SENIORITY:
        stripped = stripped.replace(sen, "").strip()
    stripped = re.sub(r"\s+", " ", stripped).strip()
    if stripped and stripped != lower:
        # Preserve original capitalisation pattern
        _add(stripped.title())

    # ── Step 2: Swap title synonyms ───────────────────────────────
    for syn_group in _TITLE_SYNONYMS:
        for word in syn_group:
            if word in lower:
                for alt in syn_group:
                    if alt != word:
                        candidate = re.sub(
                            r"\b" + re.escape(word) + r"\b",
                            alt, lower, flags=re.IGNORECASE)
                        _add(candidate.title())
                # Also try stripped+swap
                if stripped and word in stripped:
                    for alt in syn_group:
                        if alt != word:
                            candidate = re.sub(
                                r"\b" + re.escape(word) + r"\b",
                                alt, stripped, flags=re.IGNORECASE)
                            _add(candidate.title())

    # ── Step 3: Broaden role descriptor ──────────────────────────
    for specific, broad in _ROLE_BROADENERS.items():
        if specific in lower:
            broadened = lower.replace(specific, broad)
            broadened = re.sub(r"\s+", " ", broadened).strip()
            _add(broadened.title())

    # ── Step 4: Add Remote variant ────────────────────────────────
    # Only add if keyword doesn't already have Remote
    if "remote" not in lower:
        _add(original + " Remote")
        if stripped and stripped != lower:
            _add(stripped.title() + " Remote")

    # ── Step 5: Strip tech prefix (last resort) ───────────────────
    # "Python Backend Developer" → "Software Developer"
    # "Java Engineer" → "Software Engineer"
    words = lower.split()
    title_words = {"developer","engineer","programmer","analyst",
                   "architect","consultant","specialist","manager"}
    for tw in title_words:
        if tw in words:
            idx = words.index(tw)
            if idx > 0:  # there IS a prefix to strip
                _add(("Software " + tw).title())
                break

    return results[:6]  # cap at 6 fallbacks


def is_internship_search(keyword: str) -> bool:
    kw = keyword.lower()
    return any(w in kw for w in ["intern", "internship", "trainee", "co-op", "placement"])


def get_active_filters(keyword: str, apply_mode: str = "easy_apply") -> dict:
    from core.config import JOB_FILTERS
    import copy
    filters = copy.deepcopy(JOB_FILTERS)
    if apply_mode == "all":
        filters["easy_apply_only"] = False
    else:
        filters["easy_apply_only"] = True
    if is_internship_search(keyword):
        if "internship" not in filters["experience_levels"]:
            filters["experience_levels"].insert(0, "internship")
        if "internship" not in filters["job_types"]:
            filters["job_types"].append("internship")
    return filters


# -- Human-like Actions ------------------------------------------

async def _human_pause(min_ms=500, max_ms=2000):
    await asyncio.sleep(random.uniform(min_ms / 1000, max_ms / 1000))


async def _human_scroll(page, distance):
    """Scrolls smoothly like a human."""
    scrolled = 0
    while scrolled < distance:
        chunk = min(random.randint(80, 220), distance - scrolled)
        await page.mouse.wheel(0, chunk)
        scrolled += chunk
        await asyncio.sleep(random.uniform(0.05, 0.15))


async def _scroll_job_list(page, distance):
    """Scrolls the job list panel on the left."""
    try:
        for selector in [".jobs-search-results-list", "ul.jobs-search-results__list"]:
            panel = await page.query_selector(selector)
            if panel:
                scrolled = 0
                while scrolled < distance:
                    chunk = min(random.randint(150, 300), distance - scrolled)
                    await panel.evaluate(f"el => el.scrollBy(0, {chunk})")
                    scrolled += chunk
                    await asyncio.sleep(random.uniform(0.08, 0.18))
                return
    except Exception:
        pass


def score_job(title: str, keyword: str) -> int:
    title_lower   = title.lower()
    keyword_lower = keyword.lower()
    if keyword_lower in title_lower:
        return 100
    keyword_words = [w for w in keyword_lower.split() if len(w) > 2]
    skip_words = ["senior","junior","lead","intern","engineer","developer"]
    matched = sum(1 for w in keyword_words if w not in skip_words and w in title_lower)
    if matched >= 2:
        return matched * 25 + 30
    for adj in ADJACENT_ROLES:
        if adj in title_lower:
            return 15
    return 0


def build_search_url(keyword, location, filters, start=0):
    params = [
        f"keywords={keyword.replace(' ', '%20')}",
        f"location={location.replace(' ', '%20')}",
        "sortBy=DD",
        f"start={start}",
    ]
    if filters.get("easy_apply_only"):
        params.append("f_AL=true")
    date_code = DATE_MAP.get(filters.get("date_posted", "any"), "")
    if date_code:
        params.append(date_code)
    exp = filters.get("experience_levels", [])
    if exp:
        codes = "%2C".join(EXPERIENCE_MAP[e] for e in exp if e in EXPERIENCE_MAP)
        if codes:
            params.append(f"f_E={codes}")
    jt = filters.get("job_types", [])
    if jt:
        codes = "%2C".join(JOB_TYPE_MAP[j] for j in jt if j in JOB_TYPE_MAP)
        if codes:
            params.append(f"f_JT={codes}")
    wp = filters.get("workplace", ["on_site","remote","hybrid"])
    if wp:
        codes = "%2C".join(WORKPLACE_MAP[w] for w in wp if w in WORKPLACE_MAP)
        if codes:
            params.append(f"f_WT={codes}")
    return "https://www.linkedin.com/jobs/search/?" + "&".join(params)


async def find_valid_location(page, keyword, preferred_location, filters):
    from core.config import LOCATION_FALLBACKS
    candidates = [preferred_location] + [
        loc for loc in LOCATION_FALLBACKS if loc.lower() != preferred_location.lower()
    ]
    for location in candidates:
        print(f"   [LOC] Trying: '{location}'...")
        url = build_search_url(keyword, location, filters, start=0)
        await page.goto(url, wait_until="domcontentloaded")
        await _human_pause(3000, 5000)
        
        cards = []
        for selector in CARD_SELECTORS:
            cards = await page.query_selector_all(selector)
            if cards:
                break
        
        no_results = await page.query_selector(".jobs-search-no-results-banner")
        if cards and len(cards) > 0 and not no_results:
            print(f"   [OK] '{location}' works -- {len(cards)} jobs found\n")
            return location
        print(f"   [WARN]  No results for '{location}'")
    print(f"   (i)  Falling back to Worldwide\n")
    return "Worldwide"


# -- Human-like Job Card Collection ------------------------------

async def collect_job_cards_human_way(page, keyword) -> list:
    """
    Collects job card elements (not data).
    Returns list of clickable card elements.
    """
    cards = []
    for selector in CARD_SELECTORS:
        try:
            cards = await page.query_selector_all(selector)
            if cards and len(cards) > 0:
                break
        except Exception:
            continue
    
    if not cards:
        print(f"   [WARN]  No job cards found")
        return []
    
    print(f"   [>>] Found {len(cards)} job cards")
    
    # Filter by relevance based on visible title
    relevant_cards = []
    for card in cards:
        try:
            # Get title from card
            title_el = None
            for sel in [".job-card-list__title", "h3", ".artdeco-entity-lockup__title"]:
                title_el = await card.query_selector(sel)
                if title_el:
                    break
            
            if not title_el:
                continue

            # inner_text() on LinkedIn title elements returns TWO lines:
            # line 1 — the real job title
            # line 2 — "<title> with verification" (a hidden accessibility span)
            # Take only the first non-empty line.
            raw_title = (await title_el.inner_text()).strip()
            title = raw_title.splitlines()[0].strip()
            if not title:
                title = raw_title  # fallback: use full text if first line is empty
            relevance = score_job(title, keyword)
            
            if relevance > 0:
                relevant_cards.append({
                    "element": card,
                    "title": title,
                    "relevance": relevance,
                })
        except Exception:
            continue
    
    # Sort by relevance
    relevant_cards.sort(key=lambda x: x["relevance"], reverse=True)
    
    print(f"   [OK] {len(relevant_cards)} relevant jobs")
    return relevant_cards


async def scrape_page_human_way(page, keyword, location, filters, start):
    """
    Navigates to search page and scrolls to load cards.
    Returns list of clickable card elements.
    """
    url = build_search_url(keyword, location, filters, start=start)
    await _human_pause(800, 1500)
    await page.goto(url, wait_until="domcontentloaded")
    await _human_pause(3000, 5000)

    if await page.query_selector(".jobs-search-no-results-banner"):
        return []

    # Scroll naturally to load all cards
    for _ in range(random.randint(3, 5)):
        await _human_scroll(page, random.randint(300, 600))
        await _scroll_job_list(page, random.randint(400, 800))
        await _human_pause(500, 1000)

    return await collect_job_cards_human_way(page, keyword)


# -- Click Job Card and Read Description -------------------------

async def click_job_card_and_get_details(page, card_data: dict) -> dict:
    """
    Clicks a job card (human-like), waits for details to load,
    scrolls to read description, extracts URL and details.
    Returns job dict with all info.
    """
    card = card_data["element"]
    
    try:
        # Scroll card into view
        await card.scroll_into_view_if_needed()
        await _human_pause(300, 700)
        
        # Hover over card (human behavior)
        await card.hover()
        await _human_pause(200, 500)
        
        # Click the card
        print(f"   [MOUSE]  Clicking: {card_data['title']}")
        await card.click()
        await _human_pause(2000, 3000)
        
        # Wait for job details panel to load
        details_loaded = False
        for selector in [
            ".jobs-details",
            ".jobs-unified-top-card",
            ".job-view-layout",
        ]:
            try:
                await page.wait_for_selector(selector, timeout=5000)
                details_loaded = True
                break
            except Exception:
                continue
        
        if not details_loaded:
            print(f"   [WARN]  Details panel didn't load")
            return None
        
        # Scroll to read description (human behavior)
        await _human_pause(500, 1000)
        await _human_scroll(page, random.randint(200, 400))
        await _human_pause(800, 1500)
        
        # Get job URL from current page
        current_url = page.url
        
        # Get company from details panel
        company = "Unknown"
        for sel in [
            ".jobs-unified-top-card__company-name",
            ".job-details-jobs-unified-top-card__company-name",
            "a.ember-view.job-details-jobs-unified-top-card__company-name",
        ]:
            try:
                company_el = await page.query_selector(sel)
                if company_el:
                    company = (await company_el.inner_text()).strip()
                    break
            except Exception:
                continue
        
        # Get location
        location = "Unknown"
        for sel in [
            ".jobs-unified-top-card__bullet",
            ".job-details-jobs-unified-top-card__bullet",
        ]:
            try:
                location_el = await page.query_selector(sel)
                if location_el:
                    location = (await location_el.inner_text()).strip()
                    break
            except Exception:
                continue
        
        # ── Detect apply status (Easy Apply, External, Already Applied) ──
        easy_apply       = False
        already_applied  = False
        apply_type       = "unknown"  # easy_apply | external | applied

        try:
            # Priority 1: Check if already applied (Easy Apply or tracked)
            # LinkedIn shows these selectors when job is already applied:
            applied_selectors = [
                "button[aria-label*='Applied']",           # aria-label
                ".jobs-apply-button--applied",               # CSS class
                "button:has-text('Applied')",               # button text
                ".artdeco-button--applied",                  # artdeco applied
                "[class*='jobs-apply-button']:has-text('Applied')",
            ]
            for sel in applied_selectors:
                try:
                    el = await page.query_selector(sel)
                    if el:
                        txt = (await el.inner_text()).strip().lower()
                        if "applied" in txt or not txt:
                            already_applied = True
                            apply_type      = "applied"
                            break
                except Exception:
                    continue

            # Also check for "Application submitted" or similar text on page
            if not already_applied:
                submitted_selectors = [
                    ".jobs-applied-indicator",
                    "[class*='applied-indicator']",
                    ".artdeco-inline-feedback--success",
                ]
                for sel in submitted_selectors:
                    try:
                        el = await page.query_selector(sel)
                        if el:
                            already_applied = True
                            apply_type      = "applied"
                            break
                    except Exception:
                        continue

            # Priority 2: Easy Apply button present (not yet applied)
            if not already_applied:
                easy_apply_selectors = [
                    'button:has-text("Easy Apply")',
                    '.jobs-apply-button:has-text("Easy Apply")',
                    '[class*="easy-apply"]',
                ]
                for sel in easy_apply_selectors:
                    try:
                        el = await page.query_selector(sel)
                        if el:
                            easy_apply = True
                            apply_type = "easy_apply"
                            break
                    except Exception:
                        continue

            # Priority 3: External apply button
            if not already_applied and not easy_apply:
                ext_selectors = [
                    'button:has-text("Apply")',
                    'a:has-text("Apply")',
                    '[class*="apply-button"]',
                ]
                for sel in ext_selectors:
                    try:
                        el = await page.query_selector(sel)
                        if el:
                            apply_type = "external"
                            break
                    except Exception:
                        continue

        except Exception as _ae:
            pass  # apply status detection failed — continue normally

            easy_apply = True
        
        # Get description by scrolling and reading
        description = await get_job_description_human_way(page)
        
        return {
            "title": card_data["title"],
            "company": company,
            "location": location,
            "url": current_url,
            "description": description,
            "easy_apply":      easy_apply,
            "already_applied": already_applied,
            "apply_type":      apply_type,    # easy_apply | external | applied | unknown
            "site": "linkedin",
            "relevance": card_data["relevance"],
        }
        
    except Exception as e:
        print(f"   [ERR] Error clicking card: {e}")
        return None


async def get_job_description_human_way(page) -> str:
    """
    Scrolls through and reads the job description like a human.
    """
    try:
        # Scroll down to expand any "Show more" buttons
        await _human_pause(500, 1000)
        
        # Look for "Show more" button
        show_more_selectors = [
            "button:has-text('Show more')",
            "button:has-text('See more')",
            ".show-more-less-html__button--more",
        ]
        for selector in show_more_selectors:
            try:
                btn = await page.query_selector(selector)
                if btn and await btn.is_visible():
                    await btn.click()
                    await _human_pause(800, 1500)
                    break
            except Exception:
                continue
        
        # Scroll to read description
        await _human_scroll(page, random.randint(300, 600))
        await _human_pause(1000, 2000)
        
        # Extract description text
        for selector in [
            ".jobs-description__content",
            ".jobs-box__html-content",
            ".job-details-jobs-unified-top-card__job-description",
        ]:
            el = await page.query_selector(selector)
            if el:
                text = await el.inner_text()
                if text and len(text.strip()) > 100:
                    return text.strip()
        
        return ""
        
    except Exception as e:
        print(f"   [WARN]  Description read error: {e}")
        return ""


# -- Continuous Job Search Generator -----------------------------

async def continuous_job_search(page, keyword: str, location: str, apply_mode: str = "easy_apply"):
    """
    Async generator that yields jobs one at a time.
    Clicks cards like a human, reads details naturally.

    seen_urls is seeded from tracker.csv so jobs processed in
    previous runs are skipped -- the bot remembers across restarts,
    not just within a single run.
    """
    filters     = get_active_filters(keyword, apply_mode)

    # Seed with jobs already logged in past runs (applied/skipped/failed)
    try:
        from db.tracker import load_seen_urls
        seen_urls = load_seen_urls()
        if seen_urls:
            print(f"   [CARDS]  Loaded {len(seen_urls)} previously seen jobs "
                  f"from db.tracker -- these will be skipped")
    except Exception as e:
        print(f"   [WARN]  Could not load tracker history: {e}")
        seen_urls = set()

    page_number = 0
    empty_pages = 0
    max_empty   = 3

    print(f"\n[LOC] Validating location...")
    valid_location = await find_valid_location(page, keyword, location, filters)

    mode_label = "[!] Easy Apply only" if apply_mode == "easy_apply" else "[WEB] All jobs"
    print(f"\n[>>] Continuous search:")
    print(f"   Role       : {keyword}")
    print(f"   Location   : {valid_location}")
    print(f"   Apply Mode : {mode_label}\n")

    while True:
        start = page_number * JOBS_PER_PAGE
        print(f"\n[FILE] Page {page_number + 1} (jobs {start + 1}-{start + JOBS_PER_PAGE})...")

        try:
            cards = await scrape_page_human_way(page, keyword, valid_location, filters, start)
        except Exception as e:
            if any(m in str(e) for m in [
                "Target page, context or browser has been closed",
                "Browser closed", "Connection closed",
            ]):
                print("\n[>>] Browser closed -- stopping.")
                return
            print(f"   [WARN]  Page error: {e} -- retrying...")
            await asyncio.sleep(5)
            continue

        if not cards:
            empty_pages += 1
            print(f"   [WARN]  No new jobs ({empty_pages}/{max_empty} empty pages)")
            if empty_pages >= max_empty:
                print(f"\n   [DONE] LinkedIn results exhausted for '{keyword}'.")
                print(f"   All available jobs have been processed.")
                return   # stop generator — orchestrator moves to next role
        else:
            empty_pages = 0
            print(f"   [OK] Processing {len(cards)} jobs...")

            for card_data in cards:
                # Click card and get full details
                job = await click_job_card_and_get_details(page, card_data)
                
                if not job:
                    continue
                
                if job["url"] in seen_urls:
                    print(f"   [SKIP]  Already seen: {job['title']}")
                    continue
                
                seen_urls.add(job["url"])
                
                badge = "[!]" if job["easy_apply"] else "[WEB]"
                print(f"   {badge} {job['title']} @ {job['company']}")
                
                yield job
                
                # Pause between jobs (human behavior)
                await _human_pause(2000, 4000)

        page_number += 1
        await _human_pause(3000, 6000)


# -- Get Job Description (for backwards compatibility) -----------

async def get_job_description(page, url: str) -> str:
    """
    This function is called from main.py but we already have description.
    Just return what we got from clicking the card.
    """
    # Description is already fetched when clicking card
    # This is a compatibility function
    return ""