# pdf_converter.py
import sys as _sys, os as _os
if not getattr(_sys, "frozen", False):
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

"""
Converts DOCX resumes to PDF using whatever is available:

  1. docx2pdf   -- wraps Microsoft Word on Windows (best quality)
  2. LibreOffice headless -- free, cross-platform (good quality)
  3. Graceful skip -- copies the DOCX path back, logs a warning

Install the best option available:
  pip install docx2pdf        (requires Microsoft Word to be installed)
  -- OR --
  Install LibreOffice from https://www.libreoffice.org/

The bot will still work without a PDF converter -- it uploads
the DOCX directly when applying. PDF is only for your own records.
"""

import os
import shutil
import subprocess


def _try_docx2pdf(docx_path: str, pdf_path: str) -> bool:
    """Try converting via docx2pdf (requires Microsoft Word)."""
    try:
        from docx2pdf import convert
        convert(docx_path, pdf_path)
        return os.path.exists(pdf_path)
    except ImportError:
        return False
    except Exception as e:
        print(f"   [WARN]  docx2pdf failed: {e}")
        return False


def _try_libreoffice(docx_path: str, pdf_folder: str) -> str | None:
    """Try converting via LibreOffice headless. Returns pdf path or None."""
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        # Check common Windows install paths
        import platform as _plat
        _sys = _plat.system()
        _lo_paths = (
            [r"C:\Program Files\LibreOffice\program\soffice.exe",
             r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"]
            if _sys == "Windows" else
            ["/Applications/LibreOffice.app/Contents/MacOS/soffice"]
            if _sys == "Darwin" else
            ["/usr/bin/soffice", "/usr/bin/libreoffice"]
        )
        for path in _lo_paths:
            if os.path.exists(path):
                soffice = path
                break

    if not soffice:
        return None

    try:
        result = subprocess.run(
            [soffice, "--headless", "--convert-to", "pdf",
             "--outdir", pdf_folder, docx_path],
            capture_output=True, text=True, timeout=60,
        )
        # LibreOffice names the output <basename>.pdf in the outdir
        base = os.path.splitext(os.path.basename(docx_path))[0]
        pdf_path = os.path.join(pdf_folder, base + ".pdf")
        if os.path.exists(pdf_path):
            return pdf_path
        print(f"   [WARN]  LibreOffice ran but PDF not found: {result.stderr[:200]}")
        return None
    except Exception as e:
        print(f"   [WARN]  LibreOffice failed: {e}")
        return None


def convert_to_pdf(docx_path: str, pdf_folder: str) -> str:
    """
    Convert a DOCX file to PDF, saving it in the matching
    company sub-folder under pdf_folder.

    Returns the PDF path on success, or the DOCX path if
    conversion is unavailable (so the rest of the bot can
    continue without crashing).
    """
    # Mirror the company sub-folder structure
    company_subfolder = os.path.basename(os.path.dirname(docx_path))
    target_folder = (
        os.path.join(pdf_folder, company_subfolder)
        if company_subfolder else pdf_folder
    )
    os.makedirs(target_folder, exist_ok=True)

    base     = os.path.splitext(os.path.basename(docx_path))[0]
    pdf_path = os.path.join(target_folder, base + ".pdf")

    print(f"   [FILE] Converting to PDF...")

    # Method 1 — docx2pdf (Microsoft Word)
    if _try_docx2pdf(docx_path, pdf_path):
        print(f"   [OK] PDF saved -> {pdf_path}")
        return pdf_path

    # Method 2 — LibreOffice headless
    lo_pdf = _try_libreoffice(docx_path, target_folder)
    if lo_pdf:
        # Rename to exact expected path if needed
        if lo_pdf != pdf_path and os.path.exists(lo_pdf):
            os.replace(lo_pdf, pdf_path)
        print(f"   [OK] PDF saved (LibreOffice) -> {pdf_path}")
        return pdf_path

    # Method 3 — graceful skip
    print(
        f"   [WARN]  PDF conversion unavailable.\n"
        f"     Install Microsoft Word + 'pip install docx2pdf',\n"
        f"     OR install LibreOffice (https://www.libreoffice.org/).\n"
        f"     The DOCX resume is still usable: {docx_path}"
    )
    return docx_path   # return DOCX path so the bot can continue