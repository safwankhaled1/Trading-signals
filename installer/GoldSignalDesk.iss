#define AppName "Gold Signal Desk"
#define AppVersion "0.1.1"
[Setup]
AppId={{13A274C6-77BD-4271-90EC-4C4DD49BF4B8}
AppName={#AppName}
AppVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\GoldSignalDesk
DefaultGroupName={#AppName}
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=output
OutputBaseFilename=GoldSignalDesk-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\GoldSignalDesk.exe
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "arabic"; MessagesFile: "compiler:Languages\Arabic.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "إنشاء اختصار على سطح المكتب"; Flags: unchecked
Name: "startup"; Description: "تشغيل محرك MT5 عند تسجيل الدخول إلى Windows"; Flags: unchecked

[Files]
Source: "..\dist\GoldSignalDesk-{#AppVersion}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\GoldSignalDesk.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\GoldSignalDesk.exe"; Tasks: desktopicon
Name: "{userstartup}\Gold Signal Desk Engine"; Filename: "{app}\GoldSignalDesk.exe"; Parameters: "--engine --mode live"; Tasks: startup

[Run]
Filename: "{app}\GoldSignalDesk.exe"; Description: "فتح تطبيق إشارات الذهب"; Flags: nowait postinstall skipifsilent
