#define AppName "墨頁 PDF"
[Setup]
AppId={{8A941655-2B51-46FD-986C-C4BDA0A3D0F8}
AppName={#AppName}
AppVersion=0.16.0
DefaultDirName={localappdata}\Programs\LocalPDFEditor
DefaultGroupName={#AppName}
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist\installer
OutputBaseFilename=LocalPDFEditor-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName={#AppName}
MinVersion=10.0.22000
[Languages]
Name: "chinesetraditional"; MessagesFile: "ChineseTraditional.isl"
[Files]
Source: "..\dist\LocalPDFEditor\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\LocalPDFEditor.exe"
Name: "{group}\解除安裝"; Filename: "{uninstallexe}"
[Run]
Filename: "{app}\LocalPDFEditor.exe"; Description: "啟動墨頁 PDF"; Flags: nowait postinstall skipifsilent
