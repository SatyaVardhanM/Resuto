@echo off
:: Resuto first-run setup
:: Creates Documents\Resuto\ structure and default local_settings.json
:: Called silently by the installer

set RESUTO_DIR=%USERPROFILE%\Documents\Resuto

:: Create directory structure
mkdir "%RESUTO_DIR%" 2>nul
mkdir "%RESUTO_DIR%\output" 2>nul
mkdir "%RESUTO_DIR%\output\resumes" 2>nul
mkdir "%RESUTO_DIR%\output\resumes\docx" 2>nul
mkdir "%RESUTO_DIR%\output\resumes\pdf" 2>nul
mkdir "%RESUTO_DIR%\logs" 2>nul
mkdir "%RESUTO_DIR%\BotChromeProfile" 2>nul

:: Write default local_settings.json only if it doesn't exist
if exist "%RESUTO_DIR%\local_settings.json" goto :done

(
echo {
echo     "chrome_profile_path": "%RESUTO_DIR%\BotChromeProfile",
echo     "output_dir": "%RESUTO_DIR%\output",
echo     "resume_data_path": "%RESUTO_DIR%\resume_data.xml"
echo }
) > "%RESUTO_DIR%\local_settings.json"

:done
exit /b 0