# my_profile.py
import sys as _sys, os as _os
if not getattr(_sys, "frozen", False):
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import os
import xml.etree.ElementTree as ET
from api.intake import sanitize_xml
try:
    from core.logger import log, log_warn, log_error
except Exception:
    def log(m,*a): pass
    def log_warn(m,*a): pass
    def log_error(m,*a,**k): pass
from core.settings import get_resume_data_path


def load_profile_from_xml(xml_path: str = None) -> dict:
    """
    Loads user profile data from an XML file.

    The path comes from local_settings.json (git-ignored), set on first
    run. The real resume_data.xml is never committed; a placeholder
    template ships as resume_data.example.xml instead.
    """
    # Resolve the path from local settings if not explicitly passed
    if xml_path is None:
        xml_path = get_resume_data_path()

    # Try as given, then relative to this script's folder
    if not os.path.exists(xml_path):
        import sys as _sys
        if getattr(_sys, "frozen", False):
            script_dir = os.path.dirname(_sys.executable)
        else:
            script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        candidate  = os.path.join(script_dir, os.path.basename(xml_path))
        if os.path.exists(candidate):
            xml_path = candidate

    if not os.path.exists(xml_path):
        raise FileNotFoundError(
            f"Could not find your resume data file at: {xml_path}\n"
            f"   -> Copy resume_data.example.xml to resume_data.xml\n"
            f"   -> Fill in your real details\n"
            f"   -> Or re-run setup to point at a different path"
        )
    
    # Parse XML
    raw = open(xml_path, encoding="utf-8", errors="replace").read()
    tree = ET.ElementTree(ET.fromstring(sanitize_xml(raw)))
    root = tree.getroot()
    
    profile = {}

    # -- Meta -- bot behaviour settings --------------------------
    meta = root.find("meta")
    if meta is not None:
        try:
            profile["years_experience"] = float(
                meta.findtext("years_experience", "0").strip()
            )
        except ValueError:
            profile["years_experience"] = 0.0
        profile["work_authorization"]  = meta.findtext("work_authorization", "").strip()
        profile["experience_highlight"] = meta.findtext(
            "experience_highlight", "false"
        ).strip().lower() == "true"
    else:
        profile["years_experience"]    = 0.0
        profile["work_authorization"]  = ""
        profile["experience_highlight"] = False

    # -- Personal Information ------------------------------------
    def _clean(val: str) -> str:
        """Strip [PLACEHOLDER] and similar sentinel values — treat as empty."""
        v = (val or "").strip()
        if v.upper() in ("[PLACEHOLDER]", "PLACEHOLDER", "N/A", "NONE", "NULL", "TBD"):
            return ""
        if v.startswith("[") and v.endswith("]"):
            return ""
        return v

    personal = root.find("personal")
    if personal is not None:
        profile["name"]     = _clean(personal.findtext("name", ""))
        profile["email"]    = _clean(personal.findtext("email", ""))
        profile["phone"]    = _clean(personal.findtext("phone", ""))
        profile["location"] = _clean(personal.findtext("location", ""))
        profile["linkedin"] = _clean(personal.findtext("linkedin", ""))
        profile["github"]   = _clean(personal.findtext("github", ""))
    
    # -- Summary -------------------------------------------------
    profile["summary"] = root.findtext("summary", "").strip()
    
    # -- Skills --------------------------------------------------
    profile["skills"] = {}
    skills_section = root.find("skills")
    if skills_section is not None:
        for category in skills_section.findall("category"):
            category_name = category.get("name", "")
            skills_list = [
                skill.text.strip() 
                for skill in category.findall("skill") 
                if skill.text
            ]
            if category_name and skills_list:
                profile["skills"][category_name] = skills_list
    
    # -- Experience ----------------------------------------------
    profile["experience"] = []
    experience_section = root.find("experience")
    if experience_section is not None:
        for job in experience_section.findall("job"):
            job_data = {
                "title":    job.findtext("title", "").strip(),
                "company":  job.findtext("company", "").strip(),
                "location": job.findtext("location", "").strip(),
                "duration": job.findtext("duration", "").strip(),
                "bullets":  [],
            }
            
            bullets_section = job.find("bullets")
            if bullets_section is not None:
                job_data["bullets"] = [
                    bullet.text.strip()
                    for bullet in bullets_section.findall("bullet")
                    if bullet.text
                ]

            # tech_stack — from Environment: lines (IT consultant resumes)
            tech_stack = job.findtext("tech_stack", "").strip()
            if tech_stack:
                job_data["tech_stack"] = tech_stack

            profile["experience"].append(job_data)
    
    # -- Education -----------------------------------------------
    profile["education"] = []
    education_section = root.find("education")
    if education_section is not None:
        for degree in education_section.findall("degree"):
            degree_data = {
                "degree": _clean(degree.findtext("name", "")),
                "school": _clean(degree.findtext("school", "")),
                "year":   _clean(degree.findtext("year", "")),
            }
            # Skip entirely empty education entries
            if any(degree_data.values()):
                profile["education"].append(degree_data)
    
    # -- Projects ------------------------------------------------
    profile["projects"] = []
    projects_section = root.find("projects")
    if projects_section is not None:
        for project in projects_section.findall("project"):
            proj_bullets_sec = project.find("bullets")
            proj_bullets = []
            if proj_bullets_sec is not None:
                proj_bullets = [
                    b.text.strip() for b in proj_bullets_sec.findall("bullet") if b.text
                ]
            project_data = {
                "name":        _clean(project.findtext("name", "")),
                "tech":        _clean(project.findtext("tech", "")),
                "description": _clean(project.findtext("description", "")),
                "bullets":     proj_bullets,
            }
            # Only include projects that have at least a name
            if project_data["name"]:
                profile["projects"].append(project_data)
    
    # -- Volunteer -----------------------------------------------
    profile["volunteer"] = []
    volunteer_section = root.find("volunteer")
    if volunteer_section is not None:
        for activity in volunteer_section.findall("activity"):
            volunteer_data = {
                "role":         activity.findtext("role", "").strip(),
                "organization": activity.findtext("organization", "").strip(),
                "description":  activity.findtext("description", "").strip(),
            }
            profile["volunteer"].append(volunteer_data)
    
    log("Profile parsed: %s (%s yrs, %d skills)" % (
        profile.get("name","?"),
        profile.get("years_experience",0),
        sum(len(v) for v in profile.get("skills",{}).values())))
    return profile


# -- Lazy profile loading ----------------------------------------
# MY_PROFILE is populated on first access, not at import time.
# Avoids input() EOF errors when imported as a subprocess.
MY_PROFILE: dict = {}
_profile_loaded: bool = False


def get_profile() -> dict:
    """Returns cached profile, loading from XML on first call."""
    global MY_PROFILE, _profile_loaded
    if not _profile_loaded:
        _profile_loaded = True
        try:
            MY_PROFILE = load_profile_from_xml()
        except FileNotFoundError:
            MY_PROFILE = {}
        except Exception as e:
            log_error("Profile load error: %s" % e)
            MY_PROFILE = {}
    return MY_PROFILE
    MY_PROFILE = {}