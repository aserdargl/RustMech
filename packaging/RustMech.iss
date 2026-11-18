; RustMech Windows kurulum betigi (Inno Setup 6)
; Derleme: ISCC.exe /DAppVersion=0.2.0 packaging\RustMech.iss
; Girdi: Build-Exe.ps1 ciktisi dist\RustMech

#ifndef AppVersion
  #define AppVersion "0.2.0"
#endif
#ifndef OutputDir
  #define OutputDir "..\dist-setup"
#endif
#ifndef PayloadDir
  #define PayloadDir "..\dist\RustMech"
#endif
#ifndef SetupName
  #define SetupName "RustMech-" + AppVersion + "-Setup"
#endif
#ifndef NumericVersion
  #define NumericVersion AppVersion
#endif

[Setup]
AppId={{6F3B8E2A-4C1D-4E7B-9A52-7D2C1B9E5A31}
AppName=RustMech
AppVersion={#AppVersion}
AppVerName=RustMech {#AppVersion}
AppPublisher=Ahmet Serdar Guldibi
AppPublisherURL=https://github.com/aserdargl/RustMech
AppUpdatesURL=https://github.com/aserdargl/RustMech-Releases/releases
DefaultDirName={autopf}\RustMech
DefaultGroupName=RustMech
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename={#SetupName}
VersionInfoVersion={#NumericVersion}
VersionInfoProductTextVersion={#AppVersion}
SetupIconFile=..\assets\ui\rustmech.ico
UninstallDisplayIcon={app}\RustMech.exe
Compression=lzma2/max
SolidCompression=yes
LZMAUseSeparateProcess=yes
WizardStyle=modern

[Languages]
Name: "turkish"; MessagesFile: "compiler:Languages\Turkish.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Excludes: "settings.json,RustMech.log,icuuc-poppler-unused.dll"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\RustMech"; Filename: "{app}\RustMech.exe"
Name: "{autodesktop}\RustMech"; Filename: "{app}\RustMech.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\RustMech.exe"; Description: "{cm:LaunchProgram,RustMech}"; Flags: nowait postinstall skipifsilent
