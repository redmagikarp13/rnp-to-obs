"""Mede a CPU da JANELA REAL do dock (app.py + WebView2) visível vs minimizada.

A matriz headless superestima raster (software); aqui o WebView2 usa GPU, então o
número é o que o operador paga de verdade. Minimizar torna document.hidden=true e
os guards (polls, mesa, fps, monitor local suspenso) devem derrubar o custo.
A ponte (server.py) NÃO entra na conta: só a árvore do app (python app.py + filhos
WebView2 marcados pelo user-data-dir RNP_to_OBS_Profile).
"""
import sys
import time

import psutil

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
JANELA = 15


def eh_janela_dock(cmd):
    c = ' '.join(cmd).lower()
    if 'measure_profile' in c:
        return False
    if 'app.py' in c and ('python' in c):
        return True
    return 'rnp_to_obs_profile' in c and 'msedge' in c


def pids():
    out = {}
    for p in psutil.process_iter(['pid', 'cmdline']):
        try:
            cmd = p.info['cmdline'] or []
            if cmd and eh_janela_dock(cmd):
                out[p.info['pid']] = p
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return out


def aquecer():
    for p in pids().values():
        try:
            p.cpu_percent(None)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass


def amostrar(rotulo):
    tot = n = 0.0
    for _ in range(JANELA):
        time.sleep(1.0)
        tot += sum(_cpu(p) for p in pids().values())
        n += 1
    v = tot / n
    print(f'{rotulo}: janela do dock {v:6.1f}%')
    return v


def _cpu(p):
    try:
        return p.cpu_percent(None)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return 0.0


def main():
    import ctypes
    import subprocess
    user32 = ctypes.windll.user32
    proc = subprocess.Popen([sys.executable, 'app.py'], cwd='.')
    time.sleep(20)
    ps = pids()
    print('processos da janela:', len(ps))
    if not ps:
        print('ERRO: janela nao detectada.'); proc.kill(); sys.exit(1)
    # psutil não expõe MainWindowHandle; EnumWindows pelo título da janela nativa.
    titulo = 'RNP to OBS — Painel'
    achado = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def enum(hwnd_, _):
        n = user32.GetWindowTextLengthW(hwnd_)
        if n:
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd_, buf, n + 1)
            if buf.value == titulo:
                achado.append(hwnd_)
        return True

    user32.EnumWindows(enum, 0)
    hwnd = achado[0] if achado else 0
    print('hwnd da janela:', hwnd)
    if not hwnd:
        proc.kill(); sys.exit(1)

    aquecer()
    visivel = amostrar('VISIVEL ')
    user32.ShowWindow(hwnd, 6)   # SW_MINIMIZE -> document.hidden=true
    time.sleep(4)
    aquecer()
    oculta = amostrar('MINIMIZADA')
    user32.PostMessageW(hwnd, 0x0010, 0, 0)   # WM_CLOSE: fecha a janela
    time.sleep(3)
    print(f'delta ao minimizar: {oculta - visivel:+6.1f}% '
          f'({(oculta - visivel) / max(visivel, 1) * 100:+.0f}%)')
    try:
        proc.wait(timeout=10)
    except Exception:
        proc.kill()


if __name__ == '__main__':
    main()
