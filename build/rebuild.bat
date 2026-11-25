@echo off
chcp 65001 >nul
rem 重新打包 Clicker.exe。没有 build\venv 时会自动创建并安装 PyInstaller。
rem 批处理必须存成 CRLF 行尾；产物文件名一律 ASCII，中文只出现在注释和 echo 里。
cd /d "%~dp0.."
set "ROOT=%CD%"

if exist "build\venv\Scripts\python.exe" goto :have_env
echo 正在创建打包用的虚拟环境...
python -m venv build\venv
if errorlevel 1 goto :fail
echo 正在安装 PyInstaller...
build\venv\Scripts\python.exe -m pip install --isolated --index-url https://pypi.org/simple pyinstaller
if errorlevel 1 goto :fail

:have_env
echo 正在生成图标...
build\venv\Scripts\python.exe build\make_icon.py build\clicker.ico
if errorlevel 1 goto :fail

echo 正在打包...
build\venv\Scripts\python.exe -m PyInstaller --noconfirm --distpath "%ROOT%\build\dist" --workpath "%ROOT%\build\work" "%ROOT%\build\clicker.spec"
if errorlevel 1 goto :fail

echo 正在把产物放到本文件夹...
build\venv\Scripts\python.exe build\finalize.py
if errorlevel 1 goto :fail

echo.
echo 完成：双击 Clicker.exe 即可使用。
pause
exit /b 0

:fail
echo.
echo 打包失败，请把上面的报错信息一起发给我。
pause
exit /b 1
