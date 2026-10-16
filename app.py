import os
import sys
import time
import socket
import subprocess
import ctypes
import urllib.request
import urllib.error

class SafeLogWriter:
    def __init__(self, filepath=None):
        self.file = None
        if filepath:
            try:
                os.makedirs(os.path.dirname(filepath), exist_ok=True)
                self.file = open(filepath, "a", encoding="utf-8", errors="replace")
            except Exception:
                pass

    def write(self, s):
        if self.file:
            try:
                self.file.write(s)
                self.file.flush()
            except Exception:
                pass

    def flush(self):
        if self.file:
            try:
                self.file.flush()
            except Exception:
                pass

    def isatty(self):
        return False

    def reconfigure(self, **kwargs):
        pass

log_dir = os.path.join(os.environ.get("LOCALAPPDATA", os.path.dirname(os.path.abspath(__file__))), "RNP_to_OBS")
if sys.stdout is None or not hasattr(sys.stdout, 'isatty'):
    sys.stdout = SafeLogWriter(os.path.join(log_dir, "app_stdout.log"))
elif hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

if sys.stderr is None or not hasattr(sys.stderr, 'isatty'):
    sys.stderr = SafeLogWriter(os.path.join(log_dir, "app_stderr.log"))
elif hasattr(sys.stderr, 'reconfigure'):
    try:
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

# Suporte ao modo servidor (execução direta ou dentro do .exe PyInstaller)
if len(sys.argv) > 1 and sys.argv[1] == "--server":
    import uvicorn
    base_dir = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    if base_dir not in sys.path:
        sys.path.insert(0, base_dir)
    from server import app as fastapi_app
    uvicorn.run(fastapi_app, host="127.0.0.1", port=3333, log_level="warning")
    sys.exit(0)


PROJECT_DIR = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))

import dock_window

def is_server_running() -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.3)
            return s.connect_ex(('127.0.0.1', 3333)) == 0
    except Exception:
        return False

def show_error(message: str, title: str = "RNP to OBS - Erro"):
    try:
        ctypes.windll.user32.MessageBoxW(0, message, title, 0x10)  # MB_ICONERROR
    except Exception:
        print(f"[{title}] {message}")

def start_server():
    if is_server_running():
        return True

    if getattr(sys, 'frozen', False):
        # Executável empacotado (.exe)
        server_cmd = [sys.executable, "--server"]
    else:
        # Script Python (.py)
        python_dir = os.path.dirname(sys.executable)
        python_exe = os.path.join(python_dir, "python.exe")
        if not os.path.exists(python_exe):
            python_exe = sys.executable
        server_script = os.path.join(PROJECT_DIR, "server.py")
        server_cmd = [python_exe, server_script]

    try:
        subprocess.Popen(
            server_cmd,
            cwd=PROJECT_DIR,
            creationflags=0x08000000,
            close_fds=True
        )
    except Exception as e:
        show_error(f"Não foi possível iniciar o servidor:\n{e}")
        return False

    t0 = time.time()
    while time.time() - t0 < 15:
        if is_server_running():
            return True
        time.sleep(0.2)

    show_error("O servidor RNP to OBS não respondeu na porta 3333 após 15 segundos.\nVerifique se o Firewall permite conexões locais.")
    return False

def stop_server_api():
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:3333/api/server/stop",
            data=b"{}",
            headers={"Content-Type": "application/json"}
        )
        urllib.request.urlopen(req, timeout=2)
    except Exception:
        pass

def prompt_on_window_close():
    if not is_server_running():
        return

    MB_YESNO = 0x04
    MB_ICONQUESTION = 0x20
    IDYES = 6

    # Pergunta se o usuário quer encerrar tudo ou manter o servidor para o OBS Studio
    res = ctypes.windll.user32.MessageBoxW(
        0,
        "A janela do Painel de Controle foi fechada.\n\n"
        "Deseja desconectar da sala RNP e encerrar o servidor agora?\n\n"
        "• Clique em 'Sim' para sair da sala e fechar o aplicativo completamente.\n"
        "• Clique em 'Não' caso o OBS Studio ainda esteja transmitindo em segundo plano.",
        "RNP to OBS - Encerrar Sessão?",
        MB_YESNO | MB_ICONQUESTION
    )

    if res == IDYES:
        stop_server_api()

def main():
    if start_server():
        # Janela nativa (pywebview) com fallback automático para navegador em
        # modo app; o prompt de encerrar o servidor roda ao fechar a janela.
        dock_window.launch_app_window(prompt_on_window_close)

if __name__ == "__main__":
    main()

