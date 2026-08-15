#ifndef MyAppVersion
  #define MyAppVersion "0.2.0"
#endif
#ifndef MyAppSourceExe
  #define MyAppSourceExe "..\outputs\Soulvise-Plmate-v0.2.0-Windows-x64.exe"
#endif
#ifndef MyOutputDir
  #define MyOutputDir "..\outputs"
#endif
#ifndef MyOutputBaseFilename
  #define MyOutputBaseFilename "Soulvise-Plmate-Setup-v0.2.0-Windows-x64"
#endif
#ifndef MyAppId
  #define MyAppId "{{A1A7519E-D09C-49C2-AEDC-13463264051C}"
#endif

#define MyAppName "Soulvise Plmate"
#define MyAppPublisher "Soulvise"
#define MyAppExeName "SoulvisePlmate.exe"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVerName={#MyAppName} {#MyAppVersion}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\SoulvisePlmate
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
MinVersion=10.0.10240
OutputDir={#MyOutputDir}
OutputBaseFilename={#MyOutputBaseFilename}
SetupIconFile=..\src\desktop_companion_agent\resources\app_icon.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
UsePreviousAppDir=yes
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
VersionInfoVersion={#MyAppVersion}.0
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} 安装程序
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}

[Languages]
Name: "chinesesimplified"; MessagesFile: "languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："

[Files]
Source: "{#MyAppSourceExe}"; DestDir: "{app}"; DestName: "{#MyAppExeName}"; Flags: ignoreversion

[InstallDelete]
Type: files; Name: "{app}\DonggeDesktopAgent.exe"

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent
