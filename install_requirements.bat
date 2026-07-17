@echo off
echo ============================================
echo  ReelForge — installing requirements
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
echo You can now run run_reelforge.bat
pause
