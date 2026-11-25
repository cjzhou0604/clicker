# -*- mode: python ; coding: utf-8 -*-
# 连点器打包配置。相对路径都相对本文件（build/）解析。产物文件名一律用 ASCII。
#
# 用 onedir 而不是 onefile：onefile 每次启动都要把运行时解压到 %TEMP%，一旦用户会话的
# 临时目录不可用就会弹「Could not create temporary directory!」；onedir 直接从 exe 旁边的
# _internal 运行，没有这个依赖，启动也更快。

a = Analysis(
    ['../main.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Clicker',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version='version_info.txt',
    icon=['clicker.ico'],
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='Clicker',
)
