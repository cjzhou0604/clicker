@echo off
rem 从源码启动连点器（无控制台窗口）；打包版请直接双击 Clicker.exe
cd /d "%~dp0"
where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw main.py
) else (
    start "" python main.py
)
