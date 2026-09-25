@echo off
title Compilador de Executavel - RNP to OBS
chcp 65001 >nul
cd /d "%~dp0"
python build_exe.py
pause
