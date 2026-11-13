; Installer for Hey Buddy. Build the app first (HeyBuddy.spec), then:
;     "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" HeyBuddy.iss
; and send installer\HeyBuddy-Setup-<version>.exe.
;
; Installs just for the current user (no admin prompt) into %LOCALAPPDATA%\Programs\Hey Buddy.
; Tasks and settings live in %APPDATA%\Hey Buddy and are kept on uninstall and update.

#define AppVersion "1.0.0"

[Setup]
AppId={{6F2B7C1E-4E0B-4B7B-9C0D-3A5D1E7F9B21}
AppName=Hey Buddy
AppVersion={#AppVersion}
AppPublisher=thatvibecoderr
DefaultDirName={autopf}\Hey Buddy
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=installer
OutputBaseFilename=HeyBuddy-Setup-{#AppVersion}
SetupIconFile=ui\app.ico
UninstallDisplayIcon={app}\HeyBuddy.exe
UninstallDisplayName=Hey Buddy
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; the app holds this while running: setup and uninstall ask to close it first
AppMutex=HeyBuddy.SingleInstance

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked
; "Start with Windows" is a switch in the app's Settings, so there's no startup option here

[Files]
Source: "dist\HeyBuddy\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{autoprograms}\Hey Buddy"; Filename: "{app}\HeyBuddy.exe"
Name: "{autodesktop}\Hey Buddy"; Filename: "{app}\HeyBuddy.exe"; Tasks: desktopicon

[Registry]
; the app's Start with Windows switch writes this value; don't leave it pointing at nothing
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueName: "Hey Buddy"; \
    ValueType: none; Flags: uninsdeletevalue

[Run]
Filename: "{app}\HeyBuddy.exe"; Description: "Open Hey Buddy now"; Flags: nowait postinstall skipifsilent
