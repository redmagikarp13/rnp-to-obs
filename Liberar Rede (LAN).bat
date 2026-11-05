@echo off
chcp 65001 >nul
title Liberar Rede (LAN) - RNP to OBS Studio Bridge

:: ---------------------------------------------------------------------
::  Libera o acesso da ponte RNP->OBS por outras maquinas da mesma rede.
::  Abre a porta 3333 (TCP e UDP) no Firewall do Windows, somente para
::  redes Privadas/Corporativas (nunca Publica, por seguranca).
::
::  Precisa de ADMINISTRADOR: o script se auto-eleva ao ser executado.
:: ---------------------------------------------------------------------

:: Verifica se ja esta como administrador
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Solicitando privilegios de administrador...
    powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

echo ======================================================================
echo        LIBERAR ACESSO PELA REDE (LAN) - RNP to OBS Studio Bridge
echo ======================================================================
echo.

echo [1/3] Removendo regras antigas com o mesmo nome (se existirem)...
netsh advfirewall firewall delete rule name="RNP to OBS LAN (TCP 3333)" >nul 2>&1
netsh advfirewall firewall delete rule name="RNP to OBS LAN (UDP 3333)" >nul 2>&1

echo [2/3] Liberando porta 3333 TCP (painel, /tv, /stage, WebRTC signaling)...
netsh advfirewall firewall add rule name="RNP to OBS LAN (TCP 3333)" dir=in action=allow protocol=TCP localport=3333 profile=private,domain
if %errorlevel% neq 0 (
    echo [ERRO] Falha ao criar a regra TCP. Execute este arquivo como Administrador.
    pause
    exit /b 1
)

echo [3/3] Liberando porta 3333 UDP (audio PCM / midia WebRTC)...
netsh advfirewall firewall add rule name="RNP to OBS LAN (UDP 3333)" dir=in action=allow protocol=UDP localport=3333 profile=private,domain
if %errorlevel% neq 0 (
    echo [ERRO] Falha ao criar a regra UDP. Execute este arquivo como Administrador.
    pause
    exit /b 1
)

echo.
echo ======================================================================
echo   [SUCESSO] Rede liberada! Outras maquinas da mesma rede ja podem
echo   acessar o painel e as fontes.
echo.
echo   No dock /control, bloco "Fontes", ative "Acesso por outra maquina
echo   (LAN)" e copie o link da TV (ex.: http://SEU-IP:3333/tv).
echo.
echo   OBS: o video das cameras vai por WebRTC (porta dinamica) via Microsoft
echo   Edge. Se a imagem nao aparecer na outra maquina, permita o Edge no
echo   Firewall quando o Windows perguntar, ou rode (como Administrador):
echo     netsh advfirewall firewall add rule name="RNP to OBS LAN (Edge)" dir=in action=allow program="C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" profile=private,domain
echo ======================================================================
echo.
pause
