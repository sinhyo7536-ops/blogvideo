; Inno Setup 6 스크립트 — 실행: iscc packaging\installer.iss (저장소 루트에서, PyInstaller 빌드 후)
#define AppName "블로그 영상 변환기"
#define AppExe "BlogVideo.exe"
#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif

[Setup]
AppId={{FE061E3D-A228-4C78-A929-231ED25A15D0}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=BlogVideo
DefaultDirName={autopf}\BlogVideo
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\build\installer
OutputBaseFilename=BlogVideo-Setup
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=force

[Languages]
Name: "korean"; MessagesFile: "Korean.isl"

[Tasks]
Name: "desktopicon"; Description: "바탕화면에 바로가기 만들기"; Flags: checkedonce

[Files]
Source: "..\build\dist\BlogVideo\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\{#AppName} 제거"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
; 같은 와이파이의 휴대폰이 접속할 수 있도록 개인(사설) 네트워크에서만 8765 포트를 연다
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""BlogVideo"""; Flags: runhidden waituntilterminated
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""BlogVideo"" dir=in action=allow protocol=TCP localport=8765 profile=private"; Flags: runhidden waituntilterminated
Filename: "{app}\{#AppExe}"; Description: "지금 실행하기"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM {#AppExe}"; Flags: runhidden; RunOnceId: "KillApp"
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""BlogVideo"""; Flags: runhidden; RunOnceId: "DelFirewall"

[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
begin
  { 업데이트 설치 시 백그라운드에서 실행 중인 프로그램을 먼저 종료 }
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM {#AppExe}', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Result := '';
end;
