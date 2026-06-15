# my_profile.py
import sys as _sys, os as _os
if not getattr(_sys, "frozen", False):
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import os
import xml.etree.ElementTree as ET
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
    tree = ET.parse(xml_path)
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
    personal = root.find("personal")
    if personal is not None:
        profile["name"]     = personal.findtext("name", "").strip()
        profile["email"]    = personal.findtext("email", "").strip()
        profile["phone"]    = personal.findtext("phone", "").strip()
        profile["location"] = personal.findtext("location", "").strip()
        profile["linkedin"] = personal.findtext("linkedin", "").strip()
        profile["github"]   = personal.findtext("github", "").strip()
    
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
            
            profile["experience"].append(job_data)
    
    # -- Education -----------------------------------------------
    profile["education"] = []
    education_section = root.find("education")
    if education_section is not None:
        for degree in education_section.findall("degree"):
            degree_data = {
                "degree": degree.findtext("name", "").strip(),
                "school": degree.findtext("school", "").strip(),
                "year":   degree.findtext("year", "").strip(),
            }
            profile["education"].append(degree_data)
    
    # -- Projects ------------------------------------------------
    profile["projects"] = []
    projects_section = root.find("projects")
    if projects_section is not None:
        for project in projects_section.findall("project"):
            project_data = {
                "name":        project.findtext("name", "").strip(),
                "tech":        project.findtext("tech", "").strip(),
                "description": project.findtext("description", "").strip(),
            }
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


# -- Load profile on import --------------------------------------
try:
    MY_PROFILE = load_profile_from_xml()
    print(f"[OK] Profile loaded: {MY_PROFILE['name']}")
except FileNotFoundError:
    # No profile yet — user hasn't done intake. This is normal on first run.
    MY_PROFILE = {}
    print("[INFO] No resume_data.xml found yet — run intake in Settings to create one.")
except Exception as e:
    log_error("Profile load error: %s" % e)
    print("[ERR] Error loading profile from XML: %s" % e)
    MY_PROFILE = {}