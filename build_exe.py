import os
import sys
import subprocess
import shutil

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))

def build_executable():
    print("=" * 70)
    print("       COMPILAÇÃO DO EXECUTÁVEL - RNP TO OBS STUDIO")
    print("=" * 70)

    # 1. Verificar PyInstaller
    try:
        import PyInstaller
        print("[OK] PyInstaller detectado.")
    except ImportError:
        print("[INFO] Instalando PyInstaller...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])

    # 2. Argumentos do PyInstaller
    # Usamos o app_icon.ico e coletamos pacotes necessários
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--onedir",
        "--windowed",  # Não exibe console preto
        "--name=RNP to OBS",
        f"--icon={os.path.join(PROJECT_DIR, 'app_icon.ico')}",
        f"--add-data={os.path.join(PROJECT_DIR, 'app_icon.ico')};.",
        f"--add-data={os.path.join(PROJECT_DIR, 'app_icon.png')};.",
        f"--add-data={os.path.join(PROJECT_DIR, 'config.json')};.",
        f"--add-data={os.path.join(PROJECT_DIR, 'server.py')};.",
        f"--add-data={os.path.join(PROJECT_DIR, 'web')};web",
        "--hidden-import=media_bridge",
        "--collect-all=playwright",
        "--collect-all=uvicorn",
        "--collect-all=fastapi",
        "--collect-all=pytchat",
        "--collect-all=emoji",
        "--hidden-import=uvicorn.logging",
        "--hidden-import=uvicorn.loops",
        "--hidden-import=uvicorn.loops.auto",
        "--hidden-import=uvicorn.protocols",
        "--hidden-import=uvicorn.protocols.http",
        "--hidden-import=uvicorn.protocols.http.auto",
        "--hidden-import=uvicorn.protocols.websockets",
        "--hidden-import=uvicorn.protocols.websockets.auto",
        "--hidden-import=uvicorn.lifespan",
        "--hidden-import=uvicorn.lifespan.on",
        os.path.join(PROJECT_DIR, "app.py")
    ]

    print("\n[INFO] Compilando aplicativo... Aguarde alguns instantes...")
    res = subprocess.run(cmd, cwd=PROJECT_DIR)

    if res.returncode == 0:
        dist_dir = os.path.join(PROJECT_DIR, "dist", "RNP to OBS")
        # Copia config.json e ícone também para a raiz da pasta do exe para fácil acesso
        for extra in ["config.json", "app_icon.ico", "app_icon.png"]:
            src = os.path.join(PROJECT_DIR, extra)
            dst = os.path.join(dist_dir, extra)
            if os.path.exists(src) and not os.path.exists(dst):
                try:
                    shutil.copy2(src, dst)
                except Exception:
                    pass

        print("\n" + "=" * 70)
        print("[SUCESSO] COMPILACAO CONCLUIDA COM SUCESSO!")
        print(f"[PASTA] Pasta do executavel gerada em:\n   {dist_dir}")
        print("=" * 70)
        print("DICA: Para distribuir, basta compactar a pasta 'RNP to OBS' em um arquivo .ZIP!")
    else:
        print("\n[ERRO] Erro durante a compilacao. Codigo de saida:", res.returncode)

if __name__ == "__main__":
    build_executable()

