; Instalador de Windows (Inno Setup 6): https://jrsoftware.org/isinfo.php
; Compilar: ISCC.exe /DAppVersion=1.0.0 packaging\installer.iss
;   (después de PyInstaller y de python packaging\make_installer_images.py, que crea las imágenes de la marca)
;
; En inglés o en español: se elige al empezar y la app queda en ese idioma (se puede cambiar en Ajustes).
; La primera vez pregunta la tienda de Shopify (dirección y Client ID) para adelantar: la app los recoge al abrirse
; y pide la clave secreta, que nunca pasa por el instalador.

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#define AppName "LabelBridge"
#define AppExe "LabelBridge.exe"
#define AppPublisher "LabelBridge"
#define Images "..\build\installer"

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
; La marca: la máscara dorada en el lateral y el medallón arriba (Inno elige el tamaño según la escala de Windows).
WizardImageFile={#Images}\wizard-100.bmp,{#Images}\wizard-125.bmp,{#Images}\wizard-150.bmp,{#Images}\wizard-175.bmp,{#Images}\wizard-200.bmp,{#Images}\wizard-225.bmp,{#Images}\wizard-250.bmp
WizardSmallImageFile={#Images}\small-100.bmp,{#Images}\small-125.bmp,{#Images}\small-150.bmp,{#Images}\small-175.bmp,{#Images}\small-200.bmp,{#Images}\small-225.bmp,{#Images}\small-250.bmp
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Siempre por usuario, sin preguntar ni pedir administrador. Hasta la 1.9.0 se podía elegir «para todos»: esos
; equipos se siguen actualizando en Archivos de programa (ver AdminInstallExists en [Code]); /ALLUSERS lo permite.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=commandline
UsedUserAreasWarning=no
; La app crea el mutex LabelBridgeRunningMutex mientras está abierta. No se usa AppMutex: en modo silencioso
; (actualización automática) el instalador se cancelaría si la app aún no terminó de cerrarse. Ver [Code].
CloseApplications=yes
; English / Español al empezar. Las actualizaciones automáticas repiten el idioma de la primera instalación.
ShowLanguageDialog=yes
UsePreviousLanguage=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[CustomMessages]
english.NetworkGroup=Network:
spanish.NetworkGroup=Red:
english.FirewallTask=Allow the local API from other computers on the private network (firewall rule)
spanish.FirewallTask=Permitir la API local desde otros equipos de la red privada (regla de firewall)
english.AdminInstall=LabelBridge is installed for all users of this computer. To update it, Windows will ask for administrator permission.%n%nTo stop it asking on future updates, uninstall LabelBridge once and install it again: it will be installed just for you and it keeps your settings.
spanish.AdminInstall=LabelBridge está instalada para todos los usuarios de este equipo. Para actualizarla, Windows pedirá permiso de administrador.%n%nPara que no lo pida en las próximas actualizaciones, desinstálala una vez y vuelve a instalarla: quedará solo para tu usuario y conserva tu configuración.
english.CloseApp=LabelBridge is open. Close it (right-click its icon next to the clock → Exit) and press OK.
spanish.CloseApp=LabelBridge está abierta. Ciérrala (clic derecho en el icono junto al reloj → Salir) y pulsa Aceptar.
english.ShopTitle=Your Shopify store
spanish.ShopTitle=Tu tienda de Shopify
english.ShopSubtitle=LabelBridge prints the shipping labels of this store's orders.
spanish.ShopSubtitle=LabelBridge imprime las guías de envío de los pedidos de esta tienda.
english.ShopInfo=Type your store now to save time; you can also leave it empty and do it later in the app.%n%nThe Client secret isn't asked here: when LabelBridge opens it asks for it, keeps it in the Windows secure store and tests the connection.
spanish.ShopInfo=Escribe ya tu tienda para adelantar; también puedes dejarla vacía y hacerlo después en la app.%n%nLa clave secreta (Client secret) no se pide aquí: al abrirse, LabelBridge la pide, la guarda en el almacén seguro de Windows y prueba la conexión.
english.ShopDomain=Store address (for example my-store.myshopify.com):
spanish.ShopDomain=Dirección de la tienda (por ejemplo mi-tienda.myshopify.com):
english.ShopClientId=Client ID of your app in the Shopify Dev Dashboard (optional):
spanish.ShopClientId=Client ID de tu app en el Dev Dashboard de Shopify (opcional):
english.ShopInvalid=That isn't a Shopify store address. Example: my-store.myshopify.com
spanish.ShopInvalid=Esa no es la dirección de una tienda de Shopify. Ejemplo: mi-tienda.myshopify.com
english.FinishConnect=Open LabelBridge and finish connecting the store
spanish.FinishConnect=Abrir LabelBridge y terminar de conectar la tienda

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "firewall"; Description: "{cm:FirewallTask}"; GroupDescription: "{cm:NetworkGroup}"; Flags: unchecked; Check: IsAdminInstallMode

[Files]
Source: "..\dist\LabelBridge\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; Parameters: "--show"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Parameters: "--show"; Tasks: desktopicon

[Registry]
; El idioma elegido: la app lo usa mientras no se cambie en Ajustes.
Root: HKCU; Subkey: "Software\LabelBridge"; ValueType: string; ValueName: "Language"; ValueData: "en"; Languages: english; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\LabelBridge"; ValueType: string; ValueName: "Language"; ValueData: "es"; Languages: spanish; Flags: uninsdeletekey

[Run]
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""LabelBridge API"" dir=in action=allow program=""{app}\{#AppExe}"" enable=yes profile=private"; Flags: runhidden; Tasks: firewall
Filename: "{app}\{#AppExe}"; Parameters: "--show --connect-store"; Description: "{cm:FinishConnect}"; Flags: nowait postinstall skipifsilent; Check: ShopGiven
Filename: "{app}\{#AppExe}"; Parameters: "--show"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent; Check: not ShopGiven
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
  ShopSuffix = '.myshopify.com';

var
  ShopPage: TInputQueryWizardPage;

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
    if MsgBox(CustomMessage('CloseApp'), mbError, MB_OKCANCEL) <> IDOK then
    begin
      Result := False;
      exit;
    end;
end;

{ --- Instalada «para todos los usuarios» (hasta la 1.9.0 se podía elegir) ---------------------------- }

function AdminInstallExists(): Boolean;
var
  Key: String;
begin
  Key := ExpandConstant('Software\Microsoft\Windows\CurrentVersion\Uninstall\{#emit SetupSetting("AppId")}_is1');
  Result := RegKeyExists(HKLM64, Key) or RegKeyExists(HKLM32, Key);
end;

{ Los mismos parámetros, en modo «para todos» y con el idioma ya elegido. El registro (/LOG) va a otro archivo:
  este instalador todavía tiene abierto el suyo. }
function AdminParams(): String;
var
  I: Integer;
  Param: String;
begin
  Result := '/ALLUSERS /LANG=' + ActiveLanguage;
  for I := 1 to ParamCount do
  begin
    Param := Uppercase(ParamStr(I));
    if (Pos('/SL5=', Param) = 1) or (Pos('/LANG=', Param) = 1) or (Param = '/ALLUSERS') or (Param = '/CURRENTUSER') then
      continue;
    if Pos('/LOG=', Param) = 1 then
      Result := Result + ' ' + AddQuotes(ChangeFileExt(ParamStr(I), '') + '-todos.log')
    else
      Result := Result + ' ' + AddQuotes(ParamStr(I));
  end;
end;

{ Se actualiza esa misma copia (Windows pide permiso de administrador) en vez de crear otra solo para este usuario,
  también cuando lo lanza la actualización automática de una versión anterior. }
function UpdateAdminInstall(): Boolean;
var
  Params: String;
  ResultCode: Integer;
begin
  Params := AdminParams();
  Log('LabelBridge está instalada para todos los usuarios: se actualiza esa copia (' + Params + ').');
  if not WizardSilent then
    MsgBox(CustomMessage('AdminInstall'), mbInformation, MB_OK);
  { Exec espera de verdad al otro instalador (y este, a su copia con permiso de administrador): quien lanzó este
    instalador, como la actualización automática, sabe cuándo terminó. }
  if Exec(ExpandConstant('{srcexe}'), Params, '', SW_SHOWNORMAL, ewWaitUntilTerminated, ResultCode) then
    Log('El instalador para todos los usuarios terminó con código ' + IntToStr(ResultCode))
  else
    Log('No se pudo abrir el instalador para todos los usuarios: ' + SysErrorMessage(ResultCode));
  Result := False;  { esta copia del instalador termina aquí }
end;

function InitializeSetup(): Boolean;
begin
  if (not IsAdminInstallMode) and AdminInstallExists() then
  begin
    Result := UpdateAdminInstall();
    exit;
  end;
  Result := WaitForAppClosed(WizardSilent);
end;

function InitializeUninstall(): Boolean;
begin
  Result := WaitForAppClosed(UninstallSilent);
end;

{ --- La tienda de Shopify (solo la primera vez) ------------------------------------------------- }

{ Ya hay configuración de una instalación anterior: no se vuelve a preguntar. }
function AlreadyConfigured(): Boolean;
begin
  Result := FileExists(ExpandConstant('{localappdata}\LabelBridge\config.enc')) or
    FileExists(ExpandConstant('{localappdata}\LabelBridge\config.json'));
end;

{ «Mi-Tienda», «https://mi-tienda.myshopify.com/admin» → «mi-tienda.myshopify.com». }
function NormalizeShop(Value: String): String;
begin
  Result := Lowercase(Trim(Value));
  if Pos('https://', Result) = 1 then
    Delete(Result, 1, 8);
  if Pos('http://', Result) = 1 then
    Delete(Result, 1, 7);
  if Pos('/', Result) > 0 then
    Result := Copy(Result, 1, Pos('/', Result) - 1);
  if (Result <> '') and (Pos('.', Result) = 0) then
    Result := Result + ShopSuffix;
end;

function ValidShop(Value: String): Boolean;
var
  I: Integer;
  Handle: String;
begin
  Result := Value = '';
  if Result then
    exit;
  if Length(Value) <= Length(ShopSuffix) then
    exit;
  if Copy(Value, Length(Value) - Length(ShopSuffix) + 1, Length(ShopSuffix)) <> ShopSuffix then
    exit;
  Handle := Copy(Value, 1, Length(Value) - Length(ShopSuffix));
  for I := 1 to Length(Handle) do
    if not (((Handle[I] >= 'a') and (Handle[I] <= 'z')) or ((Handle[I] >= '0') and (Handle[I] <= '9')) or (Handle[I] = '-')) then
      exit;
  Result := True;
end;

procedure InitializeWizard();
begin
  ShopPage := CreateInputQueryPage(wpSelectTasks, CustomMessage('ShopTitle'), CustomMessage('ShopSubtitle'),
    CustomMessage('ShopInfo'));
  ShopPage.Add(CustomMessage('ShopDomain'), False);
  ShopPage.Add(CustomMessage('ShopClientId'), False);
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := (PageID = ShopPage.ID) and AlreadyConfigured();
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = ShopPage.ID then
  begin
    ShopPage.Values[0] := NormalizeShop(ShopPage.Values[0]);
    ShopPage.Values[1] := Trim(ShopPage.Values[1]);
    if not ValidShop(ShopPage.Values[0]) then
    begin
      MsgBox(CustomMessage('ShopInvalid'), mbError, MB_OK);
      Result := False;
    end;
  end;
end;

{ Se escribió una tienda en esta instalación (nueva). }
function ShopGiven(): Boolean;
begin
  Result := (not WizardSilent) and (ShopPage.Values[0] <> '') and not AlreadyConfigured();
end;

{ La app recoge la tienda al abrirse por primera vez y borra estos valores. }
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall) and ShopGiven() then
  begin
    RegWriteStringValue(HKEY_CURRENT_USER, 'Software\LabelBridge\Setup', 'ShopDomain', ShopPage.Values[0]);
    if ShopPage.Values[1] <> '' then
      RegWriteStringValue(HKEY_CURRENT_USER, 'Software\LabelBridge\Setup', 'ClientId', ShopPage.Values[1]);
  end;
end;
