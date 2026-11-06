; Script de Instalação Inno Setup para RNP to OBS Studio
; Desenvolvido para Cefor / Ifes

#define MyAppName "RNP to OBS"
#define MyAppVersion "1.1.15"
#define MyAppPublisher "Cefor / Ifes"
#define MyAppExeName "RNP to OBS.exe"
#define MyAppIcon "app_icon.ico"

[Setup]
AppId={{8B490E0F-3A1A-4D72-8C83-FE8E29E2F80B}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=dist_installer
OutputBaseFilename=Instalador_RNP_to_OBS_Setup
SetupIconFile={#MyAppIcon}
UninstallDisplayIcon={app}\{#MyAppIcon}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequiredOverridesAllowed=commandline dialog

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "Criar atalho na Área de Trabalho"; GroupDescription: "Atalhos adicionais:"; Flags: unchecked

[Files]
Source: "dist\RNP to OBS\*"; DestDir: "{app}"; Excludes: "*.old"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}\{#MyAppName} - Painel de Controle"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppIcon}"
Name: "{autoprograms}\{#MyAppName}\Desinstalar {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName} - Painel de Controle"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppIcon}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Abrir o {#MyAppName} agora"; Flags: nowait postinstall skipifsilent

[Code]
function InitializeSetup(): Boolean;
var
  ResultCode: Integer;
begin
  // Encerra qualquer instância anterior (janela ou subprocesso --server em segundo plano)
  // para garantir que a nova versão instalada assuma a porta 3333.
  Exec('taskkill.exe', '/F /IM "RNP to OBS.exe"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Result := True;
end;
