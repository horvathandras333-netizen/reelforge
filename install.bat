@echo off
setlocal
echo ============================================
echo  ReelForge - install
echo ============================================
echo.
python -m pip install --upgrade pip
python -m pip install PySide6 imageio-ffmpeg Pillow
echo.
echo Core install done.
echo.
echo OPTIONAL: Beat sync (cuts to the music beat) needs a larger library.
echo If you want it, run this separately:
echo     python -m pip install librosa
echo.

set "SCRIPT_DIR=%~dp0"
set "ICON_PATH=%SCRIPT_DIR%icon.ico"

for /f "delims=" %%i in ('where pythonw 2^>nul') do set "PYTHONW=%%i"
if not defined PYTHONW set "PYTHONW=pythonw.exe"

echo Creating desktop shortcut...
powershell -NoProfile -Command "$s=(New-Object -COMObject WScript.Shell).CreateShortcut([System.IO.Path]::Combine([Environment]::GetFolderPath('Desktop'),'ReelForge.lnk')); $s.TargetPath='%PYTHONW%'; $s.Arguments='\"%SCRIPT_DIR%reelforge.py\"'; $s.WorkingDirectory='%SCRIPT_DIR%'; $s.IconLocation='%ICON_PATH%'; $s.Save()"

if exist "%USERPROFILE%\Desktop\ReelForge.lnk" (
    echo Desktop shortcut created: ReelForge
) else (
    echo Could not create the desktop shortcut - you can still launch ReelForge with run_reelforge.bat
)

echo.
echo ============================================
echo  Install complete.
echo  Launch ReelForge from the new desktop shortcut,
echo  or by double-clicking run_reelforge.bat
echo ============================================
pause
