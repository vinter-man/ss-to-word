@echo off
rem Collects what is needed to work out why the launcher fails on this machine.
rem Writes diagnose-log.txt next to itself; ASCII and CRLF, like start_windows.bat.
setlocal enableextensions
cd /d "%~dp0"
set "LOG=%~dp0diagnose-log.txt"

echo ss-to-word diagnostics> "%LOG%"
echo %DATE% %TIME%>> "%LOG%"
echo.>> "%LOG%"

echo == Windows ==>> "%LOG%"
ver>> "%LOG%" 2>&1
echo PROCESSOR_ARCHITECTURE=%PROCESSOR_ARCHITECTURE%>> "%LOG%"
echo folder=%~dp0>> "%LOG%"
echo.>> "%LOG%"

echo == py launcher ==>> "%LOG%"
py --list>> "%LOG%" 2>&1
echo.>> "%LOG%"

echo == python on PATH ==>> "%LOG%"
where python>> "%LOG%" 2>&1
python -VV>> "%LOG%" 2>&1
echo.>> "%LOG%"

echo == tkinter ==>> "%LOG%"
py -3.11 -c "import tkinter; print('tkinter ok', tkinter.TkVersion)">> "%LOG%" 2>&1
python -c "import tkinter; print('tkinter ok', tkinter.TkVersion)">> "%LOG%" 2>&1
echo.>> "%LOG%"

echo == tesseract ==>> "%LOG%"
where tesseract>> "%LOG%" 2>&1
if exist "C:\Program Files\Tesseract-OCR\tesseract.exe" echo found in Program Files>> "%LOG%"
echo.>> "%LOG%"

echo == this folder ==>> "%LOG%"
dir /b>> "%LOG%" 2>&1
echo.>> "%LOG%"

echo == .venv ==>> "%LOG%"
if exist ".venv\Scripts\python.exe" (
  .venv\Scripts\python.exe -VV>> "%LOG%" 2>&1
  .venv\Scripts\python.exe -c "import rapidocr, onnxruntime, cv2, numpy, docx, PIL, pillow_heif, tkinter; print('packages ok')">> "%LOG%" 2>&1
) else (
  echo no .venv yet>> "%LOG%"
)
echo.>> "%LOG%"

echo == last lines of setup-log.txt ==>> "%LOG%"
if exist "setup-log.txt" (
  powershell -NoProfile -Command "Get-Content setup-log.txt -Tail 40">> "%LOG%" 2>&1
) else (
  echo no setup-log.txt>> "%LOG%"
)

echo Done.
echo.
echo A file called diagnose-log.txt was created next to this one.
echo Send it to whoever gave you this program.
echo.
pause
