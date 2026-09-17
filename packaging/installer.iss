#define AppName "墨頁 PDF"
[Setup]
AppId={{8A941655-2B51-46FD-986C-C4BDA0A3D0F8}
AppName={#AppName}
AppVersion=0.17.3
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
[Registry]
; 只加入「開啟方式」候選，不強制搶走使用者目前的預設 PDF 程式。
Root: HKCU; Subkey: "Software\Classes\LocalPDFEditor.Document"; ValueType: string; ValueName: ""; ValueData: "PDF 文件（墨頁 PDF）"; Flags: uninsdeletekey; Tasks: pdfassoc
Root: HKCU; Subkey: "Software\Classes\LocalPDFEditor.Document\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\LocalPDFEditor.exe,0"; Tasks: pdfassoc
Root: HKCU; Subkey: "Software\Classes\LocalPDFEditor.Document\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\LocalPDFEditor.exe"" ""%1"""; Tasks: pdfassoc
Root: HKCU; Subkey: "Software\Classes\.pdf\OpenWithProgids"; ValueType: string; ValueName: "LocalPDFEditor.Document"; ValueData: ""; Flags: uninsdeletevalue; Tasks: pdfassoc
Root: HKCU; Subkey: "Software\Classes\Applications\LocalPDFEditor.exe\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\LocalPDFEditor.exe"" ""%1"""; Flags: uninsdeletekey; Tasks: pdfassoc
Root: HKCU; Subkey: "Software\Classes\Applications\LocalPDFEditor.exe\SupportedTypes"; ValueType: string; ValueName: ".pdf"; ValueData: ""; Tasks: pdfassoc
[UninstallDelete]
Type: files; Name: "{userdesktop}\{#AppName}.lnk"
[Code]
{ 桌面捷徑改由程式碼建立：Windows「受控資料夾存取」會封鎖寫入桌面，
  失敗時只提示使用者，不中斷安裝。 }
procedure CurStepChanged(CurStep: TSetupStep);
var
  LinkPath: String;
begin
  if (CurStep = ssPostInstall) and WizardIsTaskSelected('desktopicon') then
  begin
    LinkPath := ExpandConstant('{userdesktop}\{#AppName}.lnk');
    try
      CreateShellLink(LinkPath, '{#AppName}', ExpandConstant('{app}\LocalPDFEditor.exe'),
        '', ExpandConstant('{app}'), '', 0, SW_SHOWNORMAL);
    except
      Log('無法建立桌面捷徑：' + GetExceptionMessage);
      if not WizardSilent then
        MsgBox('無法在桌面建立捷徑，可能被 Windows 安全性的「受控資料夾存取」封鎖。' + #13#10#13#10 +
          '墨頁 PDF 已正常安裝，可從開始功能表開啟；如需桌面捷徑，可在開始功能表的「墨頁 PDF」按右鍵手動建立。',
          mbInformation, MB_OK);
    end;
  end;
end;
[Run]
Filename: "{app}\LocalPDFEditor.exe"; Description: "啟動墨頁 PDF"; Flags: nowait postinstall skipifsilent
