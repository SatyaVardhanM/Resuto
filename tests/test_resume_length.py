"""
tests/test_resume_length.py — page-limit fitting of tailored resumes.
(The estimate was calibrated against real LibreOffice renders of
build_resume_docx: 70 resumes, every fitted one came out within its limit.)
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from api import resume_length as rl  # noqa: E402

PROFILE = {"name": "T", "email": "a@b.com", "years_experience": 3.5,
           "education": [{"degree": "B.Tech", "school": "JNTU", "year": "2020"}]}
LONG = "Developed scalable REST APIs using C# .NET Core and SQL Server, " \
       "improving response time by 40% for 2M users across services."


def _resume(jobs=5, bullets=7, projects=2):
    return {
        "summary": LONG * 2,
        "skills_grouped": {"Backend": ["C#", ".NET", "SQL"] * 3, "Cloud": ["AWS"] * 8},
        "experience": [{"title": "Engineer", "company": "Co%d" % i,
                        "duration": "2020 - 2023",
                        "bullets": [LONG] * bullets} for i in range(jobs)],
        "projects": [{"name": "P%d" % k, "tech": "Py", "bullets": [LONG, LONG]}
                     for k in range(projects)],
    }


def test_target_pages_auto_and_fixed():
    assert rl.target_pages({"years_experience": 3.5}, "auto") == 1
    assert rl.target_pages({"years_experience": 6}, "auto") == 2
    assert rl.target_pages({"years_experience": 6}, "1") == 1
    assert rl.target_pages({"years_experience": 1}, "2") == 2


def test_fit_keeps_every_role_and_does_not_touch_input():
    t = _resume()
    before = [len(j["bullets"]) for j in t["experience"]]
    fitted, left_out = rl.fit_to_pages(t, PROFILE, 1)
    assert [len(j["bullets"]) for j in t["experience"]] == before   # input unchanged
    assert len(fitted["experience"]) == len(t["experience"])        # no role removed
    assert rl.estimate_pages(fitted, PROFILE) <= 1.0
    assert left_out and all(k in ("bullet", "project") for k, _, _ in left_out)


def test_older_roles_are_cut_before_recent_ones():
    fitted, _ = rl.fit_to_pages(_resume(jobs=4, bullets=5, projects=0), PROFILE, 1)
    b = [len(j["bullets"]) for j in fitted["experience"]]
    assert b[0] >= b[1] >= b[2] >= b[3] and b[0] >= 2


def test_two_page_target_leaves_short_resume_alone():
    t = _resume(jobs=2, bullets=3, projects=0)
    fitted, left_out = rl.fit_to_pages(t, PROFILE, 2)
    assert left_out == [] and fitted == t


def test_summary_text():
    s = rl.summarize_left_out([("bullet", "TSS", "x"), ("bullet", "TSS", "y"),
                               ("project", "Chatbot", "")])
    assert s == "left out 2 bullets (2 from TSS), 1 project (Chatbot)"
    assert rl.summarize_left_out([]) == "nothing left out"


def test_profile_overview():
    prof = dict(PROFILE, summary=LONG, skills={"A": ["x"]},
                experience=_resume(jobs=6, bullets=8)["experience"])
    ov = rl.profile_overview(prof)
    assert ov["roles"] == 6 and ov["bullets"] == 48 and ov["pages"] > 2
