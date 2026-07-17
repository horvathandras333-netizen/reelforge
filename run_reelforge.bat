@echo off
python reelforge.py
if %errorlevel% neq 0 (
    echo.
    echo ReelForge exited with an error.
    pause
)
