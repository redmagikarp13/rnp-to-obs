@echo off
chcp 65001 >nul
title RNP to OBS Studio Bridge - Servidor Ativo
echo ======================================================================
echo           RNP WebConf / BBB to OBS Studio Bridge
echo ======================================================================
echo.
echo [INFO] Servidor iniciando em: http://localhost:3333
echo [INFO] Painel de Controle (Dock OBS): http://localhost:3333/control
echo.
echo [DICA] Mantenha esta janela aberta enquanto transmitir pelo OBS.
echo.
python server.py
pause
