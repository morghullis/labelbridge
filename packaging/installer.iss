; Instalador de Windows (Inno Setup 6): https://jrsoftware.org/isinfo.php
; Compilar: ISCC.exe /DAppVersion=1.0.0 packaging\installer.iss   (después de PyInstaller)

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#define AppName "LabelBridge"
#define AppExe "LabelBridge.exe"
#define AppPublisher "LabelBridge"

[Setup]
AppId={{8B5C6F1E-3C2A-4E7B-9C1D-2F4A6B8C0D1E}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=..\dist\installer
OutputBaseFilename=LabelBridge-Setup-{#AppVersion}
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Instalación por usuario sin pedir administrador (se puede elegir "para todos").
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
; La app crea el mutex LabelBridgeRunningMutex mientras está abierta. No se usa AppMutex: en modo silencioso
; (actualización automática) el instalador se cancelaría si la app aún no terminó de cerrarse. Ver [Code].
CloseApplications=yes
ShowLanguageDialog=no

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "firewall"; Description: "Permitir la API local desde otros equipos de la red privada (regla de firewall)"; GroupDescription: "Red:"; Flags: unchecked; Check: IsAdminInstallMode

[Files]
Source: "..\dist\LabelBridge\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; Parameters: "--show"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Parameters: "--show"; Tasks: desktopicon

[Run]
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""LabelBridge API"" dir=in action=allow program=""{app}\{#AppExe}"" enable=yes profile=private"; Flags: runhidden; Tasks: firewall
Filename: "{app}\{#AppExe}"; Parameters: "--show"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
; Actualización automática (modo silencioso): la app se vuelve a abrir sola y muestra las novedades.
Filename: "{app}\{#AppExe}"; Parameters: "--updated"; Flags: nowait runasoriginaluser; Check: WizardSilent

[UninstallRun]
; El inicio automático lo gestiona la app (Ajustes); al desinstalar se elimina.
Filename: "{sys}\reg.exe"; Parameters: "delete ""HKCU\Software\Microsoft\Windows\CurrentVersion\Run"" /v LabelBridge /f"; Flags: runhidden; RunOnceId: "RemoveAutostart"
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""LabelBridge API"""; Flags: runhidden; RunOnceId: "RemoveFirewallRule"; Check: IsAdminInstallMode

[Code]
const
  AppMutexName = 'LabelBridgeRunningMutex';
  SilentWaitMs = 90000;

{ Cierra a la fuerza una LabelBridge que no terminó de cerrarse (solo en actualizaciones automáticas). }
procedure ForceCloseApp();
var
  ResultCode: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/IM LabelBridge.exe /F', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Sleep(1500);
end;

{ Espera a que la app se cierre. Actualización automática (silenciosa): espera hasta 90 s y, si sigue abierta,
  la cierra. Instalación normal: pide al usuario que la cierre. }
function WaitForAppClosed(Silent: Boolean): Boolean;
var
  Waited: Integer;
begin
  Result := True;
  if Silent then
  begin
    Waited := 0;
    while CheckForMutexes(AppMutexName) and (Waited < SilentWaitMs) do
    begin
      Sleep(500);
      Waited := Waited + 500;
    end;
    if CheckForMutexes(AppMutexName) then
    begin
      Log('LabelBridge sigue abierta después de esperar: se cierra para actualizar.');
      ForceCloseApp();
    end;
    exit;
  end;
  while CheckForMutexes(AppMutexName) do
    if MsgBox('LabelBridge está abierta. Ciérrala (clic derecho en el icono junto al reloj → Salir) y pulsa Aceptar.',
      mbError, MB_OKCANCEL) <> IDOK then
    begin
      Result := False;
      exit;
    end;
end;

function InitializeSetup(): Boolean;
begin
  Result := WaitForAppClosed(WizardSilent);
end;

function InitializeUninstall(): Boolean;
begin
  Result := WaitForAppClosed(UninstallSilent);
end;
