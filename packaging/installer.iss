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
ChangesAssociations=yes
MinVersion=10.0.22000
[Languages]
Name: "chinesetraditional"; MessagesFile: "ChineseTraditional.isl"
[Tasks]
Name: "pdfassoc"; Description: "將墨頁 PDF 加入 PDF 檔案的「開啟方式」清單"; GroupDescription: "檔案關聯："
Name: "desktopicon"; Description: "建立桌面捷徑"; GroupDescription: "其他："; Flags: unchecked
[Files]
Source: "..\dist\LocalPDFEditor\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\LocalPDFEditor.exe"
Name: "{group}\解除安裝"; Filename: "{uninstallexe}"
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\LocalPDFEditor.exe"; Tasks: desktopicon
[Registry]
; 只加入「開啟方式」候選，不強制搶走使用者目前的預設 PDF 程式。
Root: HKCU; Subkey: "Software\Classes\LocalPDFEditor.Document"; ValueType: string; ValueName: ""; ValueData: "PDF 文件（墨頁 PDF）"; Flags: uninsdeletekey; Tasks: pdfassoc
Root: HKCU; Subkey: "Software\Classes\LocalPDFEditor.Document\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\LocalPDFEditor.exe,0"; Tasks: pdfassoc
Root: HKCU; Subkey: "Software\Classes\LocalPDFEditor.Document\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\LocalPDFEditor.exe"" ""%1"""; Tasks: pdfassoc
Root: HKCU; Subkey: "Software\Classes\.pdf\OpenWithProgids"; ValueType: string; ValueName: "LocalPDFEditor.Document"; ValueData: ""; Flags: uninsdeletevalue; Tasks: pdfassoc
Root: HKCU; Subkey: "Software\Classes\Applications\LocalPDFEditor.exe\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\LocalPDFEditor.exe"" ""%1"""; Flags: uninsdeletekey; Tasks: pdfassoc
Root: HKCU; Subkey: "Software\Classes\Applications\LocalPDFEditor.exe\SupportedTypes"; ValueType: string; ValueName: ".pdf"; ValueData: ""; Tasks: pdfassoc
[Run]
Filename: "{app}\LocalPDFEditor.exe"; Description: "啟動墨頁 PDF"; Flags: nowait postinstall skipifsilent
