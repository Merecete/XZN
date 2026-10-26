@echo off
rem ======================================================================
rem  Builds XZN and XZN-CDR for Windows, plus the two setup programs.
rem  Run from anywhere:   installer\build_windows.bat
rem
rem  Needs:  Python 3.10+ (python.org), Inno Setup 6 (for the installers)
rem  XZN-CDR: put a *full* ffmpeg build into xzn-cdr\ffmpeg\
rem           (ffmpeg.exe + ffprobe.exe) so it gets bundled.
rem  Output:  dist\XZN\, xzn-cdr\dist\XZN-CDR\, release\*_Setup_*.exe
rem ======================================================================
setlocal
cd /d "%~dp0\.."

if not exist .venv-build (
    python -m venv .venv-build || goto :fail
)
call .venv-build\Scripts\activate.bat || goto :fail
python -m pip install --upgrade pip
pip install -r requirements.txt -r xzn-cdr\requirements.txt pyinstaller || goto :fail

for /f "delims=" %%v in ('python installer\version.py xzn.py') do set VER=%%v
for /f "delims=" %%v in ('python installer\version.py xzn-cdr\xzn-cdr.py') do set CDR_VER=%%v
echo.
echo === Building XZN %VER% / XZN-CDR %CDR_VER% ===

pyinstaller --noconfirm --clean xzn.spec || goto :fail

if not exist xzn-cdr\ffmpeg\ffmpeg.exe (
    echo WARNING: xzn-cdr\ffmpeg\ffmpeg.exe not found - XZN-CDR will need ffmpeg on the PATH.
)
pushd xzn-cdr
pyinstaller --noconfirm --clean xzn-cdr.spec || (popd & goto :fail)
popd

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
%ISCC% /Q /DMyAppVersion=%VER% setup.iss || goto :fail
%ISCC% /Q /DMyAppVersion=%CDR_VER% xzn-cdr\setup-xzn-cdr.iss || goto :fail

:done
echo.
echo Done:
echo   dist\XZN\XZN.exe
echo   xzn-cdr\dist\XZN-CDR\XZN-CDR.exe
if defined ISCC echo   release\  - setup programs
exit /b 0

:fail
echo.
echo BUILD FAILED - see the messages above.
exit /b 1
