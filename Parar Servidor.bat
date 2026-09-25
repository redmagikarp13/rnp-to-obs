@echo off
title RNP to OBS - Encerrando Servidor
chcp 65001 >nul
echo [INFO] Encerrando servidor e liberando recursos...
python -u -c "import urllib.request; urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:3333/api/server/stop', data=b'{}', headers={'Content-Type': 'application/json'}))" 2>nul
timeout /t 1 /nobreak >nul
echo [OK] Servidor finalizado com sucesso.
exit
