; ==================================================================
;  XZN — Inno Setup script (Branding: XZN)
;  Built by build_windows.bat; by hand: iscc installer\xzn.iss
; ==================================================================

#define MyAppName       "XZN"
; Version: passed in by build_windows.bat (/DMyAppVersion=...),
; this value is only the fallback for building by hand.
#ifndef MyAppVersion
  #define MyAppVersion  "1.1.3"
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
; XZN logo in the setup pages (made by installer\wizard_images.py,
; one image per display scaling). Without them the default images are used.
#if FileExists(AddBackslash(SourcePath) + "..\build\wizard\large_100.bmp")
WizardImageFile=..\build\wizard\large_100.bmp,..\build\wizard\large_125.bmp,..\build\wizard\large_150.bmp,..\build\wizard\large_175.bmp,..\build\wizard\large_200.bmp,..\build\wizard\large_225.bmp,..\build\wizard\large_250.bmp
WizardSmallImageFile=..\build\wizard\small_100.bmp,..\build\wizard\small_125.bmp,..\build\wizard\small_150.bmp,..\build\wizard\small_175.bmp,..\build\wizard\small_200.bmp,..\build\wizard\small_225.bmp,..\build\wizard\small_250.bmp
#endif

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
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".mp3"; ValueData: "XZN.Media"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".mp2"; ValueData: "XZN.Media"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".ogg"; ValueData: "XZN.Media"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".oga"; ValueData: "XZN.Media"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".opus"; ValueData: "XZN.Media"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".spx"; ValueData: "XZN.Media"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".flac"; ValueData: "XZN.Media"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".wav"; ValueData: "XZN.Media"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".w64"; ValueData: "XZN.Media"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".wma"; ValueData: "XZN.Media"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".aac"; ValueData: "XZN.Media"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".m4a"; ValueData: "XZN.Media"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".m4b"; ValueData: "XZN.Media"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".aiff"; ValueData: "XZN.Media"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".aif"; ValueData: "XZN.Media"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".aifc"; ValueData: "XZN.Media"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".caf"; ValueData: "XZN.Media"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".ape"; ValueData: "XZN.Media"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".wv"; ValueData: "XZN.Media"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".tta"; ValueData: "XZN.Media"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".mpc"; ValueData: "XZN.Media"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".dsf"; ValueData: "XZN.Media"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".dff"; ValueData: "XZN.Media"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".ac3"; ValueData: "XZN.Media"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".eac3"; ValueData: "XZN.Media"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".dts"; ValueData: "XZN.Media"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".mka"; ValueData: "XZN.Media"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".weba"; ValueData: "XZN.Media"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".amr"; ValueData: "XZN.Media"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".au"; ValueData: "XZN.Media"; Flags: uninsdeletevalue

; --- 5. FileAssociations (Video) ----------------------------------
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".mp4"; ValueData: "XZN.Media"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".mkv"; ValueData: "XZN.Media"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".avi"; ValueData: "XZN.Media"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".mov"; ValueData: "XZN.Media"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".wmv"; ValueData: "XZN.Media"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".flv"; ValueData: "XZN.Media"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".webm"; ValueData: "XZN.Media"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".ogv"; ValueData: "XZN.Media"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".mpg"; ValueData: "XZN.Media"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".mpeg"; ValueData: "XZN.Media"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".ts"; ValueData: "XZN.Media"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".m2ts"; ValueData: "XZN.Media"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".mts"; ValueData: "XZN.Media"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".m4v"; ValueData: "XZN.Media"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".asf"; ValueData: "XZN.Media"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".3gp"; ValueData: "XZN.Media"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".3g2"; ValueData: "XZN.Media"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".vob"; ValueData: "XZN.Media"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".divx"; ValueData: "XZN.Media"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".ogm"; ValueData: "XZN.Media"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".rm"; ValueData: "XZN.Media"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".rmvb"; ValueData: "XZN.Media"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".f4v"; ValueData: "XZN.Media"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".mxf"; ValueData: "XZN.Media"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppRegKey}\{#MyAppName}\Capability\FileAssociations"; ValueType: string; ValueName: ".dv"; ValueData: "XZN.Media"; Flags: uninsdeletevalue

; --- 6. "Öffnen mit"-Hinweise (OpenWithProgids) -------------------
Root: HKLM; Subkey: "Software\Classes\.mp3\OpenWithProgids"; ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\.mp2\OpenWithProgids"; ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\.ogg\OpenWithProgids"; ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\.oga\OpenWithProgids"; ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\.opus\OpenWithProgids"; ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\.spx\OpenWithProgids"; ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\.flac\OpenWithProgids"; ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\.wav\OpenWithProgids"; ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\.w64\OpenWithProgids"; ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\.wma\OpenWithProgids"; ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\.aac\OpenWithProgids"; ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\.m4a\OpenWithProgids"; ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\.m4b\OpenWithProgids"; ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\.aiff\OpenWithProgids"; ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\.aif\OpenWithProgids"; ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\.aifc\OpenWithProgids"; ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\.caf\OpenWithProgids"; ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\.ape\OpenWithProgids"; ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\.wv\OpenWithProgids"; ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\.tta\OpenWithProgids"; ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\.mpc\OpenWithProgids"; ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\.dsf\OpenWithProgids"; ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\.dff\OpenWithProgids"; ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\.ac3\OpenWithProgids"; ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\.eac3\OpenWithProgids"; ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\.dts\OpenWithProgids"; ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\.mka\OpenWithProgids"; ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\.weba\OpenWithProgids"; ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\.amr\OpenWithProgids"; ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\.au\OpenWithProgids"; ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\.mp4\OpenWithProgids"; ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\.mkv\OpenWithProgids"; ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\.avi\OpenWithProgids"; ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\.mov\OpenWithProgids"; ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\.wmv\OpenWithProgids"; ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\.flv\OpenWithProgids"; ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\.webm\OpenWithProgids"; ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\.ogv\OpenWithProgids"; ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\.mpg\OpenWithProgids"; ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\.mpeg\OpenWithProgids"; ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\.ts\OpenWithProgids"; ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\.m2ts\OpenWithProgids"; ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\.mts\OpenWithProgids"; ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\.m4v\OpenWithProgids"; ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\.asf\OpenWithProgids"; ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\.3gp\OpenWithProgids"; ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\.3g2\OpenWithProgids"; ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\.vob\OpenWithProgids"; ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\.divx\OpenWithProgids"; ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\.ogm\OpenWithProgids"; ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\.rm\OpenWithProgids"; ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\.rmvb\OpenWithProgids"; ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\.f4v\OpenWithProgids"; ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\.mxf\OpenWithProgids"; ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\.dv\OpenWithProgids"; ValueType: string; ValueName: "XZN.Media"; ValueData: ""; Flags: uninsdeletevalue
