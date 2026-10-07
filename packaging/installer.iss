; Instalador de Windows (Inno Setup 6): https://jrsoftware.org/isinfo.php
; Compilar: ISCC.exe /DAppVersion=1.0.0 packaging\installer.iss
;   (después de PyInstaller y de python packaging\make_installer_images.py, que crea las imágenes de la marca)
;
; En inglés o en español: se elige al empezar y la app queda en ese idioma (se puede cambiar en Ajustes).
; La primera vez pregunta, si se quiere, la tienda de Shopify (dirección, Client ID y Client secret): al abrirse, la
; app guarda la clave en el almacén seguro de Windows, borra lo que dejó el instalador, prueba la conexión y, si
; funciona, activa la tienda.

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#define AppName "LabelBridge"
#define AppExe "LabelBridge.exe"
#define AppPublisher "LabelBridge"
#ifndef TermsVersion
  #define TermsVersion "1"
#endif
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
; Siempre por usuario, sin preguntar ni pedir administrador. Hasta la 1.9.0 se podía elegir «para todos»: esa copia
; se desinstala al actualizar y queda solo para el usuario (ver RemoveAdminInstall en [Code]). /ALLUSERS sigue valiendo.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=commandline
UsedUserAreasWarning=no
; La app crea el mutex LabelBridgeRunningMutex mientras está abierta. No se usa AppMutex: en modo silencioso
; (actualización automática) el instalador se cancelaría si la app aún no terminó de cerrarse. Ver [Code].
CloseApplications=yes
; English / Español al empezar. Las actualizaciones automáticas repiten el idioma de la primera instalación.
ShowLanguageDialog=yes
UsePreviousLanguage=yes

; La página «Acuerdo de licencia» (Acepto / No acepto): términos de uso y política de privacidad, los mismos textos
; de la app (packaging/make_legal.py los pasa a RTF desde assets/legal).
[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"; LicenseFile: "..\build\legal\license-en.rtf"
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"; LicenseFile: "..\build\legal\license-es.rtf"

[CustomMessages]
english.NetworkGroup=Network:
spanish.NetworkGroup=Red:
english.FirewallTask=Allow the local API from other computers on the private network (firewall rule)
spanish.FirewallTask=Permitir la API local desde otros equipos de la red privada (regla de firewall)
english.AdminInstall=This PC has an earlier LabelBridge installed for all users. It will be removed so the new one is installed just for you: Windows will ask for administrator permission one last time.%n%nYour settings, orders and printers are kept.
spanish.AdminInstall=Esta PC tiene una versión anterior de LabelBridge instalada para todos los usuarios. Se va a quitar para instalar la nueva solo para ti: Windows pedirá permiso de administrador por última vez.%n%nTu configuración, tus pedidos y tus impresoras se conservan.
english.AdminInstallFailed=The earlier LabelBridge (installed for all users) couldn't be removed. Uninstall it from Settings → Apps and open this installer again: your settings are kept.
spanish.AdminInstallFailed=No se pudo quitar la versión anterior de LabelBridge (instalada para todos los usuarios). Desinstálala desde Configuración → Aplicaciones y vuelve a abrir este instalador: tu configuración se conserva.
english.CloseApp=LabelBridge is open. Close it (right-click its icon next to the clock → Exit) and press OK.
spanish.CloseApp=LabelBridge está abierta. Ciérrala (clic derecho en el icono junto al reloj → Salir) y pulsa Aceptar.
english.ShopTitle=Your Shopify store
spanish.ShopTitle=Tu tienda de Shopify
english.ShopSubtitle=LabelBridge prints the shipping labels of this store's orders.
spanish.ShopSubtitle=LabelBridge imprime las guías de envío de los pedidos de esta tienda.
english.ShopInfo=Optional: with the three fields, the store is connected and activated as soon as LabelBridge opens. You can also leave them empty and do it later in the app.%n%nThe Client secret goes to the Windows secure store when the app opens.
spanish.ShopInfo=Opcional: con los tres datos, la tienda queda conectada y activa en cuanto LabelBridge se abre. También puedes dejarlos vacíos y hacerlo después en la app.%n%nLa clave secreta (Client secret) pasa al almacén seguro de Windows al abrirse la app.
english.ShopDomain=Store address (for example my-store.myshopify.com):
spanish.ShopDomain=Dirección de la tienda (por ejemplo mi-tienda.myshopify.com):
english.ShopClientId=Client ID of your app in the Shopify Dev Dashboard (optional):
spanish.ShopClientId=Client ID de tu app en el Dev Dashboard de Shopify (opcional):
english.ShopClientSecret=Client secret of that app (optional):
spanish.ShopClientSecret=Client secret de esa app (opcional):
english.ShopInvalid=That isn't a Shopify store address. Example: my-store.myshopify.com
spanish.ShopInvalid=Esa no es la dirección de una tienda de Shopify. Ejemplo: mi-tienda.myshopify.com
english.FinishConnect=Open LabelBridge and connect the store
spanish.FinishConnect=Abrir LabelBridge y conectar la tienda

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "firewall"; Description: "{cm:FirewallTask}"; GroupDescription: "{cm:NetworkGroup}"; Flags: unchecked; Check: IsAdminInstallMode

[Files]
Source: "..\dist\LabelBridge\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; AppUserModelID: el mismo de la app (system/toasts.py), para que los avisos de Windows salgan como LabelBridge.
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; Parameters: "--show"; AppUserModelID: "LabelBridge.App"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Parameters: "--show"; Tasks: desktopicon; AppUserModelID: "LabelBridge.App"

[Registry]
; Los términos aceptados en el instalador (solo si se vio la página: no en las actualizaciones silenciosas). La app no
; los vuelve a pedir.
Root: HKCU; Subkey: "Software\LabelBridge"; ValueType: string; ValueName: "TermsAccepted"; ValueData: "{#TermsVersion}"; Check: not WizardSilent
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
; «Funcionar siempre» (Ajustes → General): las tareas de Windows se quitan y, si LabelBridge trabajaba en segundo plano
; (sin sesión iniciada), se cierra para poder borrar sus archivos.
Filename: "{sys}\schtasks.exe"; Parameters: "/Delete /TN ""LabelBridge - Sin sesión ({username})"" /F"; Flags: runhidden; RunOnceId: "RemoveBackgroundTask"
Filename: "{sys}\schtasks.exe"; Parameters: "/Delete /TN ""LabelBridge - Despertar ({username})"" /F"; Flags: runhidden; RunOnceId: "RemoveWakeTask"
Filename: "{sys}\taskkill.exe"; Parameters: "/IM LabelBridge.exe /F /FI ""SESSION eq 0"""; Flags: runhidden; RunOnceId: "CloseBackground"
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
  BackgroundPaused: Boolean;

{ Cierra a la fuerza una LabelBridge que no terminó de cerrarse (solo en actualizaciones automáticas). }
procedure ForceCloseApp();
var
  ResultCode: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/IM LabelBridge.exe /F', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Sleep(1500);
end;

{ «Funcionar siempre»: mientras se instala, la tarea «sin sesión» queda en pausa (no abre LabelBridge en segundo plano a
  mitad de la copia de archivos) y se cierra la copia de segundo plano que hubiera (sesión 0). Al terminar, o si se
  cancela, la tarea vuelve a quedar activa. Si la tarea no existe, schtasks falla sin más. }
procedure PauseBackground(Pause: Boolean);
var
  ResultCode: Integer;
  TaskName: String;
begin
  TaskName := 'LabelBridge - Sin sesión (' + GetUserNameString + ')';
  if Pause then
  begin
    Exec(ExpandConstant('{sys}\schtasks.exe'), '/Change /TN "' + TaskName + '" /DISABLE', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    BackgroundPaused := ResultCode = 0;
    Exec(ExpandConstant('{sys}\schtasks.exe'), '/End /TN "' + TaskName + '"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    Exec(ExpandConstant('{sys}\taskkill.exe'), '/IM LabelBridge.exe /F /FI "SESSION eq 0"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  end
  else if BackgroundPaused then
  begin
    Exec(ExpandConstant('{sys}\schtasks.exe'), '/Change /TN "' + TaskName + '" /ENABLE', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    BackgroundPaused := False;
  end;
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

{ --- Copia «para todos los usuarios» (hasta la 1.9.0 se podía elegir) --------------------------------- }

{ Esas copias, en Archivos de programa, pedían permiso de administrador en cada actualización. Ahora siempre se instala
  solo para el usuario: si hay una, se desinstala (Windows pide permiso una última vez; la configuración, los pedidos y
  las claves no se tocan) y la instalación sigue normal. Inno no deja relanzar este mismo instalador «para todos» antes
  de instalar, y así además no lo vuelve a pedir. }
function AdminUninstaller(var Uninstaller: String): Boolean;
var
  Key: String;
begin
  Key := ExpandConstant('Software\Microsoft\Windows\CurrentVersion\Uninstall\{#emit SetupSetting("AppId")}_is1');
  Result := RegQueryStringValue(HKLM64, Key, 'UninstallString', Uninstaller) or
    RegQueryStringValue(HKLM32, Key, 'UninstallString', Uninstaller);
  Uninstaller := RemoveQuotes(Uninstaller);
end;

function RemoveAdminInstall(): Boolean;
var
  Uninstaller: String;
  ResultCode, Waited: Integer;
begin
  Result := True;
  if IsAdminInstallMode or not AdminUninstaller(Uninstaller) then
    exit;
  Log('Hay una copia para todos los usuarios (' + Uninstaller + '): se desinstala para instalar solo para este usuario.');
  if not WizardSilent then
    MsgBox(CustomMessage('AdminInstall'), mbInformation, MB_OK);
  { ShellExec: el desinstalador necesita permiso de administrador y Windows lo pide. }
  if ShellExec('', Uninstaller, '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART', '', SW_SHOWNORMAL, ewWaitUntilTerminated, ResultCode) then
  begin
    { El desinstalador termina en una copia temporal: se espera a que la copia anterior desaparezca del registro. }
    Waited := 0;
    while AdminUninstaller(Uninstaller) and (Waited < 120000) do
    begin
      Sleep(500);
      Waited := Waited + 500;
    end;
  end else
    Log('No se pudo abrir el desinstalador de la copia anterior: ' + SysErrorMessage(ResultCode));
  Result := not AdminUninstaller(Uninstaller);
  if Result then
    Log('Copia para todos los usuarios desinstalada.')
  else
  begin
    Log('La copia para todos los usuarios sigue instalada: no se instala otra.');
    if not WizardSilent then
      MsgBox(CustomMessage('AdminInstallFailed'), mbError, MB_OK);
  end;
end;

function InitializeSetup(): Boolean;
begin
  Result := WaitForAppClosed(WizardSilent) and RemoveAdminInstall();
  if Result then
    PauseBackground(True);
end;

procedure DeinitializeSetup();
begin
  PauseBackground(False);  { también si se canceló: la tarea nunca queda en pausa }
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
  ShopPage.Add(CustomMessage('ShopClientSecret'), True);  { se escribe oculta }
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
    ShopPage.Values[2] := Trim(ShopPage.Values[2]);
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
    { Solo hasta que la app se abre: la pasa al almacén seguro de Windows y borra esta clave. }
    if ShopPage.Values[2] <> '' then
      RegWriteStringValue(HKEY_CURRENT_USER, 'Software\LabelBridge\Setup', 'ClientSecret', ShopPage.Values[2]);
  end;
end;
