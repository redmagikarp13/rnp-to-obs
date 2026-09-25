@echo off
chcp 65001 >nul
title Compilador do Instalador - RNP to OBS Studio
cls

echo ======================================================================
echo    CRIADOR DO INSTALADOR PROFISSIONAL (SETUP.EXE) - RNP TO OBS
echo ======================================================================
echo.

set "ISCC_PATH=C:\Users\leona\AppData\Local\Programs\Inno Setup 6\ISCC.exe"
if not exist "%ISCC_PATH%" (
    set "ISCC_PATH=C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
)
if not exist "%ISCC_PATH%" (
    set "ISCC_PATH=C:\Program Files\Inno Setup 6\ISCC.exe"
)

echo [1/2] Compilando os binarios do aplicativo com PyInstaller...
python build_exe.py
if errorlevel 1 (
    echo.
    echo [ERRO] A compilacao dos binarios falhou!
    pause
    exit /b 1
)

echo.
echo [2/2] Gerando Instalador Windows com Inno Setup...
if not exist "dist_installer" mkdir dist_installer
if not exist "%ISCC_PATH%" (
    echo [AVISO] Compilador Inno Setup nao encontrado em %ISCC_PATH%.
    echo Tentando executar iscc via PATH...
    iscc /O"%TEMP%" installer.iss
) else (
    "%ISCC_PATH%" /O"%TEMP%" installer.iss
)

if errorlevel 1 (
    echo.
    echo [ERRO] A geracao do instalador Setup.exe falhou!
    pause
    exit /b 1
)

copy /y "%TEMP%\Instalador_RNP_to_OBS_Setup.exe" "dist_installer\Instalador_RNP_to_OBS_Setup.exe" >nul

echo.
echo ======================================================================
echo  SUCESSO! O instalador foi gerado com sucesso!
echo.
echo  Arquivo gerado:
echo    dist_installer\Instalador_RNP_to_OBS_Setup.exe
echo.
echo  Este e o arquivo que voce envia para qualquer pessoa instalar!
echo ======================================================================
echo.
explorer dist_installer
pause
