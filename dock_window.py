"""Janela nativa do painel de controle (pywebview) com fallback de navegador.

O painel continua sendo o /control servido pela ponte; aqui só muda o casco da
janela: em vez de abrir o Edge/Chrome em modo --app=, abre uma janela nativa
(WebView2 no Windows, WebKit no macOS) sem UI de navegador. O fallback para o
caminho antigo garante que máquina sem backend WebView2 não fique sem painel.
"""
import os
import shutil
import subprocess


def _resolve_port() -> int:
    try:
        return int(os.environ.get('RNP_PORT', '3333'))
    except ValueError:
        return 3333


SERVER_URL = f'http://127.0.0.1:{_resolve_port()}/control'
JANELA_TITULO = 'RNP to OBS — Painel'
JANELA_SIZE = (1360, 860)
JANELA_MIN = (320, 420)

# Mantém o painel 100% ativo mesmo com a janela minimizada/inativa. Sem isto o
# WebView2 (Chromium) coloca o renderer em segundo plano e suspende timers quando a
# janela perde a visibilidade — o monitor de áudio local "trava" até o operador
# voltar para a janela. As flags desligam esse throttling/occlusion.
#
# Fonte única: as MESMAS flags valem para as duas rotas de janela. Antes viviam só
# na rota nativa (variável de ambiente do WebView2); o fallback de navegador abria
# um Edge/Chrome --app= sem nenhuma delas, e era justamente ai que minimizar voltava
# a estrangular o painel — o defeito do §5.2 reaparecendo pela porta dos fundos.
KEEP_ALIVE_FLAGS = [
    '--disable-background-timer-throttling',
    '--disable-backgrounding-occluded-windows',
    '--disable-renderer-backgrounding',
    '--autoplay-policy=no-user-gesture-required',
]
# '--disable-features' so pode aparecer uma vez na linha de comando: os valores
# precisam ser mesclados num unico argumento, sena um sobrescreve o outro.
KEEP_ALIVE_FEATURES = ['CalculateNativeWinOcclusion']

WEBVIEW2_KEEP_ALIVE_ARGS = ' '.join(
    KEEP_ALIVE_FLAGS + ['--disable-features=' + ','.join(KEEP_ALIVE_FEATURES)])


def _aplicar_args_webview2():
    """Injeta as flags anti-throttling no processo do WebView2. Elas são lidas da
    variável de ambiente antes do CoreWebView2Environment nascer, por isso precisa
    ser chamada antes do webview.start()."""
    atual = os.environ.get('WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS', '').strip()
    os.environ['WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS'] = (
        (atual + ' ' + WEBVIEW2_KEEP_ALIVE_ARGS).strip() if atual else WEBVIEW2_KEEP_ALIVE_ARGS
    )


_JANELA_ATIVA = None
_BROWSER_PROC = None


class _AppApi:
    def __init__(self, get_window_fn):
        self._get_window = get_window_fn

    def close_window(self):
        """Chamado pelo JavaScript (/control) quando o operador clica em Parar."""
        win = self._get_window() if self._get_window else None
        if win:
            try:
                win.destroy()
            except Exception:
                pass


def fechar_janela_ativa():
    """Fecha a janela nativa ou o processo de navegador aberto pelo dock."""
    global _JANELA_ATIVA, _BROWSER_PROC
    if _JANELA_ATIVA:
        try:
            _JANELA_ATIVA.destroy()
        except Exception:
            pass
        _JANELA_ATIVA = None
    if _BROWSER_PROC:
        try:
            _BROWSER_PROC.terminate()
        except Exception:
            pass
        _BROWSER_PROC = None


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
    global _JANELA_ATIVA
    import webview
    _aplicar_args_webview2()
    api = _AppApi(lambda: _JANELA_ATIVA)
    janela = webview.create_window(
        title=JANELA_TITULO, url=url,
        width=JANELA_SIZE[0], height=JANELA_SIZE[1],
        min_size=JANELA_MIN,
        js_api=api,
    )
    _JANELA_ATIVA = janela

    def _ao_fechar_interno():
        global _JANELA_ATIVA
        _JANELA_ATIVA = None
        if ao_fechar:
            ao_fechar()

    janela.events.closed += _ao_fechar_interno
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
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        shutil.which("microsoft-edge") or "",
        shutil.which("google-chrome") or "",
        shutil.which("chromium") or "",
    ]
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return ""


def abrir_janela_navegador(url=SERVER_URL, ao_fechar=None):
    """Caminho legado (Edge/Chrome --app=): fallback quando não há backend nativo."""
    global _BROWSER_PROC
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
        '--disable-features=' + ','.join(['Translate'] + KEEP_ALIVE_FEATURES),
        "--app-id=rnp_to_obs_control",
        "--no-first-run",
        "--disable-default-apps",
        # Sem isto, minimizar o painel no fallback suspende o renderer e o audio
        # local para — o mesmo motivo que ja foi resolvido na rota nativa.
        *KEEP_ALIVE_FLAGS,
    ])
    _BROWSER_PROC = proc
    proc.wait()
    _BROWSER_PROC = None
    if ao_fechar:
        ao_fechar()


def launch_app_window(ao_fechar=None):
    """Janela nativa primeiro; qualquer falha de backend cai no navegador."""
    try:
        abrir_janela_pywebview(SERVER_URL, ao_fechar)
    except Exception:
        abrir_janela_navegador(SERVER_URL, ao_fechar)
