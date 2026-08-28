@echo off
setlocal

echo ===================================================
echo Instalador do Plugin: obs-rnp-bridge.dll
echo ===================================================

set SOURCE_DLL=bin\64bit\obs-rnp-bridge.dll
set USER_PLUGIN_DIR=%APPDATA%\obs-studio\plugins\obs-rnp-bridge\bin\64bit
set PROG_PLUGIN_DIR=C:\Program Files\obs-studio\obs-plugins\64bit

if not exist "%SOURCE_DLL%" (
    echo [ERRO] DLL nao encontrada. Execute build.bat primeiro!
    pause
    exit /b 1
)

echo [1/2] Instalando na pasta de plugins do Usuario (%USER_PLUGIN_DIR%)...
if not exist "%USER_PLUGIN_DIR%" mkdir "%USER_PLUGIN_DIR%"
copy /Y "%SOURCE_DLL%" "%USER_PLUGIN_DIR%\" >nul

if %ERRORLEVEL% equ 0 (
    echo [OK] Instalado com sucesso na pasta de plugins do Usuario!
)

echo.
echo [2/2] Tentando instalar na pasta global do sistema (%PROG_PLUGIN_DIR%)...
copy /Y "%SOURCE_DLL%" "%PROG_PLUGIN_DIR%\" >nul 2>&1
if %ERRORLEVEL% equ 0 (
    echo [OK] Instalado tambem na pasta global do sistema!
) else (
    echo [INFO] Pasta global ignorada (necessario Administrador, mas a pasta do usuario ja basta).
)

echo.
echo ===================================================
echo [SUCESSO] Plugin instalado no OBS Studio!
echo Reinicie o OBS Studio para carregar as novas fontes.
echo ===================================================
echo.
pause
