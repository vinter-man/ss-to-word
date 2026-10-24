@echo off
rem Double-click launcher for Windows. ASCII only and CRLF line endings on purpose:
rem cmd.exe misparses a .bat that mixes UTF-8 text with chcp, and one saved with Unix
rem line endings can die inside a parenthesised block before it reaches any message.
setlocal enableextensions
cd /d "%~dp0"

set "LOG=%~dp0setup-log.txt"
set "STAMP=.venv\.install-ok"

echo ss-to-word launcher> "%LOG%"
echo %DATE% %TIME%>> "%LOG%"
ver>> "%LOG%" 2>&1

if exist "%STAMP%" goto run

echo Preparing the environment. The first run takes 2-5 minutes and needs the internet.
echo.

set "PY="
for %%V in (3.13 3.12 3.11 3.14) do call :try_python %%V
if not defined PY call :try_plain_python

if not defined PY (
  echo Python 3.11 or newer with Tkinter was not found.
  echo Install it from https://www.python.org/downloads/windows/
  echo and tick "Add python.exe to PATH" in the first installer screen.
  echo Python 3.11 or newer with Tkinter was not found.>> "%LOG%"
  goto fail
)

echo Using: %PY%>> "%LOG%"
%PY% -VV>> "%LOG%" 2>&1

if exist ".venv" rmdir /s /q ".venv"
%PY% -m venv .venv>> "%LOG%" 2>&1
if errorlevel 1 (
  echo Could not create the .venv folder.
  goto fail
)

echo Installing the packages, please wait...
.venv\Scripts\python.exe -m pip install --upgrade pip>> "%LOG%" 2>&1
.venv\Scripts\python.exe -m pip install -r requirements.txt>> "%LOG%" 2>&1
if errorlevel 1 goto pip_failed

.venv\Scripts\python.exe -c "import rapidocr, onnxruntime, cv2, numpy, docx, PIL, pillow_heif, tkinter">> "%LOG%" 2>&1
if errorlevel 1 (
  echo The environment is incomplete. Run this file again to reinstall it from scratch.
  goto fail
)
echo ok> "%STAMP%"

:run
if not exist "C:\Program Files\Tesseract-OCR\tesseract.exe" if not exist "%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe" (
  echo Tip: for the best accuracy install Tesseract: https://github.com/UB-Mannheim/tesseract/wiki
  echo.
)
echo Starting...
echo --- program output --->> "%LOG%"
.venv\Scripts\python.exe main.py>> "%LOG%" 2>&1
set "CODE=%ERRORLEVEL%"
if not "%CODE%"=="0" (
  echo.
  echo The program stopped with code %CODE%.
  goto fail
)
echo.
echo Done. This window can be closed.
pause
exit /b 0

:pip_failed
findstr /i /c:"No matching distribution" /c:"Could not find a version" "%LOG%" >nul
if errorlevel 1 goto pip_no_network
echo There are no ready-made OCR packages for this Python on this computer.
echo Install Python 3.13 from https://www.python.org/downloads/windows/ and run this again.
echo Windows on ARM is not supported.
goto fail

:pip_no_network
echo Could not install the packages - check the internet connection and run this again.
goto fail

:try_python
if defined PY goto :eof
py -%1 -c "import sys,tkinter; sys.exit(0 if sys.version_info>=(3,11) else 1)">nul 2>&1
if not errorlevel 1 set "PY=py -%1"
goto :eof

:try_plain_python
python -c "import sys,tkinter; sys.exit(0 if sys.version_info>=(3,11) else 1)">nul 2>&1
if not errorlevel 1 set "PY=python"
goto :eof

:fail
echo.
echo The details are written to setup-log.txt next to this file.
echo Send that file to whoever gave you this program.
echo.
pause
exit /b 1
