@echo off
setlocal

:: Copia a DLL para o diretorio temporario antes de elevar permissoes
set SRC_LOCAL=%~dp0bin\64bit\obs-rnp-bridge.dll
set TEMP_DLL=%TEMP%\obs-rnp-bridge.dll

if exist "%SRC_LOCAL%" (
    copy /Y "%SRC_LOCAL%" "%TEMP_DLL%" >nul
)

:: Instalar tambem na pasta de plugins do Usuario
set USER_PLUGIN_DIR=%APPDATA%\obs-studio\plugins\obs-rnp-bridge\bin\64bit
if not exist "%USER_PLUGIN_DIR%" mkdir "%USER_PLUGIN_DIR%" >nul 2>&1
copy /Y "%SRC_LOCAL%" "%USER_PLUGIN_DIR%\obs-rnp-bridge.dll" >nul 2>&1

:: Solicita elevacao de Administrador automaticamente no Windows (UAC)
>nul 2>&1 "%SYSTEMROOT%\system32\cacls.exe" "%SYSTEMROOT%\system32\config\system"
if '%errorlevel%' NEQ '0' (
    echo Solicitando permissao de Administrador para instalar em Program Files...
    goto UACPrompt
) else ( goto gotAdmin )

:UACPrompt
    echo Set UAC = CreateObject^("Shell.Application"^) > "%temp%\getadmin.vbs"
    echo UAC.ShellExecute "%~s0", "", "", "runas", 1 >> "%temp%\getadmin.vbs"
    "%temp%\getadmin.vbs"
    exit /B

:gotAdmin
    if exist "%temp%\getadmin.vbs" ( del "%temp%\getadmin.vbs" )

echo =========================================================
echo Instalando Plugin RNP Bridge no OBS Studio
echo =========================================================
echo.

set TARGET_DIR=C:\Program Files\obs-studio\obs-plugins\64bit
set TEMP_DLL=%TEMP%\obs-rnp-bridge.dll

if not exist "%TEMP_DLL%" (
    echo [ERRO] Arquivo temporario nao encontrado em %TEMP_DLL%
    pause
    exit /b 1
)

echo Copiando obs-rnp-bridge.dll para "%TARGET_DIR%"...
copy /Y "%TEMP_DLL%" "%TARGET_DIR%\obs-rnp-bridge.dll"
if errorlevel 1 goto erro

echo.
echo =========================================================
echo [SUCESSO] Plugin instalado com sucesso no OBS Studio!
echo Arquivo copiado para: %TARGET_DIR%\obs-rnp-bridge.dll
echo.
echo Abra o OBS Studio e clique em '+' em Fontes:
echo  - RNP - Camera de Participante (Webcam)
echo  - RNP - Apresentacao / Slides
echo  - RNP - Chat Overlay
echo =========================================================
goto fim

:erro
echo.
echo [ERRO] Nao foi possivel copiar para "%TARGET_DIR%".
echo Verifique se o OBS Studio esta fechado durante a instalacao.

:fim
echo.
pause
