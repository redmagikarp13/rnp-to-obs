@echo off
setlocal enabledelayedexpansion

echo ===================================================
echo Compilando Plugin Nativo OBS: obs-rnp-bridge.dll
echo ===================================================

set OBS_DIR=C:\Program Files\obs-studio
set SRC_FILES=src\plugin_main.cpp src\rnp_camera_source.cpp src\rnp_presentation_source.cpp src\rnp_chat_source.cpp src\rnp_session.cpp
set OUT_DIR=bin\64bit
set OUT_DLL=%OUT_DIR%\obs-rnp-bridge.dll

if not exist "%OUT_DIR%" mkdir "%OUT_DIR%"

echo [1/2] Compilando C++ com C++ Runtime estatica...
clang++ -shared -std=c++17 -O2 -Iinclude ^
    -static-libgcc ^
    -Wl,-Bstatic -lc++ -lunwind -Wl,-Bdynamic ^
    "%OBS_DIR%\bin\64bit\obs.dll" ^
    %SRC_FILES% ^
    -o "%OUT_DLL%"

if %ERRORLEVEL% equ 0 (
    echo.
    echo ===================================================
    echo [SUCESSO] Plugin compilado com sucesso!
    echo Arquivo gerado: %OUT_DLL%
    echo ===================================================
) else (
    echo.
    echo [ERRO] Falha na compilacao do plugin.
)
