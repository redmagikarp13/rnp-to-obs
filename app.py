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
    from server import app as fastapi_app, resolve_bind_host, resolve_bind_port, _lan_ip_candidates
    _host = resolve_bind_host()
    _port = resolve_bind_port()
    if _host == "0.0.0.0":
        _ips = _lan_ip_candidates()
        print(f"[Bridge] Acesso LAN ativo. Outras máquinas: http://{(_ips[0] if _ips else '<IP>')}:{_port}/tv", flush=True)
    uvicorn.run(fastapi_app, host=_host, port=_port, log_level="warning", http="h11")
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
# Backoff do watchdog: espera base entre checagens, dobra por queda rápida
# consecutiva e satura no teto, para não virar respawn infinito se o boot
# passar a falhar. Uma queda isolada depois de horas de uptime religa em ~base s.
_WATCHDOG_BASE = 3
_WATCHDOG_TETO = 60
_GRACA_STAB = 8  # s para considerar que o servidor reanimado ficou de pé

def _resolve_port() -> int:
    try:
        return int(os.environ.get("RNP_PORT", "3333"))
    except ValueError:
        return 3333

def is_server_running() -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.3)
            return s.connect_ex(('127.0.0.1', _resolve_port())) == 0
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
    kwargs = {"cwd": PROJECT_DIR, "close_fds": True}
    if sys.platform == "win32":
        kwargs["creationflags"] = 0x08000000
    return subprocess.Popen(_comando_servidor(), **kwargs)

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

    show_error(f"O servidor RNP to OBS não respondeu na porta {_resolve_port()} após 15 segundos.\nVerifique se o Firewall permite conexões locais.")
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


def _proximo_backoff(falhas_consecutivas, base=_WATCHDOG_BASE, teto=_WATCHDOG_TETO):
    """Segundos a esperar dado o número de quedas rápidas consecutivas.
    Cresce exponencialmente (base, base, 2·base, 4·base, ...) e satura no teto."""
    if falhas_consecutivas <= 1:
        return base
    return min(teto, base * (2 ** (falhas_consecutivas - 1)))


def _decidir_espera(ficou_de_pe, falhas_consecutivas, base=_WATCHDOG_BASE, teto=_WATCHDOG_TETO):
    """Retorna (novas_falhas, nova_espera). Servidor estável zera a contagem; caiu
    de novo incrementa e cresce o backoff."""
    if ficou_de_pe:
        return 0, base
    novas = falhas_consecutivas + 1
    return novas, _proximo_backoff(novas, base, teto)


def _dorme(segundos):
    """Dorme em pedaços, acordando cedo se o app estiver encerrando (não segura o
    fechamento da janela por causa de um backoff longo)."""
    fim = time.time() + segundos
    while time.time() < fim and not ENCERRANDO:
        time.sleep(max(0.0, min(0.5, fim - time.time())))


def _zelo_do_servidor():
    """Vigia o processo do servidor enquanto a janela do painel está aberta; se ele
    morre (crash, queda), religa automaticamente. Não religa quando o usuário fecha o
    app de propósito (ENCERRANDO) nem mexe num servidor que já rodava (não é nosso).
    Usa backoff exponencial com teto: se o servidor cai em loop no boot, as tentativas
    espaçam cada vez mais (até 60s) em vez de queimar CPU re-spawnando a cada 3s."""
    global SERVER_PROC
    falhas = 0
    espera = _WATCHDOG_BASE
    while not ENCERRANDO:
        _dorme(espera)
        if ENCERRANDO:
            break
        if not _precisa_reanimar(SERVER_PROC, ENCERRANDO, SERVIDOR_INICIADO_PELO_APP):
            # Servidor vivo: descarta histórico de quedas rápidas.
            falhas, espera = 0, _WATCHDOG_BASE
            continue
        print(f"[Watchdog] Servidor caiu (código {SERVER_PROC.poll()}); reanimando...", flush=True)
        novo = _reanimar_servidor()
        if novo is None:
            falhas, espera = _decidir_espera(False, falhas)
            print(f"[Watchdog] Spawn falhou; nova tentativa em {espera}s.", flush=True)
            continue
        SERVER_PROC = novo
        _dorme(_GRACA_STAB)
        if ENCERRANDO:
            break
        ficou_de_pe = is_server_running() and novo.poll() is None
        falhas, espera = _decidir_espera(ficou_de_pe, falhas)
        if ficou_de_pe:
            print("[Watchdog] Servidor reanimado e estável.", flush=True)
        else:
            print(f"[Watchdog] Servidor não se manteve ({falhas} queda(s) rápida(s)); "
                  f"tentativa em {espera}s.", flush=True)

def stop_server_api():
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{_resolve_port()}/api/server/stop",
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

