#define MyAppName "TRIAZ Groove Builder"
#define MyAppVersion "0.1.9"
#define MyAppPublisher "TRIAZ Groove Builder"
#define MyAppExeName "TRIAZ Groove Builder.exe"

[Setup]
AppId={{9A5409E1-5CC5-43FD-96C7-7AB205E9C2D4}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir={#SourcePath}\..\artifacts
OutputBaseFilename=TRIAZ_Groove_Builder_Setup_{#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}
CloseApplications=yes
RestartApplications=no

[Files]
Source: "{#SourcePath}\..\dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourcePath}\..\app.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourcePath}\..\requirements.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourcePath}\..\triaz_groove_builder\*"; DestDir: "{app}\triaz_groove_builder"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#SourcePath}\..\build\python-3.11.9-amd64.exe"; DestDir: "{app}\bootstrap"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
