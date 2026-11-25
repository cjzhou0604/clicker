; 连点器安装包脚本（Inno Setup 7）。
; 编译：build\make_installer.bat，或
;   "E:\Program Files\Inno Setup 7\ISCC.exe" build\installer.iss
; 产物：build\installer\Clicker-Setup-1.0.0.exe
;
; 本文件含中文，必须存成「UTF-8 带 BOM」，否则 Inno 会按 ANSI 读取而出现乱码。
; 产物文件名一律 ASCII，中文只出现在界面文字里。

#define MyAppName "连点器"
#define MyAppNameEn "Clicker"
#define MyAppVersion "1.0.0"
#define MyAppExeName "Clicker.exe"

[Setup]
; AppId 固定，升级安装才会覆盖同一款程序而不是留下两份卸载项。
AppId={{29C25F91-AE90-43D8-B3F4-CB1CD844910F}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
DefaultGroupName={#MyAppName}
DefaultDirName={autopf}\{#MyAppNameEn}
UninstallDisplayName={#MyAppName} {#MyAppVersion}
UninstallDisplayIcon={app}\{#MyAppExeName}
OutputDir=installer
OutputBaseFilename={#MyAppNameEn}-Setup-{#MyAppVersion}
SetupIconFile=clicker.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
; 默认按当前用户安装到 %LOCALAPPDATA%\Programs\Clicker，全程不需要管理员权限；
; 选「为所有用户安装」时才弹 UAC。
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
; 装到 Program Files 时 data\ 必须仍可写，否则录制和宏存不下来。
; 这里给普通用户补上修改权限；按当前用户安装时目录本来就可写，不必处理。
DisableProgramGroupPage=yes
CloseApplications=yes
RestartApplications=no
VersionInfoVersion=1.0.0.0
VersionInfoProductName={#MyAppName}
ShowLanguageDialog=no

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："

[Files]
Source: "..\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\USER_GUIDE.md"; DestDir: "{app}"; Flags: ignoreversion
; data\ 里是用户自己的录制和宏，既不打包也不删除。

[Dirs]
Name: "{app}"; Permissions: users-modify; Check: IsAdminInstallMode

[InstallDelete]
; 升级时先清掉旧的运行时，避免 _internal 里留下上一版没用的 dll。
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autoprograms}\使用说明"; Filename: "{app}\USER_GUIDE.md"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "立即运行{#MyAppName}"; Flags: postinstall nowait skipifsilent

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: string;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{app}\data');
    if DirExists(DataDir) then
      MsgBox('卸载完成。你的录制、宏和设置仍保留在：' + #13#10 + DataDir + #13#10 +
             '需要彻底清理时手动删除这个文件夹即可。', mbInformation, MB_OK);
  end;
end;
