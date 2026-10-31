; ==================================================================
;  XZN — Inno Setup script (Branding: XZN)
;  Built by build_windows.bat; by hand: iscc installer\xzn.iss
; ==================================================================

#define MyAppName       "XZN"
; Version: passed in by build_windows.bat (/DMyAppVersion=...),
; this value is only the fallback for building by hand.
#ifndef MyAppVersion
  #define MyAppVersion  "1.1.1"
#endif
#define MyAppPublisher  "Merecete"
#define MyAppURL        "https://example.com/xzn"
#define MyAppExeName    "XZN.exe"
#define MyAppRegKey     "Merecete"

; Safety check: the program must be at ..\dist\XZN\XZN.exe,
; otherwise the shortcuts would point to a file that doesn't exist.
#if !FileExists(AddBackslash(SourcePath) + "..\dist\XZN\XZN.exe")
  #error "..\dist\XZN\XZN.exe not found - build it first (installer\build_windows.bat)"
#endif

[Setup]
AppId={{8F1A2B3C-4D5E-6F70-8192-A3B4C5D6E7F8}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
AppCopyright=© 2026 {#MyAppPublisher}

DefaultDirName={autopf}\{#MyAppRegKey}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
DisableWelcomePage=no

LicenseFile=..\LICENSE.txt

OutputDir=..\release
OutputBaseFilename={#MyAppName}_Setup_v{#MyAppVersion}

SetupIconFile=..\xzn.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName} {#MyAppVersion}

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
Source: "..\dist\XZN\*"; DestDir: "{app}"; \
    Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\LICENSE.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; \
    Filename: "{app}\{#MyAppExeName}"; \
    IconFilename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; \
    Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; \
    Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; \
    Description: "{cm:LaunchProgram,{#MyAppName}}"; \
    Flags: nowait postinstall skipifsilent

[Registry]
; --- 1. Capability-Beschreibung -----------------------------------
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}"; \
    Flags: uninsdeletekey
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability"; \
    Flags: uninsdeletekey
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability"; \
    ValueType: string; ValueName: "ApplicationName"; \
    ValueData: "{#MyAppName}"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability"; \
    ValueType: string; ValueName: "ApplicationDescription"; \
    ValueData: "A lightweight media player in classic Winamp style."; \
    Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability"; \
    ValueType: string; ValueName: "ApplicationIcon"; \
    ValueData: "{app}\{#MyAppExeName},0"; Flags: uninsdeletevalue

; --- 2. Capability bei Windows anmelden ---------------------------
Root: HKLM; Subkey: "Software\RegisteredApplications"; \
    ValueType: string; ValueName: "{#MyAppName}"; \
    ValueData: "Software\{#MyAppRegKey}\{#MyAppName}\Capability"; \
    Flags: uninsdeletevalue

; --- 3. Multimedia-ProgID -----------------------------------------
Root: HKLM; Subkey: "Software\Classes\XZN.Media"; Flags: uninsdeletekey
Root: HKLM; Subkey: "Software\Classes\XZN.Media"; \
    ValueType: string; ValueName: ""; \
    ValueData: "Media File (XZN)"
Root: HKLM; Subkey: "Software\Classes\XZN.Media\DefaultIcon"; \
    ValueType: string; ValueName: ""; \
    ValueData: "{app}\{#MyAppExeName},0"
Root: HKLM; Subkey: "Software\Classes\XZN.Media\shell\open"; \
    ValueType: string; ValueName: "FriendlyAppName"; \
    ValueData: "{#MyAppName}"
Root: HKLM; Subkey: "Software\Classes\XZN.Media\shell\open\command"; \
    ValueType: string; ValueName: ""; \
    ValueData: """{app}\{#MyAppExeName}"" ""%1"""

; --- 4. FileAssociations (Audio) ----------------------------------
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".mp3";  ValueData: "XZN.Media"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".ogg";  ValueData: "XZN.Media"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".oga";  ValueData: "XZN.Media"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".opus"; ValueData: "XZN.Media"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".flac"; ValueData: "XZN.Media"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".wav";  ValueData: "XZN.Media"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".m4a";  ValueData: "XZN.Media"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".m4b";  ValueData: "XZN.Media"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".aac";  ValueData: "XZN.Media"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".aiff"; ValueData: "XZN.Media"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".ape";  ValueData: "XZN.Media"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".wma";  ValueData: "XZN.Media"; Flags: uninsdeletevalue

; --- 5. FileAssociations (Video) ----------------------------------
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".mp4";  ValueData: "XZN.Media"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".mkv";  ValueData: "XZN.Media"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".avi";  ValueData: "XZN.Media"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".mov";  ValueData: "XZN.Media"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".webm"; ValueData: "XZN.Media"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".flv";  ValueData: "XZN.Media"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".m4v";  ValueData: "XZN.Media"; Flags: uninsdeletevalue

; --- 6. "Öffnen mit"-Hinweise (OpenWithProgids) -------------------
Root: HKLM; Subkey: "Software\Classes\.mp3\OpenWithProgids";  ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\.ogg\OpenWithProgids";  ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\.oga\OpenWithProgids";  ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\.opus\OpenWithProgids"; ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\.flac\OpenWithProgids"; ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\.wav\OpenWithProgids";  ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\.m4a\OpenWithProgids";  ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\.m4b\OpenWithProgids";  ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\.aac\OpenWithProgids";  ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\.aiff\OpenWithProgids"; ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\.ape\OpenWithProgids";  ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\.wma\OpenWithProgids";  ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\.mp4\OpenWithProgids";  ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\.mkv\OpenWithProgids";  ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\.avi\OpenWithProgids";  ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\.mov\OpenWithProgids";  ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\.webm\OpenWithProgids"; ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\.flv\OpenWithProgids";  ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\.m4v\OpenWithProgids";  ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
