@echo off
chcp 65001 >nul
title Instalador - RNP to OBS Studio Bridge
echo ======================================================================
echo           RNP WebConf / BBB to OBS Studio Bridge - Instalador
echo ======================================================================
echo.

echo [1/3] Verificando versão do Python...
python --version
if %errorlevel% neq 0 (
    echo [ERRO] Python não encontrado no sistema. Por favor instale o Python 3.10+ e marque a opção 'Add Python to PATH'.
    pause
    exit /b 1
)

echo.
echo [2/3] Instalando dependências do Python...
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo [ERRO] Falha ao instalar dependências. Verifique sua conexão com a internet.
    pause
    exit /b 1
)

echo.
echo [3/3] Instalando navegadores do Playwright (Chromium)...
playwright install chromium
if %errorlevel% neq 0 (
    echo [ERRO] Falha ao instalar o Chromium do Playwright.
    pause
    exit /b 1
)

echo.
echo ======================================================================
echo      [SUCESSO] Instalação concluída com sucesso!
echo      Para iniciar, execute o arquivo 'start.bat'.
echo ======================================================================
echo.
pause
