"""Janela nativa do painel de controle (pywebview) com fallback de navegador.

O painel continua sendo o /control servido pela ponte; aqui só muda o casco da
janela: em vez de abrir o Edge/Chrome em modo --app=, abre uma janela nativa
(WebView2 no Windows, WebKit no macOS) sem UI de navegador. O fallback para o
caminho antigo garante que máquina sem backend WebView2 não fique sem painel.
"""
import os
import subprocess

SERVER_URL = 'http://127.0.0.1:3333/control'
JANELA_TITULO = 'RNP to OBS — Painel'
JANELA_SIZE = (1360, 860)
JANELA_MIN = (900, 600)


def profile_dir() -> str:
    """Mesmo perfil de antes: o dock guarda prefs no localStorage (aba ativa,
    previews desligados), então a janela nativa precisa apontar para o mesmo
    storage ou o operador recomeçaria do zero a cada abertura."""
    base = os.environ.get('LOCALAPPDATA', os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, 'RNP_to_OBS_Profile')


def abrir_janela_pywebview(url=SERVER_URL, ao_fechar=None):
    """Abre a janela nativa e bloqueia até ela fechar.

    ImportError/RuntimeException propagam: o chamador decide o fallback.
    O ícone da janela é o do executável quando empacotado (pywebview não recebe
    icon por janela); em desenvolvimento herda o ícone do pythonw.
    """
    import webview
    janela = webview.create_window(
        title=JANELA_TITULO, url=url,
        width=JANELA_SIZE[0], height=JANELA_SIZE[1],
        min_size=JANELA_MIN,
    )
    if ao_fechar:
        janela.events.closed += ao_fechar
    # private_mode=False + storage_path: persiste o localStorage do dock.
    webview.start(private_mode=False, storage_path=profile_dir())


def find_browser_app() -> str:
    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    ]
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return ""


def abrir_janela_navegador(url=SERVER_URL, ao_fechar=None):
    """Caminho legado (Edge/Chrome --app=): fallback quando não há backend nativo."""
    browser = find_browser_app()
    os.makedirs(profile_dir(), exist_ok=True)
    if not browser:
        import webbrowser
        webbrowser.open(url)
        return
    proc = subprocess.Popen([
        browser,
        f"--app={url}",
        f"--window-size={JANELA_SIZE[0]},{JANELA_SIZE[1]}",
        f"--user-data-dir={profile_dir()}",
        "--disable-features=Translate",
        "--app-id=rnp_to_obs_control",
        "--no-first-run",
        "--disable-default-apps",
    ])
    proc.wait()
    if ao_fechar:
        ao_fechar()


def launch_app_window(ao_fechar=None):
    """Janela nativa primeiro; qualquer falha de backend cai no navegador."""
    try:
        abrir_janela_pywebview(SERVER_URL, ao_fechar)
    except Exception:
        abrir_janela_navegador(SERVER_URL, ao_fechar)
