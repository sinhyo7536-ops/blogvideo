; Inno Setup 6 스크립트 — 실행: iscc packaging\installer.iss (저장소 루트에서, PyInstaller 빌드 후)
#define AppName "블로그 영상 변환기"
#define OldExe "BlogVideo.exe"
; 2026-11-24: 서명 없는 BlogVideo.exe(PyInstaller)가 백신에 걸려서, 파이썬 재단이 서명한 공식 파이썬으로 켠다
#define AppRun "runtime\pythonw.exe"
#define AppArgs "-m app.launcher"
#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif

[Setup]
AppId={{FE061E3D-A228-4C78-A929-231ED25A15D0}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=BlogVideo
VersionInfoCompany=BlogVideo
VersionInfoProductName=BlogVideo
DefaultDirName={autopf}\BlogVideo
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\build\installer
OutputBaseFilename=BlogVideo-Setup
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\icon.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=force

[Languages]
Name: "korean"; MessagesFile: "Korean.isl"

[Tasks]
Name: "desktopicon"; Description: "바탕화면에 바로가기 만들기"; Flags: checkedonce

[InstallDelete]
; 예전 버전(PyInstaller)의 실행 파일과 지난 버전의 코드를 지우고 새로 넣는다
Type: files; Name: "{app}\{#OldExe}"
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\app"
Type: filesandordirs; Name: "{app}\runtime"

[Files]
Source: "..\build\app\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppRun}"; Parameters: "{#AppArgs}"; WorkingDir: "{app}"; IconFilename: "{app}\icon.ico"
Name: "{group}\{#AppName} 제거"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppRun}"; Parameters: "{#AppArgs}"; WorkingDir: "{app}"; IconFilename: "{app}\icon.ico"; Tasks: desktopicon

[Run]
; 이 프로그램은 PC 안(127.0.0.1)에서만 열리므로 방화벽을 열지 않는다. 예전 버전이 만든 규칙은 지운다.
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""BlogVideo"""; Flags: runhidden waituntilterminated
Filename: "{app}\{#AppRun}"; Parameters: "{#AppArgs}"; WorkingDir: "{app}"; Description: "지금 실행하기"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; 실행 중인 프로그램은 Restart Manager(CloseApplications=force)가 닫는다. pythonw.exe를 이름으로 끄면 다른 파이썬까지 꺼진다.
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""BlogVideo"""; Flags: runhidden; RunOnceId: "DelFirewall"

[UninstallDelete]
Type: filesandordirs; Name: "{app}\app"
Type: filesandordirs; Name: "{app}\runtime"

[Code]
const
  TrailerSize = 1024;

{ 지인용 설치 파일: 이 설치 파일 끝 1024바이트에 덧붙인 키를 꺼내
  %APPDATA%\BlogVideo\import.key 로 남긴다. 프로그램이 처음 켜질 때
  윈도우 계정 암호화로 옮기고 이 파일을 지운다. }
procedure ExtractBundledKey();
var
  Stream: TFileStream;
  Buf, Data, Dir: AnsiString;
  Len: Integer;
begin
  try
    Stream := TFileStream.Create(ExpandConstant('{srcexe}'), fmOpenRead or fmShareDenyNone);
    try
      if Stream.Size <= TrailerSize then Exit;
      Stream.Seek(-TrailerSize, soFromEnd);
      SetLength(Buf, TrailerSize);
      Stream.ReadBuffer(Buf, TrailerSize);
    finally
      Stream.Free;
    end;
    if Copy(Buf, 1, 6) <> 'BVKEY1' then Exit;
    Len := StrToIntDef(Copy(Buf, 7, 4), 0);
    if (Len <= 0) or (Len > TrailerSize - 10) then Exit;
    Data := Copy(Buf, 11, Len);
    Dir := ExpandConstant('{userappdata}\BlogVideo');
    ForceDirectories(Dir);
    SaveStringToFile(Dir + '\import.key', Data, False);
  except
    Log('bundled key extraction failed: ' + GetExceptionMessage);
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    ExtractBundledKey();
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
begin
  { 예전 버전(BlogVideo.exe)이 켜져 있으면 먼저 종료. 새 버전(pythonw.exe)은 Restart Manager가 닫는다 }
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM {#OldExe}', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Result := '';
end;
