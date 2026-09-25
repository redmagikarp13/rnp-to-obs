@echo off
title RNP to OBS - Painel de Controle
chcp 65001 >nul
cd /d "%~dp0"
start "" pythonw "%~dp0app.pyw"
exit
