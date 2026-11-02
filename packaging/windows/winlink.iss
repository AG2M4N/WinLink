; WinLink's Windows installer (Inno Setup 6). Built by .github/workflows/release.yml:
;   iscc /DAppVersion=2026.11.4 packaging\windows\winlink.iss
; Packs dist\WinLink (PyInstaller's folder, with the Deskflow engine in dist\WinLink\deskflow).
; Installs for the current user only, so it needs no administrator.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define Root "..\.."

[Setup]
AppId={{8C1F2B7E-4F0B-4C77-9C1E-2B3D5A7E9F11}
AppName=WinLink
AppVersion={#AppVersion}
AppVerName=WinLink {#AppVersion}
AppPublisherURL=https://github.com/AG2M4N/winlink
AppSupportURL=https://github.com/AG2M4N/winlink/issues
DefaultDirName={localappdata}\Programs\WinLink
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#Root}\release
OutputBaseFilename=WinLink-Setup
SetupIconFile={#Root}\assets\WinLink.ico
UninstallDisplayIcon={app}\WinLink.exe
UninstallDisplayName=WinLink
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "Put WinLink on the desktop"; Flags: unchecked

[InstallDelete]
; an update replaces the whole program, so files from older versions don't linger
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\deskflow"

[Files]
Source: "{#Root}\dist\WinLink\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\WinLink"; Filename: "{app}\WinLink.exe"
Name: "{autodesktop}\WinLink"; Filename: "{app}\WinLink.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\WinLink.exe"; Description: "Open WinLink"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/f /im WinLink.exe"; Flags: runhidden; RunOnceId: "StopWinLink"
; "Open at login" (Settings) and the Send to menu point at this copy: take them away with it
Filename: "{sys}\reg.exe"; Parameters: "delete HKCU\Software\Microsoft\Windows\CurrentVersion\Run /v WinLink /f"; Flags: runhidden; RunOnceId: "NoAutostart"

[UninstallDelete]
Type: files; Name: "{userappdata}\Microsoft\Windows\SendTo\WinLink - *.lnk"
