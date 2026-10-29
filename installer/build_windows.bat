@echo off
rem ======================================================================
rem  Builds XZN for Windows, plus the setup program.
rem  Run from anywhere:   installer\build_windows.bat
rem
rem  Needs:  Python 3.10+ (python.org), Inno Setup 6 (for the installers)
rem  Output:  dist\XZN\, release\XZN_Setup_v<version>.exe
rem  All build files (specs, Inno scripts) live in this installer\ folder.
rem ======================================================================
setlocal
cd /d "%~dp0\.."

if not exist .venv-build (
    python -m venv .venv-build || goto :fail
)
call .venv-build\Scripts\activate.bat || goto :fail
python -m pip install --upgrade pip
pip install -r requirements.txt pyinstaller || goto :fail

for /f "delims=" %%v in ('python installer\version.py xzn.py') do set VER=%%v
echo.
echo === Building XZN %VER% ===

pyinstaller --noconfirm --clean installer\xzn.spec || goto :fail

rem --- Inno Setup ------------------------------------------------------
set ISCC=
where iscc >nul 2>nul && set ISCC=iscc
if not defined ISCC if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set ISCC="%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set ISCC="%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not defined ISCC (
    echo.
    echo Inno Setup not found - skipping the installers.
    echo Install it from https://jrsoftware.org/isinfo.php and run this again.
    goto :done
)
%ISCC% /Q /DMyAppVersion=%VER% installer\xzn.iss || goto :fail

:done
echo.
echo Done:
echo   dist\XZN\XZN.exe
if defined ISCC echo   release\XZN_Setup_v%VER%.exe
exit /b 0

:fail
echo.
echo BUILD FAILED - see the messages above.
exit /b 1
