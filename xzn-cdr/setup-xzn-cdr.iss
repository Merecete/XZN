; ==================================================================
;  XZN-CDR (Xenon-Zones CD Ripper) — Inno Setup script
; ==================================================================

#define MyAppShortName  "XZN-CDR"
#define MyAppLongName   "Xenon-Zones CD Ripper"
#define MyAppVersion    "1.0.2"
#define MyAppPublisher  "Merecete"
#define MyAppURL        "https://example.com/xzn"
#define MyAppExeName    "XZN-CDR.exe"
#define MyAppRegKey     "Merecete"

[Setup]
AppId={{B2C3D4E5-F6G7-8H90-1234-56789ABCDEF0}
AppName={#MyAppShortName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppShortName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
AppCopyright=© 2026 {#MyAppPublisher}

DefaultDirName={autopf}\{#MyAppRegKey}\{#MyAppShortName}
DefaultGroupName={#MyAppShortName}
DisableProgramGroupPage=yes
DisableWelcomePage=no

LicenseFile=LICENSE.txt

OutputDir=installer
OutputBaseFilename={#MyAppShortName}_Setup_v{#MyAppVersion}

SetupIconFile=xzn.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppShortName} {#MyAppVersion}

Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern

PrivilegesRequired=admin
MinVersion=10.0
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "german";  MessagesFile: "compiler:Languages\German.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; \
    GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "dist\*"; DestDir: "{app}"; \
    Flags: ignoreversion recursesubdirs createallsubdirs
Source: "LICENSE.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppShortName}"; \
    Filename: "{app}\{#MyAppExeName}"; \
    IconFilename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppShortName}}"; \
    Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppShortName}"; \
    Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; \
    Description: "{cm:LaunchProgram,{#MyAppShortName}}"; \
    Flags: nowait postinstall skipifsilent
