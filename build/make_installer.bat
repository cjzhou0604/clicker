@echo off
chcp 65001 >/dev/null
rem 用 Inno Setup 把 Clicker.exe + _internal 打成安装包。
rem 本文件必须是 UTF-8（无 BOM）且每行以 CRLF 结尾；文件名保持 ASCII，中文只出现在注释和 echo 里。
cd /d "%~dp0.."

set "ISCC=%~1"
if defined ISCC goto :check_iscc

for %%D in (
  "E:\Program Files\Inno Setup 7\ISCC.exe"
  "C:\Program Files\Inno Setup 7\ISCC.exe"
  "E:\Program Files (x86)\Inno Setup 7\ISCC.exe"
  "C:\Program Files (x86)\Inno Setup 7\ISCC.exe"
) do if exist %%D set "ISCC=%%~D"

:check_iscc
if not defined ISCC goto :no_inno
if not exist "%ISCC%" goto :no_inno

if not exist "Clicker.exe" goto :no_exe
if not exist "_internal" goto :no_exe

echo 正在编译安装包...
"%ISCC%" build\installer.iss
if errorlevel 1 goto :fail

echo.
echo 完成：build\installer\Clicker-Setup-1.0.1.exe
echo 建议接着自检：python tests\installer_check.py
pause
exit /b 0

:no_inno
echo 没找到 Inno Setup 的 ISCC.exe。
echo 也可以把它的完整路径作为参数传进来：make_installer.bat "D:\Inno Setup 7\ISCC.exe"
pause
exit /b 1

:no_exe
echo 还没有 Clicker.exe 和 _internal，请先运行 buildebuild.bat 打包。
pause
exit /b 1

:fail
echo.
echo 编译失败，请把上面的报错文字一起发给我。
pause
exit /b 1
