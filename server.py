import asyncio
import base64
import json
import os
import re
import sys
import time
import urllib.parse
from typing import Dict, List, Optional, Set
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, Response, FileResponse, RedirectResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Frame
import pytchat
import emoji

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
    sys.stdout = SafeLogWriter(os.path.join(log_dir, "server_stdout.log"))
elif hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

if sys.stderr is None or not hasattr(sys.stderr, 'isatty'):
    sys.stderr = SafeLogWriter(os.path.join(log_dir, "server_stderr.log"))
elif hasattr(sys.stderr, 'reconfigure'):
    try:
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass


def emojize_text(text: str) -> str:
    if not text:
        return ""
    try:
        return emoji.emojize(text, language='alias')
    except Exception:
        return text

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

app = FastAPI(title="RNP & YouTube to OBS Bridge Server")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DEFAULT_CONFIG = {
    "room_url": "https://conferenciaweb.rnp.br/ifes/ifes-cefor-01",
    "user_name": "OBS Studio",
    "user_email": "obs@obs.com",
    "youtube_url": "",
    "youtube_auto_connect": False,
    "control_theme": "hardware",
    "show_camera_names": True,
    "camera_name_style": "glass",
    "grid_layout_mode": "auto",
    "stage_preset": "side-by-side",
    "libras_size": "equal",
    "libras_auto_detect": True,
    "libras_saved_names": [],
    "poll_display_mode": "results_only",
    "poll_display_duration": 15,
    "poll_force_visible": False,
    "poll_style": "neon",
    "chat_style": "glass",
    "chat_font_size": "medium",
    "chat_max_messages": 6,
    "show_chat_avatars": True,
    "custom_camera_names": {},
    "libras_camera_ids": []
}

def load_disk_config() -> dict:
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                cfg = DEFAULT_CONFIG.copy()
                cfg.update(data)
                return cfg
        except Exception as e:
            print(f"[Config] Erro ao carregar config.json: {e}")
    return DEFAULT_CONFIG.copy()

def save_disk_config(cfg: dict):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=4, ensure_ascii=False)
        print(f"[Config] Configurações salvas com sucesso em {CONFIG_FILE}")
    except Exception as e:
        print(f"[Config] Erro ao salvar config.json: {e}")

def extract_youtube_video_id(url_or_id: str) -> str:
    if not url_or_id:
        return ""
    url_or_id = url_or_id.strip()
    match = re.search(r'(?:v=|\/|be\/|live\/)([0-9A-Za-z_-]{11})', url_or_id)
    if match:
        return match.group(1)
    if len(url_or_id) == 11 and re.match(r'^[0-9A-Za-z_-]{11}$', url_or_id):
        return url_or_id
    return url_or_id

class BridgeState:
    def __init__(self):
        cfg = load_disk_config()
        self.connected = False
        self.connecting = False
        self.status_message = "Pronto para conectar."
        self.room_url = cfg.get("room_url", "https://conferenciaweb.rnp.br/ifes/ifes-cefor-01")
        self.user_name = cfg.get("user_name", "OBS Studio")
        self.user_email = cfg.get("user_email", "obs@obs.com")
        self.youtube_url = cfg.get("youtube_url", "")
        self.youtube_auto_connect = bool(cfg.get("youtube_auto_connect", False))
        self.youtube_connected = False
        self.youtube_status = "Desconectado"
        self.control_theme = cfg.get("control_theme", "hardware")
        
        self.cameras_list: List[dict] = []
        self.chat_messages: List[dict] = []
        self.rnp_messages_map: Dict[str, dict] = {}
        self.youtube_messages_list: List[dict] = []
        self.featured_message: Optional[dict] = None
        self.poll_data: Optional[dict] = None
        self.last_active_poll: Optional[dict] = None
        self.published_poll_timestamp: float = 0
        self.poll_display_mode: str = cfg.get("poll_display_mode", "results_only")
        self.poll_display_duration: int = int(cfg.get("poll_display_duration", 15))
        self.poll_force_visible: bool = bool(cfg.get("poll_force_visible", False))
        self.poll_style: str = cfg.get("poll_style", "neon")
        self.poll_nonce: int = 0
        self.poll_dismissed: bool = False
        
        self.screenshare_active = False
        self.presentation_mode = "Nenhum"
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.bbb_frame: Optional[Frame] = None
        self.playwright = None
        self.latest_camera_frames: Dict[str, bytes] = {}
        self.latest_presentation_frame: Optional[bytes] = None
        self.pcm_clients: Set[WebSocket] = set()
        self.last_speaker_map: Dict[str, bool] = {}
        
        # Opções de Grid, Câmeras e Libras
        self.focused_camera_id: Optional[str] = None
        self.pinned_camera_ids: List[str] = []
        self.libras_camera_ids: List[str] = [str(x) for x in cfg.get("libras_camera_ids", [])]
        self.libras_disabled_camera_ids: Set[str] = set()
        self.libras_saved_names: List[str] = [str(x) for x in cfg.get("libras_saved_names", [])]
        self.libras_auto_detect: bool = cfg.get("libras_auto_detect", True)
        self.libras_size: str = cfg.get("libras_size", "equal")
        self.show_camera_names: bool = cfg.get("show_camera_names", True)
        self.camera_name_style: str = cfg.get("camera_name_style", "glass")
        self.grid_layout_mode: str = cfg.get("grid_layout_mode", "auto")
        self.stage_preset: str = cfg.get("stage_preset", "side-by-side")
        self.custom_camera_names: Dict[str, str] = cfg.get("custom_camera_names", {})

        # Opções de Estilização do Chat
        self.chat_style: str = cfg.get("chat_style", "glass")
        self.chat_font_size: str = cfg.get("chat_font_size", "medium")
        self.chat_max_messages: int = cfg.get("chat_max_messages", 6)
        self.show_chat_avatars: bool = cfg.get("show_chat_avatars", True)
        
        # Blindagem Anti-Inatividade
        self.inactivity_bypassed_count: int = 0
        self.last_keep_alive_time: float = 0
        
        self.sync_task: Optional[asyncio.Task] = None
        self.content_sync_task: Optional[asyncio.Task] = None
        self.keep_alive_task: Optional[asyncio.Task] = None
        self.youtube_task: Optional[asyncio.Task] = None

    def export_config(self) -> dict:
        return {
            "room_url": self.room_url,
            "user_name": self.user_name,
            "user_email": self.user_email,
            "youtube_url": self.youtube_url,
            "youtube_auto_connect": self.youtube_auto_connect,
            "control_theme": self.control_theme,
            "show_camera_names": self.show_camera_names,
            "camera_name_style": self.camera_name_style,
            "grid_layout_mode": self.grid_layout_mode,
            "stage_preset": self.stage_preset,
            "libras_size": self.libras_size,
            "libras_auto_detect": self.libras_auto_detect,
            "libras_saved_names": self.libras_saved_names,
            "poll_display_mode": self.poll_display_mode,
            "poll_display_duration": self.poll_display_duration,
            "poll_force_visible": self.poll_force_visible,
            "poll_style": self.poll_style,
            "chat_style": self.chat_style,
            "chat_font_size": self.chat_font_size,
            "chat_max_messages": self.chat_max_messages,
            "show_chat_avatars": self.show_chat_avatars,
            "custom_camera_names": self.custom_camera_names,
            "libras_camera_ids": self.libras_camera_ids
        }

state = BridgeState()

def log(msg: str):
    state.status_message = msg
    print(f"[Bridge] {msg}", flush=True)

def find_active_bbb_frame(page: Page) -> Frame:
    for f in page.frames:
        if "html5client" in f.url or "mconf" in f.url:
            return f
    return page.main_frame

# --------------------------------------------------------------------------
# YOUTUBE LIVE CHAT ENGINE (pytchat)
# --------------------------------------------------------------------------
async def start_youtube_chat(url_or_id: str):
    vid = extract_youtube_video_id(url_or_id)
    if not vid:
        state.youtube_status = "ID ou URL do YouTube inválido."
        state.youtube_connected = False
        return

    if state.youtube_task and not state.youtube_task.done():
        state.youtube_task.cancel()

    state.youtube_url = url_or_id.strip()
    save_disk_config(state.export_config())
    state.youtube_task = asyncio.create_task(youtube_chat_loop(vid))

async def stop_youtube_chat():
    state.youtube_connected = False
    state.youtube_status = "Desconectado"
    if state.youtube_task and not state.youtube_task.done():
        state.youtube_task.cancel()

async def youtube_chat_loop(video_id: str):
    state.youtube_connected = True
    state.youtube_status = f"Conectando ao YouTube ({video_id})..."
    print(f"[YouTube] Iniciando captura do chat para o vídeo {video_id}...")
    try:
        chat = await asyncio.to_thread(pytchat.create, video_id=video_id)
        state.youtube_status = f"Ao Vivo no YouTube ({video_id})"
        print(f"[YouTube] Conectado com sucesso ao chat da Live {video_id}!")

        while state.youtube_connected and chat.is_alive():
            data = await asyncio.to_thread(chat.get)
            items = data.sync_items()
            for c in items:
                t_str = ""
                if hasattr(c, 'datetime') and c.datetime:
                    parts = str(c.datetime).split()
                    t_str = parts[-1][:5] if len(parts) > 1 else str(c.datetime)[:5]
                else:
                    t_str = time.strftime("%H:%M")

                avatar_url = ""
                if hasattr(c, 'author') and hasattr(c.author, 'imageUrl'):
                    avatar_url = c.author.imageUrl or ""

                author_name = c.author.name if hasattr(c, 'author') and hasattr(c.author, 'name') else "Usuário YouTube"
                
                msg_text = ""
                if hasattr(c, 'messageEx') and isinstance(c.messageEx, list):
                    parts = []
                    for part in c.messageEx:
                        if isinstance(part, str):
                            parts.append(emojize_text(part))
                        elif isinstance(part, dict):
                            if 'url' in part and part['url']:
                                alt = part.get('txt', 'emoji')
                                parts.append(f'<img class="yt-custom-emoji" src="{part["url"]}" alt="{alt}" style="height:1.2em; vertical-align:middle; display:inline-block; margin:0 2px;">')
                            elif 'txt' in part:
                                parts.append(emojize_text(part['txt']))
                    msg_text = "".join(parts)
                else:
                    raw_msg = c.message if hasattr(c, 'message') else ""
                    msg_text = emojize_text(raw_msg)

                new_msg = {
                    "id": f"yt_{int(time.time()*1000)}_{len(state.youtube_messages_list)}",
                    "source": "youtube",
                    "user": author_name,
                    "text": msg_text,
                    "time": t_str,
                    "avatar": avatar_url,
                    "timestamp": time.time()
                }

                state.youtube_messages_list.append(new_msg)
                if len(state.youtube_messages_list) > 120:
                    state.youtube_messages_list = state.youtube_messages_list[-120:]

                # Merge com mensagens RNP ordenando cronologicamente
                all_msgs = list(state.rnp_messages_map.values()) + state.youtube_messages_list
                all_msgs.sort(key=lambda x: x.get('timestamp', 0))
                state.chat_messages = all_msgs[-120:]

            await asyncio.sleep(1.0)

    except asyncio.CancelledError:
        pass
    except Exception as e:
        state.youtube_status = f"Erro no YouTube: {e}"
        print(f"[YouTube] Erro no listener: {e}")
    finally:
        state.youtube_connected = False
        if state.youtube_status.startswith("Ao Vivo"):
            state.youtube_status = "Desconectado"

# --------------------------------------------------------------------------
# RNP BIGBLUEBUTTON ENGINE
# --------------------------------------------------------------------------
async def run_connection(room_url: str, user_name: str, user_email: str):
    if state.connected or state.connecting:
        await stop_connection()

    state.connecting = True
    state.connected = False
    state.room_url = room_url.strip()
    state.user_name = user_name.strip() if user_name.strip() else "OBS Studio"
    state.user_email = user_email.strip() if user_email.strip() else "obs@obs.com"

    save_disk_config(state.export_config())

    log("Iniciando Chromium headless...")

    try:
        state.playwright = await async_playwright().start()
        launch_args = [
            '--use-fake-ui-for-media-stream',
            '--autoplay-policy=no-user-gesture-required',
            '--disable-web-security',
            '--headless=new',
            # --- Aceleração de Hardware (GPU) & Baixa Latência ---
            '--enable-gpu-rasterization',
            '--enable-zero-copy',
            '--enable-accelerated-video-decode',
            '--enable-accelerated-mjpeg-decode',
            '--enable-features=WebRtcHWDecoding,WebRtcHWEncoding,CanvasOopRasterization',
            '--disable-frame-rate-limit',
            '--ignore-gpu-blocklist',
            '--use-gl=angle',
            '--use-angle=d3d11',
            # --- Performance & Memória ---
            '--disable-dev-shm-usage',
            '--disable-background-timer-throttling',
            '--disable-backgrounding-occluded-windows',
            '--disable-renderer-backgrounding',
            '--disable-ipc-flooding-protection',
            '--disable-hang-monitor',
            # --- Reduzir overhead visual do browser ---
            '--disable-smooth-scrolling',
            '--disable-default-apps',
            '--no-first-run',
            '--disable-extensions',
            '--disable-component-update',
            '--disable-breakpad',
            '--metrics-recording-only',
            '--mute-audio',
        ]

        browser_obj = None
        for ch in ["msedge", "chrome", None]:
            try:
                if ch:
                    browser_obj = await state.playwright.chromium.launch(headless=True, channel=ch, args=launch_args)
                else:
                    browser_obj = await state.playwright.chromium.launch(headless=True, args=launch_args)
                if browser_obj:
                    log(f"Chromium iniciado com sucesso via {ch if ch else 'playwright default'}!")
                    break
            except Exception:
                continue

        if not browser_obj:
            raise RuntimeError("Nenhum navegador compatível (Edge, Chrome ou Chromium) foi encontrado.")

        state.browser = browser_obj
        state.context = await state.browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            bypass_csp=True,
            java_script_enabled=True,
            ignore_https_errors=True
        )
        state.page = await state.context.new_page()

        log(f"Acessando sala RNP: {state.room_url} ...")
        await state.page.goto(state.room_url, timeout=60000)
        await asyncio.sleep(2)

        # 1. Botão Convidado
        try:
            guest_btn = state.page.locator('text="Entrar como convidado"')
            if await guest_btn.count() > 0 and await guest_btn.first.is_visible():
                log("Abrindo formulário de convidado...")
                await guest_btn.first.click()
                await asyncio.sleep(1)
        except Exception:
            pass

        # 2. Formulário Nome / Email
        try:
            name_input = state.page.locator('#user_name, input[name*="name"], input[placeholder*="nome"]')
            if await name_input.count() > 0 and await name_input.first.is_visible():
                log(f"Preenchendo identificação ({state.user_name})...")
                await name_input.first.fill(state.user_name)

            email_input = state.page.locator('#user_email, input[name*="email"], input[placeholder*="email"]')
            if await email_input.count() > 0 and await email_input.first.is_visible():
                await email_input.first.fill(state.user_email)
                await email_input.first.press('Enter')
                log("Formulário de convidado enviado.")
                await asyncio.sleep(3)
        except Exception:
            pass

        # 3. Confirmar entrada (com suporte a sala ainda fechada/aguardando moderador)
        try:
            final_enter = state.page.locator('input[value="Entrar"], button:has-text("Entrar"), a.btn:has-text("Entrar")')
            if await final_enter.count() > 0 and await final_enter.first.is_visible():
                # Verificar se o botão está desabilitado (ex: "A videoconferência ainda não começou")
                if await final_enter.first.is_disabled():
                    log("⏳ Sala ainda não começou. Aguardando o moderador iniciar a sessão...")
                    while state.connecting and await final_enter.first.is_disabled():
                        await asyncio.sleep(2)
                
                if not state.connecting:
                    return

                log("Confirmando entrada na sala...")
                await final_enter.first.click(timeout=10000)
        except Exception as e:
            log(f"Aviso na confirmação de entrada: {e}")

        # 4. Aguardar sessão BigBlueButton carregar
        log("Aguardando carregamento da sessão BigBlueButton...")
        target_frame = None
        for _ in range(45):
            if not state.connecting:
                return
            curr_frame = find_active_bbb_frame(state.page)
            try:
                indicators = curr_frame.locator('#layout, #app, [data-test="webcamVideoItem"], [data-test="listenOnlyBtn"], [aria-label="Somente ouvir"], [aria-label="Listen only"]')
                if await indicators.count() > 0:
                    target_frame = curr_frame
                    break
            except Exception:
                pass
            await asyncio.sleep(1)

        if not target_frame:
            target_frame = find_active_bbb_frame(state.page)
        state.bbb_frame = target_frame
        log("Conectado ao BigBlueButton!")

        # 5. Fechar modal Somente Ouvir
        try:
            listen_btn = target_frame.locator('[aria-label="Somente ouvir"], [aria-label="Listen only"], button:has-text("Somente ouvir"), [data-test="listenOnlyBtn"]')
            await listen_btn.wait_for(state="visible", timeout=20000)
            await listen_btn.click()
            log("Modo ouvinte ativado.")
        except Exception:
            pass

        # 6. Abrir chat público
        try:
            chat_btn = target_frame.locator('[aria-label="Bate-papo público"], [aria-label="Public Chat"], [data-test="publicChat"]')
            if await chat_btn.count() > 0:
                await chat_btn.first.click()
                log("Chat público aberto.")
        except Exception:
            pass

        # 7. Iniciar áudio PCM Float32
        log("Iniciando transmissão de áudio PCM de baixa latência...")
        await target_frame.evaluate('''() => {
            if (window._pcmCaptureActive) return;
            window._pcmCaptureActive = true;

            function startPcmCapture() {
                try {
                    const ctx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 48000 });
                    if (ctx.state === 'suspended') {
                        ctx.resume().catch(() => {});
                    }

                    const masterGain = ctx.createGain();
                    masterGain.gain.setValueAtTime(1.0, ctx.currentTime);

                    const proc = ctx.createScriptProcessor(2048, 1, 1);
                    const connectedStreams = new Set();

                    const ws = new WebSocket('ws://127.0.0.1:3333/ws/pcm_in');
                    ws.binaryType = 'arraybuffer';

                    proc.onaudioprocess = (e) => {
                        if (ws.readyState === WebSocket.OPEN) {
                            const input = e.inputBuffer.getChannelData(0);
                            ws.send(input.buffer);
                        }
                    };

                    masterGain.connect(proc);
                    proc.connect(ctx.destination);

                    function scanAndMixAudioSources() {
                        try {
                            const mediaElements = Array.from(document.querySelectorAll('audio, video'));
                            for (const el of mediaElements) {
                                if (el.srcObject && el.srcObject.getAudioTracks && el.srcObject.getAudioTracks().length > 0) {
                                    const tracks = el.srcObject.getAudioTracks();
                                    const activeTracks = tracks.filter(t => t.readyState === 'live');
                                    if (activeTracks.length === 0) continue;

                                    const streamId = el.srcObject.id || (el.id ? ('el_' + el.id) : ('track_' + activeTracks[0].id));
                                    if (!connectedStreams.has(streamId)) {
                                        try {
                                            const src = ctx.createMediaStreamSource(el.srcObject);
                                            src.connect(masterGain);
                                            connectedStreams.add(streamId);
                                            console.log('[RNP Audio Mixer] Conectado novo canal de áudio:', streamId, el.tagName);
                                        } catch (err) {
                                            // Pode já estar conectado ao AudioContext
                                        }
                                    }
                                }
                            }
                        } catch(e) {}
                    }

                    scanAndMixAudioSources();
                    const scanInterval = setInterval(scanAndMixAudioSources, 1500);

                    ws.onclose = () => {
                        try {
                            clearInterval(scanInterval);
                            proc.disconnect();
                            masterGain.disconnect();
                            ctx.close();
                        } catch(e) {}
                        window._pcmCaptureActive = false;
                        setTimeout(startPcmCapture, 2000);
                    };

                    ws.onerror = () => {
                        try { ws.close(); } catch(e) {}
                    };
                } catch(e) {
                    window._pcmCaptureActive = false;
                    setTimeout(startPcmCapture, 2000);
                }
            }
            startPcmCapture();
        }''')

        state.connected = True
        state.connecting = False
        log("Conectado à sala RNP! Sincronizando vídeo, apresentação, áudio, chat e enquetes...")

        if state.sync_task and not state.sync_task.done():
            state.sync_task.cancel()
        state.sync_task = asyncio.create_task(sync_loop())

        if state.content_sync_task and not state.content_sync_task.done():
            state.content_sync_task.cancel()

        if state.keep_alive_task and not state.keep_alive_task.done():
            state.keep_alive_task.cancel()
        state.keep_alive_task = asyncio.create_task(keep_alive_loop())

        if state.youtube_auto_connect and state.youtube_url:
            asyncio.create_task(start_youtube_chat(state.youtube_url))

    except Exception as e:
        state.connected = False
        state.connecting = False
        log(f"Erro na conexão: {e}")

async def stop_connection():
    log("Desconectando da sala...")
    state.connected = False
    state.connecting = False
    if state.sync_task and not state.sync_task.done():
        state.sync_task.cancel()
    if state.content_sync_task and not state.content_sync_task.done():
        state.content_sync_task.cancel()
    if state.keep_alive_task and not state.keep_alive_task.done():
        state.keep_alive_task.cancel()
    if state.browser:
        try:
            await state.browser.close()
        except Exception:
            pass
        state.browser = None
    if state.playwright:
        try:
            await state.playwright.stop()
        except Exception:
            pass
        state.playwright = None
    state.bbb_frame = None
    state.cameras_list.clear()
    state.latest_camera_frames.clear()
    state.latest_presentation_frame = None
    state.poll_data = None
    log("Desconectado.")

async def keep_alive_loop():
    while state.connected and state.page:
        try:
            target_frame = find_active_bbb_frame(state.page)
            await target_frame.evaluate('''() => {
                try {
                    const x = 200 + Math.floor(Math.random() * 300);
                    const y = 200 + Math.floor(Math.random() * 300);
                    window.dispatchEvent(new MouseEvent('mousemove', { clientX: x, clientY: y, bubbles: true }));
                    document.dispatchEvent(new MouseEvent('mousemove', { clientX: x, clientY: y, bubbles: true }));
                    window.dispatchEvent(new Event('focus'));
                } catch(e) {}
            }''')
            
            state.last_keep_alive_time = time.time()

            inactivity_selectors = [
                '[data-test="inactivityWarningConfirmBtn"]',
                '[data-test="confirmInactivity"]',
                'button:has-text("Continuar")',
                'button:has-text("Estou aqui")',
                'button:has-text("Sim")',
                'button:has-text("Sim, ainda estou aqui")',
                'button:has-text("I\'m still here")',
                'button:has-text("Stay in meeting")'
            ]

            for sel in inactivity_selectors:
                try:
                    btn = target_frame.locator(sel)
                    if await btn.count() > 0 and await btn.first.is_visible():
                        await btn.first.click()
                        state.inactivity_bypassed_count += 1
                        log(f"🛡️ Modal de inatividade da RNP interceptado e confirmado automaticamente ({state.inactivity_bypassed_count}x)!")
                        break
                except Exception:
                    pass
        except Exception:
            pass

        await asyncio.sleep(25)

async def sync_loop():
    last_slide_check = 0.0
    last_chat_check = 0.0
    last_poll_check = 0.0
    while state.connected and state.page:
        t_start = time.time()
        try:
            target_frame = find_active_bbb_frame(state.page)
            state.bbb_frame = target_frame

            # 1. Capturar todas as Webcams com aceleracao, canvas persistente e deteccao de audio/fala
            cam_data = await target_frame.evaluate('''() => {
                if (!window._camCanvasMap) window._camCanvasMap = {};
                const list = [];
                const seenVideos = new Set();

                // 🎙️ COLETAR NOMES DE QUEM ESTÁ FALANDO NOS TALKING INDICATORS (TOPO)
                const activeTalkerNames = new Set();
                const talkerSelectors = [
                    '[data-test="isTalking"]',
                    'button[data-test="isTalking"]',
                    '[data-test="talkingIndicator"] button',
                    '[data-test="talkingIndicator"]',
                    'div[class*="TalkingIndicatorButton"]',
                    'div[class*="IsTalkingWrapper"]',
                    'div[class*="talkingIndicator"]',
                    'div[class*="Speaking"]',
                    'div[class*="speaking"]',
                    '[class*="talkingUser"]',
                    '[class*="voiceUser"]',
                    '[aria-label*="falando" i]',
                    '[aria-label*="speaking" i]',
                    '[aria-label*="talking" i]'
                ];

                talkerSelectors.forEach(sel => {
                    try {
                        const els = document.querySelectorAll(sel);
                        els.forEach(el => {
                            const isWas = el.getAttribute('data-test') === 'wasTalking' || 
                                          (el.className && typeof el.className === 'string' && el.className.includes('wasTalking'));
                            if (isWas) return;
                            
                            let txt = (el.innerText || el.getAttribute('aria-label') || '').trim();
                            if (txt) {
                                txt = txt.replace(/falando\\s*:?/i, '')
                                         .replace(/speaking\\s*:?/i, '')
                                         .replace(/talking\\s*:?/i, '')
                                         .replace(/microfone\\s*:?/i, '')
                                         .trim();
                                if (txt.length >= 2) {
                                    txt.split('\\n').forEach(part => {
                                        const p = part.trim().toLowerCase();
                                        if (p && p.length >= 2 && !p.includes('opções') && !p.includes('abrir')) {
                                            activeTalkerNames.add(p);
                                        }
                                    });
                                }
                            }
                        });
                    } catch(e) {}
                });

                const videoElements = document.querySelectorAll('video');
                videoElements.forEach((video) => {
                    if (video.id === 'screenshareVideo' || video.closest('#screenshareContainer') || video.closest('[data-test="screenshareVideo"]')) {
                        return;
                    }
                    if (seenVideos.has(video)) return;
                    seenVideos.add(video);

                    const parent = video.closest('[data-test="webcamVideoItem"]') || video.closest('[data-test="webcamItem"]') || video.closest('div[class*="VideoListItem"]') || video.parentElement;
                    
                    let name = '';
                    if (parent) {
                        const dropdown = parent.querySelector('[data-test="dropdownWebcamButton"], [class*="dropdownWebcamButton"]');
                        if (dropdown && dropdown.innerText.trim()) {
                            name = dropdown.innerText.trim();
                        }

                        if (!name) {
                            const squeezedBtn = parent.querySelector('[data-test="webcamOptionsMenuSqueezed"], button[aria-label*="Opções de webcam"], button[aria-label*="Webcam options"]');
                            if (squeezedBtn && squeezedBtn.getAttribute('aria-label')) {
                                name = squeezedBtn.getAttribute('aria-label').replace(/Opções de webcam/i, '').replace(/Webcam options/i, '').trim();
                            }
                        }

                        if (!name) {
                            const anyAria = parent.querySelector('button[aria-label]');
                            if (anyAria && anyAria.getAttribute('aria-label') && !anyAria.getAttribute('aria-label').includes('Abrir')) {
                                name = anyAria.getAttribute('aria-label').replace(/Opções de webcam/i, '').replace(/Webcam options/i, '').trim();
                            }
                        }

                        if (!name) {
                            const spans = Array.from(parent.querySelectorAll('span, [class*="userName"], h2, p, div')).map(s => s.innerText.trim()).filter(Boolean);
                            for (let s of spans) {
                                if (s && !s.includes('Opções') && !s.includes('Abrir') && !s.includes('window') && s.length > 2) {
                                    name = s.split('\\n')[0].trim();
                                    break;
                                }
                            }
                        }
                    }

                    if (!name || name.length < 2) {
                        name = `Câmera ${list.length + 1}`;
                    }

                    // 🎙️ DETECÇÃO INDIVIDUAL DE STATUS DE ÁUDIO / FALA
                    let isMuted = false;
                    let isSpeaking = false;

                    if (parent) {
                        const parentClass = (parent.className && typeof parent.className === 'string') ? parent.className : '';
                        if (parentClass.match(/talking|speaking|isTalking|activeSpeaker|voiceActive/i)) {
                            isSpeaking = true;
                        }

                        const micEls = parent.querySelectorAll('button, svg, i, span, div[class*="mic"], div[class*="audio"], div[class*="voice"], div[class*="status"]');
                        micEls.forEach(m => {
                            const mClass = (m.className && typeof m.className === 'string' ? m.className : (m.className && m.className.baseVal ? m.className.baseVal : '')).toLowerCase();
                            const mAria = (m.getAttribute('aria-label') || m.getAttribute('title') || '').toLowerCase();
                            
                            if (mClass.includes('mute') || mClass.includes('audio_off') || mAria.includes('mudo') || mAria.includes('muted') || mAria.includes('desativado')) {
                                isMuted = true;
                            }
                            if (mClass.includes('speaking') || mClass.includes('talking') || mClass.includes('voiceactive') || mAria.includes('falando') || mAria.includes('speaking')) {
                                isSpeaking = true;
                            }
                        });

                        if (parent.querySelector('[data-test="isTalking"], [class*="speaking"], [class*="isTalking"]')) {
                            isSpeaking = true;
                        }
                    }

                    const camNameLower = name.toLowerCase().trim();
                    if (!isMuted && activeTalkerNames.size > 0) {
                        for (const tName of activeTalkerNames) {
                            if (camNameLower.includes(tName) || tName.includes(camNameLower)) {
                                isSpeaking = true;
                                break;
                            }
                            const tPrefix = tName.replace(/\\.+$/, '').trim();
                            if (tPrefix.length >= 4 && camNameLower.startsWith(tPrefix)) {
                                isSpeaking = true;
                                break;
                            }
                            const camParts = camNameLower.split(/\\s+/).filter(w => w.length > 2);
                            const tParts = tName.split(/\\s+/).filter(w => w.length > 2);
                            if (camParts.length > 0 && tParts.length > 0 && camParts[0] === tParts[0]) {
                                if (tParts.length === 1 || camParts.length === 1 || (camParts.length > 1 && tParts.length > 1 && camParts[1] === tParts[1])) {
                                    isSpeaking = true;
                                    break;
                                }
                            }
                        }
                    }

                    if (isMuted) {
                        isSpeaking = false;
                    }

                    const w = video.videoWidth || video.clientWidth || 903;
                    const h = video.videoHeight || video.clientHeight || 508;
                    
                    const camKey = 'cam_' + (list.length + 1);
                    let canvas = window._camCanvasMap[camKey];
                    if (!canvas) {
                        canvas = document.createElement('canvas');
                        window._camCanvasMap[camKey] = canvas;
                    }
                    if (canvas.width !== w || canvas.height !== h) {
                        canvas.width = w;
                        canvas.height = h;
                    }

                    const ctx = canvas.getContext('2d', { alpha: false, desynchronized: true });
                    ctx.drawImage(video, 0, 0, w, h);

                    list.push({
                        id: `${list.length + 1}`,
                        name: name,
                        width: w,
                        height: h,
                        frame: canvas.toDataURL('image/jpeg', 0.80).slice(23),
                        is_speaking: isSpeaking,
                        is_muted: isMuted
                    });
                });

                return list;
            }''')

            cams_info = []
            for cam in cam_data:
                frame_bytes = base64.b64decode(cam['frame'])
                state.latest_camera_frames[str(cam['id'])] = frame_bytes
                
                display_name = state.custom_camera_names.get(str(cam['id']), cam['name'])
                is_focus = (str(state.focused_camera_id) == str(cam['id']))
                is_pin = (str(cam['id']) in [str(x) for x in state.pinned_camera_ids])

                # 🤟 RECONHECIMENTO AUTOMÁTICO E PERSISTENTE DE LIBRAS
                cid_str = str(cam['id'])
                is_libras = (cid_str in [str(x) for x in state.libras_camera_ids])
                is_explicitly_disabled = (cid_str in [str(x) for x in state.libras_disabled_camera_ids])
                
                if not is_libras and not is_explicitly_disabled:
                    if state.libras_saved_names:
                        for s_name in state.libras_saved_names:
                            if s_name and (s_name.lower() in display_name.lower() or s_name.lower() in cam['name'].lower() or display_name.lower() in s_name.lower()):
                                is_libras = True
                                if cid_str not in [str(x) for x in state.libras_camera_ids]:
                                    state.libras_camera_ids.append(cid_str)
                                break

                    if not is_libras and state.libras_auto_detect:
                        keywords = ['libras', 'intérprete', 'interprete', 'tradutor', 'tradutora', 'ils']
                        name_lower = (display_name + " " + cam['name']).lower()
                        if any(kw in name_lower for kw in keywords):
                            is_libras = True
                            if cid_str not in [str(x) for x in state.libras_camera_ids]:
                                state.libras_camera_ids.append(cid_str)
                            if display_name not in state.libras_saved_names:
                                state.libras_saved_names.append(display_name)
                                save_disk_config(state.export_config())

                cams_info.append({
                    "id": str(cam['id']),
                    "name": display_name,
                    "original_name": cam['name'],
                    "width": cam['width'],
                    "height": cam['height'],
                    "is_focused": is_focus,
                    "is_pinned": is_pin,
                    "is_libras": is_libras,
                    "is_speaking": bool(cam.get('is_speaking', False)),
                    "is_muted": bool(cam.get('is_muted', False))
                })
            
            state.cameras_list = cams_info

            # 🎙️ Notificar clientes conectados via WebSocket sobre os oradores ativos em tempo real
            speaker_map = {str(c['id']): bool(c.get('is_speaking', False)) for c in cams_info}
            if speaker_map != state.last_speaker_map and state.pcm_clients:
                state.last_speaker_map = speaker_map
                msg_txt = json.dumps({"type": "speakers", "speakers": speaker_map})
                for client in list(state.pcm_clients):
                    try:
                        asyncio.create_task(client.send_text(msg_txt))
                    except Exception:
                        pass

            # 2. Capturar Apresentação (Screenshare rápido via Canvas)
            screen_data = await target_frame.evaluate('''() => {
                const vid = document.querySelector('#screenshareVideo, #screenshareContainer video, [data-test="screenshareVideo"]');
                if (vid && vid.videoWidth > 0) {
                    if (!window._screenCanvas) {
                        window._screenCanvas = document.createElement('canvas');
                    }
                    const canvas = window._screenCanvas;
                    const w = vid.videoWidth || 1920;
                    const h = vid.videoHeight || 1080;
                    if (canvas.width !== w || canvas.height !== h) {
                        canvas.width = w;
                        canvas.height = h;
                    }
                    const ctx = canvas.getContext('2d', { alpha: false, desynchronized: true });
                    ctx.drawImage(vid, 0, 0, w, h);
                    return { mode: 'screenshare', frame: canvas.toDataURL('image/jpeg', 0.80).slice(23) };
                }
                return null;
            }''')

            if screen_data:
                state.latest_presentation_frame = base64.b64decode(screen_data['frame'])
                state.screenshare_active = True
                state.presentation_mode = "Tela Compartilhada (2.5K/1080p)"
            else:
                now = time.time()
                if now - last_slide_check >= 1.0:
                    last_slide_check = now
                    ext_video_loc = target_frame.locator('[data-test="videoPlayer"], div[class*="videoPlayer"], [data-test="externalVideoPlayer"], #external-video-player, div[class*="externalVideo"]')
                    if await ext_video_loc.count() > 0 and await ext_video_loc.first.is_visible():
                        try:
                            vid_bytes = await ext_video_loc.first.screenshot(type="jpeg", quality=80)
                            state.latest_presentation_frame = vid_bytes
                            state.screenshare_active = True
                            state.presentation_mode = "Vídeo do YouTube (RNP)"
                        except Exception:
                            pass
                    else:
                        pres_loc = target_frame.locator('#presentationContainer, [data-test="presentationContainer"], [data-test="presentationArea"], [class*="presentationWrapper"]')
                        if await pres_loc.count() > 0 and await pres_loc.first.is_visible():
                            try:
                                slide_bytes = await pres_loc.first.screenshot(type="jpeg", quality=80)
                                state.latest_presentation_frame = slide_bytes
                                state.screenshare_active = False
                                state.presentation_mode = "Slides / Apresentação"
                            except Exception:
                                pass

            now_tick = time.time()

            # 3. Capturar Chat da RNP (a cada ~0.5s para economizar CPU)
            if now_tick - last_chat_check >= 0.5:
                last_chat_check = now_tick
                await extract_rnp_chat(target_frame)

            # 4. Capturar Enquete (a cada ~1.0s)
            if now_tick - last_poll_check >= 1.0:
                last_poll_check = now_tick
                await extract_rnp_poll(target_frame)

        except Exception:
            pass

        elapsed = time.time() - t_start
        sleep_time = max(0.015, 0.05 - elapsed)
        await asyncio.sleep(sleep_time)


# Aliases de compatibilidade
video_sync_loop = sync_loop
content_sync_loop = sync_loop


async def extract_rnp_chat(target_frame):
    try:
        # 3. Capturar Chat da RNP (com abertura automática da aba e múltiplos seletores BBB/Mconf)
        rnp_messages = await target_frame.evaluate(r'''() => {
                // 1. Garantir que a aba de bate-papo público está aberta no DOM do BBB
                const hasChatItems = document.querySelector('[data-test="chatMessageItem"], div.chat-message-container, [data-test="chatUserMessageText"], [data-test="chatMessageList"]');
                if (!hasChatItems) {
                    const chatOpeners = Array.from(document.querySelectorAll('button, div, a, [role="button"], [role="tab"]')).filter(el => {
                        const t = (el.innerText || '').toLowerCase();
                        const a = (el.getAttribute('aria-label') || '').toLowerCase();
                        const dt = (el.getAttribute('data-test') || '').toLowerCase();
                        return (t.includes('bate-papo público') || a.includes('bate-papo público') || dt === 'publicchat' || dt === 'chatbutton') && !a.includes('opções');
                    });
                    if (chatOpeners.length > 0) {
                        chatOpeners[0].click();
                    }
                }

                // 2. Extrair mensagens de chat com eliminação de nós aninhados duplicados
                function cleanUserName(raw) {
                    if (!raw) return '';
                    let cleaned = raw
                        .replace(/\(Você\)|\(You\)|\(Apresentador\)|\(Presenter\)|\(Moderador\)|\(Moderator\)/gi, '')
                        .replace(/\d{1,2}:\d{2}(:\d{2})?/g, '')
                        .replace(/[•|·:]/g, '')
                        .trim();
                    return (cleaned.length >= 2 && !cleaned.match(/^\d+$/)) ? cleaned : '';
                }

                // Coletar apenas os contêineres raiz de cada mensagem (elimina nós filhos duplicados como content e toolbar)
                const rawContainers = Array.from(document.querySelectorAll(
                    'div[data-test="chatMessageItem"], div.chat-message-container, div[class*="chatMessageItem"]'
                ));
                const containers = rawContainers.filter(c => !rawContainers.some(other => other !== c && other.contains(c)));

                const list = [];
                let lastUser = 'Participante';
                let lastTime = '';
                const userAvatars = {};

                containers.forEach((container, idx) => {
                    // 1. Filtrar avisos e notificações do sistema
                    const msgType = container.getAttribute('data-message-type') || '';
                    const isSystem = msgType.includes('userIsPresenter') || 
                                     msgType === 'system' ||
                                     container.classList.contains('system-message') ||
                                     container.querySelector('[data-test="systemMessageText"]') ||
                                     container.querySelector('[class*="systemMessage"]');

                    if (isSystem) {
                        return;
                    }

                    // 2. Extrair Horário
                    const timeEl = container.querySelector('time, span[class*="time"], [data-test="chatMessageTime"]');
                    if (timeEl && timeEl.innerText.trim()) {
                        lastTime = timeEl.innerText.trim();
                    }

                    // 3. Extrair Nome do Remetente (Identifica se é novo cluster)
                    let rawUser = '';

                    // Método A: Cabeçalho pai do elemento time (no BBB o nome e o <time> ficam juntos no cabeçalho)
                    if (timeEl && timeEl.parentElement) {
                        const headerClone = timeEl.parentElement.cloneNode(true);
                        headerClone.querySelectorAll('time, button, svg, [class*="isYou"], [class*="badge"], span[class*="time"]').forEach(e => e.remove());
                        const parsed = cleanUserName(headerClone.innerText);
                        if (parsed) rawUser = parsed;

                        if (!rawUser) {
                            const candidate = Array.from(timeEl.parentElement.children).find(
                                child => child !== timeEl && !child.contains(timeEl) && cleanUserName(child.innerText)
                            );
                            if (candidate) rawUser = cleanUserName(candidate.innerText);
                        }
                    }

                    // Método B: data-test específico
                    if (!rawUser) {
                        const bbbSender = container.querySelector('[data-test="chatUserMessageSender"], [data-test="chatMessageUserName"]');
                        if (bbbSender) {
                            const clone = bbbSender.cloneNode(true);
                            clone.querySelectorAll('time, button, svg, [class*="isYou"], [class*="badge"]').forEach(e => e.remove());
                            const parsed = cleanUserName(clone.innerText);
                            if (parsed) rawUser = parsed;
                        }
                    }

                    // Método C: classes de nome ou cabeçalho
                    if (!rawUser) {
                        const nameEl = container.querySelector('span[class*="userName"], div[class*="userName"], span[class*="sender"], [class*="sender"], h4, h5, strong');
                        if (nameEl) {
                            const clone = nameEl.cloneNode(true);
                            clone.querySelectorAll('time, button, svg, [class*="isYou"], [class*="badge"]').forEach(e => e.remove());
                            const parsed = cleanUserName(clone.innerText);
                            if (parsed) rawUser = parsed;
                        }
                    }

                    // Método D: aria-label
                    if (!rawUser) {
                        const aria = container.getAttribute('aria-label') || (container.parentElement ? container.parentElement.getAttribute('aria-label') : '') || '';
                        if (aria && (aria.includes(' disse') || aria.includes(' said') || aria.includes('Mensagem de') || aria.includes('Message from') || aria.includes(':'))) {
                            const mMatch = aria.match(/(?:Mensagem de |Message from )?([^:]+?)(?: disse| said|:)/i);
                            if (mMatch) {
                                const parsed = cleanUserName(mMatch[1]);
                                if (parsed) rawUser = parsed;
                            }
                        }
                    }

                    let isNewCluster = false;
                    if (rawUser) {
                        lastUser = rawUser;
                        isNewCluster = true;
                    }

                    // 4. Extrair Avatar com isolamento estrito por usuário
                    const avatarContainer = container.querySelector('[data-test*="Avatar" i], [data-test*="avatar" i], div[class*="avatar" i], div[class*="Avatar" i]');
                    let avatarImg = null;
                    if (avatarContainer) {
                        avatarImg = avatarContainer.querySelector('img');
                    }
                    if (!avatarImg) {
                        avatarImg = container.querySelector('img[class*="avatar" i], img[class*="Avatar" i]');
                    }

                    let currentAvatar = '';
                    const imgSrc = (avatarImg && avatarImg.src) ? avatarImg.src.trim() : '';
                    const isRealPhoto = imgSrc && 
                                        imgSrc.startsWith('http') && 
                                        !imgSrc.startsWith('data:image/svg') && 
                                        !imgSrc.includes('default-avatar') && 
                                        !imgSrc.includes('default_avatar') &&
                                        !imgSrc.includes('avatar-blank') &&
                                        !imgSrc.includes('avatar-fallback');

                    if (isRealPhoto) {
                        currentAvatar = imgSrc;
                        userAvatars[lastUser] = currentAvatar;
                    } else if (isNewCluster) {
                        // Novo cabeçalho de usuário encontrado, mas SEM imagem válida -> participante não tem foto
                        userAvatars[lastUser] = '';
                        currentAvatar = '';
                    } else {
                        // Continuação de mensagem do mesmo usuário no mesmo cluster
                        currentAvatar = userAvatars[lastUser] || '';
                    }

                    // 5. Extrair Conteúdo do Texto
                    const contentEl = container.querySelector(
                        '[data-test="messageContent"], [data-test="chatMessageContent"], [data-test="chatUserMessageText"], div.chat-message-content, div[class*="messageText"], div[class*="content"], div[class*="Text"], p'
                    );

                    if (contentEl) {
                        const clone = contentEl.cloneNode(true);
                        clone.querySelectorAll('.chat-message-toolbar, [data-test="chatMessageToolbar"], button, time, i, svg').forEach(x => x.remove());
                        const cleanText = clone.innerText.trim();

                        const isAutoNotice = cleanText.startsWith('Bem-vindo à') || 
                                             cleanText.includes('gravação desta sessão') || 
                                             cleanText.includes('é agora o apresentador') ||
                                             cleanText.includes('Resultados da enquete') ||
                                             cleanText.match(/^[A-Z]: \d+ voto/);

                        if (cleanText && !isAutoNotice) {
                            const msgId = container.querySelector('[data-chat-message-id]')?.getAttribute('data-chat-message-id') || 
                                          container.getAttribute('data-chat-message-id') || 
                                          container.getAttribute('data-sequence') || 
                                          `msg_${idx}`;

                            list.push({
                                id: `rnp_${msgId}`,
                                source: 'rnp',
                                user: lastUser,
                                text: cleanText,
                                time: lastTime,
                                avatar: currentAvatar || ''
                            });
                        }
                    }
                });

                return list;
            }''')

        clean_rnp_messages = []
        for m in rnp_messages:
            m['text'] = emojize_text(m['text'])
            clean_rnp_messages.append(m)
            
            # Se a mensagem possui remetente identificado, purgar qualquer versão antiga com 'Participante'
            if m['user'] != 'Participante':
                for k in list(state.rnp_messages_map.keys()):
                    item = state.rnp_messages_map[k]
                    if item.get('user') == 'Participante' and item.get('text') == m['text']:
                        m['timestamp'] = item.get('timestamp', time.time())
                        del state.rnp_messages_map[k]

            # Isolamento estrito de avatar por usuário
            user_name = m.get('user')
            if user_name and user_name != 'Participante':
                m_avatar = m.get('avatar', '')
                if m_avatar:
                    # Se este usuário possui foto confirmada, atualizar mensagens dele que possam estar sem foto
                    for prev_m in state.rnp_messages_map.values():
                        if prev_m.get('source') == 'rnp' and prev_m.get('user') == user_name and not prev_m.get('avatar'):
                            prev_m['avatar'] = m_avatar
                else:
                    # Se este usuário NÃO possui foto, limpar qualquer contaminação anterior nas mensagens dele
                    for prev_m in state.rnp_messages_map.values():
                        if prev_m.get('source') == 'rnp' and prev_m.get('user') == user_name and prev_m.get('avatar'):
                            prev_m['avatar'] = ''

            msg_key = m.get('id') or f"{m['user']}___{m['text']}___{m['time']}"
            if msg_key not in state.rnp_messages_map:
                if 'timestamp' not in m:
                    m['timestamp'] = time.time()
                state.rnp_messages_map[msg_key] = m
            else:
                curr = state.rnp_messages_map[msg_key]
                if m.get('user') and m['user'] != 'Participante':
                    curr['user'] = m['user']
                if 'avatar' in m:
                    curr['avatar'] = m['avatar']
                if m.get('time'):
                    curr['time'] = m['time']

        # Merge e ordenação cronológica
        all_msgs = list(state.rnp_messages_map.values()) + state.youtube_messages_list
        all_msgs.sort(key=lambda x: x.get('timestamp', 0))
        state.chat_messages = all_msgs[-120:]
    except Exception:
        pass


async def extract_rnp_poll(target_frame):
    try:
        # 4. Capturar Enquete Ativa ou Publicada
        poll_info = await target_frame.evaluate('''() => {
                // Auto-fechar qualquer modal de aviso de áudio que possa sobrepor a tela
                const audioDismissBtns = Array.from(document.querySelectorAll('button')).filter(b => {
                    const t = (b.innerText || '').toLowerCase();
                    const a = (b.getAttribute('aria-label') || '').toLowerCase();
                    return t.includes('somente ouvir') || a.includes('somente ouvir') || t.includes('listen only') || a.includes('listen only');
                });
                if (audioDismissBtns.length > 0) {
                    audioDismissBtns[0].click();
                }

                // 1. Modal de Votação Ativo (Procura por ID, data-test ou texto característico)
                let pollContainer = document.querySelector('#polling-container, [data-test="pollingContainer"], [data-test="pollingModal"]');
                if (!pollContainer) {
                    pollContainer = Array.from(document.querySelectorAll('aside, div, section')).find(el => {
                        const t = el.innerText || '';
                        return (t.includes('Opções da enquete') || t.includes('Pergunta da enquete') || t.includes('Abstenção') || (t.includes('Sim') && t.includes('Não'))) && el.querySelectorAll('button').length > 0;
                    });
                }

                if (pollContainer) {
                    let question = '';
                    const qEl = pollContainer.querySelector('h1, h2, h3, p, [data-test="pollQuestion"], [class*="pollQuestion"]');
                    if (qEl && qEl.innerText.trim()) {
                        question = qEl.innerText.trim();
                    }
                    if (!question) question = 'Opções da Enquete';

                    const options = [];
                    const btns = Array.from(pollContainer.querySelectorAll('button[data-test="pollAnswerOption"], button[class*="pollAnswer"], button'));
                    btns.forEach((btn, idx) => {
                        const txt = btn.innerText.trim();
                        if (txt && !txt.includes('Fechar') && !txt.includes('Close') && !txt.includes('Ocultar') && txt.length < 60) {
                            const cleanTxt = txt.split('\\n')[0].trim();
                            if (cleanTxt && !options.find(o => o.text.toLowerCase() === cleanTxt.toLowerCase())) {
                                options.push({
                                    id: options.length,
                                    text: cleanTxt,
                                    votes: 0,
                                    percentage: 0
                                });
                            }
                        }
                    });

                    if (options.length > 0) {
                        return {
                            is_active: true,
                            status: 'voting',
                            question: question,
                            options: options,
                            total_votes: 0,
                            published: false
                        };
                    }
                }

                // 2. Resultados no Chat
                const chatPolls = Array.from(document.querySelectorAll('div[data-message-type="poll"], [data-test="pollResultItem"], [data-test="pollResultMsg"]'));
                if (chatPolls.length > 0) {
                    const lastPoll = chatPolls[chatPolls.length - 1];
                    const rawText = lastPoll.innerText || '';
                    const lines = rawText.split('\\n').map(l => l.trim()).filter(Boolean);
                    
                    let question = 'Resultados da Enquete';
                    let options = [];
                    let totalVotes = 0;

                    for (let line of lines) {
                        if (line.includes('Resultados da enquete') || line.includes('Resultados') || line.includes('Enquete')) continue;
                        
                        const parts = line.split(/—|:|-/);
                        if (parts.length >= 2) {
                            const label = parts[0].trim();
                            const numMatch = parts[1].match(/\\d+/);
                            if (numMatch && label.length > 1 && !label.includes('Atualização') && !label.includes('http') && !label.match(/^\\d+$/)) {
                                const v = parseInt(numMatch[0]) || 0;
                                const existing = options.find(o => o.text.toLowerCase() === label.toLowerCase());
                                if (!existing) {
                                    options.push({
                                        id: options.length,
                                        text: label,
                                        votes: v,
                                        percentage: 0
                                    });
                                    totalVotes += v;
                                }
                            }
                        } else {
                            const mParen = line.match(/^([^\\d]+)\\s*\\((\\d+)\\)/);
                            if (mParen) {
                                const label = mParen[1].trim();
                                const v = parseInt(mParen[2]) || 0;
                                const existing = options.find(o => o.text.toLowerCase() === label.toLowerCase());
                                if (!existing && label.length > 1) {
                                    options.push({
                                        id: options.length,
                                        text: label,
                                        votes: v,
                                        percentage: 0
                                    });
                                    totalVotes += v;
                                }
                            } else if (options.length === 0 && line.length > 3 && !line.match(/^\\d+:\\d+$/)) {
                                question = line;
                            }
                        }
                    }

                    if (options.length > 0) {
                        options.forEach(o => {
                            o.percentage = totalVotes > 0 ? Math.round((o.votes / totalVotes) * 100) : 0;
                        });

                        return {
                            is_active: true,
                            status: 'published',
                            question: question,
                            options: options,
                            total_votes: totalVotes,
                            published: true
                        };
                    }
                }

                // 3. Anotações no Whiteboard / Slide
                const wbTexts = Array.from(document.querySelectorAll('#whiteboard text, [data-test="whiteboard"] text, svg[class*="whiteboard"] text, g[shape*="poll"] text'));
                if (wbTexts.length > 0) {
                    const textLines = wbTexts.map(t => t.textContent.trim()).filter(Boolean);
                    let q = 'Resultados da Enquete';
                    let opts = [];
                    let tot = 0;

                    for (let line of textLines) {
                        const parts = line.split(/—|:|-/);
                        if (parts.length >= 2) {
                            const label = parts[0].trim();
                            const numMatch = parts[1].match(/\\d+/);
                            if (numMatch && label.length >= 1 && !label.includes('http')) {
                                const v = parseInt(numMatch[0]) || 0;
                                const existing = opts.find(o => o.text.toLowerCase() === label.toLowerCase());
                                if (!existing) {
                                    opts.push({ id: opts.length, text: label, votes: v, percentage: 0 });
                                    tot += v;
                                }
                            }
                        }
                    }

                    if (opts.length > 0) {
                        opts.forEach(o => {
                            o.percentage = tot > 0 ? Math.round((o.votes / tot) * 100) : 0;
                        });
                        return {
                            is_active: true,
                            status: 'published',
                            question: q,
                            options: opts,
                            total_votes: tot,
                            published: true
                        };
                    }
                }

                return null;
            }''')

        if poll_info:
            if not state.poll_data or state.poll_data.get('question') != poll_info.get('question'):
                state.poll_dismissed = False
            state.last_active_poll = poll_info
            state.poll_data = poll_info
        else:
            # Se não há enquete aberta agora, mas havia uma em votação que acabou de fechar:
            if state.last_active_poll and state.last_active_poll.get('status') == 'voting':
                state.last_active_poll['status'] = 'published'
                state.last_active_poll['published'] = True
                state.last_active_poll['published_at'] = time.time()
                state.poll_data = state.last_active_poll
                state.published_poll_timestamp = time.time()
                state.last_active_poll = None
    except Exception:
        pass

# --------------------------------------------------------------------------
# WEBSOCKET PCM AUDIO STREAMING
# --------------------------------------------------------------------------
@app.websocket("/ws/pcm_in")
async def ws_pcm_in(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_bytes()
            disconnected = []
            for client in list(state.pcm_clients):
                try:
                    await client.send_bytes(data)
                except Exception:
                    disconnected.append(client)
            for d in disconnected:
                state.pcm_clients.discard(d)
    except (WebSocketDisconnect, Exception):
        pass

@app.websocket("/ws/pcm_out")
async def ws_pcm_out(websocket: WebSocket):
    await websocket.accept()
    state.pcm_clients.add(websocket)
    try:
        while True:
            await websocket.receive_text()
    except (WebSocketDisconnect, Exception):
        state.pcm_clients.discard(websocket)

# --------------------------------------------------------------------------
# API REST
# --------------------------------------------------------------------------
# ÍCONES E CONTROLE DO SERVIDOR
# --------------------------------------------------------------------------
@app.get("/favicon.ico")
async def get_favicon():
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app_icon.ico")
    if os.path.exists(p):
        return FileResponse(p, media_type="image/x-icon")
    return Response(status_code=204)

@app.get("/app_icon.png")
async def get_app_icon_png():
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app_icon.png")
    if os.path.exists(p):
        return FileResponse(p, media_type="image/png")
    return Response(status_code=204)

@app.post("/api/server/stop")
async def api_server_stop():
    async def _delayed_stop():
        await asyncio.sleep(0.5)
        await stop_connection()
        os._exit(0)
    asyncio.create_task(_delayed_stop())
    return JSONResponse({"status": "shutting_down", "message": "Servidor finalizado com sucesso."})

@app.post("/api/connect")
async def api_connect(req: Request):
    data = await req.json()
    room_url = data.get("room_url", state.room_url)
    user_name = data.get("user_name", state.user_name)
    user_email = data.get("user_email", state.user_email)
    asyncio.create_task(run_connection(room_url, user_name, user_email))
    return JSONResponse({"status": "connecting", "message": "Iniciando conexão com a sala..."})

@app.post("/api/disconnect")
async def api_disconnect():
    await stop_connection()
    return JSONResponse({"status": "disconnected"})

@app.post("/api/youtube/connect")
async def api_youtube_connect(req: Request):
    data = await req.json()
    yt_url = data.get("youtube_url", "")
    await start_youtube_chat(yt_url)
    return JSONResponse({"status": "ok", "youtube_status": state.youtube_status})

@app.post("/api/youtube/disconnect")
async def api_youtube_disconnect():
    await stop_youtube_chat()
    return JSONResponse({"status": "disconnected", "youtube_status": state.youtube_status})

@app.post("/api/chat/highlight")
async def api_chat_highlight(req: Request):
    data = await req.json()
    msg = data.get("message")
    if msg and isinstance(msg, dict) and "text" in msg:
        msg["text"] = emojize_text(msg["text"])
    state.featured_message = msg
    return JSONResponse({"status": "ok", "featured_message": state.featured_message})

@app.post("/api/poll/show")
async def api_poll_show():
    state.poll_force_visible = True
    state.poll_dismissed = False
    state.poll_nonce += 1
    return JSONResponse({"status": "shown", "poll_nonce": state.poll_nonce})

@app.post("/api/poll/hide")
async def api_poll_hide():
    state.poll_force_visible = False
    state.poll_dismissed = True
    return JSONResponse({"status": "hidden"})

@app.post("/api/poll/clear")
async def api_poll_clear():
    state.poll_data = None
    state.last_active_poll = None
    state.published_poll_timestamp = 0
    state.poll_force_visible = False
    state.poll_dismissed = True
    state.poll_nonce += 1
    return JSONResponse({"status": "cleared"})

@app.post("/api/poll/simulate")
async def api_poll_simulate():
    state.poll_data = {
        "question": "Exemplo: Você está gostando da transmissão ao vivo?",
        "options": [
            {"text": "Sim, excelente qualidade!", "votes": 42, "percentage": 70},
            {"text": "Sim, mas pode melhorar o áudio", "votes": 12, "percentage": 20},
            {"text": "Ainda avaliando", "votes": 6, "percentage": 10}
        ],
        "total_votes": 60,
        "is_active": True,
        "status": "results",
        "published": True
    }
    state.poll_force_visible = True
    state.poll_dismissed = False
    state.poll_nonce += 1
    return JSONResponse({"status": "simulated", "poll": state.poll_data})

@app.get("/api/poll")
async def api_poll():
    return JSONResponse({
        "poll": state.poll_data,
        "poll_display_mode": state.poll_display_mode,
        "poll_display_duration": state.poll_display_duration,
        "poll_force_visible": state.poll_force_visible,
        "poll_style": state.poll_style,
        "poll_nonce": state.poll_nonce,
        "poll_dismissed": state.poll_dismissed
    }, headers={"Cache-Control": "no-store, no-cache"})

@app.post("/api/poll/config")
async def api_poll_config(req: Request):
    data = await req.json()
    if "poll_display_mode" in data:
        state.poll_display_mode = data["poll_display_mode"]
        if state.poll_display_mode != "hidden":
            state.poll_dismissed = False
    if "poll_display_duration" in data:
        state.poll_display_duration = int(data["poll_display_duration"])
    if "poll_force_visible" in data:
        state.poll_force_visible = bool(data["poll_force_visible"])
        if state.poll_force_visible:
            state.poll_dismissed = False
            state.poll_nonce += 1
        else:
            state.poll_dismissed = True
    if "poll_style" in data:
        state.poll_style = data["poll_style"]
    
    save_disk_config(state.export_config())
    return JSONResponse({"status": "ok"})

@app.get("/api/debug/screenshot")
async def api_debug_screenshot():
    if state.page:
        try:
            img = await state.page.screenshot(type="jpeg", quality=80)
            return Response(content=img, media_type="image/jpeg", headers={"Cache-Control": "no-store"})
        except Exception as e:
            return JSONResponse({"error": str(e)})
    return JSONResponse({"error": "no_page"})

@app.post("/api/grid/config")
async def api_grid_config(req: Request):
    data = await req.json()
    if "focused_camera_id" in data:
        state.focused_camera_id = str(data["focused_camera_id"]) if data["focused_camera_id"] is not None else None
    if "pinned_camera_ids" in data:
        state.pinned_camera_ids = [str(x) for x in data["pinned_camera_ids"]]
    if "libras_camera_ids" in data:
        new_libras_ids = [str(x) for x in data["libras_camera_ids"]]
        old_libras_ids = list(state.libras_camera_ids)
        
        # Câmeras que foram DESMARCADAS manualmente
        removed_ids = [cid for cid in old_libras_ids if cid not in new_libras_ids]
        for rid in removed_ids:
            state.libras_disabled_camera_ids.add(str(rid))
            # Remover o nome de saved_names para não reativar
            for cam in state.cameras_list:
                if str(cam.get('id')) == str(rid):
                    cam_name = cam.get('name', '').strip()
                    orig_name = cam.get('original_name', '').strip()
                    state.libras_saved_names = [n for n in state.libras_saved_names if n.lower() not in (cam_name.lower(), orig_name.lower())]

        # Câmeras que foram MARCADAS manualmente
        added_ids = [cid for cid in new_libras_ids if cid not in old_libras_ids]
        for aid in added_ids:
            state.libras_disabled_camera_ids.discard(str(aid))
            for cam in state.cameras_list:
                if str(cam.get('id')) == str(aid):
                    if cam.get('name') and cam['name'] not in state.libras_saved_names:
                        state.libras_saved_names.append(cam['name'])
                    if cam.get('original_name') and cam['original_name'] not in state.libras_saved_names:
                        state.libras_saved_names.append(cam['original_name'])

        state.libras_camera_ids = new_libras_ids
        for cam in state.cameras_list:
            cam['is_libras'] = (str(cam.get('id')) in state.libras_camera_ids)
    if "libras_size" in data:
        state.libras_size = data["libras_size"]
    if "show_camera_names" in data:
        state.show_camera_names = bool(data["show_camera_names"])
    if "camera_name_style" in data:
        state.camera_name_style = data["camera_name_style"]
    if "grid_layout_mode" in data:
        state.grid_layout_mode = data["grid_layout_mode"]
    if "stage_preset" in data:
        state.stage_preset = data["stage_preset"]
    if "custom_name" in data and "cam_id" in data:
        c_name = data["custom_name"].strip()
        cid_str = str(data["cam_id"])
        if c_name:
            state.custom_camera_names[cid_str] = c_name
            if cid_str in state.libras_camera_ids:
                if c_name not in state.libras_saved_names:
                    state.libras_saved_names.append(c_name)
        else:
            # Apaga a personalização e volta para o nome padrão da RNP!
            state.custom_camera_names.pop(cid_str, None)
    
    save_disk_config(state.export_config())
    return JSONResponse({"status": "ok", "libras_camera_ids": state.libras_camera_ids, "libras_saved_names": state.libras_saved_names})

@app.post("/api/libras/toggle_auto")
async def api_libras_toggle_auto():
    state.libras_auto_detect = not state.libras_auto_detect
    save_disk_config(state.export_config())
    return JSONResponse({"status": "ok", "libras_auto_detect": state.libras_auto_detect})

@app.post("/api/libras/clear_saved")
async def api_libras_clear_saved():
    state.libras_saved_names = []
    state.libras_camera_ids = []
    state.libras_disabled_camera_ids = set()
    for cam in state.cameras_list:
        cam['is_libras'] = False
    save_disk_config(state.export_config())
    return JSONResponse({"status": "ok", "libras_saved_names": [], "libras_camera_ids": []})

@app.post("/api/chat/config")
async def api_chat_config(req: Request):
    data = await req.json()
    if "chat_style" in data:
        state.chat_style = data["chat_style"]
    if "chat_font_size" in data:
        state.chat_font_size = data["chat_font_size"]
    if "chat_max_messages" in data:
        state.chat_max_messages = int(data["chat_max_messages"])
    if "show_chat_avatars" in data:
        state.show_chat_avatars = bool(data["show_chat_avatars"])
    
    save_disk_config(state.export_config())
    return JSONResponse({"status": "ok", "show_chat_avatars": state.show_chat_avatars})

@app.post("/api/theme/set")
async def api_theme_set(req: Request):
    data = await req.json()
    theme = data.get("theme", "hardware")
    if theme in ["hardware", "nordic", "glass", "obsidian", "ifes"]:
        state.control_theme = theme
        save_disk_config(state.export_config())
        return JSONResponse({"status": "ok", "control_theme": state.control_theme})
    return JSONResponse({"status": "error", "message": "Tema inválido"}, status_code=400)

@app.post("/api/config/save")
async def api_config_save(req: Request):
    data = await req.json()
    if "room_url" in data: state.room_url = data["room_url"].strip()
    if "user_name" in data: state.user_name = data["user_name"].strip()
    if "user_email" in data: state.user_email = data["user_email"].strip()
    if "youtube_url" in data: state.youtube_url = data["youtube_url"].strip()
    if "youtube_auto_connect" in data: state.youtube_auto_connect = bool(data["youtube_auto_connect"])
    if "control_theme" in data: state.control_theme = data["control_theme"]
    if "show_camera_names" in data: state.show_camera_names = bool(data["show_camera_names"])
    if "camera_name_style" in data: state.camera_name_style = data["camera_name_style"]
    if "grid_layout_mode" in data: state.grid_layout_mode = data["grid_layout_mode"]
    if "stage_preset" in data: state.stage_preset = data["stage_preset"]
    if "libras_size" in data: state.libras_size = data["libras_size"]
    if "libras_saved_names" in data: state.libras_saved_names = [str(x) for x in data["libras_saved_names"]]
    if "poll_display_mode" in data: state.poll_display_mode = data["poll_display_mode"]
    if "poll_display_duration" in data: state.poll_display_duration = int(data["poll_display_duration"])
    if "poll_force_visible" in data: state.poll_force_visible = bool(data["poll_force_visible"])
    if "chat_style" in data: state.chat_style = data["chat_style"]
    if "chat_font_size" in data: state.chat_font_size = data["chat_font_size"]
    if "chat_max_messages" in data: state.chat_max_messages = int(data["chat_max_messages"])
    if "show_chat_avatars" in data: state.show_chat_avatars = bool(data["show_chat_avatars"])
    if "custom_camera_names" in data: state.custom_camera_names = data["custom_camera_names"]
    if "libras_camera_ids" in data: state.libras_camera_ids = [str(x) for x in data["libras_camera_ids"]]

    save_disk_config(state.export_config())
    return JSONResponse({"status": "saved", "config": state.export_config()})

@app.post("/api/config/reset")
async def api_config_reset():
    cfg = DEFAULT_CONFIG.copy()
    state.room_url = cfg["room_url"]
    state.user_name = cfg["user_name"]
    state.user_email = cfg["user_email"]
    state.youtube_url = cfg["youtube_url"]
    state.show_camera_names = cfg["show_camera_names"]
    state.camera_name_style = cfg["camera_name_style"]
    state.grid_layout_mode = cfg["grid_layout_mode"]
    state.stage_preset = cfg["stage_preset"]
    state.libras_size = cfg["libras_size"]
    state.libras_saved_names = []
    state.poll_display_mode = "results_only"
    state.poll_display_duration = 15
    state.poll_force_visible = False
    state.chat_style = cfg["chat_style"]
    state.chat_font_size = cfg["chat_font_size"]
    state.chat_max_messages = cfg["chat_max_messages"]
    state.show_chat_avatars = cfg.get("show_chat_avatars", True)
    state.custom_camera_names = {}
    state.libras_camera_ids = []
    save_disk_config(cfg)
    return JSONResponse({"status": "reset", "config": cfg})

@app.get("/api/config")
async def api_config_get():
    return JSONResponse(state.export_config(), headers={"Cache-Control": "no-store, no-cache"})

@app.get("/api/status")
async def api_status():
    return JSONResponse(
        {
            "connected": state.connected,
            "connecting": state.connecting,
            "status_message": state.status_message,
            "room_url": state.room_url,
            "user_name": state.user_name,
            "user_email": state.user_email,
            "youtube_url": state.youtube_url,
            "youtube_connected": state.youtube_connected,
            "youtube_status": state.youtube_status,
            "youtube_auto_connect": state.youtube_auto_connect,
            "control_theme": state.control_theme,
            "cameras": state.cameras_list,
            "cameras_count": len(state.cameras_list),
            "focused_camera_id": state.focused_camera_id,
            "pinned_camera_ids": state.pinned_camera_ids,
            "libras_camera_ids": state.libras_camera_ids,
            "libras_saved_names": state.libras_saved_names,
            "libras_auto_detect": state.libras_auto_detect,
            "libras_size": state.libras_size,
            "show_camera_names": state.show_camera_names,
            "camera_name_style": state.camera_name_style,
            "grid_layout_mode": state.grid_layout_mode,
            "stage_preset": state.stage_preset,
            "chat_style": state.chat_style,
            "chat_font_size": state.chat_font_size,
            "chat_max_messages": state.chat_max_messages,
            "show_chat_avatars": state.show_chat_avatars,
            "featured_message": state.featured_message,
            "poll": state.poll_data,
            "poll_display_mode": state.poll_display_mode,
            "poll_display_duration": state.poll_display_duration,
            "poll_force_visible": state.poll_force_visible,
            "poll_style": state.poll_style,
            "poll_nonce": state.poll_nonce,
            "poll_dismissed": state.poll_dismissed,
            "custom_camera_names": state.custom_camera_names,
            "inactivity_bypassed_count": state.inactivity_bypassed_count,
            "screenshare_active": state.screenshare_active,
            "presentation_mode": state.presentation_mode,
            "has_presentation": state.latest_presentation_frame is not None,
            "pcm_listeners": len(state.pcm_clients),
            "chat_messages": state.chat_messages,
            "chat_messages_count": len(state.chat_messages)
        },
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"}
    )

@app.get("/api/chat")
async def api_chat():
    return JSONResponse({
        "messages": state.chat_messages,
        "featured_message": state.featured_message,
        "chat_style": state.chat_style,
        "chat_font_size": state.chat_font_size,
        "chat_max_messages": state.chat_max_messages,
        "show_chat_avatars": state.show_chat_avatars
    }, headers={"Cache-Control": "no-store, no-cache"})

# --------------------------------------------------------------------------
# SCRIPTS DE ÁUDIO PCM
# --------------------------------------------------------------------------
PCM_AUDIO_SCRIPT = """
<script>
    (function startWebAudioPcm() {
        const urlParams = new URLSearchParams(window.location.search);
        if (urlParams.get('audio') === '0' || urlParams.get('muted') === '1' || (window.self !== window.top)) {
            return;
        }

        let audioCtx = null;
        let nextStartTime = 0;
        let monitorGain = null;

        function getAudioContext() {
            if (!audioCtx) {
                audioCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 48000 });
                monitorGain = audioCtx.createGain();
                monitorGain.gain.setValueAtTime(1.0, audioCtx.currentTime);
                monitorGain.connect(audioCtx.destination);
            }
            if (audioCtx.state === 'suspended') {
                audioCtx.resume();
            }
            return audioCtx;
        }

        function connectWs() {
            const ctx = getAudioContext();
            const ws = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws/pcm_out');
            ws.binaryType = 'arraybuffer';

            ws.onmessage = (event) => {
                if (typeof event.data === 'string') {
                    try {
                        const msg = JSON.parse(event.data);
                        if (msg.type === 'speakers' && window.onSpeakersUpdate) {
                            window.onSpeakersUpdate(msg.speakers);
                        }
                    } catch(e) {}
                    return;
                }
                const ctx = getAudioContext();
                const float32Data = new Float32Array(event.data);
                const audioBuffer = ctx.createBuffer(1, float32Data.length, 48000);
                audioBuffer.copyToChannel(float32Data, 0);

                const bufferSource = ctx.createBufferSource();
                bufferSource.buffer = audioBuffer;
                bufferSource.connect(monitorGain);

                const currentTime = ctx.currentTime;
                if (nextStartTime < currentTime) {
                    nextStartTime = currentTime;
                }
                bufferSource.start(nextStartTime);
                nextStartTime += audioBuffer.duration;

                if (window.onAudioPcmChunk) {
                    let peak = 0;
                    for (let i = 0; i < float32Data.length; i++) {
                        const val = Math.abs(float32Data[i]);
                        if (val > peak) peak = val;
                    }
                    window.onAudioPcmChunk(peak);
                }
            };

            ws.onclose = () => {
                setTimeout(connectWs, 2000);
            };
        }

        window.setLocalMonitorVolume = function(vol) {
            const ctx = getAudioContext();
            if (monitorGain) {
                monitorGain.gain.setValueAtTime(vol, ctx.currentTime);
            }
        };

        connectWs();
        window.addEventListener('load', () => getAudioContext());
        document.addEventListener('click', () => getAudioContext());
    })();
</script>
"""

# --------------------------------------------------------------------------
# ESTILOS VISUAIS PARA TAGS DE NOMES E BADGE DE LIBRAS
# --------------------------------------------------------------------------
NAME_TAG_CSS = """
.name-tag.style-glass {
    position: absolute;
    bottom: 10px;
    left: 10px;
    background: rgba(15, 23, 42, 0.85);
    backdrop-filter: blur(10px);
    -webkit-backdrop-filter: blur(10px);
    border: 1px solid rgba(255, 255, 255, 0.2);
    padding: 5px 12px;
    border-radius: 8px;
    color: #ffffff;
    font-size: 13px;
    font-weight: 700;
    box-shadow: 0 4px 12px rgba(0,0,0,0.6);
    display: flex;
    align-items: center;
    gap: 6px;
    z-index: 10;
}
.name-tag.style-glass::before {
    content: '';
    display: inline-block;
    width: 7px;
    height: 7px;
    background: #38bdf8;
    border-radius: 50%;
}

.name-tag.style-pill {
    position: absolute;
    bottom: 10px;
    left: 10px;
    background: #0f172a;
    border: 2px solid #38bdf8;
    padding: 4px 14px;
    border-radius: 24px;
    color: #ffffff;
    font-size: 12.5px;
    font-weight: 800;
    box-shadow: 0 4px 14px rgba(56, 189, 248, 0.4);
    display: flex;
    align-items: center;
    gap: 6px;
    z-index: 10;
}
.name-tag.style-pill::before {
    content: 'LIVE';
    font-size: 9px;
    font-weight: 800;
    background: #ef4444;
    color: #fff;
    padding: 1px 4px;
    border-radius: 4px;
}

.name-tag.style-minimal {
    position: absolute;
    bottom: 0;
    left: 0;
    width: 100%;
    background: rgba(0, 0, 0, 0.85);
    padding: 5px 12px;
    color: #f1f5f9;
    font-size: 12px;
    font-weight: 600;
    border-top: 1px solid rgba(255, 255, 255, 0.1);
    z-index: 10;
}

.name-tag.style-glow {
    position: absolute;
    bottom: 10px;
    left: 10px;
    background: linear-gradient(135deg, rgba(14, 165, 233, 0.95), rgba(99, 102, 241, 0.95));
    padding: 5px 12px;
    border-radius: 6px;
    color: #ffffff;
    font-size: 13px;
    font-weight: 800;
    text-shadow: 0 1px 3px rgba(0,0,0,0.5);
    box-shadow: 0 4px 16px rgba(14, 165, 233, 0.5);
    z-index: 10;
}

.libras-badge-overlay {
    position: absolute;
    top: 8px;
    right: 8px;
    background: linear-gradient(135deg, #06b6d4, #0284c7);
    color: #ffffff;
    font-size: 11px;
    font-weight: 800;
    padding: 3px 8px;
    border-radius: 6px;
    box-shadow: 0 2px 8px rgba(6, 182, 212, 0.6);
    display: flex;
    align-items: center;
    gap: 4px;
    z-index: 20;
    text-transform: uppercase;
    letter-spacing: 0.3px;
}

/* LIVE HEALTH MONITOR STYLES */
.live-pulse-dot {
    width: 8px;
    height: 8px;
    background: #10b981;
    border-radius: 50%;
    display: inline-block;
    box-shadow: 0 0 8px #10b981;
    animation: pulseLive 1.5s infinite;
}
@keyframes pulseLive {
    0%, 100% { transform: scale(1); opacity: 1; }
    50% { transform: scale(1.3); opacity: 0.6; }
}
.source-health-pill {
    background: rgba(11, 17, 32, 0.7);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 8px;
    padding: 5px 8px;
    display: flex;
    align-items: center;
    gap: 8px;
    transition: all 0.25s ease;
}
.source-health-pill.is-active {
    border-color: rgba(16, 185, 129, 0.4);
    background: rgba(16, 185, 129, 0.08);
}
.pill-dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    flex-shrink: 0;
}
.pill-dot.connected { background: #10b981; box-shadow: 0 0 6px #10b981; }
.pill-dot.disconnected { background: #64748b; }
.pill-dot.connecting { background: #eab308; box-shadow: 0 0 6px #eab308; }
.pill-dot.alert { background: #ef4444; box-shadow: 0 0 6px #ef4444; }
.pill-info { display: flex; flex-direction: column; line-height: 1.15; }
.pill-name { font-size: 10px; color: #94a3b8; font-weight: 700; text-transform: uppercase; letter-spacing: 0.4px; }
.pill-state { font-size: 11px; color: #f8fafc; font-weight: 800; }
"""

# --------------------------------------------------------------------------
# ESTILOS CSS DOS 6 TEMAS DO CHAT (FEED NORMAL)
# --------------------------------------------------------------------------
CHAT_THEMES_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Outfit', sans-serif; }
html, body {
    background: transparent !important;
    width: 100vw;
    height: 100vh;
    overflow: hidden;
}
#chat-container {
    width: 100%;
    height: 100%;
    display: flex;
    flex-direction: column;
    justify-content: flex-end;
    padding: 20px 14px;
    gap: 10px;
}

.font-small .chat-user { font-size: 13px !important; }
.font-small .chat-text { font-size: 13px !important; }
.font-medium .chat-user { font-size: 15px !important; }
.font-medium .chat-text { font-size: 15.5px !important; }
.font-large .chat-user { font-size: 18px !important; }
.font-large .chat-text { font-size: 19px !important; }

@keyframes chatSlideIn {
    from { opacity: 0; transform: translateY(18px) scale(0.96); }
    to { opacity: 1; transform: translateY(0) scale(1); }
}

.source-badge {
    font-size: 9.5px;
    font-weight: 800;
    padding: 2px 6px;
    border-radius: 4px;
    display: inline-flex;
    align-items: center;
    gap: 3px;
    text-transform: uppercase;
}
.source-rnp { background: rgba(56, 189, 248, 0.2); color: #38bdf8; border: 1px solid rgba(56, 189, 248, 0.4); }
.source-yt { background: rgba(239, 68, 68, 0.2); color: #ef4444; border: 1px solid rgba(239, 68, 68, 0.4); }

.theme-glass .chat-card {
    background: rgba(15, 23, 42, 0.88);
    backdrop-filter: blur(14px);
    -webkit-backdrop-filter: blur(14px);
    border: 1px solid rgba(255, 255, 255, 0.18);
    box-shadow: 0 4px 16px rgba(0, 0, 0, 0.45);
    border-radius: 12px;
    padding: 10px 14px;
    animation: chatSlideIn 0.3s cubic-bezier(0.16, 1, 0.3, 1);
}
.theme-glass .chat-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 4px; }
.theme-glass .chat-user { font-weight: 800; color: #38bdf8; letter-spacing: 0.2px; text-shadow: 0 1px 2px rgba(0,0,0,0.8); display: flex; align-items: center; gap: 6px; }
.theme-glass .chat-time { font-size: 11px; color: #94a3b8; font-weight: 500; }
.theme-glass .chat-text { color: #ffffff; font-weight: 500; line-height: 1.45; text-shadow: 0 1px 2px rgba(0,0,0,0.7); }

.theme-streamer .chat-card {
    background: #18181b;
    border-left: 4px solid #a855f7;
    border-radius: 8px;
    padding: 10px 14px;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.6);
    display: flex;
    gap: 10px;
    animation: chatSlideIn 0.3s cubic-bezier(0.16, 1, 0.3, 1);
}
.theme-streamer .chat-avatar {
    width: 34px;
    height: 34px;
    border-radius: 50%;
    background: linear-gradient(135deg, #a855f7, #ec4899);
    color: #fff;
    display: flex;
    align-items: center;
    justify-content: center;
    font-weight: 800;
    font-size: 13px;
    flex-shrink: 0;
    overflow: hidden;
}
.theme-streamer .chat-avatar img { width: 100%; height: 100%; object-fit: cover; }
.theme-streamer .chat-body { flex: 1; }
.theme-streamer .chat-header { display: flex; align-items: center; gap: 8px; margin-bottom: 2px; }
.theme-streamer .chat-user { font-weight: 800; color: #f472b6; display: flex; align-items: center; gap: 6px; }
.theme-streamer .chat-time { font-size: 10.5px; color: #71717a; }
.theme-streamer .chat-text { color: #f4f4f5; font-weight: 500; line-height: 1.4; }

.theme-broadcast .chat-card {
    background: #0f172a;
    border: 1px solid #1e293b;
    border-top: 3px solid #ef4444;
    border-radius: 4px;
    padding: 10px 16px;
    box-shadow: 0 6px 18px rgba(0,0,0,0.7);
    animation: chatSlideIn 0.25s ease-out;
}
.theme-broadcast .chat-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 4px; border-bottom: 1px solid rgba(255,255,255,0.08); padding-bottom: 4px; }
.theme-broadcast .chat-user { font-weight: 900; color: #ffffff; text-transform: uppercase; font-size: 13px; letter-spacing: 0.5px; display: flex; align-items: center; gap: 6px; }
.theme-broadcast .chat-time { font-size: 11px; color: #94a3b8; font-weight: 700; }
.theme-broadcast .chat-text { color: #e2e8f0; font-weight: 600; line-height: 1.4; }

.theme-neon .chat-card {
    background: rgba(10, 15, 30, 0.92);
    border: 1.5px solid #06b6d4;
    border-radius: 10px;
    padding: 11px 15px;
    box-shadow: 0 0 16px rgba(6, 182, 212, 0.35), inset 0 0 8px rgba(6, 182, 212, 0.15);
    animation: chatSlideIn 0.3s cubic-bezier(0.16, 1, 0.3, 1);
}
.theme-neon .chat-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 4px; }
.theme-neon .chat-user { font-weight: 800; color: #22d3ee; text-shadow: 0 0 10px rgba(34, 211, 238, 0.8); display: flex; align-items: center; gap: 6px; }
.theme-neon .chat-time { font-size: 10.5px; color: #38bdf8; font-weight: 600; }
.theme-neon .chat-text { color: #ffffff; font-weight: 600; line-height: 1.45; }

.theme-minimal .chat-card {
    background: linear-gradient(90deg, rgba(0,0,0,0.85) 0%, rgba(0,0,0,0.5) 75%, transparent 100%);
    padding: 8px 14px;
    border-left: 3px solid #38bdf8;
    animation: chatSlideIn 0.3s ease-out;
}
.theme-minimal .chat-header { display: flex; align-items: center; gap: 8px; margin-bottom: 2px; }
.theme-minimal .chat-user { font-weight: 800; color: #38bdf8; text-shadow: 0 2px 4px rgba(0,0,0,0.9); display: flex; align-items: center; gap: 6px; }
.theme-minimal .chat-time { font-size: 11px; color: #cbd5e1; text-shadow: 0 1px 3px rgba(0,0,0,0.9); }
.theme-minimal .chat-text { color: #ffffff; font-weight: 600; text-shadow: 0 2px 6px rgba(0,0,0,0.95); line-height: 1.4; }

.theme-bubbles .chat-card {
    background: #313338;
    border-radius: 18px 18px 18px 4px;
    padding: 10px 16px;
    max-width: 90%;
    align-self: flex-start;
    box-shadow: 0 4px 10px rgba(0,0,0,0.3);
    animation: chatSlideIn 0.25s cubic-bezier(0.16, 1, 0.3, 1);
}
.theme-bubbles .chat-header { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 3px; }
.theme-bubbles .chat-user { font-weight: 800; color: #5865f2; display: flex; align-items: center; gap: 6px; }
.theme-bubbles .chat-time { font-size: 10px; color: #949ba4; }
.theme-bubbles .chat-text { color: #dbdee1; font-weight: 500; line-height: 1.4; }
"""

# --------------------------------------------------------------------------
# ESTILOS CSS DO PAINEL DE CONTROLE (COMPACTO E ULTRA-RESPONSIVO PARA DOCKS)
# --------------------------------------------------------------------------
CONTROL_PANEL_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Outfit', sans-serif; }
body { background: #0b1120; color: #f8fafc; padding: 14px; min-height: 100vh; overflow-x: hidden; }

::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-track { background: #030712; }
::-webkit-scrollbar-thumb { background: #1e293b; border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: #38bdf8; }

.header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; gap: 8px; flex-wrap: wrap; }
h1 { font-size: 18px; font-weight: 800; color: #38bdf8; letter-spacing: -0.5px; display: flex; align-items: center; gap: 6px; }

.badge { padding: 4px 10px; border-radius: 20px; font-size: 11.5px; font-weight: 700; display: inline-flex; align-items: center; gap: 5px; }
.badge.connected { background: rgba(16, 185, 129, 0.2); color: #10b981; border: 1px solid #10b981; }
.badge.disconnected { background: rgba(239, 68, 68, 0.2); color: #ef4444; border: 1px solid #ef4444; }
.badge.connecting { background: rgba(234, 179, 8, 0.2); color: #eab308; border: 1px solid #eab308; }

.card { background: rgba(30, 41, 59, 0.85); border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 12px; padding: 14px; margin-bottom: 12px; backdrop-filter: blur(12px); }
.form-row { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.input-group { margin-bottom: 10px; }
label { display: block; font-size: 12px; font-weight: 600; color: #94a3b8; margin-bottom: 4px; }
input[type="text"], input[type="email"], select { width: 100%; padding: 8px 12px; border-radius: 7px; border: 1px solid rgba(255, 255, 255, 0.15); background: #1e293b; color: #ffffff; font-size: 13px; outline: none; transition: border-color 0.2s; }
input[type="text"]:focus, input[type="email"]:focus, select:focus { border-color: #38bdf8; }

.actions { display: flex; align-items: center; gap: 8px; margin-top: 4px; flex-wrap: wrap; }
.btn { padding: 8px 16px; border-radius: 7px; font-size: 13px; font-weight: 700; border: none; cursor: pointer; transition: all 0.2s; display: inline-flex; align-items: center; justify-content: center; gap: 5px; }
.btn-primary { background: #38bdf8; color: #0f172a; flex: 1; }
.btn-primary:hover { background: #0ea5e9; }
.btn-danger { background: #ef4444; color: #ffffff; }
.btn-danger:hover { background: #dc2626; }
.btn-success { background: #10b981; color: #ffffff; }
.btn-success:hover { background: #059669; }

.log-box { background: #030712; border: 1px solid rgba(56, 189, 248, 0.25); border-radius: 7px; padding: 10px 12px; font-family: monospace; font-size: 12px; color: #38bdf8; margin-top: 10px; min-height: 38px; display: flex; align-items: center; word-break: break-all; }

.section-title { font-size: 15px; font-weight: 700; color: #38bdf8; margin-bottom: 10px; display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 8px; }
.preview-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 12px; margin-bottom: 12px; }
.mod-box { background: #1e293b; border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 10px; padding: 12px; display: flex; flex-direction: column; justify-content: space-between; gap: 10px; transition: border-color 0.2s; }
.mod-title { font-weight: 700; font-size: 13.5px; color: #f1f5f9; display: flex; align-items: center; justify-content: space-between; }
.thumb-box { width: 100%; height: 140px; background: #030712; border-radius: 7px; overflow: hidden; display: flex; align-items: center; justify-content: center; border: 1px solid rgba(255, 255, 255, 0.06); position: relative; }
.thumb-box img { width: 100%; height: 100%; object-fit: contain; }

.btn-copy { background: rgba(56, 189, 248, 0.15); color: #38bdf8; border: 1px solid rgba(56, 189, 248, 0.35); font-size: 11.5px; padding: 6px 10px; border-radius: 6px; cursor: pointer; text-align: center; font-weight: 700; transition: all 0.2s; }
.btn-copy:hover { background: #38bdf8; color: #0f172a; }

.cam-controls { display: flex; gap: 4px; flex-wrap: wrap; margin-top: 2px; }
.btn-ctrl { padding: 6px 10px; border-radius: 6px; font-size: 11.5px; font-weight: 700; border: 1px solid rgba(255,255,255,0.15); background: #0f172a; color: #cbd5e1; cursor: pointer; transition: all 0.2s; display: inline-flex; align-items: center; justify-content: center; gap: 3px; flex: 1; min-width: 70px; text-align: center; }
.btn-ctrl:hover { background: #1e293b; color: #fff; }
.btn-ctrl.active-focus { background: #eab308 !important; color: #000 !important; border-color: #eab308 !important; box-shadow: 0 0 10px rgba(234, 179, 8, 0.6); }
.btn-ctrl.active-pin { background: #10b981 !important; color: #000 !important; border-color: #10b981 !important; box-shadow: 0 0 10px rgba(16, 185, 129, 0.6); }
.btn-ctrl.active-libras { background: #06b6d4 !important; color: #082f49 !important; border-color: #06b6d4 !important; box-shadow: 0 0 10px rgba(6, 182, 212, 0.7); }
.btn-ctrl.active-preset { background: #38bdf8 !important; color: #0f172a !important; border-color: #38bdf8 !important; box-shadow: 0 0 10px rgba(56, 189, 248, 0.5); }

.grid-options-bar { background: rgba(15, 23, 42, 0.6); border: 1px solid rgba(255,255,255,0.08); border-radius: 9px; padding: 10px 12px; margin-bottom: 12px; display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap; }
.grid-opt-group { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }

.preset-buttons-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
    gap: 8px;
    margin-bottom: 12px;
}

/* MESA DE SOM BROADCAST COM MEDIDORES VERTICAIS (AUDIO MIXER RACK) */
.audio-mixer-rack {
    display: flex;
    gap: 12px;
    align-items: stretch;
    overflow-x: auto;
    padding: 14px;
    background: rgba(3, 7, 18, 0.75);
    border: 1px solid rgba(56, 189, 248, 0.2);
    border-radius: 12px;
    margin-bottom: 14px;
}
.audio-channel-strip {
    background: rgba(15, 23, 42, 0.85);
    border: 1px solid rgba(255, 255, 255, 0.1);
    border-radius: 10px;
    padding: 10px;
    display: flex;
    flex-direction: column;
    align-items: center;
    width: 140px;
    min-width: 140px;
    position: relative;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
    transition: all 0.2s ease;
}
.audio-channel-strip.master-channel {
    background: rgba(16, 185, 129, 0.08);
    border-color: rgba(16, 185, 129, 0.4);
    box-shadow: 0 0 16px rgba(16, 185, 129, 0.15);
}
.audio-channel-strip.speaking-active {
    border-color: #10b981 !important;
    box-shadow: 0 0 16px rgba(16, 185, 129, 0.45) !important;
}
.channel-voice-badge {
    font-size: 9px;
    font-weight: 700;
    padding: 1px 5px;
    border-radius: 4px;
    display: inline-flex;
    align-items: center;
    gap: 2px;
    transition: all 0.2s ease;
}
.channel-voice-badge.speaking {
    background: #10b981;
    color: #ffffff;
    box-shadow: 0 0 6px rgba(16, 185, 129, 0.6);
}
.channel-voice-badge.muted {
    background: rgba(239, 68, 68, 0.2);
    color: #ef4444;
}
.channel-voice-badge.open {
    background: rgba(56, 189, 248, 0.15);
    color: #38bdf8;
}
.channel-header {
    width: 100%;
    text-align: center;
    margin-bottom: 8px;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 2px;
}
.channel-title {
    font-size: 12px;
    font-weight: 800;
    color: #f8fafc;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    max-width: 120px;
}
.channel-tag {
    font-size: 9.5px;
    font-family: monospace;
    color: #94a3b8;
    background: rgba(255,255,255,0.06);
    padding: 1px 6px;
    border-radius: 4px;
}
.channel-meter-box {
    display: flex;
    align-items: center;
    gap: 6px;
    margin: 6px 0;
    height: 140px;
    position: relative;
}
.meter-scale-vertical {
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    height: 100%;
    font-size: 8.5px;
    font-family: monospace;
    color: #64748b;
    text-align: right;
    user-select: none;
    padding-right: 2px;
}
.vu-track-vertical {
    width: 22px;
    height: 100%;
    background: #020617;
    border-radius: 6px;
    border: 1px solid rgba(255, 255, 255, 0.15);
    position: relative;
    overflow: hidden;
    box-shadow: inset 0 2px 6px rgba(0,0,0,0.8);
}
.vu-fill-vertical {
    position: absolute;
    bottom: 0;
    left: 0;
    right: 0;
    width: 100%;
    height: 0%;
    background: linear-gradient(0deg, #10b981 0%, #10b981 65%, #f59e0b 82%, #ef4444 100%);
    transition: height 0.05s linear;
}
.vu-peak-vertical {
    position: absolute;
    left: 0;
    right: 0;
    height: 2px;
    background: #ffffff;
    bottom: 0%;
    box-shadow: 0 0 5px #ffffff;
    transition: bottom 0.25s ease-out;
}
.meter-db-readout {
    font-size: 11px;
    font-weight: 800;
    font-family: monospace;
    color: #38bdf8;
    text-align: center;
    margin: 4px 0;
}
.channel-fader-box {
    width: 100%;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 4px;
    margin-top: 6px;
}
.channel-btn-copy {
    width: 100%;
    padding: 5px 6px;
    font-size: 10.5px;
    font-weight: 700;
    border-radius: 6px;
    text-align: center;
    background: rgba(56, 189, 248, 0.15);
    border: 1px solid rgba(56, 189, 248, 0.35);
    color: #38bdf8;
    cursor: pointer;
    transition: all 0.2s;
    margin-top: 6px;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 4px;
    text-decoration: none;
}
.channel-btn-copy:hover {
    background: #38bdf8;
    color: #0f172a;
}

/* Live Preview do Grid OBS */
.obs-grid-preview-box { width: 100%; height: 200px; background: #030712; border-radius: 10px; overflow: hidden; border: 1px solid rgba(56, 189, 248, 0.3); margin-bottom: 14px; position: relative; }
.obs-grid-preview-box iframe { width: 100%; height: 100%; border: none; }

/* Customização do Chat & Destaques */
.chat-preview-container { width: 100%; height: 200px; background: #030712; border-radius: 10px; overflow: hidden; border: 1px solid rgba(56, 189, 248, 0.25); position: relative; }
.chat-preview-container iframe { width: 100%; height: 100%; border: none; }

.featured-active-banner {
    background: linear-gradient(135deg, rgba(234, 179, 8, 0.15), rgba(245, 158, 11, 0.25));
    border: 1.5px solid #eab308;
    border-radius: 9px;
    padding: 10px 12px;
    margin-bottom: 12px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    flex-wrap: wrap;
    box-shadow: 0 0 15px rgba(234, 179, 8, 0.2);
}

.chat-manager-list {
    max-height: 260px;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding-right: 4px;
    margin-top: 10px;
}
.chat-msg-row {
    background: #0f172a;
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 8px;
    padding: 8px 10px;
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 8px;
    transition: all 0.2s;
}
.chat-msg-row.is-featured {
    border-color: #eab308;
    background: rgba(234, 179, 8, 0.1);
    box-shadow: 0 0 8px rgba(234, 179, 8, 0.3);
}

.source-tag { font-size: 9px; font-weight: 800; padding: 1px 4px; border-radius: 3px; }
.source-tag.rnp { background: #0284c7; color: #fff; }
.source-tag.yt { background: #ef4444; color: #fff; }

/* 📊 ESTILOS DA ENQUETE (POLL) */
.poll-card {
    background: linear-gradient(135deg, rgba(16, 185, 129, 0.12), rgba(6, 182, 212, 0.12));
    border: 1.5px solid #10b981;
    border-radius: 10px;
    padding: 12px;
    margin-bottom: 14px;
    box-shadow: 0 0 16px rgba(16, 185, 129, 0.2);
}
.poll-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 8px;
    gap: 8px;
    flex-wrap: wrap;
}
.poll-question {
    font-size: 13.5px;
    font-weight: 800;
    color: #ffffff;
    margin-bottom: 10px;
}
.poll-options-grid {
    display: flex;
    flex-direction: column;
    gap: 6px;
}
.poll-opt-btn {
    background: rgba(15, 23, 42, 0.8);
    border: 1px solid rgba(255,255,255,0.15);
    border-radius: 7px;
    padding: 8px 12px;
    color: #f8fafc;
    font-size: 12.5px;
    font-weight: 600;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: space-between;
    transition: all 0.2s;
    position: relative;
    overflow: hidden;
}
.poll-opt-btn:hover {
    border-color: #10b981;
    background: #1e293b;
}
.poll-progress-bg {
    position: absolute;
    top: 0;
    left: 0;
    height: 100%;
    background: rgba(16, 185, 129, 0.25);
    z-index: 1;
    transition: width 0.3s ease-out;
}
.poll-opt-content {
    position: relative;
    z-index: 2;
    display: flex;
    align-items: center;
    justify-content: space-between;
    width: 100%;
}

@media (max-width: 640px) {
    body { padding: 8px 6px !important; }
    .header { margin-bottom: 10px !important; }
    .header h1 { font-size: 16px !important; }
    .card { padding: 10px 8px !important; margin-bottom: 10px !important; border-radius: 9px !important; }
    .form-row { grid-template-columns: 1fr !important; gap: 6px !important; }
    .input-group { margin-bottom: 6px !important; }
    .input-group label { font-size: 11px !important; margin-bottom: 2px !important; }
    input[type="text"], input[type="email"], select { padding: 6px 8px !important; font-size: 12px !important; }
    .btn { padding: 7px 12px !important; font-size: 12px !important; }
    .actions { gap: 6px !important; }
    .section-title { font-size: 13.5px !important; margin-bottom: 8px !important; }
    .grid-options-bar { padding: 8px !important; gap: 8px !important; flex-direction: column !important; align-items: stretch !important; }
    .grid-opt-group { width: 100% !important; justify-content: space-between !important; }
    .grid-opt-group select { flex: 1 !important; max-width: 60% !important; }
    .obs-grid-preview-box { height: 160px !important; margin-bottom: 10px !important; }
    .chat-preview-container { height: 170px !important; }
    .preview-grid { grid-template-columns: 1fr !important; gap: 8px !important; }
    .preset-buttons-grid { grid-template-columns: 1fr 1fr !important; gap: 6px !important; }
    .thumb-box { height: 130px !important; }
    .audio-monitor-bar { padding: 8px 10px !important; gap: 8px !important; }
    .vu-container { min-width: 100% !important; }
}

/* ==========================================================================
   THEME STYLES FOR BROADCAST CONTROL PANEL (Organic & Broadcast Design System)
   ========================================================================== */

/* 1. 🎛️ STUDIO MASTER / HARDWARE CONSOLE (Default) */
body.theme-hardware {
    background: #101318 !important;
    color: #e2e8f0;
}
body.theme-hardware .card {
    background: #171a21 !important;
    border: 1px solid #282f3d !important;
    box-shadow: 0 4px 18px rgba(0,0,0,0.6);
}
body.theme-hardware .section-title {
    color: #f1f5f9 !important;
    font-family: 'JetBrains Mono', 'Outfit', monospace;
    font-size: 13px;
    letter-spacing: 0.5px;
    text-transform: uppercase;
}
body.theme-hardware .btn-primary {
    background: linear-gradient(180deg, #242a36 0%, #171b23 100%) !important;
    border: 1px solid #3b4559 !important;
    border-bottom: 2.5px solid #0d1015 !important;
    color: #38bdf8 !important;
    box-shadow: 0 2px 4px rgba(0,0,0,0.5), inset 0 1px 0 rgba(255,255,255,0.08);
}
body.theme-hardware .btn-primary:hover {
    background: linear-gradient(180deg, #2d3545 0%, #1e2430 100%) !important;
    color: #7dd3fc !important;
}
body.theme-hardware .btn-ctrl {
    background: #12151c !important;
    border: 1px solid #2a3140 !important;
    border-bottom: 2px solid #0b0d12 !important;
    color: #94a3b8 !important;
    box-shadow: 0 2px 4px rgba(0,0,0,0.4);
}
body.theme-hardware .btn-ctrl:hover {
    color: #f8fafc !important;
    border-color: #475569 !important;
}
body.theme-hardware .btn-ctrl.active-focus {
    background: #1a1e27 !important;
    border-color: #f59e0b !important;
    color: #fbbf24 !important;
    box-shadow: 0 0 12px rgba(245, 158, 11, 0.4), inset 0 1px 3px rgba(0,0,0,0.8);
}
body.theme-hardware .btn-ctrl.active-pin {
    background: #1a1e27 !important;
    border-color: #10b981 !important;
    color: #34d399 !important;
    box-shadow: 0 0 12px rgba(16, 185, 129, 0.4), inset 0 1px 3px rgba(0,0,0,0.8);
}
body.theme-hardware .btn-ctrl.active-libras {
    background: #1a1e27 !important;
    border-color: #06b6d4 !important;
    color: #22d3ee !important;
    box-shadow: 0 0 12px rgba(6, 182, 212, 0.4), inset 0 1px 3px rgba(0,0,0,0.8);
}
body.theme-hardware .btn-ctrl.active-preset {
    background: #1a1e27 !important;
    border-color: #38bdf8 !important;
    color: #38bdf8 !important;
    box-shadow: 0 0 12px rgba(56, 189, 248, 0.4), inset 0 1px 3px rgba(0,0,0,0.8);
}
body.theme-hardware input[type="text"], body.theme-hardware input[type="email"], body.theme-hardware select {
    background: #12151c !important;
    border: 1px solid #282f3d !important;
    color: #f1f5f9 !important;
}

/* 2. 🌿 NORDIC MINIMAL / WARM SLATE & CLAY */
body.theme-nordic {
    background: #141518 !important;
    color: #e6e2dd;
}
body.theme-nordic .card {
    background: #1b1d22 !important;
    border: 1px solid rgba(244, 241, 222, 0.08) !important;
    box-shadow: 0 10px 30px rgba(0,0,0,0.25);
    border-radius: 14px !important;
}
body.theme-nordic .section-title {
    color: #f4f1de !important;
    font-weight: 700;
}
body.theme-nordic .btn-primary {
    background: #e07a5f !important;
    color: #ffffff !important;
    border-radius: 10px !important;
}
body.theme-nordic .btn-primary:hover {
    background: #d46b50 !important;
}
body.theme-nordic .btn-ctrl {
    background: #23262d !important;
    border: 1px solid rgba(244, 241, 222, 0.08) !important;
    color: #d1cbc1 !important;
    border-radius: 9px !important;
}
body.theme-nordic .btn-ctrl.active-focus {
    background: #e07a5f !important;
    color: #ffffff !important;
    border-color: #e07a5f !important;
    box-shadow: 0 4px 14px rgba(224, 122, 95, 0.4);
}
body.theme-nordic .btn-ctrl.active-pin {
    background: #81b29a !important;
    color: #141518 !important;
    border-color: #81b29a !important;
    box-shadow: 0 4px 14px rgba(129, 178, 154, 0.4);
}
body.theme-nordic .btn-ctrl.active-libras {
    background: #3d5a80 !important;
    color: #ffffff !important;
    border-color: #3d5a80 !important;
    box-shadow: 0 4px 14px rgba(61, 90, 128, 0.4);
}
body.theme-nordic .btn-ctrl.active-preset {
    background: #81b29a !important;
    color: #141518 !important;
    border-color: #81b29a !important;
    box-shadow: 0 4px 14px rgba(129, 178, 154, 0.4);
}
body.theme-nordic input[type="text"], body.theme-nordic input[type="email"], body.theme-nordic select {
    background: #23262d !important;
    border: 1px solid rgba(244, 241, 222, 0.1) !important;
    color: #f4f1de !important;
    border-radius: 9px !important;
}

/* 3. 📺 BROADCAST TITANIUM / CLEAN GLASS PRO */
body.theme-glass {
    background: #0b1120 !important;
    color: #f8fafc;
}
body.theme-glass .card {
    background: rgba(15, 23, 42, 0.88) !important;
    backdrop-filter: blur(16px);
    -webkit-backdrop-filter: blur(16px);
    border: 1px solid rgba(56, 189, 248, 0.25) !important;
    box-shadow: 0 8px 32px rgba(0,0,0,0.5);
}
body.theme-glass .section-title {
    color: #38bdf8 !important;
}
body.theme-glass .btn-primary {
    background: #38bdf8 !important;
    color: #0f172a !important;
    box-shadow: 0 0 16px rgba(56, 189, 248, 0.35);
}

/* 4. ⬛ CYBER OBSIDIAN / DEEP MONOLITH */
body.theme-obsidian {
    background: #050507 !important;
    color: #f4f4f5;
}
body.theme-obsidian .card {
    background: #0c0d11 !important;
    border: 1px solid #22252e !important;
    border-radius: 6px !important;
    box-shadow: none !important;
}
body.theme-obsidian .section-title {
    color: #ffffff !important;
    font-weight: 800;
    text-transform: uppercase;
    letter-spacing: 1px;
}
body.theme-obsidian .btn-primary {
    background: #22c55e !important;
    color: #050507 !important;
    border-radius: 4px !important;
    font-weight: 900;
}
body.theme-obsidian .btn-ctrl {
    background: #111318 !important;
    border: 1px solid #27272a !important;
    border-radius: 4px !important;
    color: #a1a1aa !important;
}
body.theme-obsidian .btn-ctrl.active-focus {
    background: #eab308 !important;
    color: #000000 !important;
    border-color: #eab308 !important;
}
body.theme-obsidian .btn-ctrl.active-pin {
    background: #22c55e !important;
    color: #000000 !important;
    border-color: #22c55e !important;
}
body.theme-obsidian .btn-ctrl.active-libras {
    background: #06b6d4 !important;
    color: #000000 !important;
    border-color: #06b6d4 !important;
}
body.theme-obsidian .btn-ctrl.active-preset {
    background: #ffffff !important;
    color: #000000 !important;
    border-color: #ffffff !important;
}
body.theme-obsidian input[type="text"], body.theme-obsidian input[type="email"], body.theme-obsidian select {
    background: #0d0e12 !important;
    border: 1px solid #27272a !important;
    border-radius: 4px !important;
    color: #ffffff !important;
}

/* 5. 🏛️ INSTITUTO FEDERAL (IF CLEAN / IFES - BRANCO, VERDE E VERMELHO) */
body.theme-ifes {
    background: #f1f5f9 !important;
    color: #0f172a !important;
}
body.theme-ifes .card {
    background: #ffffff !important;
    border: 1px solid #e2e8f0 !important;
    border-top: 3.5px solid #15803d !important;
    box-shadow: 0 4px 18px rgba(0, 0, 0, 0.05), 0 1px 3px rgba(0, 0, 0, 0.04) !important;
    border-radius: 12px !important;
}
body.theme-ifes .section-title {
    color: #15803d !important;
    font-weight: 800;
    font-size: 13.5px;
    letter-spacing: -0.2px;
}
body.theme-ifes .section-title span {
    color: #15803d !important;
}
body.theme-ifes .btn-primary {
    background: linear-gradient(135deg, #15803d 0%, #16a34a 100%) !important;
    color: #ffffff !important;
    border: 1px solid #166534 !important;
    font-weight: 800 !important;
    border-radius: 8px !important;
    box-shadow: 0 2px 8px rgba(22, 163, 74, 0.25) !important;
}
body.theme-ifes .btn-primary:hover {
    background: linear-gradient(135deg, #166534 0%, #15803d 100%) !important;
    box-shadow: 0 4px 14px rgba(22, 163, 74, 0.35) !important;
    transform: translateY(-1px);
}
body.theme-ifes .btn-danger {
    background: linear-gradient(135deg, #dc2626 0%, #ef4444 100%) !important;
    color: #ffffff !important;
    border: 1px solid #b91c1c !important;
    font-weight: 800 !important;
    border-radius: 8px !important;
    box-shadow: 0 2px 8px rgba(220, 38, 38, 0.25) !important;
}
body.theme-ifes .btn-danger:hover {
    background: linear-gradient(135deg, #b91c1c 0%, #dc2626 100%) !important;
    box-shadow: 0 4px 14px rgba(220, 38, 38, 0.35) !important;
}
body.theme-ifes .btn-ctrl {
    background: #f8fafc !important;
    border: 1px solid #cbd5e1 !important;
    color: #334155 !important;
    border-radius: 8px !important;
    font-weight: 600;
}
body.theme-ifes .btn-ctrl:hover {
    background: #f0fdf4 !important;
    border-color: #16a34a !important;
    color: #15803d !important;
}
body.theme-ifes .btn-ctrl.active-focus {
    background: #dc2626 !important;
    color: #ffffff !important;
    border-color: #b91c1c !important;
    box-shadow: 0 2px 10px rgba(220, 38, 38, 0.3) !important;
}
body.theme-ifes .btn-ctrl.active-pin {
    background: #15803d !important;
    color: #ffffff !important;
    border-color: #166534 !important;
    box-shadow: 0 2px 10px rgba(22, 163, 74, 0.3) !important;
}
body.theme-ifes .btn-ctrl.active-libras {
    background: #0284c7 !important;
    color: #ffffff !important;
    border-color: #0369a1 !important;
    box-shadow: 0 2px 10px rgba(2, 132, 199, 0.3) !important;
}
body.theme-ifes .btn-ctrl.active-preset {
    background: #15803d !important;
    color: #ffffff !important;
    border-color: #166534 !important;
    box-shadow: 0 2px 10px rgba(22, 163, 74, 0.3) !important;
}
body.theme-ifes input[type="text"], 
body.theme-ifes input[type="email"], 
body.theme-ifes select {
    background: #ffffff !important;
    border: 1.5px solid #cbd5e1 !important;
    color: #0f172a !important;
    border-radius: 8px !important;
}
body.theme-ifes input[type="text"]:focus, 
body.theme-ifes input[type="email"]:focus, 
body.theme-ifes select:focus {
    border-color: #15803d !important;
    box-shadow: 0 0 0 3px rgba(21, 128, 61, 0.15) !important;
    outline: none;
}
body.theme-ifes label {
    color: #475569 !important;
    font-weight: 600;
}
body.theme-ifes .log-box {
    background: #f8fafc !important;
    border: 1px solid #e2e8f0 !important;
    color: #15803d !important;
    font-weight: 600;
}
body.theme-ifes .source-health-pill {
    background: #f8fafc !important;
    border: 1px solid #e2e8f0 !important;
    color: #0f172a !important;
}
body.theme-ifes .audio-monitor-bar, body.theme-ifes #audioDockCard {
    background: #ffffff !important;
    border: 1px solid #e2e8f0 !important;
    box-shadow: 0 2px 12px rgba(0,0,0,0.04) !important;
}
body.theme-ifes .audio-mixer-rack { background: #f8fafc !important; border-color: #cbd5e1 !important; }
body.theme-ifes .audio-channel-strip { background: #ffffff !important; border-color: #e2e8f0 !important; box-shadow: 0 2px 8px rgba(0,0,0,0.06) !important; }
body.theme-ifes .audio-channel-strip.master-channel { background: #f0fdf4 !important; border-color: #86efac !important; }
body.theme-ifes .audio-channel-strip.speaking-active { border-color: #16a34a !important; box-shadow: 0 0 16px rgba(22, 163, 74, 0.3) !important; }
body.theme-ifes .channel-voice-badge.speaking { background: #16a34a !important; color: #ffffff !important; }
body.theme-ifes .channel-voice-badge.muted { background: rgba(239, 68, 68, 0.15) !important; color: #dc2626 !important; }
body.theme-ifes .channel-voice-badge.open { background: rgba(14, 165, 233, 0.15) !important; color: #0284c7 !important; }
body.theme-ifes .channel-title { color: #0f172a !important; }
body.theme-ifes .channel-tag { color: #475569 !important; background: #f1f5f9 !important; }
body.theme-ifes .vu-track-vertical { background: #e2e8f0 !important; border-color: #cbd5e1 !important; }
body.theme-ifes .channel-btn-copy { background: rgba(21, 128, 61, 0.1) !important; border-color: rgba(21, 128, 61, 0.3) !important; color: #15803d !important; }
body.theme-ifes .channel-btn-copy:hover { background: #15803d !important; color: #ffffff !important; }
body.theme-ifes .meter-db-readout { color: #15803d !important; }
body.theme-ifes .audio-monitor-controls-bar {
    background: #f1f5f9 !important;
    border: 1px solid #e2e8f0 !important;
}
body.theme-ifes .audio-monitor-controls-bar span,
body.theme-ifes .audio-monitor-controls-bar div {
    color: #334155 !important;
}
body.theme-ifes #btnMuteMonitor {
    background: #ffffff !important;
    color: #1e293b !important;
    border-color: #cbd5e1 !important;
}
body.theme-ifes .btn-copy {
    background: #f0fdf4 !important;
    border-color: #86efac !important;
    color: #15803d !important;
}
body.theme-ifes .btn-copy:hover {
    background: #dcfce7 !important;
    border-color: #4ade80 !important;
}
body.theme-ifes .badge {
    background: #f1f5f9 !important;
    border-color: #cbd5e1 !important;
    color: #475569 !important;
}
body.theme-ifes .badge.connected {
    background: #f0fdf4 !important;
    border-color: #22c55e !important;
    color: #15803d !important;
}
body.theme-ifes .badge.disconnected {
    background: #fef2f2 !important;
    border-color: #ef4444 !important;
    color: #dc2626 !important;
}
body.theme-ifes #shieldBadge {
    background: #eff6ff !important;
    border-color: #93c5fd !important;
    color: #1d4ed8 !important;
}
body.theme-ifes h1 {
    color: #0f172a !important;
}
body.theme-ifes h1 span {
    color: #15803d !important;
}
body.theme-ifes .header-theme-box {
    background: #ffffff !important;
    border: 1px solid #cbd5e1 !important;
}
body.theme-ifes #selControlTheme {
    color: #0f172a !important;
}
body.theme-ifes .poll-question {
    color: #0f172a !important;
}
body.theme-ifes .obs-grid-preview-box {
    background: #f8fafc !important;
    border-color: #e2e8f0 !important;
}
"""

# --------------------------------------------------------------------------
# PAINEL DE CONTROLE (/control)
# --------------------------------------------------------------------------
@app.get("/")
@app.get("/dock")
async def root_redirect():
    return RedirectResponse(url="/control")

@app.get("/control", response_class=HTMLResponse)
async def get_control_panel():
    headers = {"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"}
    
    chk_show_names = "checked" if state.show_camera_names else ""
    sel_glass = "selected" if state.camera_name_style == "glass" else ""
    sel_pill = "selected" if state.camera_name_style == "pill" else ""
    sel_min = "selected" if state.camera_name_style == "minimal" else ""
    sel_glow = "selected" if state.camera_name_style == "glow" else ""

    sel_layout_auto = "selected" if state.grid_layout_mode == "auto" else ""
    sel_layout_row = "selected" if state.grid_layout_mode == "row" else ""
    sel_layout_col = "selected" if state.grid_layout_mode == "column" else ""
    sel_layout_grid = "selected" if state.grid_layout_mode == "grid" else ""

    sel_lib_equal = "selected" if state.libras_size == "equal" else ""
    sel_lib_huge = "selected" if state.libras_size == "huge" else ""
    sel_lib_large = "selected" if state.libras_size == "large" else ""
    sel_lib_med = "selected" if state.libras_size == "medium" else ""

    # Poll selectors
    sel_pm_results = "selected" if state.poll_display_mode == "results_only" else ""
    sel_pm_always = "selected" if state.poll_display_mode == "always" else ""
    sel_pm_hidden = "selected" if state.poll_display_mode == "hidden" else ""

    sel_pd_10 = "selected" if state.poll_display_duration == 10 else ""
    sel_pd_15 = "selected" if state.poll_display_duration == 15 else ""
    sel_pd_30 = "selected" if state.poll_display_duration == 30 else ""
    sel_pd_60 = "selected" if state.poll_display_duration == 60 else ""
    sel_pd_inf = "selected" if state.poll_display_duration == 0 else ""

    sel_pstyle_neon = "selected" if state.poll_style == "neon" else ""
    sel_pstyle_glass = "selected" if state.poll_style == "glass" else ""
    sel_pstyle_bubbles = "selected" if state.poll_style == "bubbles" else ""
    sel_pstyle_cyber = "selected" if state.poll_style == "cyber" else ""
    sel_pstyle_min = "selected" if state.poll_style == "minimal" else ""

    sel_chat_glass = "selected" if state.chat_style == "glass" else ""
    sel_chat_streamer = "selected" if state.chat_style == "streamer" else ""
    sel_chat_bcast = "selected" if state.chat_style == "broadcast" else ""
    sel_chat_neon = "selected" if state.chat_style == "neon" else ""
    sel_chat_min = "selected" if state.chat_style == "minimal" else ""
    sel_chat_bubbles = "selected" if state.chat_style == "bubbles" else ""

    sel_size_small = "selected" if state.chat_font_size == "small" else ""
    sel_size_med = "selected" if state.chat_font_size == "medium" else ""
    sel_size_large = "selected" if state.chat_font_size == "large" else ""

    sel_max_4 = "selected" if state.chat_max_messages == 4 else ""
    sel_max_6 = "selected" if state.chat_max_messages == 6 else ""
    sel_max_8 = "selected" if state.chat_max_messages == 8 else ""
    sel_max_12 = "selected" if state.chat_max_messages == 12 else ""

    sel_th_hardware = "selected" if state.control_theme == "hardware" else ""
    sel_th_nordic = "selected" if state.control_theme == "nordic" else ""
    sel_th_glass = "selected" if state.control_theme == "glass" else ""
    sel_th_obsidian = "selected" if state.control_theme == "obsidian" else ""
    sel_th_ifes = "selected" if state.control_theme == "ifes" else ""
    chk_yt_auto_connect = "checked" if state.youtube_auto_connect else ""

    chk_chat_avatars = "checked" if state.show_chat_avatars else ""

    html_content = f"""
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>RNP to OBS - Painel de Controle</title>
    <link rel="icon" type="image/x-icon" href="/favicon.ico">
    <link rel="icon" type="image/png" href="/app_icon.png">
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;600;700;800&family=JetBrains+Mono:wght@500;700&display=swap" rel="stylesheet">
    <style>
        {CONTROL_PANEL_CSS}
    </style>
</head>
<body class="theme-{state.control_theme}">
    <div class="header">
        <h1><span>RNP to OBS</span> 🎛️</h1>
        <div style="display: flex; gap: 6px; align-items: center; flex-wrap: wrap;">
            <div class="header-theme-box" style="display: flex; align-items: center; gap: 4px; background: rgba(0,0,0,0.35); padding: 3px 8px; border-radius: 8px; border: 1px solid rgba(255,255,255,0.12);">
                <span style="font-size: 11px; font-weight: 700; color: #94a3b8;">🎨 Estilo:</span>
                <select id="selControlTheme" onchange="changeControlTheme(this.value)" style="width: auto; padding: 2px 6px; font-size: 11.5px; font-weight: 700; background: transparent; border: none; cursor: pointer; color: #f8fafc;">
                    <option value="hardware" {sel_th_hardware}>🎛️ Studio Master</option>
                    <option value="nordic" {sel_th_nordic}>🌿 Nordic Minimal</option>
                    <option value="glass" {sel_th_glass}>📺 Broadcast Titanium</option>
                    <option value="obsidian" {sel_th_obsidian}>⬛ Cyber Obsidian</option>
                    <option value="ifes" {sel_th_ifes}>🏛️ Instituto Federal (IF Clean)</option>
                </select>
                <a href="/catalog" target="_blank" class="btn-copy" style="padding: 2px 7px; font-size: 10.5px; text-decoration: none; display: inline-flex; align-items: center; gap: 3px;" title="Ver Catálogo Visual de Estilos">📖 Catálogo</a>
            </div>
            <div id="saveToast" class="badge" style="display:none; background: rgba(16, 185, 129, 0.2); color: #10b981; border: 1px solid #10b981;">💾 Salvo!</div>
            <div id="shieldBadge" class="badge" style="background: rgba(56, 189, 248, 0.15); color: #38bdf8; border: 1px solid rgba(56, 189, 248, 0.4);">🛡️ Anti-Inatividade</div>
            <div id="statusBadge" class="badge disconnected">● Desconectado</div>
            <button onclick="handleExitApplication()" class="btn" style="background: rgba(239, 68, 68, 0.2); border: 1px solid rgba(239, 68, 68, 0.5); color: #f87171; font-weight: 700; font-size: 11px; padding: 3px 9px; border-radius: 8px; cursor: pointer; display: flex; align-items: center; gap: 4px;" title="Desconectar da sala e encerrar o servidor e aplicativo">
                <span>🔴 Encerrar Aplicativo</span>
            </button>
        </div>
    </div>

    <!-- MONITOR ATIVO DE FONTES & CONEXÃO (LIVE HEALTH MONITOR) -->
    <div class="card" style="background: rgba(15, 23, 42, 0.85); border-color: rgba(56, 189, 248, 0.35); padding: 10px 14px;">
        <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px;">
            <span style="color: #38bdf8; font-weight: 800; font-size: 12.5px; display: flex; align-items: center; gap: 6px;">
                <span class="live-pulse-dot"></span>
                <span>Monitor de Fontes em Tempo Real</span>
            </span>
            <span id="healthHeartbeat" style="font-size: 10.5px; color: #94a3b8; font-family: monospace;">● Ativo (500ms)</span>
        </div>

        <div class="sources-health-grid" style="display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 6px;">
            <div class="source-health-pill" id="healthPillRnp">
                <span class="pill-dot disconnected" id="dotRnp"></span>
                <div class="pill-info">
                    <span class="pill-name">Sala RNP</span>
                    <span class="pill-state" id="healthStateRnp">Desconectado</span>
                </div>
            </div>

            <div class="source-health-pill" id="healthPillSlides">
                <span class="pill-dot disconnected" id="dotSlides"></span>
                <div class="pill-info">
                    <span class="pill-name">Apresentação</span>
                    <span class="pill-state" id="healthStateSlides">Sem Slides</span>
                </div>
            </div>

            <div class="source-health-pill" id="healthPillCams">
                <span class="pill-dot disconnected" id="dotCams"></span>
                <div class="pill-info">
                    <span class="pill-name">Webcams</span>
                    <span class="pill-state" id="healthStateCams">0 Câmeras</span>
                </div>
            </div>

            <div class="source-health-pill" id="healthPillLibras">
                <span class="pill-dot disconnected" id="dotLibras"></span>
                <div class="pill-info">
                    <span class="pill-name">Libras</span>
                    <span class="pill-state" id="healthStateLibras">Nenhum</span>
                </div>
            </div>

            <div class="source-health-pill" id="healthPillYt">
                <span class="pill-dot disconnected" id="dotYt"></span>
                <div class="pill-info">
                    <span class="pill-name">YouTube Chat</span>
                    <span class="pill-state" id="healthStateYt">Desconectado</span>
                </div>
            </div>

            <div class="source-health-pill" id="healthPillAudio">
                <span class="pill-dot disconnected" id="dotAudio"></span>
                <div class="pill-info">
                    <span class="pill-name">Áudio PCM</span>
                    <span class="pill-state" id="healthStateAudio">Standby</span>
                </div>
            </div>
        </div>
        
        <!-- BANNER DE EVENTO / NOTIFICAÇÃO AO VIVO (EX: NOVA CÂMERA ENTROU / SAIU) -->
        <div id="liveSourceEventBanner" style="display: none; margin-top: 8px; padding: 6px 10px; border-radius: 6px; font-size: 11.5px; font-weight: 700; align-items: center; gap: 6px;"></div>
    </div>

    <!-- SALVAR / CARREGAR CONFIGURAÇÕES -->
    <div class="card" style="background: rgba(15, 23, 42, 0.75); border-color: rgba(56, 189, 248, 0.2);">
        <div style="display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 8px;">
            <div>
                <strong style="color: #38bdf8; font-size: 13px;">💾 Memória & Persistência</strong>
                <p style="font-size: 11px; color: #94a3b8;">Salvo em disco e restaurado no F5.</p>
            </div>
            <div style="display: flex; gap: 6px; flex-wrap: wrap;">
                <button class="btn btn-success" style="padding: 5px 10px; font-size: 11.5px;" onclick="saveAllConfigurations()">💾 Salvar</button>
                <button class="btn btn-ctrl" style="padding: 5px 10px; font-size: 11.5px;" onclick="loadAllConfigurations()">📂 Recarregar</button>
                <button class="btn btn-ctrl" style="padding: 5px 10px; font-size: 11.5px;" onclick="resetAllConfigurations()">🔄 Padrões</button>
            </div>
        </div>
    </div>

    <!-- 🔗 CENTRAL DE FONTES DO OBS STUDIO (LINKS RÁPIDOS PARA MONTAR AS CENAS) -->
    <div class="card" style="background: rgba(15, 23, 42, 0.9); border-color: rgba(99, 102, 241, 0.45); padding: 12px 14px;">
        <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; flex-wrap: wrap; gap: 6px;">
            <div style="display: flex; align-items: center; gap: 8px;">
                <span style="font-size: 16px;">🔗</span>
                <div>
                    <strong style="color: #a5b4fc; font-size: 13px;">Fontes para o OBS Studio (Montagem Prévia de Cenas)</strong>
                    <div style="font-size: 11px; color: #94a3b8;">Copie os links abaixo e adicione no OBS como <strong>Navegador (Browser Source)</strong> a qualquer momento:</div>
                </div>
            </div>
            <a href="/catalog" target="_blank" class="btn-copy" style="background: rgba(99, 102, 241, 0.2); color: #a5b4fc; border-color: #6366f1; padding: 4px 10px; font-size: 11px;" title="Ver Catálogo Visual de Estilos">📖 Ver Catálogo Visual</a>
        </div>

        <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(185px, 1fr)); gap: 8px;">
            <!-- Enquete -->
            <button class="btn-copy" style="justify-content: flex-start; text-align: left; padding: 8px 10px; background: rgba(16, 185, 129, 0.15); border-color: #10b981; color: #10b981;" onclick="copyUrl('http://localhost:3333/poll', this)" title="Copiar URL da Enquete (1920x1080 transparente)">
                <span style="font-size: 18px; margin-right: 6px;">📊</span>
                <div style="line-height: 1.2;">
                    <div style="font-weight: 800; font-size: 11.5px;">Enquete / Votação</div>
                    <div style="font-size: 10px; opacity: 0.8; font-family: monospace;">/poll (1920x1080)</div>
                </div>
            </button>

            <!-- Palco Composto -->
            <button class="btn-copy" style="justify-content: flex-start; text-align: left; padding: 8px 10px; background: rgba(168, 85, 247, 0.15); border-color: #c084fc; color: #c084fc;" onclick="copyUrl('http://localhost:3333/stage', this)" title="Copiar URL do Palco Composto (1920x1080)">
                <span style="font-size: 18px; margin-right: 6px;">🎭</span>
                <div style="line-height: 1.2;">
                    <div style="font-weight: 800; font-size: 11.5px;">Palco Composto</div>
                    <div style="font-size: 10px; opacity: 0.8; font-family: monospace;">/stage (1920x1080)</div>
                </div>
            </button>

            <!-- Janela Libras -->
            <button class="btn-copy" style="justify-content: flex-start; text-align: left; padding: 8px 10px; background: rgba(6, 182, 212, 0.15); border-color: #06b6d4; color: #22d3ee;" onclick="copyUrl('http://localhost:3333/libras', this)" title="Copiar URL da Janela de Libras (PIP)">
                <span style="font-size: 18px; margin-right: 6px;">🤟</span>
                <div style="line-height: 1.2;">
                    <div style="font-weight: 800; font-size: 11.5px;">Janela de Libras</div>
                    <div style="font-size: 10px; opacity: 0.8; font-family: monospace;">/libras (PIP / Paritária)</div>
                </div>
            </button>

            <!-- Chat Feed -->
            <button class="btn-copy" style="justify-content: flex-start; text-align: left; padding: 8px 10px; background: rgba(59, 130, 246, 0.15); border-color: #3b82f6; color: #60a5fa;" onclick="copyUrl('http://localhost:3333/chat', this)" title="Copiar URL do Feed de Chat RNP + YouTube">
                <span style="font-size: 18px; margin-right: 6px;">💬</span>
                <div style="line-height: 1.2;">
                    <div style="font-weight: 800; font-size: 11.5px;">Feed de Chat (RNP+YT)</div>
                    <div style="font-size: 10px; opacity: 0.8; font-family: monospace;">/chat (Transparente)</div>
                </div>
            </button>

            <!-- Chat Destaque -->
            <button class="btn-copy" style="justify-content: flex-start; text-align: left; padding: 8px 10px; background: rgba(234, 179, 8, 0.15); border-color: #eab308; color: #fde047;" onclick="copyUrl('http://localhost:3333/chat/featured', this)" title="Copiar URL do Destaque de Mensagem">
                <span style="font-size: 18px; margin-right: 6px;">⭐</span>
                <div style="line-height: 1.2;">
                    <div style="font-weight: 800; font-size: 11.5px;">Chat em Destaque</div>
                    <div style="font-size: 10px; opacity: 0.8; font-family: monospace;">/chat/featured (Lower Third)</div>
                </div>
            </button>

            <!-- Grid de Câmeras -->
            <button class="btn-copy" style="justify-content: flex-start; text-align: left; padding: 8px 10px; background: rgba(14, 165, 233, 0.15); border-color: #0ea5e9; color: #38bdf8;" onclick="copyUrl('http://localhost:3333/cameras/grid', this)" title="Copiar URL do Grid Dinâmico de Webcams">
                <span style="font-size: 18px; margin-right: 6px;">👥</span>
                <div style="line-height: 1.2;">
                    <div style="font-weight: 800; font-size: 11.5px;">Grid de Câmeras</div>
                    <div style="font-size: 10px; opacity: 0.8; font-family: monospace;">/cameras/grid (1920x1080)</div>
                </div>
            </button>

            <!-- Slides / Apresentação -->
            <button class="btn-copy" style="justify-content: flex-start; text-align: left; padding: 8px 10px; background: rgba(244, 63, 94, 0.15); border-color: #f43f5e; color: #fda4af;" onclick="copyUrl('http://localhost:3333/presentation', this)" title="Copiar URL dos Slides da Apresentação">
                <span style="font-size: 18px; margin-right: 6px;">📽️</span>
                <div style="line-height: 1.2;">
                    <div style="font-weight: 800; font-size: 11.5px;">Slides Apresentação</div>
                    <div style="font-size: 10px; opacity: 0.8; font-family: monospace;">/presentation (Slides Puros)</div>
                </div>
            </button>

            <!-- Áudio PCM Stream -->
            <button class="btn-copy" style="justify-content: flex-start; text-align: left; padding: 8px 10px; background: rgba(34, 197, 94, 0.15); border-color: #22c55e; color: #86efac;" onclick="copyUrl('http://localhost:3333/audio', this)" title="Copiar URL do Stream de Áudio">
                <span style="font-size: 18px; margin-right: 6px;">🔊</span>
                <div style="line-height: 1.2;">
                    <div style="font-weight: 800; font-size: 11.5px;">Áudio PCM Direto</div>
                    <div style="font-size: 10px; opacity: 0.8; font-family: monospace;">/audio (Controle Áudio OBS)</div>
                </div>
            </button>
        </div>
    </div>

    <!-- SALA RNP -->
    <div class="card">
        <div class="input-group">
            <label>URL da Sala RNP:</label>
            <input type="text" id="roomUrl" value="{state.room_url}" onchange="autoSaveField('room_url', this.value)">
        </div>
        <div class="form-row">
            <div class="input-group">
                <label>Nome do Convidado:</label>
                <input type="text" id="userName" value="{state.user_name}" onchange="autoSaveField('user_name', this.value)">
            </div>
            <div class="input-group">
                <label>E-mail:</label>
                <input type="email" id="userEmail" value="{state.user_email}" onchange="autoSaveField('user_email', this.value)">
            </div>
        </div>
        <div class="actions">
            <button id="btnConnect" class="btn btn-primary" onclick="handleConnect()">⚡ Conectar à RNP</button>
            <button id="btnDisconnect" class="btn btn-danger" onclick="handleDisconnect()">⏹ Desconectar</button>
        </div>
        <div class="log-box" id="logBox">> Pronto para conectar.</div>
    </div>

    <!-- 🎭 PRESETS DE CENA AO VIVO (SLIDES + CÂMERAS) -->
    <div class="card" style="border-color: rgba(168, 85, 247, 0.35);">
        <div class="section-title">
            <span style="color: #c084fc; display: flex; align-items: center; gap: 6px;">
                <span>🎭 Palco Composto (Slides + Câmeras)</span>
            </span>
            <div style="display: flex; gap: 6px; flex-wrap: wrap;">
                <button class="btn-copy" style="background: rgba(168, 85, 247, 0.2); color: #c084fc; border-color: #c084fc;" onclick="copyUrl('http://localhost:3333/stage')">📋 Link Palco OBS</button>
                <button class="btn-copy" style="background: rgba(6, 182, 212, 0.2); color: #22d3ee; border-color: #06b6d4;" onclick="copyUrl('http://localhost:3333/libras')">🤟 Link Janela Libras</button>
            </div>
        </div>

        <div class="obs-grid-preview-box" style="height: 180px; border-color: rgba(168, 85, 247, 0.35); background: transparent;">
            <iframe id="stagePreviewIframe" src="/stage?audio=0"></iframe>
        </div>

        <div class="preset-buttons-grid">
            <button id="btnPreset_side-by-side" class="btn-ctrl active-preset" onclick="setStagePreset('side-by-side')">
                📺 Lado a Lado (70/30)
            </button>
            <button id="btnPreset_bottom-bar" class="btn-ctrl" onclick="setStagePreset('bottom-bar')">
                ⬇️ Slide Top + Rodapé
            </button>
            <button id="btnPreset_pip-bottom-right" class="btn-ctrl" onclick="setStagePreset('pip-bottom-right')">
                🖼️ PIP Canto Inferior
            </button>
            <button id="btnPreset_pip-top-right" class="btn-ctrl" onclick="setStagePreset('pip-top-right')">
                ↗️ PIP Canto Superior
            </button>
            <button id="btnPreset_split-50-50" class="btn-ctrl" onclick="setStagePreset('split-50-50')">
                ⚖️ Split 50 / 50
            </button>
            <button id="btnPreset_slide-only" class="btn-ctrl" onclick="setStagePreset('slide-only')">
                📊 Somente Slides
            </button>
        </div>

        <!-- CONFIGURAÇÃO DE TAMANHO DA JANELA DE LIBRAS & AUTO-RECONHECIMENTO -->
        <div class="grid-options-bar" style="margin-top: 8px;">
            <div class="grid-opt-group" style="width: 100%; justify-content: space-between;">
                <label style="margin: 0; color: #22d3ee; font-size: 12px; font-weight: 700;">🤟 Tamanho Libras:</label>
                <select id="selLibrasSize" style="width: auto; padding: 4px 8px; font-size: 12px; border-color: #06b6d4;" onchange="updateLibrasSizeConfig()">
                    <option value="equal" {sel_lib_equal}>👥 Igual ao Palestrante (Padrão IFES)</option>
                    <option value="huge" {sel_lib_huge}>📐 Gigante Flutuante (560x315)</option>
                    <option value="large" {sel_lib_large}>📺 Grande Flutuante (480x270)</option>
                    <option value="medium" {sel_lib_med}>▫️ Médio Flutuante (380x214)</option>
                </select>
            </div>
            <div style="width: 100%; display: flex; align-items: center; justify-content: space-between; margin-top: 6px; font-size: 11px; flex-wrap: wrap; gap: 6px;">
                <button id="btnAutoLibras" class="btn-ctrl active-pin" style="padding: 3px 8px; font-size: 11px;" onclick="toggleLibrasAutoDetect()">
                    🛡️ Auto-Reconhecimento: ATIVO
                </button>
                <div style="display: flex; align-items: center; gap: 6px;">
                    <span id="savedInterpretersCount" style="color:#38bdf8; font-size: 11px;">0 salvos</span>
                    <button class="btn-ctrl" style="padding: 2px 6px; font-size: 10.5px; color: #f87171;" onclick="clearSavedLibras()" title="Limpar lista de nomes de intérpretes salvos">🗑️ Limpar</button>
                </div>
            </div>
        </div>
    </div>

    <!-- SEÇÃO DE ENQUETE / POLL DA SALA RNP COM MONITORAMENTO E CONTROLE -->
    <div id="pollDockCard" class="card poll-card" style="border-color: rgba(16, 185, 129, 0.35);">
        <div class="poll-header" style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; flex-wrap: wrap; gap: 6px;">
            <div style="display: flex; align-items: center; gap: 6px;">
                <span style="font-size: 14px;">📊</span>
                <strong style="color: #10b981; font-size: 13px;" id="pollStatusTitle">Enquete da Sala RNP</strong>
                <span id="pollBadgeStatus" class="badge disconnected" style="font-size: 10px; padding: 2px 6px;">⚪ Standby</span>
            </div>
            <div style="display: flex; gap: 6px; flex-wrap: wrap;">
                <button class="btn-copy" style="background: rgba(16, 185, 129, 0.2); color: #10b981; border-color: #10b981; font-weight: 700;" onclick="copyUrl('http://localhost:3333/poll', this)">📋 Link OBS Enquete</button>
                <button class="btn btn-ctrl" style="padding: 3px 8px; font-size: 11px; color: #38bdf8; border-color: rgba(56, 189, 248, 0.4);" onclick="simulatePoll()" title="Simular uma enquete de teste no OBS para testar o posicionamento">🧪 Testar no OBS</button>
                <button class="btn btn-ctrl" style="padding: 3px 8px; font-size: 11px; color: #f87171; border-color: rgba(239, 68, 68, 0.4);" onclick="clearPoll()">🗑️ Limpar</button>
            </div>
        </div>

        <div class="grid-options-bar" style="margin-bottom: 10px; background: rgba(11, 17, 32, 0.85); border-color: rgba(16, 185, 129, 0.3);">
            <div class="grid-opt-group" style="width: 100%; justify-content: space-between;">
                <label style="margin: 0; color: #10b981; font-size: 12px; font-weight: 700;">🎯 Exibir no OBS:</label>
                <select id="selPollDisplayMode" style="width: auto; padding: 4px 8px; font-size: 12px; border-color: #10b981;" onchange="updatePollConfig()">
                    <option value="results_only" {sel_pm_results}>📊 Somente Resultados Finais (Recomendado)</option>
                    <option value="always" {sel_pm_always}>🗳️ Votação Ao Vivo + Resultados</option>
                    <option value="hidden" {sel_pm_hidden}>❌ Ocultar no OBS</option>
                </select>
            </div>
            <div class="grid-opt-group" style="width: 100%; justify-content: space-between; margin-top: 4px;">
                <label style="margin: 0; color: #94a3b8; font-size: 12px; font-weight: 600;">⏱️ Tempo na Tela:</label>
                <select id="selPollDuration" style="width: auto; padding: 4px 8px; font-size: 12px;" onchange="updatePollConfig()">
                    <option value="10" {sel_pd_10}>⏱️ 10 segundos</option>
                    <option value="15" {sel_pd_15}>⏱️ 15 segundos (Padrão)</option>
                    <option value="30" {sel_pd_30}>⏱️ 30 segundos</option>
                    <option value="60" {sel_pd_60}>⏱️ 60 segundos (1 min)</option>
                    <option value="0" {sel_pd_inf}>♾️ Fixo (Até Fechar)</option>
                </select>
            </div>
            <div class="grid-opt-group" style="width: 100%; justify-content: space-between; margin-top: 4px;">
                <label style="margin: 0; color: #38bdf8; font-size: 12px; font-weight: 600;">🎨 Estilo da Enquete:</label>
                <select id="selPollStyle" style="width: auto; padding: 4px 8px; font-size: 12px; border-color: #38bdf8;" onchange="updatePollConfig()">
                    <option value="neon" {sel_pstyle_neon}>⚡ Neon Glow</option>
                    <option value="glass" {sel_pstyle_glass}>💎 Glassmorphism</option>
                    <option value="bubbles" {sel_pstyle_bubbles}>💬 Discord Bubbles</option>
                    <option value="cyber" {sel_pstyle_cyber}>🎬 Cyberpunk Broadcast</option>
                    <option value="minimal" {sel_pstyle_min}>📄 Minimal Slate</option>
                </select>
            </div>
            <div style="width: 100%; display: flex; gap: 6px; margin-top: 6px;">
                <button id="btnForcePollShow" class="btn-ctrl" style="flex:1; border-color: #10b981; color: #10b981;" onclick="toggleForcePoll(true)">
                    ✨ Mostrar no OBS Agora
                </button>
                <button id="btnForcePollHide" class="btn-ctrl" style="flex:1;" onclick="toggleForcePoll(false)">
                    ❌ Ocultar Agora
                </button>
            </div>
        </div>

        <div style="font-size: 11px; color: #94a3b8; margin-bottom: 6px; display: flex; align-items: center; gap: 4px;">
            <span>📡 <strong>Dica OBS:</strong> Cole o link acima em uma fonte de <em>Navegador (1920x1080 transparente)</em>. Ela se ativará automaticamente quando houver enquete.</span>
        </div>

        <div class="poll-question" id="pollQuestionText" style="font-size: 13px; font-weight: 800; color: #ffffff; margin-bottom: 8px;">Aguardando início de enquete...</div>
        <div class="poll-options-grid" id="pollOptionsContainer" style="display: flex; flex-direction: column; gap: 6px;"></div>
    </div>

    <!-- INTEGRAÇÃO COM YOUTUBE LIVE CHAT -->
    <div class="card" style="border-color: rgba(239, 68, 68, 0.3);">
        <div class="section-title">
            <span style="color: #ef4444; display: flex; align-items: center; gap: 6px;">
                <span>🔴 YouTube Live Chat</span>
            </span>
            <div id="ytStatusBadge" class="badge disconnected" style="font-size: 10.5px;">● Desconectado</div>
        </div>
        <div class="input-group">
            <label>Link ou ID da Transmissão do YouTube:</label>
            <input type="text" id="ytUrl" placeholder="Ex: https://youtube.com/live/xxxx ou watch?v=xxxx" value="{state.youtube_url}" onchange="autoSaveField('youtube_url', this.value)">
        </div>
        <div style="margin-top: 8px; margin-bottom: 8px; display: flex; align-items: center; gap: 8px;">
            <input type="checkbox" id="chkYtAutoConnect" {chk_yt_auto_connect} onchange="autoSaveField('youtube_auto_connect', this.checked)" style="width: 16px; height: 16px; cursor: pointer; accent-color: #ef4444;">
            <label for="chkYtAutoConnect" style="margin: 0; font-size: 12px; cursor: pointer; color: #cbd5e1; user-select: none;">
                Conectar chat do YouTube automaticamente ao conectar à sala
            </label>
        </div>
        <div class="actions">
            <button id="btnYtConnect" class="btn btn-danger" style="flex:1;" onclick="handleYtConnect()">▶️ Integrar Chat do YouTube</button>
            <button id="btnYtDisconnect" class="btn btn-ctrl" onclick="handleYtDisconnect()">⏹ Parar</button>
        </div>
    </div>

    <!-- SEÇÃO DEDICADA DE ÁUDIO DA SALA & TRANSMISSÃO OBS -->
    <div class="card" id="audioDockCard">
        <div class="section-title">
            <span style="display: flex; align-items: center; gap: 8px;">
                <span>🔊</span> Transmissão de Áudio da Sala & Mixer OBS
            </span>
            <div style="display: flex; gap: 6px; flex-wrap: wrap;">
                <button class="btn-copy" style="background: rgba(16, 185, 129, 0.2); color: #10b981; border-color: #10b981;" onclick="copyUrl('http://localhost:3333/audio', this)" title="Copiar URL Master para adicionar como Fonte de Navegador no OBS Studio">
                    📋 Copiar Link Master OBS
                </button>
                <button class="btn-ctrl" onclick="testAudioVuMeter()" style="font-size: 11.5px; padding: 4px 10px;" title="Testar animação de todos os medidores de áudio">
                    🧪 Testar Medidores
                </button>
                <a href="/audio" target="_blank" class="btn-ctrl" style="text-decoration: none; display: inline-flex; align-items: center; padding: 4px 10px; font-size: 11.5px;" title="Abrir áudio no navegador">
                    🔗 Ouvir no Navegador
                </a>
            </div>
        </div>

        <!-- Informação do Mixer Unificado -->
        <div style="background: rgba(16, 185, 129, 0.08); border: 1px solid rgba(16, 185, 129, 0.25); border-radius: 8px; padding: 8px 12px; margin-bottom: 12px; display: flex; align-items: center; justify-content: space-between; gap: 8px; flex-wrap: wrap;">
            <div style="display: flex; align-items: center; gap: 8px; font-size: 12px; color: #10b981;">
                <span class="source-health-dot ok"></span>
                <span><strong>Mixer Broadcast:</strong> Medidores verticais calibrados para cada fonte e Master geral para o OBS Studio (PCM Float32 48kHz).</span>
            </div>
            <span class="badge connected" style="font-size: 11px;">OBS: /audio</span>
        </div>

        <!-- MESA DE SOM BROADCAST COM MEDIDORES VERTICAIS -->
        <div class="audio-mixer-rack" id="audioMixerRack">
            <!-- CANAL MASTER (GERAL) -->
            <div class="audio-channel-strip master-channel" id="channelStripMaster">
                <div class="channel-header">
                    <span style="font-size: 18px;">🎛️</span>
                    <span class="channel-title">Master Geral</span>
                    <span class="channel-tag" style="background: rgba(16, 185, 129, 0.2); color: #10b981;">OBS: /audio</span>
                </div>

                <div class="channel-meter-box">
                    <div class="meter-scale-vertical">
                        <span style="color:#ef4444; font-weight:bold;">0</span>
                        <span style="color:#f59e0b;">-3</span>
                        <span style="color:#f59e0b;">-6</span>
                        <span>-12</span>
                        <span>-20</span>
                        <span>-30</span>
                        <span>-40</span>
                        <span>-60</span>
                    </div>
                    <div class="vu-track-vertical">
                        <div class="vu-fill-vertical" id="vuMasterBar"></div>
                        <div class="vu-peak-vertical" id="vuMasterPeak"></div>
                    </div>
                </div>

                <div class="meter-db-readout" id="vuMasterDbText">-∞ dB (0%)</div>

                <button class="channel-btn-copy" style="background: rgba(16, 185, 129, 0.2); color: #10b981; border-color: #10b981;" onclick="copyUrl('http://localhost:3333/audio', this)" title="Copiar link do áudio Master para o OBS">
                    📋 Link Master
                </button>

                <div class="channel-fader-box">
                    <div style="display:flex; justify-content:space-between; width:100%; font-size:10px; color:#94a3b8;">
                        <span>Vol:</span>
                        <span id="lblMasterVol">100%</span>
                    </div>
                    <input type="range" id="rngMasterVol" min="0" max="1.5" step="0.05" value="1" style="width:100%; accent-color:#10b981; cursor:pointer;" oninput="updateMasterVol(this.value)">
                    <button class="btn-ctrl" id="btnMuteMaster" style="width:100%; padding:3px 6px; font-size:10.5px; margin-top:2px;" onclick="toggleMasterMute()">
                        🔊 Ativo
                    </button>
                </div>
            </div>

            <!-- FONTES INDIVIDUAIS (GERADAS DINAMICAMENTE PARA CADA CÂMERA / PARTICIPANTE) -->
            <div id="individualChannelsContainer" style="display: flex; gap: 12px; align-items: stretch;"></div>

            <!-- PLACEHOLDER QUANDO NÃO HÁ FONTES ADICIONAIS -->
            <div class="audio-channel-strip" id="noIndividualChannelsPlaceholder" style="border-style: dashed; opacity: 0.7; justify-content: center; min-width: 140px; text-align: center;">
                <span style="font-size: 24px; margin-bottom: 6px;">🎙️</span>
                <span style="font-size: 11px; font-weight: 700; color: #94a3b8;">Fontes Individuais</span>
                <p style="font-size: 10px; color: #64748b; margin-top: 4px; line-height: 1.3;">Aguardando participantes entrarem com câmera/voz na sala RNP.</p>
            </div>
        </div>

        <!-- Monitor Local de Áudio (Para ouvir ou mutar no computador da live) -->
        <div class="audio-monitor-controls-bar" style="display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap;">
            <div style="display: flex; align-items: center; gap: 10px;">
                <button id="btnMuteMonitor" class="btn-ctrl" style="padding: 6px 12px; font-size: 12px; border-radius: 6px;" onclick="toggleMonitorMute()">
                    🔊 <span id="lblMonitorStatus">Monitor Local: Ligado</span>
                </button>
                <div style="display: flex; align-items: center; gap: 6px;">
                    <span style="font-size: 11.5px; color: #94a3b8; font-weight: 600;">Volume Local:</span>
                    <input type="range" id="rngMonitorVol" min="0" max="1" step="0.05" value="1" style="width: 85px; accent-color: #10b981; cursor: pointer;" oninput="updateMonitorVol(this.value)">
                </div>
            </div>
            <div style="font-size: 11px; color: #64748b;">
                💡 <em>Dica OBS: Cole a URL em uma Fonte de Navegador e marque "Controlar áudio via OBS" nas propriedades.</em>
            </div>
        </div>
    </div>

    <!-- CONFIGURAÇÕES DE GRID, FOCO E ESTILO DE NOMES -->
    <div class="card">
        <div class="section-title">
            <span>🎥 Grid de Câmeras OBS (Ao Vivo)</span>
            <div style="display: flex; gap: 6px; flex-wrap: wrap;">
                <button class="btn-copy" onclick="copyUrl('http://localhost:3333/cameras/grid', this)">📋 Link Grid</button>
                <button class="btn-copy" style="background: rgba(6, 182, 212, 0.2); color: #22d3ee; border-color: #06b6d4;" onclick="copyUrl('http://localhost:3333/libras', this)">🤟 Janela Libras</button>
            </div>
        </div>

        <div class="obs-grid-preview-box">
            <iframe id="gridPreviewIframe" src="/cameras/grid?audio=0"></iframe>
        </div>

        <div class="grid-options-bar">
            <div class="grid-opt-group">
                <label style="margin: 0; display: flex; align-items: center; gap: 6px; cursor: pointer; color: #f8fafc; font-size: 12px;">
                    <input type="checkbox" id="chkShowNames" {chk_show_names} onchange="updateGridConfig()">
                    <span>Exibir Nomes</span>
                </label>
            </div>

            <div class="grid-opt-group">
                <label style="margin: 0; color: #94a3b8; font-size: 12px;">Estilo:</label>
                <select id="selNameStyle" style="width: auto; padding: 4px 8px; font-size: 12px;" onchange="updateGridConfig()">
                    <option value="glass" {sel_glass}>Glass (Vidro)</option>
                    <option value="pill" {sel_pill}>Pill (Pílula)</option>
                    <option value="minimal" {sel_min}>Minimal Dark</option>
                    <option value="glow" {sel_glow}>Neon Glow</option>
                </select>
            </div>

            <div class="grid-opt-group">
                <label style="margin: 0; color: #94a3b8; font-size: 12px;">Layout:</label>
                <select id="selGridLayout" style="width: auto; padding: 4px 8px; font-size: 12px;" onchange="updateGridConfig()">
                    <option value="auto" {sel_layout_auto}>🤖 Auto (1920x600)</option>
                    <option value="row" {sel_layout_row}>➡️ Linha Horizontal</option>
                    <option value="column" {sel_layout_col}>⬇️ Coluna Vertical</option>
                    <option value="grid" {sel_layout_grid}>🔲 Grid Quadrantes</option>
                </select>
            </div>

            <div class="grid-opt-group" style="width: 100%;">
                <button class="btn-ctrl" style="width: 100%;" onclick="clearFocusAndPins()">🔄 Restaurar Grid Equilibrado</button>
            </div>
        </div>

        <div class="section-title">
            <span>Câmeras Sincronizadas (<span id="camTotalCount">0</span>)</span>
        </div>

        <div class="preview-grid" id="camerasGrid">
            <div style="color: #64748b; font-size: 12px; padding: 10px;">Nenhuma câmera ligada no momento.</div>
        </div>
    </div>

    <!-- SEÇÃO EXCLUSIVA DE TEMAS, DESTAQUES E GERENCIAMENTO DO CHAT -->
    <div class="card">
        <div class="section-title">
            <span>💬 Chat & Destaque (Lower-Third)</span>
            <div style="display: flex; gap: 6px; flex-wrap: wrap;">
                <button class="btn-copy" onclick="copyUrl('http://localhost:3333/chat')">📋 Link Feed</button>
                <button class="btn-copy" style="background: rgba(234, 179, 8, 0.2); color: #eab308; border-color: #eab308;" onclick="copyUrl('http://localhost:3333/chat/featured')">🌟 Link Destaque</button>
            </div>
        </div>

        <!-- BANNER DE MENSAGEM EM DESTAQUE ATUAL -->
        <div id="featuredBox" class="featured-active-banner" style="display: none;">
            <div style="flex: 1;">
                <div style="display: flex; align-items: center; gap: 6px; margin-bottom: 2px;">
                    <span style="font-size: 11px; font-weight: 800; color: #eab308;">🌟 EM DESTAQUE NA TRANSMISSÃO:</span>
                    <span id="featSourceBadge" class="source-tag rnp">RNP</span>
                </div>
                <div style="font-size: 12.5px; font-weight: 700; color: #ffffff;" id="featUser">Nome</div>
                <div style="font-size: 12px; color: #fef08a;" id="featText">Texto da mensagem em destaque...</div>
            </div>
            <button class="btn btn-danger" style="padding: 6px 10px; font-size: 11.5px;" onclick="clearFeaturedMessage()">❌ Ocultar Destaque</button>
        </div>

        <div class="grid-options-bar">
            <div class="grid-opt-group">
                <label style="margin: 0; color: #94a3b8; font-size: 12px;">Tema:</label>
                <select id="selChatTheme" style="width: auto; padding: 4px 8px; font-size: 12px;" onchange="updateChatConfig()">
                    <option value="glass" {sel_chat_glass}>💎 StreamYard Glass</option>
                    <option value="streamer" {sel_chat_streamer}>🔴 Streamer Avatar</option>
                    <option value="broadcast" {sel_chat_bcast}>🎬 Broadcast News</option>
                    <option value="neon" {sel_chat_neon}>⚡ Neon Glow</option>
                    <option value="minimal" {sel_chat_min}>📄 Minimal Legenda</option>
                    <option value="bubbles" {sel_chat_bubbles}>💬 Discord Bubbles</option>
                </select>
            </div>

            <div class="grid-opt-group">
                <label style="margin: 0; color: #94a3b8; font-size: 12px;">Fonte:</label>
                <select id="selChatSize" style="width: auto; padding: 4px 8px; font-size: 12px;" onchange="updateChatConfig()">
                    <option value="small" {sel_size_small}>Pequena</option>
                    <option value="medium" {sel_size_med}>Média</option>
                    <option value="large" {sel_size_large}>Grande</option>
                </select>
            </div>

            <div class="grid-opt-group">
                <label style="margin: 0; color: #94a3b8; font-size: 12px;">Máx:</label>
                <select id="selChatMax" style="width: auto; padding: 4px 8px; font-size: 12px;" onchange="updateChatConfig()">
                    <option value="4" {sel_max_4}>4 msgs</option>
                    <option value="6" {sel_max_6}>6 msgs</option>
                    <option value="8" {sel_max_8}>8 msgs</option>
                    <option value="12" {sel_max_12}>12 msgs</option>
                </select>
            </div>

            <div class="grid-opt-group" style="width: 100%; margin-top: 4px;">
                <label style="margin: 0; display: flex; align-items: center; gap: 6px; cursor: pointer; color: #f8fafc; font-size: 12px;">
                    <input type="checkbox" id="chkShowAvatars" {chk_chat_avatars} onchange="updateChatConfig()" style="width: 15px; height: 15px; accent-color: #38bdf8;">
                    <span>👤 Exibir Avatares dos Usuários (YouTube / RNP)</span>
                </label>
            </div>
        </div>

        <div class="chat-preview-container">
            <iframe id="chatPreviewIframe" src="/chat"></iframe>
        </div>

        <!-- LISTA INTERATIVA DE MENSAGENS PARA DESTACAR COM 1 CLIQUE -->
        <div style="margin-top: 14px;">
            <div style="display: flex; align-items: center; justify-content: space-between;">
                <label style="font-size: 12px; font-weight: 700; color: #38bdf8;">Mensagens Recentes (Clique para Destacar na Tela):</label>
                <span id="chatTotalCount" style="font-size: 11px; color: #94a3b8;">0 msgs</span>
            </div>
            <div class="chat-manager-list" id="chatListContainer">
                <div style="color: #64748b; font-size: 12px; padding: 8px;">Nenhuma mensagem recebida ainda.</div>
            </div>
        </div>
    </div>

    <!-- SEÇÃO APRESENTAÇÃO -->
    <div class="card">
        <div class="section-title">
            <span>📊 Apresentação / Slides</span>
            <button class="btn-copy" onclick="copyUrl('http://localhost:3333/presentation')">📋 Link Slides</button>
        </div>
        <div class="thumb-box" style="height: 200px; background: transparent;">
            <img id="thumbScreen" src="/raw_frame/presentation" onerror="this.style.opacity=0.3" onload="this.style.opacity=1">
        </div>
    </div>

    {PCM_AUDIO_SCRIPT}

    <script>
        let currentCameras = [];
        let currentMessages = [];
        let activeFeaturedMsg = null;
        let focusedCamId = null;
        let pinnedCamIds = [];
        let librasCamIds = [];
        let activeLibrasSize = "equal";
        let isLocalMuted = false;
        let activeStagePreset = "side-by-side";

        let masterPeakHold = 0;
        let masterPeakHoldTimer = null;
        let currentMasterDisplayPct = 0;
        let channelPeaks = {{}};

        function updatePeakHoldUI(val) {{
            const el = document.getElementById('vuMasterPeak');
            if (el) el.style.bottom = Math.min(99, val) + '%';
        }}

        window.onAudioPcmChunk = function(peak) {{
            let db = -Infinity;
            let pct = 0;
            if (peak > 0.0001) {{
                db = Math.max(-60, Math.min(0, 20 * Math.log10(peak)));
                pct = Math.min(100, Math.round(((db + 60) / 60) * 100));
            }}

            if (pct >= currentMasterDisplayPct) {{
                currentMasterDisplayPct = pct;
            }} else {{
                currentMasterDisplayPct = Math.max(0, currentMasterDisplayPct - 4);
            }}

            const masterBarEl = document.getElementById('vuMasterBar');
            const masterDbEl = document.getElementById('vuMasterDbText');

            if (masterBarEl) {{
                masterBarEl.style.height = currentMasterDisplayPct + '%';
            }}

            if (masterDbEl) {{
                if (currentMasterDisplayPct > 0) {{
                    const dbStr = db.toFixed(1);
                    masterDbEl.innerText = `${{dbStr}} dB (${{currentMasterDisplayPct}}%)`;
                    masterDbEl.style.color = currentMasterDisplayPct > 85 ? '#ef4444' : (currentMasterDisplayPct > 65 ? '#f59e0b' : '#10b981');
                }} else {{
                    masterDbEl.innerText = '-∞ dB (0%)';
                    masterDbEl.style.color = '#94a3b8';
                }}
            }}

            if (pct > masterPeakHold) {{
                masterPeakHold = pct;
                clearTimeout(masterPeakHoldTimer);
                masterPeakHoldTimer = setTimeout(() => {{
                    const decay = setInterval(() => {{
                        masterPeakHold = Math.max(0, masterPeakHold - 3);
                        updatePeakHoldUI(masterPeakHold);
                        if (masterPeakHold <= 0) clearInterval(decay);
                    }}, 40);
                }}, 1200);
            }}
            updatePeakHoldUI(masterPeakHold);

            // Animar medidores dos canais individuais EXCLUSIVAMENTE para quem está ativamente falando
            if (currentCameras && currentCameras.length > 0) {{
                currentCameras.forEach((c) => {{
                    const cid = String(c.id);
                    const barEl = document.getElementById('vuBar_cam_' + cid);
                    const peakEl = document.getElementById('vuPeak_cam_' + cid);
                    const dbEl = document.getElementById('vuDb_cam_' + cid);
                    if (barEl) {{
                        if (c.is_speaking) {{
                            const faderVal = sourceVolumes[cid] !== undefined ? sourceVolumes[cid] : 1.0;
                            const isChanMuted = mutedSources.has(cid);
                            const chPeak = isChanMuted ? 0 : (peak * faderVal);
                            const channelPct = Math.max(0, Math.min(100, Math.round(chPeak * 100)));
                            barEl.style.height = channelPct + '%';
                            if (peakEl && channelPct > (channelPeaks[cid] || 0)) {{
                                channelPeaks[cid] = channelPct;
                                peakEl.style.bottom = channelPct + '%';
                                setTimeout(() => {{ channelPeaks[cid] = 0; }}, 1200);
                            }}
                            if (dbEl) {{
                                if (channelPct > 0) {{
                                    const chDb = chPeak > 0.0001 ? Math.max(-60, Math.min(0, 20 * Math.log10(chPeak))) : -60;
                                    dbEl.innerText = `${{chDb.toFixed(1)}} dB (${{channelPct}}%)`;
                                    dbEl.style.color = channelPct > 85 ? '#ef4444' : (channelPct > 65 ? '#f59e0b' : '#10b981');
                                }} else {{
                                    dbEl.innerText = '-∞ dB (0%)';
                                    dbEl.style.color = '#94a3b8';
                                }}
                            }}
                        }} else {{
                            // Quem NÃO está falando fica estritamente zerado (-∞ dB)
                            const cur = parseFloat(barEl.style.height || '0');
                            if (cur > 0) {{
                                const nextVal = Math.max(0, cur - 15);
                                barEl.style.height = nextVal + '%';
                                if (nextVal === 0 && dbEl) {{
                                    dbEl.innerText = '-∞ dB (0%)';
                                    dbEl.style.color = '#94a3b8';
                                }}
                            }} else {{
                                barEl.style.height = '0%';
                                if (dbEl) {{
                                    dbEl.innerText = '-∞ dB (0%)';
                                    dbEl.style.color = '#94a3b8';
                                }}
                            }}
                        }}
                    }}
                }});
            }}
        }};

        // Atualização em tempo real de quem está falando recebida via WebSocket
        window.onSpeakersUpdate = function(speakersMap) {{
            if (!currentCameras || currentCameras.length === 0) return;
            currentCameras.forEach(c => {{
                const cid = String(c.id);
                if (speakersMap && speakersMap[cid] !== undefined) {{
                    c.is_speaking = !!speakersMap[cid];
                }}
                updateChannelVisualState(c);
            }});
        }};

        function updateChannelVisualState(c) {{
            const cid = String(c.id);
            const strip = document.getElementById('channelStrip_cam_' + cid);
            const badge = document.getElementById('voiceBadge_cam_' + cid);
            if (!strip) return;
            
            if (c.is_speaking) {{
                strip.classList.add('speaking-active');
                if (badge) {{
                    badge.className = 'channel-voice-badge speaking';
                    badge.innerText = '🗣️ Falando';
                }}
            }} else if (c.is_muted) {{
                strip.classList.remove('speaking-active');
                if (badge) {{
                    badge.className = 'channel-voice-badge muted';
                    badge.innerText = '🔇 Mudo';
                }}
            }} else {{
                strip.classList.remove('speaking-active');
                if (badge) {{
                    badge.className = 'channel-voice-badge open';
                    badge.innerText = '🎙️ Aberto';
                }}
            }}
        }}

        // Decaimento suave contínuo dos medidores verticais
        setInterval(() => {{
            if (currentMasterDisplayPct > 0) {{
                currentMasterDisplayPct = Math.max(0, currentMasterDisplayPct - 8);
                const masterBarEl = document.getElementById('vuMasterBar');
                const masterDbEl = document.getElementById('vuMasterDbText');
                if (masterBarEl) masterBarEl.style.height = currentMasterDisplayPct + '%';
                if (currentMasterDisplayPct === 0 && masterDbEl) {{
                    masterDbEl.innerText = '-∞ dB (0%)';
                    masterDbEl.style.color = '#94a3b8';
                }}
            }}
            if (currentCameras && currentCameras.length > 0) {{
                currentCameras.forEach(c => {{
                    const cid = String(c.id);
                    const barEl = document.getElementById('vuBar_cam_' + cid);
                    const dbEl = document.getElementById('vuDb_cam_' + cid);
                    if (barEl) {{
                        const cur = parseFloat(barEl.style.height || '0');
                        if (cur > 0) {{
                            const nextVal = Math.max(0, cur - 8);
                            barEl.style.height = nextVal + '%';
                            if (nextVal === 0 && dbEl) {{
                                dbEl.innerText = '-∞ dB (0%)';
                                dbEl.style.color = '#94a3b8';
                            }}
                        }}
                    }}
                }});
            }}
        }}, 120);

        window.testAudioVuMeter = function() {{
            let step = 0;
            const testSteps = [0.10, 0.28, 0.52, 0.76, 0.94, 0.82, 0.60, 0.32, 0.68, 0.92, 0.98, 0.70, 0.40, 0.15, 0];
            // Para teste demonstrativo, ativa temporariamente a primeira câmera disponível
            if (currentCameras && currentCameras.length > 0) {{
                currentCameras[0].is_speaking = true;
                updateChannelVisualState(currentCameras[0]);
            }}
            const interval = setInterval(() => {{
                if (step >= testSteps.length) {{
                    clearInterval(interval);
                    if (currentCameras && currentCameras.length > 0) {{
                        currentCameras[0].is_speaking = false;
                        updateChannelVisualState(currentCameras[0]);
                    }}
                    return;
                }}
                if (window.onAudioPcmChunk) window.onAudioPcmChunk(testSteps[step]);
                step++;
            }}, 180);
        }};

        window.updateMasterVol = function(val) {{
            const pct = Math.round(parseFloat(val) * 100);
            const lbl = document.getElementById('lblMasterVol');
            if (lbl) lbl.innerText = pct + '%';
            if (!isLocalMuted && window.setLocalMonitorVolume) {{
                window.setLocalMonitorVolume(parseFloat(val));
            }}
        }};

        let isMasterMuted = false;
        window.toggleMasterMute = function() {{
            isMasterMuted = !isMasterMuted;
            const btn = document.getElementById('btnMuteMaster');
            if (isMasterMuted) {{
                if (window.setLocalMonitorVolume) window.setLocalMonitorVolume(0);
                if (btn) {{
                    btn.style.background = '#ef4444';
                    btn.style.color = '#fff';
                    btn.innerText = '🔇 Mudo';
                }}
            }} else {{
                const val = parseFloat(document.getElementById('rngMasterVol').value || '1');
                if (window.setLocalMonitorVolume) window.setLocalMonitorVolume(val);
                if (btn) {{
                    btn.style.background = '#0f172a';
                    btn.style.color = '#cbd5e1';
                    btn.innerText = '🔊 Ativo';
                }}
            }}
        }};

        let sourceVolumes = {{}};
        window.updateSourceVol = function(camId, val) {{
            sourceVolumes[camId] = parseFloat(val);
            const pct = Math.round(parseFloat(val) * 100);
            const lbl = document.getElementById('lblVol_cam_' + camId);
            if (lbl) lbl.innerText = pct + '%';
        }};

        let mutedSources = new Set();
        window.toggleSourceMute = function(camId) {{
            const btn = document.getElementById('btnMute_cam_' + camId);
            if (mutedSources.has(camId)) {{
                mutedSources.delete(camId);
                if (btn) {{
                    btn.style.background = '#0f172a';
                    btn.style.color = '#cbd5e1';
                    btn.innerText = '🔊 Ativo';
                }}
            }} else {{
                mutedSources.add(camId);
                if (btn) {{
                    btn.style.background = '#ef4444';
                    btn.style.color = '#fff';
                    btn.innerText = '🔇 Mudo';
                }}
            }}
        }};

        function showSaveNotice() {{
            const toast = document.getElementById('saveToast');
            toast.style.display = 'inline-flex';
            setTimeout(() => {{ toast.style.display = 'none'; }}, 2000);
        }}

        function toggleMonitorMute() {{
            isLocalMuted = !isLocalMuted;
            const btn = document.getElementById('btnMuteMonitor');
            const lbl = document.getElementById('lblMonitorStatus');
            if (isLocalMuted) {{
                if (window.setLocalMonitorVolume) window.setLocalMonitorVolume(0);
                if (btn) {{
                    btn.style.background = '#ef4444';
                    btn.style.color = '#ffffff';
                }}
                if (lbl) lbl.innerText = 'Monitor Local: Mudo 🔇';
                localStorage.setItem('rnp_monitor_muted', '1');
            }} else {{
                const val = parseFloat(document.getElementById('rngMonitorVol').value);
                if (window.setLocalMonitorVolume) window.setLocalMonitorVolume(val);
                if (btn) {{
                    btn.style.background = '#0f172a';
                    btn.style.color = '#cbd5e1';
                }}
                if (lbl) lbl.innerText = 'Monitor Local: Ligado 🔊';
                localStorage.setItem('rnp_monitor_muted', '0');
            }}
        }}

        function updateMonitorVol(val) {{
            if (!isLocalMuted && window.setLocalMonitorVolume) {{
                window.setLocalMonitorVolume(parseFloat(val));
            }}
            localStorage.setItem('rnp_monitor_vol', val);
        }}

        window.addEventListener('DOMContentLoaded', () => {{
            const savedVol = localStorage.getItem('rnp_monitor_vol');
            if (savedVol !== null) {{
                document.getElementById('rngMonitorVol').value = savedVol;
                if (window.setLocalMonitorVolume) window.setLocalMonitorVolume(parseFloat(savedVol));
            }}
            const savedMuted = localStorage.getItem('rnp_monitor_muted');
            if (savedMuted === '1') {{
                toggleMonitorMute();
            }}
        }});

        async function autoSaveField(field, val) {{
            const payload = {{}};
            payload[field] = val;
            await fetch('/api/config/save', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify(payload)
            }});
            showSaveNotice();
        }}

        async function saveAllConfigurations() {{
            const config = {{
                room_url: document.getElementById('roomUrl').value.trim(),
                user_name: document.getElementById('userName').value.trim(),
                user_email: document.getElementById('userEmail').value.trim(),
                youtube_url: document.getElementById('ytUrl').value.trim(),
                youtube_auto_connect: document.getElementById('chkYtAutoConnect').checked,
                show_camera_names: document.getElementById('chkShowNames').checked,
                camera_name_style: document.getElementById('selNameStyle').value,
                grid_layout_mode: document.getElementById('selGridLayout').value,
                stage_preset: activeStagePreset,
                libras_size: document.getElementById('selLibrasSize').value,
                poll_display_mode: document.getElementById('selPollDisplayMode').value,
                poll_display_duration: parseInt(document.getElementById('selPollDuration').value),
                chat_style: document.getElementById('selChatTheme').value,
                chat_font_size: document.getElementById('selChatSize').value,
                chat_max_messages: parseInt(document.getElementById('selChatMax').value),
                libras_camera_ids: librasCamIds
            }};

            await fetch('/api/config/save', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify(config)
            }});
            showSaveNotice();
        }}

        async function loadAllConfigurations() {{
            try {{
                const res = await fetch('/api/config?t=' + Date.now());
                const cfg = await res.json();
                document.getElementById('roomUrl').value = cfg.room_url || '';
                document.getElementById('userName').value = cfg.user_name || '';
                document.getElementById('userEmail').value = cfg.user_email || '';
                document.getElementById('ytUrl').value = cfg.youtube_url || '';
                document.getElementById('chkYtAutoConnect').checked = !!cfg.youtube_auto_connect;
                document.getElementById('chkShowNames').checked = !!cfg.show_camera_names;
                document.getElementById('selNameStyle').value = cfg.camera_name_style || 'glass';
                document.getElementById('selGridLayout').value = cfg.grid_layout_mode || 'auto';
                document.getElementById('selLibrasSize').value = cfg.libras_size || 'equal';
                document.getElementById('selPollDisplayMode').value = cfg.poll_display_mode || 'results_only';
                document.getElementById('selPollDuration').value = (cfg.poll_display_duration !== undefined) ? cfg.poll_display_duration : 15;
                document.getElementById('selChatTheme').value = cfg.chat_style || 'glass';
                document.getElementById('selChatSize').value = cfg.chat_font_size || 'medium';
                document.getElementById('selChatMax').value = cfg.chat_max_messages || 6;
                activeStagePreset = cfg.stage_preset || 'side-by-side';
                activeLibrasSize = cfg.libras_size || 'equal';
                librasCamIds = (cfg.libras_camera_ids || []).map(String);
                updatePresetButtonsUI(activeStagePreset);
                showSaveNotice();
            }} catch (e) {{}}
        }}

        async function resetAllConfigurations() {{
            if (confirm('Deseja restaurar as configurações padrão de fábrica?')) {{
                await fetch('/api/config/reset', {{ method: 'POST' }});
                await loadAllConfigurations();
            }}
        }}

        async function setStagePreset(presetName) {{
            activeStagePreset = presetName;
            updatePresetButtonsUI(presetName);
            await fetch('/api/grid/config', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{ stage_preset: presetName, libras_size: activeLibrasSize }})
            }});
            showSaveNotice();
        }}

        async function updateLibrasSizeConfig() {{
            const libras_size = document.getElementById('selLibrasSize').value;
            activeLibrasSize = libras_size;
            await fetch('/api/grid/config', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{ libras_size: libras_size }})
            }});
            showSaveNotice();
        }}

        async function updatePollConfig() {{
            const poll_display_mode = document.getElementById('selPollDisplayMode').value;
            const poll_display_duration = parseInt(document.getElementById('selPollDuration').value);
            await fetch('/api/poll/config', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{ poll_display_mode, poll_display_duration }})
            }});
            showSaveNotice();
        }}

        async function toggleForcePoll(forceVisible) {{
            await fetch('/api/poll/config', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{ poll_force_visible: forceVisible }})
            }});
            const btnShow = document.getElementById('btnForcePollShow');
            const btnHide = document.getElementById('btnForcePollHide');
            if (forceVisible) {{
                btnShow.className = 'btn-ctrl active-pin';
                btnHide.className = 'btn-ctrl';
            }} else {{
                btnShow.className = 'btn-ctrl';
                btnHide.className = 'btn-ctrl active-focus';
            }}
            showSaveNotice();
        }}

        function updatePresetButtonsUI(presetName) {{
            const presets = ['side-by-side', 'bottom-bar', 'pip-bottom-right', 'pip-top-right', 'split-50-50', 'slide-only'];
            presets.forEach(p => {{
                const btn = document.getElementById('btnPreset_' + p);
                if (btn) {{
                    if (p === presetName) {{
                        btn.className = 'btn-ctrl active-preset';
                    }} else {{
                        btn.className = 'btn-ctrl';
                    }}
                }}
            }});
        }}

        async function handleYtConnect() {{
            const youtube_url = document.getElementById('ytUrl').value.trim();
            if (!youtube_url) {{
                alert('Digite a URL ou ID do vídeo do YouTube!');
                return;
            }}
            const badge = document.getElementById('ytStatusBadge');
            badge.className = 'badge connecting';
            badge.innerHTML = '● Conectando...';

            await fetch('/api/youtube/connect', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{ youtube_url }})
            }});
            showSaveNotice();
        }}

        async function handleYtDisconnect() {{
            await fetch('/api/youtube/disconnect', {{ method: 'POST' }});
        }}

        async function highlightMessage(msg) {{
            activeFeaturedMsg = msg;
            await fetch('/api/chat/highlight', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{ message: msg }})
            }});
            renderFeaturedBox(msg);
            renderChatList(currentMessages);
        }}

        async function clearFeaturedMessage() {{
            activeFeaturedMsg = null;
            await fetch('/api/chat/highlight', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{ message: null }})
            }});
            renderFeaturedBox(null);
            renderChatList(currentMessages);
        }}

        function renderFeaturedBox(msg) {{
            const box = document.getElementById('featuredBox');
            if (msg) {{
                box.style.display = 'flex';
                document.getElementById('featUser').innerText = msg.user || 'Participante';
                document.getElementById('featText').innerHTML = msg.text || '';
                const b = document.getElementById('featSourceBadge');
                if (msg.source === 'youtube') {{
                    b.className = 'source-tag yt';
                    b.innerText = 'YouTube Live';
                }} else {{
                    b.className = 'source-tag rnp';
                    b.innerText = 'RNP WebConf';
                }}
            }} else {{
                box.style.display = 'none';
            }}
        }}

        function renderChatList(msgs) {{
            const container = document.getElementById('chatListContainer');
            document.getElementById('chatTotalCount').innerText = msgs.length + ' msgs';
            
            if (msgs.length === 0) {{
                container.innerHTML = '<div style="color: #64748b; font-size: 12px; padding: 8px;">Nenhuma mensagem recebida ainda.</div>';
                return;
            }}

            const reversed = [...msgs].reverse();
            container.innerHTML = '';

            reversed.forEach(m => {{
                const isFeatured = activeFeaturedMsg && (activeFeaturedMsg.text === m.text && activeFeaturedMsg.user === m.user);
                const row = document.createElement('div');
                row.className = 'chat-msg-row' + (isFeatured ? ' is-featured' : '');

                const isYt = (m.source === 'youtube');
                const srcBadge = isYt ? '<span class="source-tag yt">YT</span>' : '<span class="source-tag rnp">RNP</span>';

                row.innerHTML = `
                    <div style="flex: 1; min-width: 0;">
                        <div style="display: flex; align-items: center; gap: 6px; margin-bottom: 2px;">
                            ${{srcBadge}}
                            <strong style="font-size: 12px; color: ${{isYt ? '#f87171' : '#38bdf8'}};">${{m.user}}</strong>
                            <span style="font-size: 10px; color: #64748b;">${{m.time || ''}}</span>
                        </div>
                        <div style="font-size: 12px; color: #e2e8f0; word-break: break-word;">${{m.text}}</div>
                    </div>
                    <div style="flex-shrink: 0;">
                        ${{isFeatured ? 
                            `<button class="btn-ctrl active-focus" style="padding: 4px 8px; font-size: 11px;" onclick="clearFeaturedMessage()">❌ Desfocar</button>` :
                            `<button class="btn-ctrl" style="padding: 4px 8px; font-size: 11px;" onclick='highlightMessage(${{JSON.stringify(m)}})'>🌟 Destacar</button>`
                        }}
                    </div>
                `;
                container.appendChild(row);
            }});
        }}

        let prevConnected = null;
        let prevCamMap = new Map();
        let prevHasPresentation = false;
        let prevLibrasCount = 0;
        let lastEventTimeout = null;

        function triggerSourceAlert(icon, text, type = 'info') {{
            const banner = document.getElementById('liveSourceEventBanner');
            if (!banner) return;
            banner.style.display = 'flex';
            if (type === 'success') {{
                banner.style.background = 'rgba(16, 185, 129, 0.2)';
                banner.style.border = '1px solid #10b981';
                banner.style.color = '#34d399';
            }} else if (type === 'warn') {{
                banner.style.background = 'rgba(239, 68, 68, 0.2)';
                banner.style.border = '1px solid #ef4444';
                banner.style.color = '#f87171';
            }} else {{
                banner.style.background = 'rgba(56, 189, 248, 0.2)';
                banner.style.border = '1px solid #38bdf8';
                banner.style.color = '#38bdf8';
            }}
            banner.innerHTML = `<span>${{icon}}</span> <span>${{text}}</span>`;
            if (lastEventTimeout) clearTimeout(lastEventTimeout);
            lastEventTimeout = setTimeout(() => {{
                banner.style.display = 'none';
            }}, 4500);
        }}

        async function fetchStatus() {{
            try {{
                const res = await fetch('/api/status?t=' + Date.now());
                if (!res.ok) {{
                    document.getElementById('healthHeartbeat').innerText = '⚠️ Erro de API';
                    return;
                }}
                const data = await res.json();
                document.getElementById('healthHeartbeat').innerText = '● Ao Vivo (500ms)';
                
                const badge = document.getElementById('statusBadge');
                if (data.connecting) {{
                    badge.className = 'badge connecting';
                    badge.innerHTML = '● Conectando...';
                }} else if (data.connected) {{
                    badge.className = 'badge connected';
                    badge.innerHTML = '● Conectado à RNP';
                }} else {{
                    badge.className = 'badge disconnected';
                    badge.innerHTML = '● Desconectado';
                }}

                const ytBadge = document.getElementById('ytStatusBadge');
                if (data.youtube_connected) {{
                    ytBadge.className = 'badge connected';
                    ytBadge.innerHTML = '● YouTube Ativo';
                }} else {{
                    ytBadge.className = 'badge disconnected';
                    ytBadge.innerHTML = '● ' + (data.youtube_status || 'Desconectado');
                }}

                if (data.status_message) {{
                    document.getElementById('logBox').innerText = '> ' + data.status_message;
                }}

                focusedCamId = (data.focused_camera_id !== null && data.focused_camera_id !== undefined) ? String(data.focused_camera_id) : null;
                pinnedCamIds = (data.pinned_camera_ids || []).map(String);
                librasCamIds = (data.libras_camera_ids || []).map(String);

                const cams = data.cameras || [];
                const librasCams = cams.filter(c => librasCamIds.includes(String(c.id)) || c.is_libras);

                // --- 📡 MONITOR ATIVO DE FONTES & EVENTOS ---
                // 1. Conexão Sala RNP
                const pillRnp = document.getElementById('healthPillRnp');
                const dotRnp = document.getElementById('dotRnp');
                const stateRnp = document.getElementById('healthStateRnp');
                if (data.connecting) {{
                    dotRnp.className = 'pill-dot connecting';
                    stateRnp.innerText = 'Conectando...';
                    pillRnp.className = 'source-health-pill';
                }} else if (data.connected) {{
                    dotRnp.className = 'pill-dot connected';
                    stateRnp.innerText = '🟢 Online';
                    pillRnp.className = 'source-health-pill is-active';
                }} else {{
                    dotRnp.className = 'pill-dot disconnected';
                    stateRnp.innerText = '⚪ Offline';
                    pillRnp.className = 'source-health-pill';
                }}

                if (prevConnected !== null) {{
                    if (!prevConnected && data.connected) {{
                        triggerSourceAlert('⚡', 'Conectado à sala RNP com sucesso!', 'success');
                    }} else if (prevConnected && !data.connected) {{
                        triggerSourceAlert('⚠️', 'Conexão com a sala RNP foi interrompida ou caiu.', 'warn');
                    }}
                }}
                prevConnected = !!data.connected;

                // 2. Apresentação / Compartilhamento de Tela
                const dotSlides = document.getElementById('dotSlides');
                const stateSlides = document.getElementById('healthStateSlides');
                const pillSlides = document.getElementById('healthPillSlides');
                if (data.has_presentation) {{
                    dotSlides.className = 'pill-dot connected';
                    stateSlides.innerText = '🟢 1080p Ativo';
                    pillSlides.className = 'source-health-pill is-active';
                }} else {{
                    dotSlides.className = 'pill-dot disconnected';
                    stateSlides.innerText = data.connected ? '⚪ Aguardando' : '⚪ Sem Slides';
                    pillSlides.className = 'source-health-pill';
                }}

                if (prevConnected && data.connected) {{
                    if (!prevHasPresentation && data.has_presentation) {{
                        triggerSourceAlert('📊', 'Apresentação/Slides iniciados pelo palestrante!', 'success');
                    }} else if (prevHasPresentation && !data.has_presentation) {{
                        triggerSourceAlert('📊', 'Apresentação/Slides pausados ou finalizados.', 'info');
                    }}
                }}
                prevHasPresentation = !!data.has_presentation;

                // 3. Monitor de Câmeras & Detecção de Entrada/Saída
                const dotCams = document.getElementById('dotCams');
                const stateCams = document.getElementById('healthStateCams');
                const pillCams = document.getElementById('healthPillCams');
                stateCams.innerText = cams.length > 0 ? (cams.length + ' Ativa(s)') : '0 Câmeras';
                if (cams.length > 0) {{
                    dotCams.className = 'pill-dot connected';
                    pillCams.className = 'source-health-pill is-active';
                }} else {{
                    dotCams.className = 'pill-dot disconnected';
                    pillCams.className = 'source-health-pill';
                }}

                const currentCamMap = new Map(cams.map(c => [String(c.id), c.name]));
                if (prevConnected && data.connected) {{
                    // Câmera entrou
                    currentCamMap.forEach((name, id) => {{
                        if (!prevCamMap.has(id)) {{
                            triggerSourceAlert('🎥', 'Nova câmera conectada: ' + name, 'success');
                        }}
                    }});
                    // Câmera saiu
                    prevCamMap.forEach((name, id) => {{
                        if (!currentCamMap.has(id)) {{
                            triggerSourceAlert('🔴', 'Câmera desconectada: ' + name, 'warn');
                        }}
                    }});
                }}
                prevCamMap = currentCamMap;

                // 4. Intérprete de Libras
                const dotLibras = document.getElementById('dotLibras');
                const stateLibras = document.getElementById('healthStateLibras');
                const pillLibras = document.getElementById('healthPillLibras');
                if (librasCams.length > 0) {{
                    dotLibras.className = 'pill-dot connected';
                    stateLibras.innerText = '🟢 ' + (librasCams[0].name.split(' ')[0] || 'Libras');
                    pillLibras.className = 'source-health-pill is-active';
                }} else {{
                    dotLibras.className = 'pill-dot disconnected';
                    stateLibras.innerText = '⚪ Nenhum';
                    pillLibras.className = 'source-health-pill';
                }}

                if (prevConnected && data.connected) {{
                    if (prevLibrasCount === 0 && librasCams.length > 0) {{
                        triggerSourceAlert('🤟', 'Intérprete de Libras reconhecido: ' + librasCams[0].name, 'success');
                    }} else if (prevLibrasCount > 0 && librasCams.length === 0) {{
                        triggerSourceAlert('🤟', 'Intérprete de Libras desconectou.', 'warn');
                    }}
                }}
                prevLibrasCount = librasCams.length;

                // 5. YouTube Live Chat
                const dotYt = document.getElementById('dotYt');
                const stateYt = document.getElementById('healthStateYt');
                const pillYt = document.getElementById('healthPillYt');
                if (data.youtube_connected) {{
                    dotYt.className = 'pill-dot connected';
                    stateYt.innerText = '🟢 Ao Vivo';
                    pillYt.className = 'source-health-pill is-active';
                }} else {{
                    dotYt.className = 'pill-dot disconnected';
                    stateYt.innerText = '⚪ ' + (data.youtube_status || 'Desconectado');
                    pillYt.className = 'source-health-pill';
                }}

                // 6. Áudio Digital PCM
                const dotAudio = document.getElementById('dotAudio');
                const stateAudio = document.getElementById('healthStateAudio');
                const pillAudio = document.getElementById('healthPillAudio');
                const pcmListeners = data.pcm_listeners || 0;
                if (pcmListeners > 0) {{
                    dotAudio.className = 'pill-dot connected';
                    stateAudio.innerText = '🟢 ' + pcmListeners + ' OBS Ouvinte(s)';
                    pillAudio.className = 'source-health-pill is-active';
                }} else if (data.connected) {{
                    dotAudio.className = 'pill-dot connected';
                    stateAudio.innerText = '🟢 PCM Ativo';
                    pillAudio.className = 'source-health-pill is-active';
                }} else {{
                    dotAudio.className = 'pill-dot disconnected';
                    stateAudio.innerText = '⚪ Standby';
                    pillAudio.className = 'source-health-pill';
                }}

                const savedCount = (data.libras_saved_names || []).length;
                const elSavedCount = document.getElementById('savedInterpretersCount');
                if (elSavedCount) elSavedCount.innerText = savedCount + ' salvos';

                const elAuto = document.getElementById('btnAutoLibras');
                if (elAuto) {{
                    if (data.libras_auto_detect) {{
                        elAuto.className = 'btn-ctrl active-pin';
                        elAuto.innerText = '🛡️ Auto-Reconhecimento: ATIVO';
                    }} else {{
                        elAuto.className = 'btn-ctrl';
                        elAuto.innerText = '⚪ Auto-Reconhecimento: DESLIGADO';
                    }}
                }}

                if (data.stage_preset) {{
                    activeStagePreset = data.stage_preset;
                    updatePresetButtonsUI(activeStagePreset);
                }}

                if (data.libras_size) {{
                    activeLibrasSize = data.libras_size;
                    const el = document.getElementById('selLibrasSize');
                    if (el) el.value = activeLibrasSize;
                }}

                if (data.poll_display_mode) {{
                    const elMode = document.getElementById('selPollDisplayMode');
                    if (elMode) elMode.value = data.poll_display_mode;
                }}

                if (data.poll_display_duration !== undefined) {{
                    const elDur = document.getElementById('selPollDuration');
                    if (elDur) elDur.value = String(data.poll_display_duration);
                }}

                if (data.poll_style) {{
                    const elStyle = document.getElementById('selPollStyle');
                    if (elStyle) elStyle.value = data.poll_style;
                }}

                if (data.connected) {{
                    document.getElementById('thumbScreen').src = '/raw_frame/presentation?t=' + Date.now();
                }}

                // Poll Sync
                renderPoll(data.poll);

                // Featured Message Sync
                activeFeaturedMsg = data.featured_message || null;
                renderFeaturedBox(activeFeaturedMsg);

                // Messages sync
                currentMessages = data.chat_messages || [];
                renderChatList(currentMessages);

                // Cameras sync
                currentCameras = cams;
                document.getElementById('camTotalCount').innerText = cams.length;

                // --- 🎙️ SINCRONIZAÇÃO DA MESA DE SOM VERTICAL (CANAIS INDIVIDUAIS) ---
                const indContainer = document.getElementById('individualChannelsContainer');
                const noIndPlaceholder = document.getElementById('noIndividualChannelsPlaceholder');
                if (indContainer) {{
                    if (cams.length === 0) {{
                        if (noIndPlaceholder) noIndPlaceholder.style.display = 'flex';
                        indContainer.innerHTML = '';
                    }} else {{
                        if (noIndPlaceholder) noIndPlaceholder.style.display = 'none';
                        const existingIds = new Set(Array.from(indContainer.querySelectorAll('.audio-channel-strip')).map(el => el.getAttribute('data-cam-id')));
                        const newIds = new Set(cams.map(c => String(c.id)));
                        
                        // Remover canais desconectados
                        existingIds.forEach(id => {{
                            if (!newIds.has(id)) {{
                                const strip = document.getElementById('channelStrip_cam_' + id);
                                if (strip) strip.remove();
                            }}
                        }});

                        // Inserir ou atualizar canal de cada câmera
                        cams.forEach(c => {{
                            const cid = String(c.id);
                            let strip = document.getElementById('channelStrip_cam_' + cid);
                            if (!strip) {{
                                strip = document.createElement('div');
                                strip.className = 'audio-channel-strip';
                                strip.id = 'channelStrip_cam_' + cid;
                                strip.setAttribute('data-cam-id', cid);
                                if (c.is_libras) strip.style.borderColor = 'rgba(6, 182, 212, 0.4)';
                                strip.innerHTML = `
                                    <div class="channel-header">
                                        <span style="font-size: 18px;">${{c.is_libras ? '🤟' : '🎥'}}</span>
                                        <span class="channel-title" title="${{c.name}}">${{c.name}}</span>
                                        <div style="display:flex; align-items:center; justify-content:center; gap:4px; width:100%; margin-top:2px;">
                                            <span class="channel-tag">ID: cam_${{c.id}}</span>
                                            <span class="channel-voice-badge" id="voiceBadge_cam_${{c.id}}">${{c.is_speaking ? '🗣️ Falando' : (c.is_muted ? '🔇 Mudo' : '🎙️ Aberto')}}</span>
                                        </div>
                                    </div>
                                    <div class="channel-meter-box">
                                        <div class="meter-scale-vertical">
                                            <span style="color:#ef4444; font-weight:bold;">0</span>
                                            <span style="color:#f59e0b;">-3</span>
                                            <span style="color:#f59e0b;">-6</span>
                                            <span>-12</span>
                                            <span>-20</span>
                                            <span>-30</span>
                                            <span>-40</span>
                                            <span>-60</span>
                                        </div>
                                        <div class="vu-track-vertical">
                                            <div class="vu-fill-vertical" id="vuBar_cam_${{c.id}}"></div>
                                            <div class="vu-peak-vertical" id="vuPeak_cam_${{c.id}}"></div>
                                        </div>
                                    </div>
                                    <div class="meter-db-readout" id="vuDb_cam_${{c.id}}">-∞ dB (0%)</div>
                                    <button class="channel-btn-copy" onclick="copyUrl('http://localhost:3333/audio?source=${{c.id}}', this)" title="Copiar link de áudio exclusivo desta fonte para o OBS">
                                        📋 Link Fonte ${{c.id}}
                                    </button>
                                    <div class="channel-fader-box">
                                        <div style="display:flex; justify-content:space-between; width:100%; font-size:10px; color:#94a3b8;">
                                            <span>Vol:</span>
                                            <span id="lblVol_cam_${{c.id}}">100%</span>
                                        </div>
                                        <input type="range" id="rngVol_cam_${{c.id}}" min="0" max="1.5" step="0.05" value="1" style="width:100%; accent-color:#38bdf8; cursor:pointer;" oninput="updateSourceVol('${{c.id}}', this.value)">
                                        <button class="btn-ctrl" id="btnMute_cam_${{c.id}}" style="width:100%; padding:3px 6px; font-size:10.5px; margin-top:2px;" onclick="toggleSourceMute('${{c.id}}')">
                                            🔊 Ativo
                                        </button>
                                    </div>
                                `;
                                indContainer.appendChild(strip);
                            }} else {{
                                const titleEl = strip.querySelector('.channel-title');
                                if (titleEl && titleEl.title !== c.name) {{
                                    titleEl.title = c.name;
                                    titleEl.innerText = c.name;
                                }}
                            }}
                            updateChannelVisualState(c);
                        }});
                    }}
                }}
                
                const grid = document.getElementById('camerasGrid');
                grid.innerHTML = '';

                if (cams.length === 0) {{
                    grid.innerHTML = '<div style="color: #64748b; font-size: 12px; padding: 10px;">Nenhuma câmera ligada no momento.</div>';
                }} else {{
                    cams.forEach(c => {{
                        const cid = String(c.id);
                        const isFocus = (focusedCamId === cid);
                        const isPin = pinnedCamIds.includes(cid);
                        const isLibras = (c.is_libras || librasCamIds.includes(cid));

                        const box = document.createElement('div');
                        box.className = 'mod-box';
                        if (isFocus) box.style.borderColor = '#eab308';
                        if (isLibras) box.style.borderColor = '#06b6d4';
                        if (isPin && !isFocus && !isLibras) box.style.borderColor = '#10b981';

                        box.innerHTML = `
                            <div class="mod-title">
                                <span style="display:flex; align-items:center; gap:5px;">
                                    ${{isLibras ? '🤟' : (isFocus ? '🌟' : (isPin ? '📌' : '🎥'))}} <strong>${{c.name}}</strong>
                                </span>
                                <span style="font-size: 10.5px; color: #10b981;">● ${{c.width}}x${{c.height}}</span>
                            </div>
                            <div class="thumb-box">
                                <img src="/raw_frame/camera/${{c.id}}?t=${{Date.now()}}" id="thumbCam_${{c.id}}" onerror="this.style.opacity=0.3" onload="this.style.opacity=1">
                                ${{isLibras ? '<div class="libras-badge-overlay">🤟 Libras</div>' : ''}}
                            </div>
                            
                            <div class="cam-controls">
                                <button class="btn-ctrl ${{isFocus ? 'active-focus' : ''}}" onclick="toggleFocus('${{c.id}}')">
                                    🌟 ${{isFocus ? 'Desfocar' : 'Focar'}}
                                </button>
                                <button class="btn-ctrl ${{isLibras ? 'active-libras' : ''}}" onclick="toggleLibras('${{c.id}}')">
                                    🤟 ${{isLibras ? 'Libras (Ativo)' : 'Libras'}}
                                </button>
                                <button class="btn-ctrl ${{isPin ? 'active-pin' : ''}}" onclick="togglePin('${{c.id}}')">
                                    📌 ${{isPin ? 'Desfixar' : 'Fixar'}}
                                </button>
                                <button class="btn-ctrl" onclick="renameCamera('${{c.id}}', '${{c.name}}')">
                                    ✏️ Renomear
                                </button>
                            </div>

                            <button class="btn-copy" onclick="copyUrl('http://localhost:3333/camera/${{c.id}}')">📋 Link Câmera ${{c.id}}</button>
                        `;
                        grid.appendChild(box);
                    }});
                }}
            }} catch (err) {{
                document.getElementById('healthHeartbeat').innerText = '🔴 Servidor Offline';
            }}
        }}

        async function updateGridConfig() {{
            const show_camera_names = document.getElementById('chkShowNames').checked;
            const camera_name_style = document.getElementById('selNameStyle').value;
            const grid_layout_mode = document.getElementById('selGridLayout').value;
            await fetch('/api/grid/config', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{ show_camera_names, camera_name_style, grid_layout_mode, stage_preset: activeStagePreset, libras_camera_ids: librasCamIds, libras_size: activeLibrasSize }})
            }});
            showSaveNotice();
        }}

        async function updateChatConfig() {{
            const chat_style = document.getElementById('selChatTheme').value;
            const chat_font_size = document.getElementById('selChatSize').value;
            const chat_max_messages = document.getElementById('selChatMax').value;
            const show_chat_avatars = document.getElementById('chkShowAvatars').checked;
            await fetch('/api/chat/config', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{ chat_style, chat_font_size, chat_max_messages, show_chat_avatars }})
            }});
            showSaveNotice();
        }}

        async function toggleFocus(camId) {{
            const targetId = String(camId);
            const newFocus = (focusedCamId === targetId) ? null : targetId;
            await fetch('/api/grid/config', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{ focused_camera_id: newFocus }})
            }});
            fetchStatus();
        }}

        async function toggleLibras(camId) {{
            const targetId = String(camId);
            let newLibras = [...librasCamIds];
            if (newLibras.includes(targetId)) {{
                newLibras = newLibras.filter(id => id !== targetId);
            }} else {{
                newLibras.push(targetId);
            }}
            librasCamIds = newLibras;
            await fetch('/api/grid/config', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{ libras_camera_ids: newLibras }})
            }});
            fetchStatus();
            showSaveNotice();
        }}

        async function toggleLibrasAutoDetect() {{
            const res = await fetch('/api/libras/toggle_auto', {{ method: 'POST' }});
            const data = await res.json();
            const btn = document.getElementById('btnAutoLibras');
            if (btn) {{
                if (data.libras_auto_detect) {{
                    btn.className = 'btn-ctrl active-pin';
                    btn.innerText = '🛡️ Auto-Reconhecimento: ATIVO';
                }} else {{
                    btn.className = 'btn-ctrl';
                    btn.innerText = '⚪ Auto-Reconhecimento: DESLIGADO';
                }}
            }}
            fetchStatus();
            showSaveNotice();
        }}

        async function clearSavedLibras() {{
            if (!confirm('Deseja limpar todos os nomes de intérpretes salvos e desmarcar Libras de todas as câmeras?')) return;
            await fetch('/api/libras/clear_saved', {{ method: 'POST' }});
            librasCamIds = [];
            fetchStatus();
            showSaveNotice();
        }}

        async function togglePin(camId) {{
            const targetId = String(camId);
            let newPins = [...pinnedCamIds];
            if (newPins.includes(targetId)) {{
                newPins = newPins.filter(id => id !== targetId);
            }} else {{
                newPins.push(targetId);
            }}
            await fetch('/api/grid/config', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{ pinned_camera_ids: newPins }})
            }});
            fetchStatus();
        }}

        async function clearFocusAndPins() {{
            await fetch('/api/grid/config', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{ focused_camera_id: null, pinned_camera_ids: [], libras_camera_ids: [] }})
            }});
            librasCamIds = [];
            fetchStatus();
        }}

        async function renameCamera(camId, currentName) {{
            const newName = prompt('Digite o novo nome para esta câmera:\\n(Deixe em branco e clique em OK para restaurar o nome padrão da RNP)', currentName);
            if (newName !== null) {{
                await fetch('/api/grid/config', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ cam_id: String(camId), custom_name: newName.trim() }})
                }});
                fetchStatus();
                showSaveNotice();
            }}
        }}

        async function handleConnect() {{
            const room_url = document.getElementById('roomUrl').value.trim();
            const user_name = document.getElementById('userName').value.trim();
            const user_email = document.getElementById('userEmail').value.trim();

            document.getElementById('logBox').innerText = '> Enviando comando de conexão...';
            document.getElementById('statusBadge').className = 'badge connecting';
            document.getElementById('statusBadge').innerHTML = '● Conectando...';

            try {{
                const res = await fetch('/api/connect', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ room_url, user_name, user_email }})
                }});
                const data = await res.json();
                document.getElementById('logBox').innerText = '> ' + data.message;
            }} catch (e) {{
                document.getElementById('logBox').innerText = '> Erro ao enviar requisição: ' + e;
            }}
        }}

        async function handleDisconnect() {{
            document.getElementById('logBox').innerText = '> Desconectando...';
            try {{
                await fetch('/api/disconnect', {{ method: 'POST' }});
            }} catch (e) {{}}
        }}

        async function handleExitApplication() {{
            if (!confirm('Deseja realmente desconectar da sala RNP e encerrar o aplicativo por completo?')) {{
                return;
            }}
            document.getElementById('logBox').innerText = '> Encerrando servidor e desconectando da sala...';
            try {{
                await fetch('/api/server/stop', {{ method: 'POST' }});
            }} catch (e) {{}}
            setTimeout(() => {{
                window.close();
            }}, 600);
        }}

        function renderPoll(poll) {{
            const card = document.getElementById('pollDockCard');
            if (card) card.style.display = 'block';

            const statusTitle = document.getElementById('pollStatusTitle');
            const badgeStatus = document.getElementById('pollBadgeStatus');
            const questionText = document.getElementById('pollQuestionText');
            const optsContainer = document.getElementById('pollOptionsContainer');

            if (!poll || !poll.is_active) {{
                if (statusTitle) {{
                    statusTitle.innerText = '📊 Enquete da Sala RNP';
                    statusTitle.style.color = '#10b981';
                }}
                if (badgeStatus) {{
                    badgeStatus.className = 'badge disconnected';
                    badgeStatus.innerText = '⚪ Standby (Pronto para o OBS)';
                }}
                if (questionText) {{
                    questionText.innerHTML = '<span style="color: #94a3b8; font-weight: normal; font-size: 12px;">Nenhuma enquete ativa na sala no momento. O link acima pode ser adicionado ao OBS como <strong>Navegador (Browser Source 1920x1080 transparente)</strong> antes da transmissão começar. Quando uma enquete for aberta na RNP, ela aparecerá na tela automaticamente!</span>';
                }}
                if (optsContainer) optsContainer.innerHTML = '';
                return;
            }}

            const isPublished = (poll.published || poll.status === 'published' || poll.status === 'results');
            if (statusTitle) {{
                if (isPublished) {{
                    statusTitle.innerText = '📊 Resultados Finais (' + (poll.total_votes || 0) + ' votos)';
                    statusTitle.style.color = '#10b981';
                }} else {{
                    statusTitle.innerText = '🗳️ Enquete em Votação na Sala';
                    statusTitle.style.color = '#eab308';
                }}
            }}
            if (badgeStatus) {{
                if (isPublished) {{
                    badgeStatus.className = 'badge connected';
                    badgeStatus.innerText = '🟢 Publicada no OBS';
                }} else {{
                    badgeStatus.className = 'badge connecting';
                    badgeStatus.innerText = '🟡 Votação Aberta';
                }}
            }}

            if (questionText) {{
                questionText.innerText = poll.question || 'Opções da Enquete';
            }}

            if (optsContainer) {{
                optsContainer.innerHTML = '';
                const totalVotes = poll.total_votes || 0;

                (poll.options || []).forEach(opt => {{
                    const row = document.createElement('div');
                    row.style.cssText = 'background: rgba(30, 41, 59, 0.7); border: 1px solid rgba(255,255,255,0.08); border-radius: 8px; padding: 6px 10px;';
                    
                    const pct = opt.percentage || (totalVotes > 0 ? Math.round((opt.votes / totalVotes) * 100) : 0);
                    const votes = opt.votes || 0;

                    row.innerHTML = `
                        <div style="display: flex; justify-content: space-between; font-size: 12px; font-weight: 700; color: #ffffff; margin-bottom: 4px;">
                            <span>${{opt.text}}</span>
                            <span style="color: #38bdf8;">${{votes > 0 ? votes + ' votos (' + pct + '%)' : pct + '%'}}</span>
                        </div>
                        <div style="width: 100%; height: 5px; background: #0f172a; border-radius: 3px; overflow: hidden;">
                            <div style="width: ${{pct}}%; height: 100%; background: linear-gradient(90deg, #06b6d4, #10b981); transition: width 0.3s ease;"></div>
                        </div>
                    `;
                    optsContainer.appendChild(row);
                }});
            }}
        }}

        async function updatePollConfig() {{
            const poll_display_mode = document.getElementById('selPollDisplayMode').value;
            const poll_display_duration = parseInt(document.getElementById('selPollDuration').value);
            const poll_style = document.getElementById('selPollStyle').value;
            await fetch('/api/poll/config', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{ poll_display_mode, poll_display_duration, poll_style }})
            }});
            showSaveNotice();
        }}

        async function toggleForcePoll(show) {{
            if (show) {{
                await fetch('/api/poll/show', {{ method: 'POST' }});
            }} else {{
                await fetch('/api/poll/hide', {{ method: 'POST' }});
            }}
            showSaveNotice();
        }}

        async function clearPoll() {{
            if (!confirm('Deseja limpar e reiniciar a enquete atual?')) return;
            await fetch('/api/poll/clear', {{ method: 'POST' }});
            fetchStatus();
        }}

        async function simulatePoll() {{
            await fetch('/api/poll/simulate', {{ method: 'POST' }});
            const toast = document.getElementById('saveToast');
            if (toast) {{
                toast.innerText = '🧪 Enquete de Teste Exibida no OBS!';
                toast.style.display = 'inline-block';
                setTimeout(() => {{ toast.style.display = 'none'; }}, 2500);
            }}
            fetchStatus();
        }}

        async function changeControlTheme(th) {{
            document.body.className = 'theme-' + th;
            await fetch('/api/theme/set', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{ theme: th }})
            }});
            showSaveNotice();
        }}

        function fallbackCopy(text) {{
            const ta = document.createElement('textarea');
            ta.value = text;
            ta.style.position = 'fixed';
            ta.style.opacity = '0';
            document.body.appendChild(ta);
            ta.select();
            try {{ document.execCommand('copy'); }} catch(e){{}}
            document.body.removeChild(ta);
        }}

        function copyUrl(url, btnElement) {{
            if (navigator.clipboard && navigator.clipboard.writeText) {{
                navigator.clipboard.writeText(url).catch(() => {{
                    fallbackCopy(url);
                }});
            }} else {{
                fallbackCopy(url);
            }}

            const toast = document.getElementById('saveToast');
            if (toast) {{
                const endpoint = url.replace('http://localhost:3333', '');
                toast.innerText = '📋 Link Copiado: ' + (endpoint || url);
                toast.style.display = 'inline-block';
                setTimeout(() => {{ toast.style.display = 'none'; }}, 2500);
            }}

            if (btnElement) {{
                const orig = btnElement.innerHTML;
                btnElement.innerHTML = '✅ Copiado!';
                btnElement.style.filter = 'brightness(1.3)';
                setTimeout(() => {{
                    btnElement.innerHTML = orig;
                    btnElement.style.filter = 'none';
                }}, 1500);
            }}
        }}

        setInterval(fetchStatus, 250);
        fetchStatus();
    </script>
</body>
</html>
    """
    return HTMLResponse(content=html_content, headers=headers)

# --------------------------------------------------------------------------
# OBS OVERLAY: PALCO COMPOSTO (SLIDES + CÂMERAS + LIBRAS INTEGRADO) (/stage)
# --------------------------------------------------------------------------
@app.get("/stage")
async def get_stage_overlay(preset: Optional[str] = None, libras_size: Optional[str] = None):
    return HTMLResponse(f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@600;700;800;900&display=swap" rel="stylesheet">
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: 'Outfit', sans-serif; background: transparent; overflow: hidden; }}
        html, body {{
            width: 100vw;
            height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            background: transparent !important;
        }}

        #stage-container {{
            width: 100%;
            height: 100%;
            display: flex;
            position: relative;
            padding: 10px;
            gap: 10px;
            background: transparent !important;
            transition: all 0.3s cubic-bezier(0.16, 1, 0.3, 1);
        }}

        .slide-frame {{
            position: relative;
            background: transparent !important;
            border-radius: 12px;
            overflow: hidden;
            box-shadow: 0 4px 20px rgba(0,0,0,0.4);
            border: 1px solid rgba(255,255,255,0.08);
            display: flex;
            align-items: center;
            justify-content: center;
        }}
        .slide-frame img {{
            width: 100% !important;
            height: 100% !important;
            object-fit: contain !important;
        }}

        .cameras-column {{
            display: flex;
            flex-direction: column;
            gap: 8px;
            overflow-y: auto;
        }}

        .cameras-row {{
            display: flex;
            flex-direction: row;
            gap: 8px;
            overflow-x: auto;
        }}

        .stage-cam-wrap {{
            position: relative;
            background: #050811;
            border-radius: 10px;
            overflow: hidden;
            box-shadow: 0 4px 14px rgba(0,0,0,0.5);
            border: 1px solid rgba(255,255,255,0.08);
            display: flex;
            align-items: center;
            justify-content: center;
            flex: 1;
            min-height: 0;
            min-width: 0;
        }}
        .stage-cam-wrap.is-libras-box {{
            border: 2px solid #06b6d4 !important;
            box-shadow: 0 0 16px rgba(6, 182, 212, 0.4) !important;
        }}
        .stage-cam-wrap img {{
            width: 100% !important;
            height: 100% !important;
            object-fit: cover !important;
        }}

        /* JANELA FLUTUANTE DE LIBRAS */
        .libras-floating-box {{
            position: absolute;
            background: #050811;
            border-radius: 12px;
            overflow: hidden;
            border: 2.5px solid #06b6d4;
            box-shadow: 0 10px 30px rgba(0,0,0,0.85), 0 0 20px rgba(6, 182, 212, 0.5);
            z-index: 100;
            display: flex;
            align-items: center;
            justify-content: center;
        }}
        .libras-floating-box img {{
            width: 100% !important;
            height: 100% !important;
            object-fit: cover !important;
        }}

        /* 1. PRESET: SIDE-BY-SIDE (Slide 68% + Câmeras 32% Direita) */
        .preset-side-by-side {{
            flex-direction: row !important;
        }}
        .preset-side-by-side .slide-frame {{
            flex: 6.8 !important;
            height: 100% !important;
        }}
        .preset-side-by-side .cameras-column {{
            flex: 3.2 !important;
            height: 100% !important;
        }}

        /* 2. PRESET: BOTTOM-BAR (Slide Topo + Câmeras Rodapé) */
        .preset-bottom-bar {{
            flex-direction: column !important;
        }}
        .preset-bottom-bar .slide-frame {{
            flex: 7.2 !important;
            width: 100% !important;
        }}
        .preset-bottom-bar .cameras-row {{
            flex: 2.8 !important;
            width: 100% !important;
        }}

        /* 3. PRESET: PIP BOTTOM RIGHT */
        .preset-pip-bottom-right {{
            position: relative !important;
            padding: 0 !important;
        }}
        .preset-pip-bottom-right .slide-frame {{
            width: 100% !important;
            height: 100% !important;
            border-radius: 0 !important;
            border: none !important;
        }}
        .preset-pip-bottom-right .cameras-column {{
            position: absolute !important;
            bottom: 24px !important;
            right: 24px !important;
            width: 380px !important;
            height: 215px !important;
            z-index: 50 !important;
            box-shadow: 0 10px 30px rgba(0,0,0,0.8), 0 0 15px rgba(56, 189, 248, 0.4) !important;
            border: 2px solid #38bdf8 !important;
            border-radius: 12px !important;
            overflow: hidden !important;
        }}
        .preset-pip-bottom-right .stage-cam-wrap {{
            border-radius: 0 !important;
            border: none !important;
        }}

        /* 4. PRESET: PIP TOP RIGHT */
        .preset-pip-top-right {{
            position: relative !important;
            padding: 0 !important;
        }}
        .preset-pip-top-right .slide-frame {{
            width: 100% !important;
            height: 100% !important;
            border-radius: 0 !important;
            border: none !important;
        }}
        .preset-pip-top-right .cameras-column {{
            position: absolute !important;
            top: 24px !important;
            right: 24px !important;
            width: 380px !important;
            height: 215px !important;
            z-index: 50 !important;
            box-shadow: 0 10px 30px rgba(0,0,0,0.8), 0 0 15px rgba(56, 189, 248, 0.4) !important;
            border: 2px solid #38bdf8 !important;
            border-radius: 12px !important;
            overflow: hidden !important;
        }}
        .preset-pip-top-right .stage-cam-wrap {{
            border-radius: 0 !important;
            border: none !important;
        }}

        /* 5. PRESET: SPLIT 50/50 */
        .preset-split-50-50 {{
            flex-direction: row !important;
        }}
        .preset-split-50-50 .slide-frame {{
            flex: 1 !important;
            height: 100% !important;
        }}
        .preset-split-50-50 .cameras-column {{
            flex: 1 !important;
            height: 100% !important;
        }}

        /* 6. PRESET: SLIDE ONLY */
        .preset-slide-only .slide-frame {{
            width: 100% !important;
            height: 100% !important;
            border-radius: 0 !important;
            border: none !important;
        }}
        .preset-slide-only .cameras-column, .preset-slide-only .cameras-row {{
            display: none !important;
        }}

        {NAME_TAG_CSS}
    </style>
</head>
<body>
    <div id="stage-container" class="preset-side-by-side"></div>
    <!-- Áudio desativado no /stage para evitar saída indesejada ou eco no OBS Studio (use o endpoint /audio) -->

    <script>
        const container = document.getElementById('stage-container');
        let currentStageSignature = '';
        const urlOverridePreset = "{preset or ''}";
        const urlOverrideLibrasSize = "{libras_size or ''}";

        async function updateStage() {{
            try {{
                const res = await fetch('/api/status?t=' + Date.now());
                const data = await res.json();
                let cams = data.cameras || [];
                const focusedId = (data.focused_camera_id !== null && data.focused_camera_id !== undefined) ? String(data.focused_camera_id) : null;
                const pinnedIds = (data.pinned_camera_ids || []).map(String);
                const librasIds = (data.libras_camera_ids || []).map(String);
                const showNames = data.show_camera_names;
                const nameStyle = data.camera_name_style || 'glass';
                const activePreset = urlOverridePreset || data.stage_preset || 'side-by-side';
                const currentLibrasSize = urlOverrideLibrasSize || data.libras_size || 'equal';

                const signature = JSON.stringify({{
                    activePreset,
                    currentLibrasSize,
                    cams: cams.map(c => String(c.id) + '_' + c.name + '_' + (c.is_libras ? '1' : '0')),
                    focusedId,
                    pinnedIds,
                    librasIds,
                    showNames,
                    nameStyle
                }});

                if (signature !== currentStageSignature) {{
                    currentStageSignature = signature;
                    container.innerHTML = '';

                    // 1. Container do Slide / Apresentação
                    const slideFrame = document.createElement('div');
                    slideFrame.className = 'slide-frame';
                    slideFrame.innerHTML = `<img id="stage_slide_img" src="/raw_frame/presentation">`;

                    // Identificar Câmeras de Libras e Outras Câmeras Reais
                    const librasCams = cams.filter(c => c.is_libras || librasIds.includes(String(c.id)));
                    let speakerCams = cams.filter(c => !c.is_libras && !librasIds.includes(String(c.id)));

                    if (pinnedIds.length > 0) {{
                        speakerCams = speakerCams.filter(c => pinnedIds.includes(String(c.id)));
                    }}

                    // CASO 1: SEM NENHUMA CÂMERA CONECTADA OU MODO SOMENTE SLIDES
                    if (activePreset === 'slide-only' || (speakerCams.length === 0 && librasCams.length === 0)) {{
                        container.className = 'preset-slide-only';
                        slideFrame.style.width = '100%';
                        slideFrame.style.height = '100%';
                        slideFrame.style.borderRadius = '0';
                        slideFrame.style.border = 'none';
                        container.appendChild(slideFrame);
                        return;
                    }}

                    container.className = 'preset-' + activePreset;
                    container.appendChild(slideFrame);

                    // CASO 2: TEM LIBRAS E TEM PALESTRANTE COM MODO IGUAL (equal)
                    if (currentLibrasSize === 'equal' && librasCams.length > 0 && speakerCams.length > 0) {{
                        const isBottomRow = (activePreset === 'bottom-bar');
                        const camsCont = document.createElement('div');
                        camsCont.className = isBottomRow ? 'cameras-row' : 'cameras-column';

                        const activeLibras = librasCams[0];
                        const mainSpeaker = (focusedId ? speakerCams.find(c => String(c.id) === focusedId) : null) || speakerCams[0];

                        // 1. Box do Palestrante
                        const wrapSpeaker = document.createElement('div');
                        wrapSpeaker.className = 'stage-cam-wrap';
                        wrapSpeaker.innerHTML = `
                            <img id="stage_cam_${{mainSpeaker.id}}" src="/raw_frame/camera/${{mainSpeaker.id}}">
                            ${{showNames ? `<div class="name-tag style-${{nameStyle}}">${{mainSpeaker.name}}</div>` : ''}}
                        `;
                        camsCont.appendChild(wrapSpeaker);

                        // 2. Box do Intérprete de Libras (Exatamente do mesmo tamanho)
                        const wrapLibras = document.createElement('div');
                        wrapLibras.className = 'stage-cam-wrap is-libras-box';
                        wrapLibras.innerHTML = `
                            <img id="stage_cam_${{activeLibras.id}}" src="/raw_frame/camera/${{activeLibras.id}}">
                            <div class="libras-badge-overlay">🤟 Libras</div>
                            ${{showNames ? `<div class="name-tag style-${{nameStyle}}">${{activeLibras.name}}</div>` : ''}}
                        `;
                        camsCont.appendChild(wrapLibras);

                        container.appendChild(camsCont);

                    }} else {{
                        // CASO 3: APENAS PALESTRANTE, OU LIBRAS EM JANELA FLUTUANTE, OU APENAS LIBRAS
                        const isBottomRow = (activePreset === 'bottom-bar');
                        const isPip = activePreset.startsWith('pip');

                        if (speakerCams.length > 0) {{
                            const camsCont = document.createElement('div');
                            camsCont.className = isBottomRow ? 'cameras-row' : 'cameras-column';

                            let displayCams = speakerCams;
                            if (isPip) {{
                                const mainCam = displayCams.find(c => String(c.id) === focusedId) || displayCams[0];
                                displayCams = [mainCam];
                            }}

                            displayCams.forEach(c => {{
                                const wrap = document.createElement('div');
                                wrap.className = 'stage-cam-wrap';
                                wrap.innerHTML = `
                                    <img id="stage_cam_${{c.id}}" src="/raw_frame/camera/${{c.id}}">
                                    ${{showNames ? `<div class="name-tag style-${{nameStyle}}">${{c.name}}</div>` : ''}}
                                `;
                                camsCont.appendChild(wrap);
                            }});

                            container.appendChild(camsCont);
                        }} else if (librasCams.length > 0 && currentLibrasSize === 'equal') {{
                            // Se só tem Libras e está no modo equal, exibe a Libras na coluna
                            const camsCont = document.createElement('div');
                            camsCont.className = isBottomRow ? 'cameras-row' : 'cameras-column';

                            const activeLibras = librasCams[0];
                            const wrapLibras = document.createElement('div');
                            wrapLibras.className = 'stage-cam-wrap is-libras-box';
                            wrapLibras.innerHTML = `
                                <img id="stage_cam_${{activeLibras.id}}" src="/raw_frame/camera/${{activeLibras.id}}">
                                <div class="libras-badge-overlay">🤟 Libras</div>
                                ${{showNames ? `<div class="name-tag style-${{nameStyle}}">${{activeLibras.name}}</div>` : ''}}
                            `;
                            camsCont.appendChild(wrapLibras);
                            container.appendChild(camsCont);
                        }}

                        // JANELA FLUTUANTE DE LIBRAS (SELECIONADA OU SE NÃO FOR EQUAL)
                        if (librasCams.length > 0 && currentLibrasSize !== 'equal') {{
                            const activeLibras = librasCams[0];
                            const librasFloating = document.createElement('div');
                            librasFloating.className = 'libras-floating-box';
                            
                            if (currentLibrasSize === 'huge') {{
                                librasFloating.style.width = '560px';
                                librasFloating.style.height = '315px';
                                librasFloating.style.bottom = '24px';
                                librasFloating.style.right = '24px';
                            }} else if (currentLibrasSize === 'large') {{
                                librasFloating.style.width = '480px';
                                librasFloating.style.height = '270px';
                                librasFloating.style.bottom = '24px';
                                librasFloating.style.right = '24px';
                            }} else {{
                                librasFloating.style.width = '380px';
                                librasFloating.style.height = '214px';
                                librasFloating.style.bottom = '24px';
                                librasFloating.style.right = '24px';
                            }}

                            if (activePreset === 'pip-bottom-right') {{
                                librasFloating.style.bottom = 'auto';
                                librasFloating.style.top = '24px';
                            }}

                            librasFloating.innerHTML = `
                                <img id="stage_cam_${{activeLibras.id}}" src="/raw_frame/camera/${{activeLibras.id}}">
                                <div class="libras-badge-overlay">🤟 Libras</div>
                                ${{showNames ? `<div class="name-tag style-${{nameStyle}}" style="font-size:11px; padding:3px 8px; bottom:6px; left:6px;">${{activeLibras.name}}</div>` : ''}}
                            `;
                            container.appendChild(librasFloating);
                        }}
                    }}

                }}

                // Atualizar frames ao vivo diretamente da memória
                const slideImg = document.getElementById('stage_slide_img');
                if (slideImg) slideImg.src = '/raw_frame/presentation?t=' + Date.now();

                cams.forEach(c => {{
                    const img = document.getElementById(`stage_cam_${{c.id}}`);
                    if (img) img.src = `/raw_frame/camera/${{c.id}}?t=` + Date.now();
                }});
            }} catch (e) {{}}
        }}

        setInterval(updateStage, 66);
        updateStage();
    </script>
</body>
</html>
""")

# --------------------------------------------------------------------------
# OBS OVERLAY: JANELA DEDICADA DE LIBRAS (/libras)
# --------------------------------------------------------------------------
@app.get("/libras")
async def get_libras_overlay(cam_id: Optional[str] = None):
    return HTMLResponse(f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@600;700;800;900&display=swap" rel="stylesheet">
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: 'Outfit', sans-serif; background: transparent; overflow: hidden; }}
        html, body {{
            width: 100vw;
            height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            background: transparent !important;
        }}

        .libras-card {{
            width: 100%;
            height: 100%;
            position: relative;
            background: #050811;
            border-radius: 14px;
            overflow: hidden;
            border: 3px solid #06b6d4;
            box-shadow: 0 10px 30px rgba(0,0,0,0.8), 0 0 20px rgba(6, 182, 212, 0.4);
            display: flex;
            align-items: center;
            justify-content: center;
        }}

        img {{
            width: 100% !important;
            height: 100% !important;
            object-fit: cover !important;
        }}

        {NAME_TAG_CSS}
    </style>
</head>
<body>
    <div class="libras-card">
        <img id="librasFeed" src="/raw_frame/camera/1">
        <div class="libras-badge-overlay" style="font-size: 13px; padding: 4px 10px;">🤟 Intérprete de Libras</div>
        <div id="librasNameTag" class="name-tag style-glass" style="display: none;"></div>
    </div>
    <!-- Áudio desativado no /libras para evitar saída indesejada ou eco no OBS Studio (use o endpoint /audio) -->

    <script>
        const img = document.getElementById('librasFeed');
        const nameTag = document.getElementById('librasNameTag');
        const urlOverrideCam = "{cam_id or ''}";
        let currentTargetCamId = urlOverrideCam || '1';

        async function updateLibras() {{
            try {{
                const res = await fetch('/api/status?t=' + Date.now());
                const data = await res.json();
                const cams = data.cameras || [];
                const librasIds = (data.libras_camera_ids || []).map(String);
                const showNames = data.show_camera_names;
                const nameStyle = data.camera_name_style || 'glass';

                let targetCam = null;
                if (urlOverrideCam) {{
                    targetCam = cams.find(c => String(c.id) === urlOverrideCam);
                }} else if (librasIds.length > 0) {{
                    targetCam = cams.find(c => c.is_libras || librasIds.includes(String(c.id)));
                }} else {{
                    targetCam = cams.find(c => c.is_libras);
                }}

                const card = document.querySelector('.libras-card');
                if (!targetCam) {{
                    if (card) card.style.display = 'none';
                    return;
                }}
                if (card) card.style.display = 'flex';

                currentTargetCamId = String(targetCam.id);
                img.src = '/raw_frame/camera/' + targetCam.id + '?t=' + Date.now();
                
                if (showNames && targetCam.name) {{
                    nameTag.className = 'name-tag style-' + nameStyle;
                    nameTag.innerText = targetCam.name;
                    nameTag.style.display = 'flex';
                }} else {{
                    nameTag.style.display = 'none';
                }}
            }} catch (e) {{}}
        }}

        setInterval(updateLibras, 66);
        updateLibras();
    </script>
</body>
</html>
""")

# --------------------------------------------------------------------------
# OBS OVERLAY: ENQUETE / POLL (/poll) COM TEMPORIZADOR, TEMAS E RESULTADOS
# --------------------------------------------------------------------------
@app.get("/poll")
async def get_poll_overlay(mode: Optional[str] = None, duration: Optional[int] = None, theme: Optional[str] = None):
    return HTMLResponse(f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;600;700;800;900&family=Plus+Jakarta+Sans:wght@700;800&family=JetBrains+Mono:wght@700&display=swap" rel="stylesheet">
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: 'Outfit', sans-serif; }}
        html, body {{
            background: transparent !important;
            width: 100vw;
            height: 100vh;
            overflow: hidden;
            display: flex;
            align-items: flex-end;
            justify-content: flex-end;
            padding: 30px;
        }}

        @keyframes pollSlideIn {{
            from {{ opacity: 0; transform: translateY(40px) scale(0.95); }}
            to {{ opacity: 1; transform: translateY(0) scale(1); }}
        }}

        @keyframes pollSlideOut {{
            from {{ opacity: 1; transform: translateY(0) scale(1); }}
            to {{ opacity: 0; transform: translateY(50px) scale(0.92); }}
        }}

        .poll-box {{
            width: 440px;
            border-radius: 14px;
            padding: 18px;
            animation: pollSlideIn 0.35s cubic-bezier(0.16, 1, 0.3, 1) forwards;
            position: relative;
            transition: all 0.3s ease;
        }}

        .poll-box.is-exiting {{
            animation: pollSlideOut 0.4s ease-in forwards;
        }}

        /* ⚡ TEMA 1: NEON GLOW */
        .poll-box.theme-neon {{
            background: rgba(11, 17, 32, 0.95);
            backdrop-filter: blur(16px);
            -webkit-backdrop-filter: blur(16px);
            border: 2px solid #06b6d4;
            box-shadow: 0 10px 30px rgba(0,0,0,0.8), 0 0 24px rgba(6, 182, 212, 0.4);
        }}
        .poll-box.theme-neon .poll-tag {{
            background: rgba(6, 182, 212, 0.2);
            border: 1px solid #06b6d4;
            color: #22d3ee;
        }}
        .poll-box.theme-neon .poll-fill {{
            background: linear-gradient(90deg, rgba(6, 182, 212, 0.4), rgba(56, 189, 248, 0.4));
            border-right: 3px solid #06b6d4;
        }}
        .poll-box.theme-neon .poll-countdown-bar {{
            background: #06b6d4;
        }}

        /* 💎 TEMA 2: GLASSMORPHISM */
        .poll-box.theme-glass {{
            background: rgba(15, 23, 42, 0.85);
            backdrop-filter: blur(18px);
            -webkit-backdrop-filter: blur(18px);
            border: 1px solid rgba(255, 255, 255, 0.2);
            box-shadow: 0 12px 36px rgba(0,0,0,0.7), inset 0 1px 0 rgba(255,255,255,0.15);
        }}
        .poll-box.theme-glass .poll-tag {{
            background: rgba(56, 189, 248, 0.2);
            border: 1px solid #38bdf8;
            color: #38bdf8;
        }}
        .poll-box.theme-glass .poll-fill {{
            background: linear-gradient(90deg, rgba(255, 255, 255, 0.2), rgba(56, 189, 248, 0.3));
            border-right: 3px solid #38bdf8;
        }}
        .poll-box.theme-glass .poll-countdown-bar {{
            background: #38bdf8;
        }}

        /* 💬 TEMA 3: DISCORD BUBBLES */
        .poll-box.theme-bubbles {{
            background: #2b2d31;
            border: 1px solid #1e1f22;
            box-shadow: 0 8px 24px rgba(0,0,0,0.7);
            border-radius: 12px;
        }}
        .poll-box.theme-bubbles .poll-tag {{
            background: rgba(88, 101, 242, 0.2);
            border: 1px solid #5865f2;
            color: #818cf8;
        }}
        .poll-box.theme-bubbles .poll-bar {{
            background: #1e1f22;
            border-radius: 8px;
        }}
        .poll-box.theme-bubbles .poll-fill {{
            background: rgba(88, 101, 242, 0.5);
            border-right: 3px solid #5865f2;
        }}
        .poll-box.theme-bubbles .poll-countdown-bar {{
            background: #5865f2;
        }}

        /* 🎬 TEMA 4: CYBERPUNK BROADCAST */
        .poll-box.theme-cyber {{
            background: #09090b;
            border: 2px solid #eab308;
            box-shadow: 0 10px 30px rgba(0,0,0,0.9), 0 0 20px rgba(234, 179, 8, 0.35);
            border-radius: 4px;
        }}
        .poll-box.theme-cyber .poll-tag {{
            background: rgba(234, 179, 8, 0.2);
            border: 1px solid #eab308;
            color: #fde047;
            font-family: 'JetBrains Mono', monospace;
        }}
        .poll-box.theme-cyber .poll-fill {{
            background: linear-gradient(90deg, rgba(234, 179, 8, 0.4), rgba(249, 115, 22, 0.4));
            border-right: 3px solid #eab308;
        }}
        .poll-box.theme-cyber .poll-countdown-bar {{
            background: #eab308;
        }}

        /* 📄 TEMA 5: MINIMAL SLATE */
        .poll-box.theme-minimal {{
            background: #0f172a;
            border: 1px solid #334155;
            box-shadow: 0 4px 16px rgba(0,0,0,0.6);
            border-radius: 8px;
        }}
        .poll-box.theme-minimal .poll-tag {{
            background: rgba(148, 163, 184, 0.2);
            border: 1px solid #94a3b8;
            color: #cbd5e1;
        }}
        .poll-box.theme-minimal .poll-fill {{
            background: rgba(51, 65, 85, 0.8);
            border-right: 2px solid #94a3b8;
        }}
        .poll-box.theme-minimal .poll-countdown-bar {{
            background: #94a3b8;
        }}

        .poll-header-row {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 8px;
        }}

        .poll-tag {{
            display: inline-flex;
            align-items: center;
            gap: 5px;
            font-size: 11px;
            font-weight: 800;
            padding: 3px 8px;
            border-radius: 6px;
            text-transform: uppercase;
        }}

        .poll-timer-badge {{
            font-size: 11px;
            font-weight: 700;
            color: #94a3b8;
            background: rgba(15, 23, 42, 0.8);
            border: 1px solid rgba(255, 255, 255, 0.1);
            padding: 2px 8px;
            border-radius: 12px;
            display: inline-flex;
            align-items: center;
            gap: 4px;
        }}

        .poll-q {{
            font-size: 16px;
            font-weight: 800;
            color: #ffffff;
            line-height: 1.35;
            margin-bottom: 14px;
        }}

        .poll-opts {{
            display: flex;
            flex-direction: column;
            gap: 8px;
        }}

        .poll-bar {{
            background: rgba(30, 41, 59, 0.9);
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 8px;
            padding: 10px 14px;
            position: relative;
            overflow: hidden;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }}

        .poll-fill {{
            position: absolute;
            top: 0;
            left: 0;
            height: 100%;
            transition: width 0.45s cubic-bezier(0.16, 1, 0.3, 1);
            z-index: 1;
        }}

        .poll-content {{
            position: relative;
            z-index: 2;
            display: flex;
            justify-content: space-between;
            width: 100%;
            font-weight: 700;
            font-size: 13.5px;
            color: #ffffff;
        }}

        .poll-countdown-bar {{
            position: absolute;
            bottom: 0;
            left: 0;
            height: 3px;
            width: 100%;
            border-bottom-left-radius: 14px;
            border-bottom-right-radius: 14px;
        }}
    </style>
</head>
<body>
    <div id="pollContainer"></div>

    <script>
        const container = document.getElementById('pollContainer');
        let currentSig = '';
        let timerCountdown = null;
        let timerHideTimeout = null;
        let isCardVisible = false;
        let lastKnownPollId = '';
        let lastProcessedNonce = -1;
        let isDismissed = false;

        const urlOverrideMode = "{mode or ''}";
        const urlOverrideDuration = {duration if duration is not None else 'null'};
        const urlOverrideTheme = "{theme or ''}";

        function hidePoll() {{
            const box = container.querySelector('.poll-box');
            if (box) {{
                box.classList.add('is-exiting');
                setTimeout(() => {{
                    container.innerHTML = '';
                    isCardVisible = false;
                }}, 380);
            }} else {{
                container.innerHTML = '';
                isCardVisible = false;
            }}
            if (timerCountdown) clearInterval(timerCountdown);
            if (timerHideTimeout) clearTimeout(timerHideTimeout);
            isDismissed = true;
        }}

        async function updatePoll() {{
            try {{
                const res = await fetch('/api/poll?t=' + Date.now());
                const data = await res.json();
                const poll = data.poll;
                const mode = urlOverrideMode || data.poll_display_mode || 'results_only';
                const duration = (urlOverrideDuration !== null) ? urlOverrideDuration : (data.poll_display_duration !== undefined ? data.poll_display_duration : 15);
                const theme = urlOverrideTheme || data.poll_style || 'neon';
                const forceVisible = !!data.poll_force_visible;
                const nonce = data.poll_nonce || 0;
                const dismissed = !!data.poll_dismissed;

                // Se não há enquete, ou modo é hidden, ou o backend marcou como ocultado
                if (!poll || !poll.is_active || mode === 'hidden' || dismissed) {{
                    if (isCardVisible) hidePoll();
                    return;
                }}

                const isPublished = (poll.published || poll.status === 'published' || poll.status === 'results');
                const pollId = (poll.question || '') + '_' + (poll.options || []).map(o => o.text).join('|');

                // Se for uma enquete completamente nova, resetar dismissal
                if (pollId !== lastKnownPollId) {{
                    lastKnownPollId = pollId;
                    isDismissed = false;
                }}

                // Se o operador clicou no Dock em "Mostrar no OBS Agora" (novo nonce gerado)
                if (nonce !== lastProcessedNonce && forceVisible) {{
                    lastProcessedNonce = nonce;
                    isDismissed = false;
                }}

                if (isDismissed) {{
                    if (isCardVisible) hidePoll();
                    return;
                }}

                const shouldDisplay = forceVisible || (mode === 'always') || (mode === 'results_only' && isPublished);

                if (!shouldDisplay) {{
                    if (isCardVisible) hidePoll();
                    return;
                }}

                const pollKey = pollId + '::' + (poll.options || []).map(o => (o.votes || 0) + '_' + (o.percentage || 0)).join(',') + '::' + isPublished + '::' + mode + '::' + duration + '::' + theme + '::' + nonce;

                if (pollKey !== currentSig) {{
                    currentSig = pollKey;
                    renderCard(poll, isPublished, duration, theme);
                }}
            }} catch (e) {{}}
        }}

        function renderCard(poll, isPublished, duration, theme) {{
            let optsHtml = '';
            (poll.options || []).forEach(opt => {{
                const pct = opt.percentage || (poll.total_votes > 0 ? Math.round((opt.votes / poll.total_votes) * 100) : 0);
                optsHtml += `
                    <div class="poll-bar">
                        <div class="poll-fill" style="width: ${{pct}}%;"></div>
                        <div class="poll-content">
                            <span>${{opt.text}}</span>
                            <span style="font-weight: 800;">${{opt.votes ? opt.votes + ' (' + pct + '%)' : pct + '%'}}</span>
                        </div>
                    </div>
                `;
            }});

            const tagLabel = isPublished ? '📊 Resultados Finais' : '🗳️ Enquete Ao Vivo';
            const showTimer = (duration > 0 && isPublished);

            container.innerHTML = `
                <div class="poll-box theme-${{theme}}" id="pollBoxEl">
                    <div class="poll-header-row">
                        <div class="poll-tag">${{tagLabel}}</div>
                        ${{showTimer ? `<div class="poll-timer-badge" id="pollTimerBadge">⏱️ <span id="timerSecs">${{duration}}</span>s</div>` : ''}}
                    </div>
                    <div class="poll-q">${{poll.question}}</div>
                    <div class="poll-opts">${{optsHtml}}</div>
                    ${{showTimer ? `<div class="poll-countdown-bar" id="pollCountdownBar" style="transition: width ${{duration}}s linear; width: 100%;"></div>` : ''}}
                </div>
            `;

            isCardVisible = true;

            // Timer de Desaparecimento Automático
            if (showTimer) {{
                if (timerCountdown) clearInterval(timerCountdown);
                if (timerHideTimeout) clearTimeout(timerHideTimeout);

                setTimeout(() => {{
                    const cBar = document.getElementById('pollCountdownBar');
                    if (cBar) cBar.style.width = '0%';
                }}, 50);

                let remaining = duration;
                timerCountdown = setInterval(() => {{
                    remaining--;
                    const elSecs = document.getElementById('timerSecs');
                    if (elSecs) elSecs.innerText = remaining;
                    if (remaining <= 0) {{
                        clearInterval(timerCountdown);
                    }}
                }}, 1000);

                timerHideTimeout = setTimeout(() => {{
                    hidePoll();
                }}, duration * 1000);
            }}
        }}

        setInterval(updatePoll, 500);
        updatePoll();
    </script>
</body>
</html>
""")

# --------------------------------------------------------------------------
# OBS OVERLAY: GRID DE CÂMERAS (/cameras/grid & /grid)
# --------------------------------------------------------------------------
@app.get("/cameras/grid")
@app.get("/grid")
async def get_cameras_grid_overlay(
    audio: Optional[int] = None,
    layout: Optional[str] = None,
    names: Optional[int] = None,
    style: Optional[str] = None
):
    return HTMLResponse(f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@600;700;800;900&display=swap" rel="stylesheet">
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: 'Outfit', sans-serif; }}
        html, body {{
            width: 100vw;
            height: 100vh;
            background: transparent !important;
            overflow: hidden;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 12px;
        }}

        .grid-container {{
            width: 100%;
            height: 100%;
            display: grid;
            gap: 12px;
            align-items: center;
            justify-content: center;
            transition: all 0.4s cubic-bezier(0.16, 1, 0.3, 1);
        }}

        .cam-card {{
            position: relative;
            width: 100%;
            height: 100%;
            border-radius: 12px;
            overflow: hidden;
            background: #090d16;
            box-shadow: 0 8px 24px rgba(0,0,0,0.6);
            display: flex;
            align-items: center;
            justify-content: center;
            transition: transform 0.3s ease, box-shadow 0.3s ease;
        }}

        .cam-card img {{
            width: 100%;
            height: 100%;
            object-fit: cover;
            border-radius: 12px;
        }}

        .cam-card.is-libras {{
            border: 2.5px solid #06b6d4;
            box-shadow: 0 0 20px rgba(6, 182, 212, 0.4);
        }}

        .cam-card.is-focused {{
            border: 3px solid #38bdf8;
            box-shadow: 0 0 25px rgba(56, 189, 248, 0.6);
        }}

        /* NAME TAG STYLES */
        .name-tag {{
            position: absolute;
            bottom: 10px;
            left: 10px;
            display: flex;
            align-items: center;
            gap: 6px;
            padding: 4px 10px;
            border-radius: 6px;
            font-size: 13px;
            font-weight: 700;
            color: #ffffff;
            z-index: 10;
            text-shadow: 0 1px 3px rgba(0,0,0,0.8);
            pointer-events: none;
            backdrop-filter: blur(8px);
            -webkit-backdrop-filter: blur(8px);
        }}

        .style-glass {{
            background: rgba(15, 23, 42, 0.75);
            border: 1px solid rgba(255, 255, 255, 0.2);
            box-shadow: 0 4px 12px rgba(0,0,0,0.4);
        }}

        .style-pill {{
            background: rgba(15, 23, 42, 0.9);
            border-radius: 20px;
            padding: 4px 14px;
            border: 1px solid rgba(255, 255, 255, 0.15);
        }}

        .style-minimal {{
            background: rgba(0, 0, 0, 0.8);
            border-radius: 4px;
            padding: 3px 8px;
            border: none;
        }}

        .style-glow {{
            background: rgba(11, 17, 32, 0.85);
            border: 1.5px solid #06b6d4;
            box-shadow: 0 0 12px rgba(6, 182, 212, 0.5);
            color: #22d3ee;
        }}

        .badge-libras {{
            background: #06b6d4;
            color: #082f49;
            font-size: 10px;
            font-weight: 900;
            padding: 2px 6px;
            border-radius: 4px;
            text-transform: uppercase;
        }}

        .empty-placeholder {{
            color: #64748b;
            font-size: 16px;
            font-weight: 700;
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 10px;
        }}
    </style>
</head>
<body>
    <div class="grid-container" id="gridContainer"></div>

    <script>
        const container = document.getElementById('gridContainer');
        const overrideLayout = "{layout or ''}";
        const overrideNames = {names if names is not None else 'null'};
        const overrideStyle = "{style or ''}";
        let currentSig = '';

        async function updateGrid() {{
            try {{
                const res = await fetch('/api/status?t=' + Date.now());
                const data = await res.json();
                const cams = data.cameras || [];
                const showNames = (overrideNames !== null) ? !!overrideNames : (data.show_camera_names !== undefined ? data.show_camera_names : true);
                const nameStyle = overrideStyle || data.camera_name_style || 'glass';
                const layoutMode = overrideLayout || data.grid_layout_mode || 'auto';
                const focusedId = data.focused_camera_id;
                const librasIds = data.libras_camera_ids || [];

                const sig = cams.map(c => c.id + '_' + (c.name || '') + '_' + (librasIds.includes(String(c.id)) ? '1' : '0')).join(',') + '::' + showNames + '::' + nameStyle + '::' + layoutMode + '::' + focusedId;

                if (cams.length === 0) {{
                    if (currentSig !== 'empty') {{
                        currentSig = 'empty';
                        container.innerHTML = '';
                    }}
                    return;
                }}

                // Calculate layout grid
                let cols = 1;
                let rows = 1;
                const n = cams.length;

                if (layoutMode === 'row') {{
                    cols = n;
                    rows = 1;
                }} else if (layoutMode === 'column') {{
                    cols = 1;
                    rows = n;
                }} else if (layoutMode === 'grid') {{
                    cols = Math.ceil(Math.sqrt(n));
                    rows = Math.ceil(n / cols);
                }} else {{
                    if (n === 1) {{ cols = 1; rows = 1; }}
                    else if (n === 2) {{ cols = 2; rows = 1; }}
                    else if (n <= 4) {{ cols = 2; rows = 2; }}
                    else if (n <= 6) {{ cols = 3; rows = 2; }}
                    else if (n <= 9) {{ cols = 3; rows = 3; }}
                    else {{ cols = 4; rows = Math.ceil(n / 4); }}
                }}

                container.style.gridTemplateColumns = `repeat(${{cols}}, 1fr)`;
                container.style.gridTemplateRows = `repeat(${{rows}}, 1fr)`;

                if (sig !== currentSig) {{
                    currentSig = sig;
                    container.innerHTML = '';

                    cams.forEach(cam => {{
                        const isLibras = librasIds.includes(String(cam.id));
                        const isFocused = (focusedId && String(cam.id) === String(focusedId));

                        const card = document.createElement('div');
                        card.className = 'cam-card' + (isLibras ? ' is-libras' : '') + (isFocused ? ' is-focused' : '');
                        card.id = 'camCard_' + cam.id;

                        const img = document.createElement('img');
                        img.id = 'camImg_' + cam.id;
                        img.src = '/raw_frame/camera/' + cam.id;
                        img.onerror = () => {{
                            setTimeout(() => {{ if (img) img.src = '/raw_frame/camera/' + cam.id + '?t=' + Date.now(); }}, 1500);
                        }};
                        card.appendChild(img);

                        if (showNames) {{
                            const tag = document.createElement('div');
                            tag.className = 'name-tag style-' + nameStyle;
                            tag.innerHTML = `
                                <span>${{cam.name || 'Câmera ' + cam.id}}</span>
                                ${{isLibras ? '<span class="badge-libras">🤟 LIBRAS</span>' : ''}}
                            `;
                            card.appendChild(tag);
                        }}

                        container.appendChild(card);
                    }});
                }}

                // Atualizar imagens ao vivo diretamente da memória
                cams.forEach(cam => {{
                    const img = document.getElementById('camImg_' + cam.id);
                    if (img) img.src = '/raw_frame/camera/' + cam.id + '?t=' + Date.now();
                }});
            }} catch (e) {{}}
        }}

        setInterval(updateGrid, 66);
        updateGrid();
    </script>
</body>
</html>
""")

# --------------------------------------------------------------------------
# OBS OVERLAY: CHAT FEED AO VIVO (/chat)
# --------------------------------------------------------------------------
@app.get("/chat")
async def get_chat_overlay(
    style: Optional[str] = None,
    size: Optional[str] = None,
    max_msgs: Optional[int] = None,
    avatar: Optional[int] = None
):
    return HTMLResponse(f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;600;700;800;900&display=swap" rel="stylesheet">
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: 'Outfit', sans-serif; }}
        html, body {{
            width: 100vw;
            height: 100vh;
            background: transparent !important;
            overflow: hidden;
            display: flex;
            align-items: flex-end;
            justify-content: flex-start;
            padding: 24px;
        }}

        .chat-feed-container {{
            width: 440px;
            display: flex;
            flex-direction: column;
            gap: 10px;
            justify-content: flex-end;
        }}

        @keyframes msgSlideIn {{
            from {{ opacity: 0; transform: translateY(20px) scale(0.96); }}
            to {{ opacity: 1; transform: translateY(0) scale(1); }}
        }}

        .chat-msg-card {{
            position: relative;
            padding: 10px 14px;
            border-radius: 12px;
            animation: msgSlideIn 0.3s cubic-bezier(0.16, 1, 0.3, 1) forwards;
            display: flex;
            flex-direction: column;
            gap: 4px;
            box-shadow: 0 4px 16px rgba(0,0,0,0.5);
            transition: all 0.25s ease;
        }}

        /* MINI AVATAR */
        .chat-mini-avatar {{
            width: 24px;
            height: 24px;
            border-radius: 50%;
            overflow: hidden;
            flex-shrink: 0;
            display: flex;
            align-items: center;
            justify-content: center;
            background: #334155;
            border: 1.5px solid rgba(255,255,255,0.25);
        }}
        .chat-mini-avatar img {{
            width: 100%;
            height: 100%;
            object-fit: cover;
            border-radius: 50%;
        }}
        .mini-avatar-fallback {{
            width: 100%;
            height: 100%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 11px;
            font-weight: 800;
            color: #ffffff;
            background: linear-gradient(135deg, #0284c7, #38bdf8);
            text-transform: uppercase;
        }}
        .mini-avatar-fallback.yt {{
            background: linear-gradient(135deg, #dc2626, #f87171);
        }}

        /* FONT SIZES */
        .size-small .msg-user {{ font-size: 11.5px; }}
        .size-small .msg-text {{ font-size: 12px; }}
        .size-medium .msg-user {{ font-size: 13px; }}
        .size-medium .msg-text {{ font-size: 13.5px; }}
        .size-large .msg-user {{ font-size: 14.5px; }}
        .size-large .msg-text {{ font-size: 15.5px; }}

        /* THEMES */
        .theme-glass {{
            background: rgba(15, 23, 42, 0.85);
            backdrop-filter: blur(14px);
            -webkit-backdrop-filter: blur(14px);
            border: 1px solid rgba(255, 255, 255, 0.15);
        }}
        .theme-glass .msg-user {{ color: #38bdf8; font-weight: 800; }}
        .theme-glass .msg-text {{ color: #f8fafc; line-height: 1.35; }}

        .theme-streamer {{
            background: #18181b;
            border-left: 4px solid #a855f7;
            border-radius: 8px;
        }}
        .theme-streamer .msg-user {{ color: #c084fc; font-weight: 800; }}
        .theme-streamer .msg-text {{ color: #ffffff; }}

        .theme-broadcast {{
            background: #09090b;
            border: 1px solid #27272a;
            border-bottom: 2px solid #ef4444;
            border-radius: 4px;
        }}
        .theme-broadcast .msg-user {{ color: #ef4444; font-weight: 900; text-transform: uppercase; }}
        .theme-broadcast .msg-text {{ color: #fafafa; }}

        .theme-neon {{
            background: rgba(11, 17, 32, 0.9);
            border: 1.5px solid #06b6d4;
            box-shadow: 0 0 16px rgba(6, 182, 212, 0.35);
        }}
        .theme-neon .msg-user {{ color: #22d3ee; font-weight: 800; }}
        .theme-neon .msg-text {{ color: #ffffff; }}

        .theme-minimal {{
            background: rgba(0, 0, 0, 0.85);
            border-radius: 6px;
            padding: 6px 12px;
        }}
        .theme-minimal .msg-user {{ color: #94a3b8; font-weight: 700; }}
        .theme-minimal .msg-text {{ color: #f1f5f9; }}

        .theme-bubbles {{
            background: #2b2d31;
            border: 1px solid #1e1f22;
            border-radius: 12px;
        }}
        .theme-bubbles .msg-user {{ color: #818cf8; font-weight: 800; }}
        .theme-bubbles .msg-text {{ color: #dbdee1; }}

        .source-badge {{
            font-size: 9.5px;
            font-weight: 800;
            padding: 2px 5px;
            border-radius: 4px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            display: inline-flex;
            align-items: center;
        }}
        .badge-rnp {{ background: rgba(56, 189, 248, 0.25); color: #38bdf8; border: 1px solid rgba(56, 189, 248, 0.5); }}
        .badge-yt {{ background: rgba(239, 68, 68, 0.25); color: #ef4444; border: 1px solid rgba(239, 68, 68, 0.5); }}

        .msg-header {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 6px;
        }}
        .msg-time {{
            font-size: 10px;
            color: #64748b;
        }}
    </style>
</head>
<body>
    <div class="chat-feed-container" id="chatContainer"></div>

    <script>
        const container = document.getElementById('chatContainer');
        const overrideStyle = "{style or ''}";
        const overrideSize = "{size or ''}";
        const overrideMax = {max_msgs if max_msgs is not None else 'null'};
        const overrideAvatar = {avatar if avatar is not None else 'null'};
        let currentSig = '';

        async function updateChat() {{
            try {{
                const res = await fetch('/api/chat?t=' + Date.now());
                const data = await res.json();
                const msgs = data.messages || [];
                const theme = overrideStyle || data.chat_style || 'glass';
                const size = overrideSize || data.chat_font_size || 'medium';
                const maxCount = (overrideMax !== null) ? overrideMax : (data.chat_max_messages || 6);
                const showAvatars = (overrideAvatar !== null) ? !!overrideAvatar : (data.show_chat_avatars !== undefined ? data.show_chat_avatars : true);

                const recent = msgs.slice(-maxCount);
                const sig = recent.map(m => (m.id || '') + '_' + m.user + '_' + m.time + '_' + (m.avatar || '')).join(';') + '::' + theme + '::' + size + '::' + maxCount + '::' + showAvatars;

                if (sig !== currentSig) {{
                    currentSig = sig;
                    container.innerHTML = '';
                    container.className = 'chat-feed-container size-' + size;

                    recent.forEach(m => {{
                        const isYt = (m.source === 'youtube');
                        const initial = (m.user ? m.user.charAt(0).toUpperCase() : '?');
                        const avatarHtml = showAvatars ? `
                            <div class="chat-mini-avatar">
                                ${{m.avatar ? `<img src="${{m.avatar}}" referrerpolicy="no-referrer" onerror="this.style.display='none'; this.nextElementSibling.style.display='flex';">` : ''}}
                                <div class="mini-avatar-fallback ${{isYt ? 'yt' : 'rnp'}}" style="${{m.avatar ? 'display:none;' : ''}}">${{initial}}</div>
                            </div>
                        ` : '';

                        const card = document.createElement('div');
                        card.className = 'chat-msg-card theme-' + theme;
                        card.innerHTML = `
                            <div class="msg-header">
                                <div style="display:flex; align-items:center; gap:8px;">
                                    ${{avatarHtml}}
                                    <span class="source-badge ${{isYt ? 'badge-yt' : 'badge-rnp'}}">${{isYt ? 'YouTube' : 'RNP'}}</span>
                                    <span class="msg-user">${{m.user || 'Participante'}}</span>
                                </div>
                                <span class="msg-time">${{m.time || ''}}</span>
                            </div>
                            <div class="msg-text">${{m.text || ''}}</div>
                        `;
                        container.appendChild(card);
                    }});
                }}
            }} catch (e) {{}}
        }}

        setInterval(updateChat, 600);
        updateChat();
    </script>
</body>
</html>
""")

# --------------------------------------------------------------------------
# OBS OVERLAY: MENSAGEM EM DESTAQUE / LOWER-THIRD (/chat/featured)
# --------------------------------------------------------------------------
@app.get("/chat/featured")
async def get_chat_featured_overlay(style: Optional[str] = None, avatar: Optional[int] = None):
    return HTMLResponse(f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@600;700;800;900&display=swap" rel="stylesheet">
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: 'Outfit', sans-serif; }}
        html, body {{
            width: 100vw;
            height: 100vh;
            background: transparent !important;
            overflow: hidden;
            display: flex;
            align-items: flex-end;
            justify-content: center;
            padding: 36px 60px;
        }}

        @keyframes featSlideUp {{
            from {{ opacity: 0; transform: translateY(40px) scale(0.95); }}
            to {{ opacity: 1; transform: translateY(0) scale(1); }}
        }}
        @keyframes featSlideOut {{
            from {{ opacity: 1; transform: translateY(0) scale(1); }}
            to {{ opacity: 0; transform: translateY(40px) scale(0.95); }}
        }}

        .featured-card {{
            width: 100%;
            max-width: 960px;
            background: rgba(11, 17, 32, 0.95);
            backdrop-filter: blur(18px);
            -webkit-backdrop-filter: blur(18px);
            border: 2px solid #eab308;
            box-shadow: 0 16px 40px rgba(0,0,0,0.8), 0 0 25px rgba(234, 179, 8, 0.4);
            border-radius: 16px;
            padding: 16px 24px;
            display: flex;
            align-items: center;
            gap: 16px;
            animation: featSlideUp 0.35s cubic-bezier(0.16, 1, 0.3, 1) forwards;
            position: relative;
        }}
        .featured-card.is-exiting {{
            animation: featSlideOut 0.3s ease-in forwards;
        }}

        .feat-icon {{
            font-size: 32px;
            display: flex;
            align-items: center;
            justify-content: center;
            background: rgba(234, 179, 8, 0.15);
            border: 1.5px solid #eab308;
            border-radius: 12px;
            width: 56px;
            height: 56px;
            flex-shrink: 0;
        }}

        .feat-avatar-wrap {{
            width: 58px;
            height: 58px;
            border-radius: 50%;
            overflow: hidden;
            border: 2.5px solid #eab308;
            box-shadow: 0 0 16px rgba(234, 179, 8, 0.45);
            flex-shrink: 0;
            background: #1e293b;
            display: flex;
            align-items: center;
            justify-content: center;
        }}
        .feat-avatar-img {{
            width: 100%;
            height: 100%;
            object-fit: cover;
            border-radius: 50%;
        }}
        .feat-avatar-fallback {{
            width: 100%;
            height: 100%;
            background: linear-gradient(135deg, #ef4444, #eab308);
            color: #ffffff;
            font-size: 24px;
            font-weight: 900;
            display: flex;
            align-items: center;
            justify-content: center;
            text-transform: uppercase;
        }}

        .feat-content {{
            flex: 1;
            display: flex;
            flex-direction: column;
            gap: 4px;
        }}
        .feat-header {{
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        .feat-user {{
            font-size: 16px;
            font-weight: 800;
            color: #ffffff;
        }}
        .feat-text {{
            font-size: 17px;
            font-weight: 700;
            color: #fef08a;
            line-height: 1.35;
        }}
        .source-tag {{
            font-size: 10px;
            font-weight: 900;
            padding: 2px 6px;
            border-radius: 4px;
            text-transform: uppercase;
        }}
        .tag-rnp {{ background: #0284c7; color: #ffffff; }}
        .tag-yt {{ background: #dc2626; color: #ffffff; }}
    </style>
</head>
<body>
    <div id="featContainer"></div>

    <script>
        const container = document.getElementById('featContainer');
        const overrideAvatar = {avatar if avatar is not None else 'null'};
        let currentSig = '';
        let isVisible = false;

        async function updateFeatured() {{
            try {{
                const res = await fetch('/api/chat?t=' + Date.now());
                const data = await res.json();
                const feat = data.featured_message;
                const showAvatars = (overrideAvatar !== null) ? !!overrideAvatar : (data.show_chat_avatars !== undefined ? data.show_chat_avatars : true);

                if (!feat || !feat.text) {{
                    if (isVisible) {{
                        const el = container.querySelector('.featured-card');
                        if (el) {{
                            el.classList.add('is-exiting');
                            setTimeout(() => {{
                                container.innerHTML = '';
                                isVisible = false;
                                currentSig = '';
                            }}, 280);
                        }} else {{
                            container.innerHTML = '';
                            isVisible = false;
                            currentSig = '';
                        }}
                    }}
                    return;
                }}

                const sig = (feat.id || '') + '_' + feat.user + '_' + feat.text + '_' + (feat.avatar || '') + '_' + showAvatars;
                if (sig !== currentSig) {{
                    currentSig = sig;
                    const isYt = (feat.source === 'youtube');
                    const initial = (feat.user ? feat.user.charAt(0).toUpperCase() : '?');

                    let avatarHtml = '';
                    if (showAvatars) {{
                        avatarHtml = `
                            <div class="feat-avatar-wrap" style="border-color: ${{isYt ? '#ef4444' : '#0284c7'}}; box-shadow: 0 0 16px ${{isYt ? 'rgba(239,68,68,0.5)' : 'rgba(2,132,199,0.5)'}};">
                                ${{feat.avatar ? `<img src="${{feat.avatar}}" referrerpolicy="no-referrer" class="feat-avatar-img" onerror="this.style.display='none'; this.nextElementSibling.style.display='flex';">` : ''}}
                                <div class="feat-avatar-fallback" style="${{feat.avatar ? 'display:none;' : ''}}; background:${{isYt ? 'linear-gradient(135deg, #ef4444, #f87171)' : 'linear-gradient(135deg, #0284c7, #38bdf8)'}};">${{initial}}</div>
                            </div>
                        `;
                    }} else {{
                        avatarHtml = `<div class="feat-icon">${{isYt ? '▶️' : '💬'}}</div>`;
                    }}

                    container.innerHTML = `
                        <div class="featured-card" id="featBox" style="border-color: ${{isYt ? '#ef4444' : '#eab308'}}; box-shadow: 0 16px 40px rgba(0,0,0,0.8), 0 0 25px ${{isYt ? 'rgba(239,68,68,0.4)' : 'rgba(234,179,8,0.4)'}};">
                            ${{avatarHtml}}
                            <div class="feat-content">
                                <div class="feat-header">
                                    <span class="source-tag ${{isYt ? 'tag-yt' : 'tag-rnp'}}">${{isYt ? 'YouTube' : 'RNP'}}</span>
                                    <span class="feat-user">${{feat.user || 'Participante'}}</span>
                                </div>
                                <div class="feat-text">${{feat.text}}</div>
                            </div>
                        </div>
                    `;
                    isVisible = true;
                }}
            }} catch (e) {{}}
        }}

        setInterval(updateFeatured, 400);
        updateFeatured();
    </script>
</body>
</html>
""")

# --------------------------------------------------------------------------
# OBS OVERLAY: APRESENTAÇÃO / SLIDES (/presentation)
# --------------------------------------------------------------------------
@app.get("/presentation")
async def get_presentation_overlay():
    return HTMLResponse("""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        html, body {
            width: 100vw;
            height: 100vh;
            background: transparent !important;
            overflow: hidden;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        img {
            width: 100%;
            height: 100%;
            object-fit: contain;
        }
    </style>
</head>
<body>
    <img id="presImg" src="/raw_frame/presentation">
    <script>
        const img = document.getElementById('presImg');
        function updatePres() {
            if (img) img.src = '/raw_frame/presentation?t=' + Date.now();
        }
        setInterval(updatePres, 66);
        updatePres();
    </script>
</body>
</html>
""")

# --------------------------------------------------------------------------
# OBS OVERLAY: RECEPTOR DE ÁUDIO PCM (/audio)
# --------------------------------------------------------------------------
@app.get("/audio")
async def get_audio_receiver(source: Optional[str] = None, vol: Optional[float] = 1.0):
    source_label = f"Fonte: {source}" if source else "Master Geral"
    return HTMLResponse(f"""
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>RNP Live Audio Bridge (OBS Audio)</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }}
        body {{ 
            background: transparent; 
            color: #38bdf8; 
            display: flex; 
            align-items: center; 
            justify-content: center; 
            height: 100vh; 
            margin: 0; 
            overflow: hidden; 
        }}
        .audio-receiver-card {{
            background: rgba(15, 23, 42, 0.92);
            border: 1px solid rgba(56, 189, 248, 0.4);
            border-radius: 12px;
            padding: 12px 18px;
            display: flex;
            align-items: center;
            gap: 12px;
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.6);
            backdrop-filter: blur(8px);
        }}
        .audio-icon-box {{
            font-size: 24px;
            display: flex;
            align-items: center;
            justify-content: center;
            width: 40px;
            height: 40px;
            background: rgba(56, 189, 248, 0.15);
            border-radius: 8px;
            border: 1px solid rgba(56, 189, 248, 0.3);
        }}
        .audio-meta {{
            display: flex;
            flex-direction: column;
            gap: 2px;
        }}
        .audio-title {{
            font-size: 12.5px;
            font-weight: 800;
            color: #f8fafc;
            display: flex;
            align-items: center;
            gap: 6px;
        }}
        .audio-status {{
            font-size: 10.5px;
            color: #10b981;
            font-family: monospace;
            display: flex;
            align-items: center;
            gap: 4px;
        }}
        .audio-mini-vu {{
            width: 80px;
            height: 7px;
            background: #020617;
            border-radius: 4px;
            overflow: hidden;
            border: 1px solid rgba(255, 255, 255, 0.15);
            margin-top: 3px;
        }}
        .audio-mini-bar {{
            height: 100%;
            width: 0%;
            background: linear-gradient(90deg, #10b981 0%, #f59e0b 70%, #ef4444 100%);
            transition: width 0.05s linear;
        }}
    </style>
</head>
<body>
    <div class="audio-receiver-card" id="audioCard">
        <div class="audio-icon-box" id="audioIcon">🎧</div>
        <div class="audio-meta">
            <div class="audio-title">
                <span>RNP Audio Bridge</span>
                <span style="font-size: 10px; background: rgba(56, 189, 248, 0.2); padding: 1px 6px; border-radius: 4px; color: #38bdf8;">{source_label}</span>
            </div>
            <div class="audio-status">
                <span id="statusDot">🟢</span>
                <span id="statusText">48kHz PCM Ativo</span>
            </div>
            <div class="audio-mini-vu">
                <div class="audio-mini-bar" id="miniVuBar"></div>
            </div>
        </div>
    </div>

    <script>
        let audioCtx = null;
        let gainNode = null;
        let nextStartTime = 0;
        let ws = null;
        const initialVol = {vol if vol is not None else 1.0};
        const targetSource = "{source or ''}";

        function initAudioContext() {{
            if (!audioCtx) {{
                const AudioContext = window.AudioContext || window.webkitAudioContext;
                audioCtx = new AudioContext({{ sampleRate: 48000 }});
                gainNode = audioCtx.createGain();
                gainNode.gain.setValueAtTime(initialVol, audioCtx.currentTime);
                gainNode.connect(audioCtx.destination);
            }}
            if (audioCtx.state === 'suspended') {{
                audioCtx.resume().catch(() => {{}});
            }}
            return audioCtx;
        }}

        function unlockAudio() {{
            const ctx = initAudioContext();
            if (ctx.state === 'suspended') {{
                ctx.resume().catch(() => {{}});
            }}
            try {{
                const osc = ctx.createOscillator();
                const g = ctx.createGain();
                g.gain.setValueAtTime(0.00001, ctx.currentTime);
                osc.connect(g);
                g.connect(ctx.destination);
                osc.start();
                osc.stop(ctx.currentTime + 0.05);
            }} catch(e) {{}}
        }}

        function connectWs() {{
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            ws = new WebSocket(protocol + '//' + window.location.host + '/ws/pcm_out');
            ws.binaryType = 'arraybuffer';

            ws.onopen = () => {{
                const st = document.getElementById('statusText');
                if (st) st.innerText = 'Conectado (48kHz)';
                const dot = document.getElementById('statusDot');
                if (dot) dot.innerText = '🟢';
                unlockAudio();
            }};

            ws.onmessage = (event) => {{
                if (typeof event.data === 'string') {{
                    try {{
                        const msg = JSON.parse(event.data);
                        if (msg.type === 'speakers' && targetSource) {{
                            const isSpeaking = !!(msg.speakers && msg.speakers[targetSource]);
                            if (gainNode && audioCtx) {{
                                const targetGain = isSpeaking ? initialVol : 0.0;
                                gainNode.gain.setTargetAtTime(targetGain, audioCtx.currentTime, 0.05);
                            }}
                            const dot = document.getElementById('statusDot');
                            const st = document.getElementById('statusText');
                            if (isSpeaking) {{
                                if (dot) dot.innerText = '🗣️';
                                if (st) st.innerText = 'Falando (Ao Vivo)';
                            }} else {{
                                if (dot) dot.innerText = '💤';
                                if (st) st.innerText = 'Silêncio / Standby';
                            }}
                        }}
                    }} catch(e) {{}}
                    return;
                }}
                if (!(event.data instanceof ArrayBuffer)) return;
                const float32Array = new Float32Array(event.data);
                if (float32Array.length === 0) return;

                const ctx = initAudioContext();
                if (ctx.state === 'suspended') {{
                    ctx.resume().catch(() => {{}});
                }}

                const buffer = ctx.createBuffer(1, float32Array.length, 48000);
                buffer.copyToChannel(float32Array, 0);

                const source = ctx.createBufferSource();
                source.buffer = buffer;
                source.connect(gainNode);

                const currentTime = ctx.currentTime;
                if (nextStartTime < currentTime) {{
                    nextStartTime = currentTime + 0.015;
                }}
                source.start(nextStartTime);
                nextStartTime += buffer.duration;

                let peak = 0;
                for (let i = 0; i < float32Array.length; i += 4) {{
                    const val = Math.abs(float32Array[i]);
                    if (val > peak) peak = val;
                }}
                const pct = Math.min(100, Math.round(peak * 100));
                const bar = document.getElementById('miniVuBar');
                if (bar) bar.style.width = pct + '%';
            }};

            ws.onclose = () => {{
                const st = document.getElementById('statusText');
                if (st) st.innerText = 'Reconectando...';
                const dot = document.getElementById('statusDot');
                if (dot) dot.innerText = '🟡';
                setTimeout(connectWs, 2000);
            }};

            ws.onerror = () => {{
                try {{ ws.close(); }} catch(e) {{}}
            }};
        }}

        setInterval(() => {{
            const bar = document.getElementById('miniVuBar');
            if (bar) {{
                const cur = parseFloat(bar.style.width || '0');
                if (cur > 0) bar.style.width = Math.max(0, cur - 10) + '%';
            }}
        }}, 100);

        window.addEventListener('load', unlockAudio);
        window.addEventListener('click', unlockAudio);
        window.addEventListener('pointerdown', unlockAudio);
        window.addEventListener('keydown', unlockAudio);
        document.addEventListener('visibilitychange', () => {{
            if (!document.hidden) unlockAudio();
        }});

        unlockAudio();
        connectWs();
    </script>
</body>
</html>
""")

# Frames Brutos
@app.get("/raw_frame/camera/{cam_id}")
async def get_raw_camera_frame(cam_id: str):
    frame = state.latest_camera_frames.get(str(cam_id))
    if frame:
        return Response(content=frame, media_type="image/jpeg", headers={"Cache-Control": "no-store, no-cache, must-revalidate"})
    
    display_name = state.custom_camera_names.get(str(cam_id), f"Câmera {cam_id}")
    is_libras = (str(cam_id) in [str(x) for x in state.libras_camera_ids])
    icon = "🤟" if is_libras else "🎥"
    subtext = "Intérprete de Libras" if is_libras else "Vídeo da RNP Conectado"

    placeholder_svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="903" height="508" viewBox="0 0 903 508">
      <rect width="903" height="508" fill="#0b1120"/>
      <rect x="8" y="8" width="887" height="492" rx="12" fill="#0f172a" stroke="{('#06b6d4' if is_libras else '#1e293b')}" stroke-width="2"/>
      <circle cx="451" cy="220" r="46" fill="#1e293b" stroke="{('#06b6d4' if is_libras else '#38bdf8')}" stroke-width="2"/>
      <text x="451" y="232" fill="{('#06b6d4' if is_libras else '#38bdf8')}" font-size="32" font-family="sans-serif" text-anchor="middle">{icon}</text>
      <text x="451" y="305" fill="#f8fafc" font-size="22" font-weight="bold" font-family="sans-serif" text-anchor="middle">{display_name}</text>
      <text x="451" y="335" fill="#94a3b8" font-size="14" font-family="sans-serif" text-anchor="middle">{subtext}</text>
    </svg>'''
    return Response(content=placeholder_svg, media_type="image/svg+xml", headers={"Cache-Control": "no-store"})

@app.get("/raw_frame/presentation")
async def get_raw_presentation_frame():
    if state.latest_presentation_frame:
        return Response(content=state.latest_presentation_frame, media_type="image/jpeg", headers={"Cache-Control": "no-store, no-cache, must-revalidate"})
    
    placeholder_svg = '''<svg xmlns="http://www.w3.org/2000/svg" width="1920" height="1080" viewBox="0 0 1920 1080">
      <rect width="1920" height="1080" fill="none"/>
      <rect x="20" y="20" width="1880" height="1040" rx="16" fill="rgba(15, 23, 42, 0.75)" stroke="rgba(56, 189, 248, 0.3)" stroke-width="2"/>
      <circle cx="960" cy="460" r="60" fill="rgba(30, 41, 59, 0.8)" stroke="#38bdf8" stroke-width="2"/>
      <text x="960" y="475" fill="#38bdf8" font-size="44" font-family="sans-serif" text-anchor="middle">📊</text>
      <text x="960" y="580" fill="#f8fafc" font-size="34" font-weight="bold" font-family="sans-serif" text-anchor="middle">Apresentação / Slides da RNP</text>
      <text x="960" y="625" fill="#94a3b8" font-size="20" font-family="sans-serif" text-anchor="middle">Aguardando compartilhamento de tela ou slides do palestrante</text>
    </svg>'''
    return Response(content=placeholder_svg, media_type="image/svg+xml", headers={"Cache-Control": "no-store"})

# --------------------------------------------------------------------------
# STREAMS MJPEG CONTÍNUOS DE ALTA PERFORMANCE PARA OBS STUDIO
# --------------------------------------------------------------------------
@app.get("/stream/camera/{cam_id}")
async def stream_camera(cam_id: str):
    cid = str(cam_id)
    async def frame_generator():
        last_frame_bytes = None
        try:
            while True:
                frame = state.latest_camera_frames.get(cid)
                if frame and frame != last_frame_bytes:
                    last_frame_bytes = frame
                    yield (b"--frame\r\n"
                           b"Content-Type: image/jpeg\r\n"
                           b"Content-Length: " + str(len(frame)).encode() + b"\r\n\r\n" +
                           frame + b"\r\n")
                await asyncio.sleep(0.025)
        except (asyncio.CancelledError, GeneratorExit):
            pass

    return StreamingResponse(
        frame_generator(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache"}
    )

@app.get("/stream/presentation")
async def stream_presentation():
    async def frame_generator():
        last_frame_bytes = None
        try:
            while True:
                frame = state.latest_presentation_frame
                if frame and frame != last_frame_bytes:
                    last_frame_bytes = frame
                    yield (b"--frame\r\n"
                           b"Content-Type: image/jpeg\r\n"
                           b"Content-Length: " + str(len(frame)).encode() + b"\r\n\r\n" +
                           frame + b"\r\n")
                await asyncio.sleep(0.033)
        except (asyncio.CancelledError, GeneratorExit):
            pass

    return StreamingResponse(
        frame_generator(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache"}
    )

# --------------------------------------------------------------------------
# CATÁLOGO VISUAL DE ESTILOS & TEMAS (/catalog e /themes)
# --------------------------------------------------------------------------
@app.get("/catalog", response_class=HTMLResponse)
@app.get("/themes", response_class=HTMLResponse)
async def get_themes_catalog():
    return HTMLResponse(f"""
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Catálogo de Design Systems & Temas de Broadcast - RNP to OBS</title>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;600;700;800;900&family=JetBrains+Mono:wght@500;700&family=Plus+Jakarta+Sans:wght@500;600;700;800&display=swap" rel="stylesheet">
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: 'Outfit', sans-serif; }}
        body {{
            background: #090b10;
            color: #f1f5f9;
            padding: 36px 24px;
            min-height: 100vh;
            display: flex;
            flex-direction: column;
            align-items: center;
        }}

        .catalog-container {{
            width: 100%;
            max-width: 1200px;
            display: flex;
            flex-direction: column;
            gap: 32px;
        }}

        .catalog-header {{
            text-align: center;
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 12px;
            padding-bottom: 24px;
            border-bottom: 1px solid rgba(255,255,255,0.08);
        }}
        .catalog-header h1 {{
            font-size: 32px;
            font-weight: 900;
            background: linear-gradient(135deg, #38bdf8, #818cf8);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }}
        .catalog-header p {{
            font-size: 16px;
            color: #94a3b8;
            max-width: 700px;
            line-height: 1.5;
        }}

        .themes-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(520px, 1fr));
            gap: 24px;
        }}

        @media (max-width: 600px) {{
            .themes-grid {{ grid-template-columns: 1fr; }}
        }}

        .theme-card {{
            border-radius: 16px;
            padding: 24px;
            display: flex;
            flex-direction: column;
            gap: 20px;
            position: relative;
            overflow: hidden;
            transition: all 0.3s cubic-bezier(0.16, 1, 0.3, 1);
        }}
        .theme-card:hover {{
            transform: translateY(-3px);
        }}

        .theme-card-header {{
            display: flex;
            align-items: flex-start;
            justify-content: space-between;
            gap: 12px;
        }}
        .theme-meta h2 {{
            font-size: 20px;
            font-weight: 800;
            margin-bottom: 4px;
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        .theme-meta .inspiration {{
            font-size: 12px;
            text-transform: uppercase;
            letter-spacing: 0.8px;
            font-weight: 700;
            opacity: 0.75;
        }}

        .theme-desc {{
            font-size: 13.5px;
            line-height: 1.5;
            opacity: 0.88;
        }}

        .color-palette {{
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        .color-dot {{
            width: 22px;
            height: 22px;
            border-radius: 50%;
            border: 1.5px solid rgba(255,255,255,0.2);
            position: relative;
        }}

        /* LIVE INTERACTIVE DEMO INSIDE CARD */
        .theme-demo-box {{
            border-radius: 12px;
            padding: 16px;
            display: flex;
            flex-direction: column;
            gap: 12px;
        }}
        .demo-row {{
            display: flex;
            align-items: center;
            gap: 8px;
            flex-wrap: wrap;
        }}

        .btn-apply-theme {{
            width: 100%;
            padding: 12px 18px;
            border-radius: 10px;
            font-size: 14px;
            font-weight: 800;
            border: none;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
            transition: all 0.2s cubic-bezier(0.16, 1, 0.3, 1);
            text-decoration: none;
        }}
        .btn-apply-theme:hover {{
            transform: translateY(-1px);
            filter: brightness(1.1);
        }}

        /* --- THEME 1: STUDIO MASTER --- */
        .card-hardware {{
            background: #151820;
            border: 1.5px solid #282f3d;
            box-shadow: 0 10px 30px rgba(0,0,0,0.6);
        }}
        .card-hardware .theme-meta h2 {{ color: #f1f5f9; font-family: 'JetBrains Mono', monospace; }}
        .card-hardware .inspiration {{ color: #10b981; }}
        .card-hardware .theme-demo-box {{
            background: #111319;
            border: 1px solid #232936;
        }}
        .hw-btn {{
            background: linear-gradient(180deg, #242a36 0%, #171b23 100%);
            border: 1px solid #3b4559;
            border-bottom: 2.5px solid #0d1015;
            color: #e2e8f0;
            padding: 7px 12px;
            border-radius: 6px;
            font-size: 12px;
            font-weight: 700;
            font-family: 'JetBrains Mono', monospace;
            cursor: pointer;
        }}
        .hw-btn.active {{
            border-color: #10b981;
            color: #34d399;
            box-shadow: 0 0 12px rgba(16, 185, 129, 0.4), inset 0 1px 3px rgba(0,0,0,0.8);
        }}
        .hw-tally {{
            display: inline-flex;
            align-items: center;
            gap: 5px;
            padding: 3px 8px;
            border-radius: 4px;
            font-size: 10.5px;
            font-weight: 800;
            font-family: 'JetBrains Mono', monospace;
            background: rgba(16, 185, 129, 0.15);
            color: #10b981;
            border: 1px solid #10b981;
        }}
        .card-hardware .btn-apply-theme {{
            background: linear-gradient(180deg, #2a3140 0%, #191d26 100%);
            border: 1px solid #475569;
            border-bottom: 3px solid #0f1217;
            color: #38bdf8;
            font-family: 'JetBrains Mono', monospace;
        }}

        /* --- THEME 2: NORDIC MINIMAL --- */
        .card-nordic {{
            background: #1c1e24;
            border: 1px solid rgba(244, 241, 222, 0.08);
            box-shadow: 0 12px 36px rgba(0,0,0,0.3);
        }}
        .card-nordic .theme-meta h2 {{ color: #f4f1de; font-family: 'Plus Jakarta Sans', sans-serif; }}
        .card-nordic .inspiration {{ color: #81b29a; }}
        .card-nordic .theme-demo-box {{
            background: #141519;
            border: 1px solid rgba(244, 241, 222, 0.06);
            border-radius: 12px;
        }}
        .nordic-btn {{
            background: #23262d;
            border: 1px solid rgba(244, 241, 222, 0.08);
            color: #d1cbc1;
            padding: 7px 14px;
            border-radius: 8px;
            font-size: 12.5px;
            font-weight: 600;
            cursor: pointer;
        }}
        .nordic-btn.active {{
            background: #e07a5f;
            color: #ffffff;
            border-color: #e07a5f;
            box-shadow: 0 4px 12px rgba(224, 122, 95, 0.35);
        }}
        .nordic-pill {{
            padding: 3px 9px;
            border-radius: 20px;
            font-size: 11px;
            font-weight: 700;
            background: rgba(129, 178, 154, 0.2);
            color: #81b29a;
            border: 1px solid rgba(129, 178, 154, 0.4);
        }}
        .card-nordic .btn-apply-theme {{
            background: #e07a5f;
            color: #ffffff;
        }}

        /* --- THEME 3: BROADCAST TITANIUM --- */
        .card-glass {{
            background: rgba(15, 23, 42, 0.9);
            backdrop-filter: blur(16px);
            border: 1.5px solid rgba(56, 189, 248, 0.3);
            box-shadow: 0 12px 40px rgba(0,0,0,0.6), 0 0 25px rgba(56, 189, 248, 0.15);
        }}
        .card-glass .theme-meta h2 {{ color: #ffffff; }}
        .card-glass .inspiration {{ color: #38bdf8; }}
        .card-glass .theme-demo-box {{
            background: rgba(11, 17, 32, 0.85);
            border: 1px solid rgba(56, 189, 248, 0.2);
        }}
        .glass-btn {{
            background: rgba(15, 23, 42, 0.8);
            border: 1px solid rgba(255, 255, 255, 0.15);
            color: #e2e8f0;
            padding: 7px 14px;
            border-radius: 7px;
            font-size: 12.5px;
            font-weight: 700;
            cursor: pointer;
        }}
        .glass-btn.active {{
            background: #38bdf8;
            color: #0f172a;
            border-color: #38bdf8;
            box-shadow: 0 0 14px rgba(56, 189, 248, 0.6);
        }}
        .card-glass .btn-apply-theme {{
            background: #38bdf8;
            color: #0f172a;
            box-shadow: 0 0 18px rgba(56, 189, 248, 0.4);
        }}

        /* --- THEME 4: CYBER OBSIDIAN --- */
        .card-obsidian {{
            background: #0c0d11;
            border: 1.5px solid #22252e;
            box-shadow: 0 8px 30px rgba(0,0,0,0.8);
        }}
        .card-obsidian .theme-meta h2 {{ color: #ffffff; font-family: 'Inter', sans-serif; letter-spacing: 0.5px; }}
        .card-obsidian .inspiration {{ color: #22c55e; }}
        .card-obsidian .theme-demo-box {{
            background: #050507;
            border: 1px solid #27272a;
            border-radius: 6px;
        }}
        .obsidian-btn {{
            background: #111318;
            border: 1px solid #27272a;
            color: #a1a1aa;
            padding: 7px 12px;
            border-radius: 4px;
            font-size: 12px;
            font-weight: 800;
            cursor: pointer;
        }}
        .obsidian-btn.active {{
            background: #22c55e;
            color: #050507;
            border-color: #22c55e;
        }}
        .card-obsidian .btn-apply-theme {{
            background: #22c55e;
            color: #050507;
            border-radius: 4px;
        }}

        /* --- THEME 5: INSTITUTO FEDERAL (IF CLEAN) --- */
        .card-ifes {{
            background: #ffffff;
            border: 2px solid #e2e8f0;
            border-top: 5px solid #15803d;
            box-shadow: 0 10px 30px rgba(0,0,0,0.06);
            color: #0f172a;
        }}
        .card-ifes .theme-meta h2 {{ color: #15803d; font-family: 'Outfit', sans-serif; font-weight: 800; }}
        .card-ifes .inspiration {{ color: #dc2626; font-weight: 700; }}
        .card-ifes .theme-desc {{ color: #334155; }}
        .card-ifes .theme-demo-box {{
            background: #f8fafc;
            border: 1px solid #e2e8f0;
            border-radius: 10px;
        }}
        .ifes-btn {{
            background: #ffffff;
            border: 1px solid #cbd5e1;
            color: #334155;
            padding: 7px 12px;
            border-radius: 6px;
            font-size: 12px;
            font-weight: 700;
            cursor: pointer;
        }}
        .ifes-btn.active {{
            background: #15803d;
            color: #ffffff;
            border-color: #166534;
            box-shadow: 0 2px 8px rgba(22, 163, 74, 0.35);
        }}
        .ifes-pill {{
            padding: 3px 9px;
            border-radius: 20px;
            font-size: 11px;
            font-weight: 800;
        }}
        .card-ifes .btn-apply-theme {{
            background: linear-gradient(135deg, #15803d 0%, #16a34a 100%);
            color: #ffffff;
            border-radius: 6px;
            font-weight: 800;
        }}

        /* TOAST CONFIRMATION */
        #toastNotice {{
            position: fixed;
            bottom: 30px;
            background: #10b981;
            color: #ffffff;
            font-weight: 800;
            font-size: 15px;
            padding: 12px 24px;
            border-radius: 30px;
            box-shadow: 0 10px 30px rgba(16, 185, 129, 0.4);
            display: none;
            align-items: center;
            gap: 8px;
            z-index: 1000;
            animation: toastPop 0.3s cubic-bezier(0.16, 1, 0.3, 1) forwards;
        }}
        @keyframes toastPop {{
            from {{ opacity: 0; transform: translateY(20px) scale(0.9); }}
            to {{ opacity: 1; transform: translateY(0) scale(1); }}
        }}
    </style>
</head>
<body>
    <div id="toastNotice">✅ Tema ativado com sucesso! O painel /control foi atualizado.</div>

    <div class="catalog-container">
        <div class="catalog-header">
            <h1>🎨 Catálogo de Design Systems & Temas de Broadcast</h1>
            <p>Escolha o estilo visual para o seu <strong>Painel de Controle (Dock do OBS)</strong>. Interfaces inspiradas em hardware físico de estúdio, design escandinavo humanizado e software moderno de televisão.</p>
            <div style="display: flex; gap: 10px; margin-top: 6px;">
                <a href="/control" style="background: #38bdf8; color: #0f172a; font-weight: 800; font-size: 13.5px; padding: 8px 18px; border-radius: 8px; text-decoration: none; display: inline-flex; align-items: center; gap: 6px;">🎛️ Abrir Painel de Controle (/control)</a>
            </div>
        </div>

        <div class="themes-grid">
            <!-- 1. STUDIO MASTER -->
            <div class="theme-card card-hardware">
                <div class="theme-card-header">
                    <div class="theme-meta">
                        <h2>🎛️ Studio Master</h2>
                        <span class="inspiration">Hardware Broadcast / Blackmagic ATEM Console</span>
                    </div>
                    <div class="color-palette">
                        <div class="color-dot" style="background: #101318;" title="Onyx Titanium"></div>
                        <div class="color-dot" style="background: #282f3d;" title="Steel Border"></div>
                        <div class="color-dot" style="background: #10b981;" title="Tally Green"></div>
                        <div class="color-dot" style="background: #f59e0b;" title="Amber Warning"></div>
                    </div>
                </div>

                <div class="theme-desc">
                    Inspirado em <strong>consoles e switchers de corte físicos</strong> (Blackmagic ATEM, Roland). Acabamento em titânio fosco com chanfros táteis, relevo mecânico nos botões, lâmpadas piloto Tally iluminadas por LED e tipografia técnica <code>JetBrains Mono</code>. Zero poluição de IA.
                </div>

                <div class="theme-demo-box">
                    <div class="demo-row">
                        <span class="hw-tally">🔴 ON-AIR (RNP)</span>
                        <span class="hw-tally" style="background:rgba(56,189,248,0.15); color:#38bdf8; border-color:#38bdf8;">1080p 60FPS</span>
                        <span class="hw-tally" style="background:rgba(245,158,11,0.15); color:#f59e0b; border-color:#f59e0b;">LIBRAS OK</span>
                    </div>
                    <div class="demo-row">
                        <button class="hw-btn active">CAM 1: Leonardo</button>
                        <button class="hw-btn">CAM 2: Libras</button>
                        <button class="hw-btn">SLIDES (PIP)</button>
                    </div>
                </div>

                <button class="btn-apply-theme" onclick="applyTheme('hardware')">⚡ Ativar Studio Master no Painel</button>
            </div>

            <!-- 2. NORDIC MINIMAL -->
            <div class="theme-card card-nordic">
                <div class="theme-card-header">
                    <div class="theme-meta">
                        <h2>🌿 Nordic Minimal</h2>
                        <span class="inspiration">Warm Slate & Clay / Teenage Engineering / Raycast</span>
                    </div>
                    <div class="color-palette">
                        <div class="color-dot" style="background: #141518;" title="Warm Charcoal"></div>
                        <div class="color-dot" style="background: #e07a5f;" title="Terracota"></div>
                        <div class="color-dot" style="background: #81b29a;" title="Sage Green"></div>
                        <div class="color-dot" style="background: #f4f1de;" title="Cream Sand"></div>
                    </div>
                </div>

                <div class="theme-desc">
                    Design orgânico escandinavo com <strong>tons quentes e materiais naturais sutis</strong>. Elimina a fadiga visual durante lives de 3 a 5 horas. Cantos arredondados contínuos (*squircles*), sombras difusas e micro-interações táteis e acolhedoras.
                </div>

                <div class="theme-demo-box">
                    <div class="demo-row">
                        <span class="nordic-pill">🌿 Conectado</span>
                        <span class="nordic-pill" style="background:rgba(224,122,95,0.15); color:#e07a5f; border-color:#e07a5f;">Áudio Sincronizado</span>
                    </div>
                    <div class="demo-row">
                        <button class="nordic-btn active">Lado a Lado</button>
                        <button class="nordic-btn">Slide Topo</button>
                        <button class="nordic-btn">Split 50/50</button>
                    </div>
                </div>

                <button class="btn-apply-theme" onclick="applyTheme('nordic')">⚡ Ativar Nordic Minimal no Painel</button>
            </div>

            <!-- 3. BROADCAST TITANIUM -->
            <div class="theme-card card-glass">
                <div class="theme-card-header">
                    <div class="theme-meta">
                        <h2>📺 Broadcast Titanium</h2>
                        <span class="inspiration">Clean Glass Pro / vMix / TV Control Room</span>
                    </div>
                    <div class="color-palette">
                        <div class="color-dot" style="background: #0b1120;" title="Deep Navy"></div>
                        <div class="color-dot" style="background: #38bdf8;" title="Cyan Tech"></div>
                        <div class="color-dot" style="background: #2563eb;" title="Electric Blue"></div>
                        <div class="color-dot" style="background: #ffffff;" title="Pure White"></div>
                    </div>
                </div>

                <div class="theme-desc">
                    O visual de <strong>softwares profissionais de switchers de televisão</strong> (vMix, Vizrt). Vidro fosco escuro (*glassmorphism*), linhas perimétricas em ciano cirúrgico e alto contraste para tomada de decisão instantânea pelo diretor de transmissão.
                </div>

                <div class="theme-demo-box">
                    <div class="demo-row">
                        <button class="glass-btn active">Palco Principal</button>
                        <button class="glass-btn">Grade 4x Cams</button>
                        <button class="glass-btn">Destaque YouTube</button>
                    </div>
                </div>

                <button class="btn-apply-theme" onclick="applyTheme('glass')">⚡ Ativar Broadcast Titanium no Painel</button>
            </div>

            <!-- 4. CYBER OBSIDIAN -->
            <div class="theme-card card-obsidian">
                <div class="theme-card-header">
                    <div class="theme-meta">
                        <h2>⬛ Cyber Obsidian</h2>
                        <span class="inspiration">Deep Monolith / Vercel / Laser Precision</span>
                    </div>
                    <div class="color-palette">
                        <div class="color-dot" style="background: #050507;" title="Absolute Black"></div>
                        <div class="color-dot" style="background: #22c55e;" title="Phosphor Green"></div>
                        <div class="color-dot" style="background: #eab308;" title="Gold Focus"></div>
                        <div class="color-dot" style="background: #94a3b8;" title="Platinum"></div>
                    </div>
                </div>

                <div class="theme-desc">
                    Preto absoluto (<code>#050507</code>) com <strong>divisórias ultra-finas de 1px cortadas a laser</strong>. Acentos em verde fósforo de estúdio clássico e tipografia com contraste cirúrgico. Máximo minimalismo e economia de processamento de GPU.
                </div>

                <div class="theme-demo-box">
                    <div class="demo-row">
                        <button class="obsidian-btn active">MASTER ACTIVE</button>
                        <button class="obsidian-btn">PREVIEW</button>
                        <button class="obsidian-btn">TALLY SAFE</button>
                    </div>
                </div>

                <button class="btn-apply-theme" onclick="applyTheme('obsidian')">⚡ Ativar Cyber Obsidian no Painel</button>
            </div>

            <!-- 5. INSTITUTO FEDERAL (IF CLEAN) -->
            <div class="theme-card card-ifes">
                <div class="theme-card-header">
                    <div class="theme-meta">
                        <h2>🏛️ Instituto Federal (IF Clean / Ifes)</h2>
                        <span class="inspiration">Identidade Visual IF • Cefor / Ifes • Branco, Verde e Vermelho</span>
                    </div>
                    <div class="color-palette">
                        <div class="color-dot" style="background: #ffffff; border: 1px solid #cbd5e1;" title="Branco Puro"></div>
                        <div class="color-dot" style="background: #15803d;" title="Verde Institucional"></div>
                        <div class="color-dot" style="background: #dc2626;" title="Vermelho Oficial"></div>
                        <div class="color-dot" style="background: #0f172a;" title="Grafite Texto"></div>
                    </div>
                </div>

                <div class="theme-desc">
                    Tema institucional claro, elegante e profissional alinhado à <strong>identidade visual oficial dos Institutos Federais (IF / Ifes)</strong>. Fundo branco/off-white que harmoniza perfeitamente com monitores de estúdio claros, títulos e destaques em verde esmeralda e botões de ação e alertas em vermelho vivo.
                </div>

                <div class="theme-demo-box">
                    <div class="demo-row">
                        <span class="ifes-pill" style="background: #f0fdf4; color: #15803d; border: 1px solid #22c55e;">● Conectado RNP</span>
                        <span class="ifes-pill" style="background: #fef2f2; color: #dc2626; border: 1px solid #ef4444;">● Transmissão Ativa</span>
                    </div>
                    <div class="demo-row">
                        <button class="ifes-btn active">Lado a Lado</button>
                        <button class="ifes-btn">Palco Principal</button>
                        <button class="ifes-btn">Câmeras</button>
                    </div>
                </div>

                <button class="btn-apply-theme" onclick="applyTheme('ifes')">⚡ Ativar Instituto Federal no Painel</button>
            </div>
        </div>
    </div>

    <script>
        async function applyTheme(themeName) {{
            try {{
                const res = await fetch('/api/theme/set', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ theme: themeName }})
                }});
                const data = await res.json();
                
                const toast = document.getElementById('toastNotice');
                toast.innerHTML = '✅ Tema <strong>' + themeName.toUpperCase() + '</strong> aplicado com sucesso no painel /control!';
                toast.style.display = 'flex';
                setTimeout(() => {{
                    toast.style.display = 'none';
                }}, 3500);
            }} catch (e) {{
                alert('Erro ao aplicar tema: ' + e);
            }}
        }}
    </script>
</body>
</html>
""")

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=3333, log_level="warning")

