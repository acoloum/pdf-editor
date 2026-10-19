#define AppName "墨頁 PDF"
[Setup]
AppId={{8A941655-2B51-46FD-986C-C4BDA0A3D0F8}
AppName={#AppName}
AppVersion=0.25.2
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
SetupIconFile=..\resources\icons\app.ico
UninstallDisplayIcon={app}\LocalPDFEditor.exe
[Languages]
Name: "chinesetraditional"; MessagesFile: "ChineseTraditional.isl"
[Tasks]
Name: "pdfassoc"; Description: "將墨頁 PDF 加入 PDF 檔案的「開啟方式」清單"; GroupDescription: "檔案關聯："
Name: "desktopicon"; Description: "建立桌面捷徑"; GroupDescription: "其他："; Flags: unchecked
; 只在系統開啟「受控資料夾存取權」時顯示；安裝後跳出系統管理員確認（UAC）。
Name: "allowcfa"; Description: "允許墨頁 PDF 存檔到桌面、文件等受保護資料夾（加入受控資料夾存取權的允許清單，需要系統管理員確認）"; GroupDescription: "Windows 安全性："; Check: ControlledFolderAccessEnabled
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
const
  CfaPolicyKey = 'SOFTWARE\Policies\Microsoft\Windows Defender\Windows Defender Exploit Guard\Controlled Folder Access';
  CfaKey = 'SOFTWARE\Microsoft\Windows Defender\Windows Defender Exploit Guard\Controlled Folder Access';
  { 記錄已加入允許清單的程式路徑，解除安裝時才知道要不要移除。 }
  AppStateKey = 'Software\LocalPDFEditor';
  CfaStateValue = 'ControlledFolderAccessAllowed';
  ERROR_CANCELLED = 1223;

{ 系統是否以封鎖模式（1）啟用受控資料夾存取權；群組原則優先。一般使用者即可讀取。 }
function ControlledFolderAccessEnabled: Boolean;
var
  Mode: Cardinal;
begin
  if RegQueryDWordValue(HKLM, CfaPolicyKey, 'EnableControlledFolderAccess', Mode) then
    Result := Mode = 1
  else if RegQueryDWordValue(HKLM, CfaKey, 'EnableControlledFolderAccess', Mode) then
    Result := Mode = 1
  else
    Result := False;
end;

{ 以系統管理員身分執行 PowerShell 的 Defender 指令；Action 為 Add 或 Remove。
  使用者拒絕 UAC 時 ShellExec 回傳 False、結果碼為 ERROR_CANCELLED。 }
function RunControlledFolderAccessCommand(const Action, ExePath: String; var ResultCode: Integer): Boolean;
var
  Quoted, Params: String;
begin
  Quoted := ExePath;
  StringChangeEx(Quoted, '''', '''''', True);
  Params := '-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -Command "' +
    Action + '-MpPreference -ControlledFolderAccessAllowedApplications ''' + Quoted + ''' -ErrorAction Stop"';
  Result := ShellExec('runas', ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'), Params,
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode) and (ResultCode = 0);
end;

procedure AllowThroughControlledFolderAccess;
var
  ExePath: String;
  ResultCode: Integer;
begin
  ExePath := ExpandConstant('{app}\LocalPDFEditor.exe');
  if RunControlledFolderAccessCommand('Add', ExePath, ResultCode) then
  begin
    Log('已將 ' + ExePath + ' 加入受控資料夾存取權的允許清單。');
    RegWriteStringValue(HKCU, AppStateKey, CfaStateValue, ExePath);
    exit;
  end;
  Log('加入受控資料夾存取權的允許清單失敗，結果碼：' + IntToStr(ResultCode));
  if ResultCode = ERROR_CANCELLED then
    SuppressibleMsgBox('已略過加入受控資料夾存取權的允許清單（未取得系統管理員確認）。' + #13#10#13#10 +
      '墨頁 PDF 已正常安裝。之後若存到桌面、文件等資料夾被拒，可重新執行安裝程式並勾選此項，' +
      '或以系統管理員身分執行 PowerShell 輸入：' + #13#10 +
      'Add-MpPreference -ControlledFolderAccessAllowedApplications "' + ExePath + '"',
      mbInformation, MB_OK, IDOK)
  else
    SuppressibleMsgBox('無法將墨頁 PDF 加入受控資料夾存取權的允許清單（結果碼 ' + IntToStr(ResultCode) + '）。' + #13#10#13#10 +
      '墨頁 PDF 已正常安裝。若電腦由資訊人員統一管理，請洽系統管理員；或以系統管理員身分執行 PowerShell 輸入：' + #13#10 +
      'Add-MpPreference -ControlledFolderAccessAllowedApplications "' + ExePath + '"',
      mbInformation, MB_OK, IDOK);
end;

{ 由墨頁 PDF 自己處理桌面捷徑：安裝程式在暫存資料夾以隨機路徑執行，無法加入允許清單；
  墨頁 PDF 已加入允許清單時可寫入受保護的桌面。Flag 為 --create-desktop-shortcut 或
  --remove-desktop-shortcut，程式只處理桌面上的「{#AppName}.lnk」。 }
function RunDesktopShortcutCommand(const Flag: String): Boolean;
var
  ResultCode: Integer;
begin
  Result := Exec(ExpandConstant('{app}\LocalPDFEditor.exe'), Flag, ExpandConstant('{app}'),
    SW_HIDE, ewWaitUntilTerminated, ResultCode) and (ResultCode = 0);
  if not Result then
    Log('墨頁 PDF 處理桌面捷徑失敗（' + Flag + '），結果碼：' + IntToStr(ResultCode));
end;

{ 桌面捷徑改由程式碼建立：Windows「受控資料夾存取權」會封鎖安裝程式寫入桌面，
  此時改由墨頁 PDF 建立；仍失敗時只提示使用者，不中斷安裝。 }
procedure CurStepChanged(CurStep: TSetupStep);
var
  LinkPath: String;
begin
  if (CurStep = ssPostInstall) and WizardIsTaskSelected('allowcfa') then
    AllowThroughControlledFolderAccess;
  if (CurStep = ssPostInstall) and WizardIsTaskSelected('desktopicon') then
  begin
    LinkPath := ExpandConstant('{userdesktop}\{#AppName}.lnk');
    { 已知會被封鎖時先請墨頁 PDF 建立，避免安裝程式寫入桌面時跳出「未經授權的變更已遭封鎖」。 }
    if ControlledFolderAccessEnabled and RunDesktopShortcutCommand('--create-desktop-shortcut')
        and FileExists(LinkPath) then
    begin
      Log('已由墨頁 PDF 建立桌面捷徑。');
      exit;
    end;
    try
      CreateShellLink(LinkPath, '{#AppName}', ExpandConstant('{app}\LocalPDFEditor.exe'),
        '', ExpandConstant('{app}'), '', 0, SW_SHOWNORMAL);
    except
      Log('安裝程式無法建立桌面捷徑：' + GetExceptionMessage);
    end;
    if FileExists(LinkPath) then
      exit;
    if (not ControlledFolderAccessEnabled) and RunDesktopShortcutCommand('--create-desktop-shortcut')
        and FileExists(LinkPath) then
    begin
      Log('已改由墨頁 PDF 建立桌面捷徑。');
      exit;
    end;
    if not WizardSilent then
      MsgBox('無法在桌面建立捷徑，可能被 Windows 安全性的「受控資料夾存取權」封鎖。' + #13#10#13#10 +
        '墨頁 PDF 已正常安裝，可從開始功能表開啟。重新執行安裝程式並一併勾選「允許墨頁 PDF 存檔到桌面、' +
        '文件等受保護資料夾」，即可自動建立桌面捷徑。',
        mbInformation, MB_OK);
  end;
end;

{ 解除安裝時詢問是否一併從允許清單移除，避免留下指向已刪除程式的允許項目。
  靜默解除安裝不跳出系統管理員確認，保留允許項目。 }
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  ExePath: String;
  ResultCode: Integer;
begin
  { 程式檔案移除前，先由墨頁 PDF 移除桌面捷徑（解除安裝程式同樣會被封鎖寫入桌面）。 }
  if (CurUninstallStep = usUninstall) and FileExists(ExpandConstant('{userdesktop}\{#AppName}.lnk')) then
    RunDesktopShortcutCommand('--remove-desktop-shortcut');
  if CurUninstallStep <> usPostUninstall then
    exit;
  if not RegQueryStringValue(HKCU, AppStateKey, CfaStateValue, ExePath) then
    exit;
  if UninstallSilent then
    exit;
  if MsgBox('安裝時曾將墨頁 PDF 加入受控資料夾存取權的允許清單：' + #13#10 + ExePath + #13#10#13#10 +
      '是否一併移除？（需要系統管理員確認）', mbConfirmation, MB_YESNO) = IDYES then
  begin
    if not RunControlledFolderAccessCommand('Remove', ExePath, ResultCode) then
    begin
      Log('從受控資料夾存取權的允許清單移除失敗，結果碼：' + IntToStr(ResultCode));
      if ResultCode <> ERROR_CANCELLED then
        MsgBox('無法從允許清單移除（結果碼 ' + IntToStr(ResultCode) + '），可在「Windows 安全性」中手動移除。',
          mbInformation, MB_OK);
      exit;
    end;
  end;
  RegDeleteValue(HKCU, AppStateKey, CfaStateValue);
end;
[Run]
Filename: "{app}\LocalPDFEditor.exe"; Description: "啟動墨頁 PDF"; Flags: nowait postinstall skipifsilent
