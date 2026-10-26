; ==================================================================
;  XZN-CDR (Xenon-Zones CD Ripper) — Inno Setup script
;  Built by build_windows.bat; by hand: iscc installer\xzn-cdr.iss
; ==================================================================

#define MyAppShortName  "XZN-CDR"
#define MyAppLongName   "Xenon-Zones CD Ripper"
; Version: passed in by build_windows.bat (/DMyAppVersion=...),
; this value is only the fallback for building by hand.
#ifndef MyAppVersion
  #define MyAppVersion  "1.0.3"
#endif
#define MyAppPublisher  "Merecete"
#define MyAppURL        "https://example.com/xzn"
#define MyAppExeName    "XZN-CDR.exe"
#define MyAppRegKey     "Merecete"

; Safety check: the program must be at ..\dist\XZN-CDR\XZN-CDR.exe,
; otherwise the shortcuts would point to a file that doesn't exist.
#if !FileExists(AddBackslash(SourcePath) + "..\dist\XZN-CDR\XZN-CDR.exe")
  #error "..\dist\XZN-CDR\XZN-CDR.exe not found - build it first (installer\build_windows.bat)"
#endif

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

LicenseFile=..\LICENSE.txt

OutputDir=..\release
OutputBaseFilename={#MyAppShortName}_Setup_v{#MyAppVersion}

SetupIconFile=..\xzn-cdr\xzn.ico
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
; PyInstaller onedir output (see the .spec file)
Source: "..\dist\XZN-CDR\*"; DestDir: "{app}"; \
    Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\LICENSE.txt"; DestDir: "{app}"; Flags: ignoreversion

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
