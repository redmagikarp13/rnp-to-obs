import os
import sys
import time
import socket
import subprocess
import threading
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

# O app só encerra o serviço que ELE iniciou: um servidor que já rodava antes
# (dev, ou operação só pelo OBS sem painel) não é dele para matar.
SERVIDOR_INICIADO_PELO_APP = False
# Processo do servidor que nós spawnamos (para o watchdog vigiar) e flag de
# encerramento intencional (fechar a janela => não reanimar).
SERVER_PROC = None
ENCERRANDO = False

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

def _comando_servidor():
    if getattr(sys, 'frozen', False):
        return [sys.executable, "--server"]
    python_dir = os.path.dirname(sys.executable)
    python_exe = os.path.join(python_dir, "python.exe")
    if not os.path.exists(python_exe):
        python_exe = sys.executable
    return [python_exe, os.path.join(PROJECT_DIR, "server.py")]

def _spawn_servidor():
    return subprocess.Popen(_comando_servidor(), cwd=PROJECT_DIR,
                            creationflags=0x08000000, close_fds=True)

def start_server():
    global SERVER_PROC
    if is_server_running():
        return True
    try:
        SERVER_PROC = _spawn_servidor()
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


def _precisa_reanimar(proc, encerrando, iniciado_pelo_app) -> bool:
    """Só reanima se: não estamos encerrando de propósito, o app foi quem iniciou o
    servidor, e o processo que rastreamos morreu (poll() devolve código de saída)."""
    if encerrando or not iniciado_pelo_app:
        return False
    return proc is not None and proc.poll() is not None


def _reanimar_servidor():
    try:
        return _spawn_servidor()
    except Exception as e:
        print(f"[Watchdog] Falha ao reanimar o servidor: {e}", flush=True)
        return None


def _zelo_do_servidor():
    """Vigia o processo do servidor enquanto a janela do painel está aberta; se ele
    morre (crash, queda), religa automaticamente. Não religa quando o usuário fecha o
    app de propósito (ENCERRANDO) nem mexe num servidor que já rodava (não é nosso)."""
    global SERVER_PROC
    while not ENCERRANDO:
        time.sleep(3)
        if ENCERRANDO:
            break
        if _precisa_reanimar(SERVER_PROC, ENCERRANDO, SERVIDOR_INICIADO_PELO_APP):
            print(f"[Watchdog] Servidor caiu (código {SERVER_PROC.poll()}); reanimando...", flush=True)
            novo = _reanimar_servidor()
            if novo is not None:
                SERVER_PROC = novo
                print("[Watchdog] Servidor reanimado.", flush=True)

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

def parar_servidor_ao_fechar():
    # Fechar a janela encerra o serviço, sem prompt: quem abriu o app decidiu
    # encerrar a sessão. ENCERRANDO avisa o watchdog para NÃO reanimar.
    global ENCERRANDO
    ENCERRANDO = True
    if SERVIDOR_INICIADO_PELO_APP:
        stop_server_api()

def main():
    global SERVIDOR_INICIADO_PELO_APP
    ja_rodava = is_server_running()
    if start_server():
        SERVIDOR_INICIADO_PELO_APP = not ja_rodava
        if SERVIDOR_INICIADO_PELO_APP:
            threading.Thread(target=_zelo_do_servidor, daemon=True).start()
        dock_window.launch_app_window(parar_servidor_ao_fechar)

if __name__ == "__main__":
    main()

