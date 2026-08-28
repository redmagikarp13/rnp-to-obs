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
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Frame
import pytchat
import emoji

sys.stdout.reconfigure(encoding='utf-8')

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
        self.youtube_connected = False
        self.youtube_status = "Desconectado"
        
        self.cameras_list: List[dict] = []
        self.chat_messages: List[dict] = []
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
        self.keep_alive_task: Optional[asyncio.Task] = None
        self.youtube_task: Optional[asyncio.Task] = None

    def export_config(self) -> dict:
        return {
            "room_url": self.room_url,
            "user_name": self.user_name,
            "user_email": self.user_email,
            "youtube_url": self.youtube_url,
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
        chat = pytchat.create(video_id=video_id)
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
                    "id": f"yt_{int(time.time()*1000)}_{len(state.chat_messages)}",
                    "source": "youtube",
                    "user": author_name,
                    "text": msg_text,
                    "time": t_str,
                    "avatar": avatar_url
                }

                state.chat_messages.append(new_msg)
                if len(state.chat_messages) > 120:
                    state.chat_messages = state.chat_messages[-120:]

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
        state.browser = await state.playwright.chromium.launch(
            headless=True,
            args=[
                '--use-fake-ui-for-media-stream',
                '--autoplay-policy=no-user-gesture-required',
                '--disable-web-security'
            ]
        )
        state.context = await state.browser.new_context(viewport={'width': 1920, 'height': 1080})
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

        # 3. Confirmar entrada
        try:
            final_enter = state.page.locator('input[value="Entrar"], button:has-text("Entrar"), a.btn:has-text("Entrar")')
            if await final_enter.count() > 0 and await final_enter.first.is_visible():
                log("Confirmando entrada na sala...")
                await final_enter.first.click()
        except Exception:
            pass

        # 4. Aguardar sessão
        log("Aguardando preparação da sessão BigBlueButton (15-20s)...")
        await asyncio.sleep(15)

        target_frame = find_active_bbb_frame(state.page)
        state.bbb_frame = target_frame
        log("Conectado ao BigBlueButton!")

        # 5. Fechar modal Somente Ouvir
        try:
            listen_btn = target_frame.locator('[aria-label="Somente ouvir"], [aria-label="Listen only"], button:has-text("Somente ouvir"), [data-test="listenOnlyBtn"]')
            await listen_btn.wait_for(state="visible", timeout=15000)
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
            function startPcmCapture() {
                const audioEl = Array.from(document.querySelectorAll('audio')).find(a => a.srcObject && a.srcObject.getAudioTracks().length > 0);
                if (!audioEl || !audioEl.srcObject) {
                    setTimeout(startPcmCapture, 1000);
                    return;
                }

                try {
                    const ctx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 48000 });
                    const src = ctx.createMediaStreamSource(audioEl.srcObject);
                    const proc = ctx.createScriptProcessor(2048, 1, 1);

                    const ws = new WebSocket('ws://127.0.0.1:3333/ws/pcm_in');
                    ws.binaryType = 'arraybuffer';

                    ws.onopen = () => {
                        proc.onaudioprocess = (e) => {
                            if (ws.readyState === WebSocket.OPEN) {
                                const input = e.inputBuffer.getChannelData(0);
                                ws.send(input.buffer);
                            }
                        };
                        src.connect(proc);
                        proc.connect(ctx.destination);
                    };

                    ws.onclose = () => {
                        try { src.disconnect(); proc.disconnect(); ctx.close(); } catch(e) {}
                        setTimeout(startPcmCapture, 2000);
                    };
                } catch(e) {
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

        if state.keep_alive_task and not state.keep_alive_task.done():
            state.keep_alive_task.cancel()
        state.keep_alive_task = asyncio.create_task(keep_alive_loop())

        if state.youtube_url:
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
    while state.connected and state.page:
        try:
            target_frame = find_active_bbb_frame(state.page)
            state.bbb_frame = target_frame

            # 1. Capturar todas as Webcams
            cam_data = await target_frame.evaluate('''() => {
                const list = [];
                const seenVideos = new Set();

                document.querySelectorAll('video').forEach((video) => {
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

                    const w = video.videoWidth || video.clientWidth || 903;
                    const h = video.videoHeight || video.clientHeight || 508;
                    
                    const canvas = document.createElement('canvas');
                    canvas.width = w;
                    canvas.height = h;
                    const ctx = canvas.getContext('2d');
                    ctx.drawImage(video, 0, 0, w, h);

                    list.push({
                        id: `${list.length + 1}`,
                        name: name,
                        width: w,
                        height: h,
                        frame: canvas.toDataURL('image/jpeg', 0.85).replace(/^data:image\\/jpeg;base64,/, '')
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
                    "is_libras": is_libras
                })
            
            state.cameras_list = cams_info

            # 2. Capturar Apresentação
            screen_data = await target_frame.evaluate('''() => {
                const vid = document.querySelector('#screenshareVideo, #screenshareContainer video, [data-test="screenshareVideo"]');
                if (vid && vid.videoWidth > 0) {
                    const canvas = document.createElement('canvas');
                    const w = vid.videoWidth || 1920;
                    const h = vid.videoHeight || 1080;
                    canvas.width = w;
                    canvas.height = h;
                    const ctx = canvas.getContext('2d');
                    ctx.drawImage(vid, 0, 0, w, h);
                    return { mode: 'screenshare', frame: canvas.toDataURL('image/jpeg', 0.85).replace(/^data:image\\/jpeg;base64,/, '') };
                }
                return null;
            }''')

            if screen_data:
                state.latest_presentation_frame = base64.b64decode(screen_data['frame'])
                state.screenshare_active = True
                state.presentation_mode = "Tela Compartilhada (2.5K/1080p)"
            else:
                pres_loc = target_frame.locator('#presentationContainer, [data-test="presentationContainer"]')
                if await pres_loc.count() > 0 and await pres_loc.first.is_visible():
                    try:
                        slide_bytes = await pres_loc.first.screenshot(type="jpeg", quality=85)
                        state.latest_presentation_frame = slide_bytes
                        state.screenshare_active = False
                        state.presentation_mode = "Slides / Apresentação"
                    except Exception:
                        pass

            # 3. Capturar Chat da RNP
            rnp_messages = await target_frame.evaluate('''() => {
                const containers = document.querySelectorAll('div[data-test="chatMessageItem"], div.chat-message-container');
                const list = [];
                let lastUser = 'Participante';
                let lastTime = '';

                containers.forEach((container, idx) => {
                    const msgType = container.getAttribute('data-message-type');
                    if (msgType && msgType !== 'default') return;

                    const timeEl = container.querySelector('time');
                    if (timeEl && timeEl.innerText.trim()) {
                        lastTime = timeEl.innerText.trim();
                    }

                    const headerNameEl = container.querySelector('div[class*="lfMFCt"] > div:first-child, div[class*="sc-deEcOf"] > div:first-child, [data-test="chatUserMessageSender"]');
                    if (headerNameEl && headerNameEl.innerText.trim()) {
                        const raw = headerNameEl.innerText.trim().split('\\n')[0].trim();
                        if (raw && !raw.includes(':') && raw.length > 2) {
                            lastUser = raw;
                        }
                    }

                    const contentEl = container.querySelector('div.chat-message-content, [data-test="chatUserMessageText"]');
                    if (contentEl) {
                        const clone = contentEl.cloneNode(true);
                        clone.querySelectorAll('.chat-message-toolbar, [data-test="chatMessageToolbar"], button, time, i').forEach(x => x.remove());
                        const cleanText = clone.innerText.trim();
                        if (cleanText) {
                            list.push({
                                id: `rnp_${idx}`,
                                source: 'rnp',
                                user: lastUser,
                                text: cleanText,
                                time: lastTime,
                                avatar: ''
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

            yt_msgs = [m for m in state.chat_messages if m.get("source") == "youtube"]
            combined = clean_rnp_messages + yt_msgs
            state.chat_messages = combined[-100:]

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

        await asyncio.sleep(0.06)

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

@app.post("/api/config/save")
async def api_config_save(req: Request):
    data = await req.json()
    if "room_url" in data: state.room_url = data["room_url"].strip()
    if "user_name" in data: state.user_name = data["user_name"].strip()
    if "user_email" in data: state.user_email = data["user_email"].strip()
    if "youtube_url" in data: state.youtube_url = data["youtube_url"].strip()
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

/* Monitor de Áudio & VU Meter */
.audio-monitor-bar { background: rgba(15, 23, 42, 0.9); border: 1px solid rgba(56, 189, 248, 0.25); border-radius: 10px; padding: 10px 14px; margin-bottom: 14px; display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.vu-container { flex: 1; min-width: 140px; display: flex; flex-direction: column; gap: 4px; }
.vu-label { display: flex; justify-content: space-between; font-size: 11px; font-weight: 700; color: #94a3b8; }
.vu-track { width: 100%; height: 10px; background: #030712; border-radius: 5px; overflow: hidden; border: 1px solid rgba(255,255,255,0.1); position: relative; }
.vu-fill { height: 100%; width: 0%; background: linear-gradient(90deg, #10b981 0%, #10b981 70%, #f59e0b 85%, #ef4444 100%); transition: width 0.08s ease-out; }

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
"""

# --------------------------------------------------------------------------
# PAINEL DE CONTROLE (/control)
# --------------------------------------------------------------------------
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

    chk_chat_avatars = "checked" if state.show_chat_avatars else ""

    html_content = f"""
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>RNP & YouTube to OBS Bridge - Painel de Controle</title>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;600;700;800&display=swap" rel="stylesheet">
    <style>
        {CONTROL_PANEL_CSS}
    </style>
</head>
<body>
    <div class="header">
        <h1><span>RNP to OBS</span> 🎛️</h1>
        <div style="display: flex; gap: 6px; align-items: center; flex-wrap: wrap;">
            <div id="saveToast" class="badge" style="display:none; background: rgba(16, 185, 129, 0.2); color: #10b981; border: 1px solid #10b981;">💾 Salvo!</div>
            <div id="shieldBadge" class="badge" style="background: rgba(56, 189, 248, 0.15); color: #38bdf8; border: 1px solid rgba(56, 189, 248, 0.4);">🛡️ Anti-Inatividade</div>
            <div id="statusBadge" class="badge disconnected">● Desconectado</div>
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
    <div id="pollDockCard" class="card poll-card" style="display: none; border-color: rgba(16, 185, 129, 0.35);">
        <div class="poll-header" style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; flex-wrap: wrap; gap: 6px;">
            <div style="display: flex; align-items: center; gap: 6px;">
                <span style="font-size: 14px;">📊</span>
                <strong style="color: #10b981; font-size: 13px;" id="pollStatusTitle">Enquete Detectada</strong>
                <span id="pollBadgeStatus" class="badge connected" style="font-size: 10px; padding: 2px 6px;">● Monitorando</span>
            </div>
            <div style="display: flex; gap: 6px; flex-wrap: wrap;">
                <button class="btn-copy" style="background: rgba(16, 185, 129, 0.2); color: #10b981; border-color: #10b981;" onclick="copyUrl('http://localhost:3333/poll')">📋 Link OBS</button>
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
            <span>📡 <strong>Modo Transmissão:</strong> Monitorando votos em tempo real (Votação desabilitada).</span>
        </div>

        <div class="poll-question" id="pollQuestionText" style="font-size: 13px; font-weight: 800; color: #ffffff; margin-bottom: 8px;">Pergunta da Enquete...</div>
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
        <div class="actions">
            <button id="btnYtConnect" class="btn btn-danger" style="flex:1;" onclick="handleYtConnect()">▶️ Integrar Chat do YouTube</button>
            <button id="btnYtDisconnect" class="btn btn-ctrl" onclick="handleYtDisconnect()">⏹ Parar</button>
        </div>
    </div>

    <!-- MONITOR DE ÁUDIO EM TEMPO REAL & VU METER -->
    <div class="audio-monitor-bar">
        <div style="display: flex; align-items: center; justify-content: space-between; width: 100%; gap: 10px; flex-wrap: wrap;">
            <button id="btnMuteMonitor" class="btn-ctrl" style="padding: 6px 10px; font-size: 12px; flex: inherit;" onclick="toggleMonitorMute()">
                🔊 <span id="lblMonitorStatus">Som: Ligado</span>
            </button>
            <div style="display: flex; align-items: center; gap: 6px;">
                <span style="font-size: 11.5px; color: #94a3b8; font-weight: 600;">Vol:</span>
                <input type="range" id="rngMonitorVol" min="0" max="1" step="0.05" value="1" style="width: 75px; accent-color: #38bdf8; cursor: pointer;" oninput="updateMonitorVol(this.value)">
            </div>
            <div class="vu-label" style="margin-left: auto;">
                <span style="font-size: 11.5px; color: #38bdf8; font-weight: 800;" id="vuDbText">0%</span>
            </div>
        </div>

        <div class="vu-container">
            <div class="vu-track">
                <div class="vu-fill" id="vuBar"></div>
            </div>
        </div>
    </div>

    <!-- CONFIGURAÇÕES DE GRID, FOCO E ESTILO DE NOMES -->
    <div class="card">
        <div class="section-title">
            <span>🎥 Grid de Câmeras OBS (Ao Vivo)</span>
            <div style="display: flex; gap: 6px; flex-wrap: wrap;">
                <button class="btn-copy" onclick="copyUrl('http://localhost:3333/cameras/grid')">📋 Link Grid</button>
                <button class="btn-copy" style="background: rgba(6, 182, 212, 0.2); color: #22d3ee; border-color: #06b6d4;" onclick="copyUrl('http://localhost:3333/libras')">🤟 Janela Libras</button>
                <button class="btn-copy" style="background: rgba(16, 185, 129, 0.2); color: #10b981; border-color: #10b981;" onclick="copyUrl('http://localhost:3333/audio')">🎧 Áudio</button>
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

        const vuBar = document.getElementById('vuBar');
        const vuDbText = document.getElementById('vuDbText');
        
        window.onAudioPcmChunk = function(peak) {{
            const pct = Math.min(100, Math.round(peak * 180));
            vuBar.style.width = pct + '%';
            vuDbText.innerText = pct + '%';
            if (pct === 0) {{
                setTimeout(() => {{ if (vuBar) vuBar.style.width = '0%'; }}, 100);
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
                btn.style.background = '#ef4444';
                btn.style.color = '#ffffff';
                lbl.innerText = 'Mudo 🔇';
                localStorage.setItem('rnp_monitor_muted', '1');
            }} else {{
                const val = parseFloat(document.getElementById('rngMonitorVol').value);
                if (window.setLocalMonitorVolume) window.setLocalMonitorVolume(val);
                btn.style.background = '#0f172a';
                btn.style.color = '#cbd5e1';
                lbl.innerText = 'Som: Ligado 🔊';
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

        async function votePoll(optIndex) {{
            await fetch('/api/poll/vote', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{ option_index: optIndex }})
            }});
            alert('Voto computado com sucesso!');
        }}

        function renderPoll(poll) {{
            const card = document.getElementById('pollDockCard');
            if (!poll || !poll.is_active) {{
                card.style.display = 'none';
                return;
            }}

            card.style.display = 'block';
            document.getElementById('pollQuestionText').innerText = poll.question || 'Enquete da Sessão';
            
            const title = document.getElementById('pollStatusTitle');
            if (poll.status === 'voting' && !poll.published) {{
                title.innerText = '📊 Enquete Aberta para Votação';
            }} else if (poll.status === 'results' || poll.published) {{
                title.innerText = `📊 Resultados Finais da Enquete (${{poll.total_votes || 0}} votos)`;
            }}

            const cont = document.getElementById('pollOptionsContainer');
            cont.innerHTML = '';

            (poll.options || []).forEach((opt, idx) => {{
                const btn = document.createElement('div');
                btn.className = 'poll-opt-btn';
                btn.onclick = () => votePoll(idx);

                const pct = opt.percentage || (poll.total_votes > 0 ? Math.round((opt.votes / poll.total_votes) * 100) : 0);

                btn.innerHTML = `
                    <div class="poll-progress-bg" style="width: ${{pct}}%;"></div>
                    <div class="poll-opt-content">
                        <span><strong>${{opt.text}}</strong></span>
                        <span style="font-weight: 800; color: #10b981;">${{opt.votes ? opt.votes + ' votos (' + pct + '%)' : pct + '%'}}</span>
                    </div>
                `;
                cont.appendChild(btn);
            }});
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
                document.getElementById('camTotalCount').innerText = cams.length;
                
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

        function renderPoll(poll) {{
            const card = document.getElementById('pollDockCard');
            if (!poll) {{
                card.style.display = 'none';
                return;
            }}

            card.style.display = 'block';
            const isPublished = (poll.published || poll.status === 'published' || poll.status === 'results');
            const statusTitle = document.getElementById('pollStatusTitle');
            const badgeStatus = document.getElementById('pollBadgeStatus');

            if (isPublished) {{
                statusTitle.innerText = '📊 Resultados Finais (' + (poll.total_votes || 0) + ' votos)';
                statusTitle.style.color = '#10b981';
                if (badgeStatus) {{
                    badgeStatus.className = 'badge connected';
                    badgeStatus.innerText = '🟢 Publicada no OBS';
                }}
            }} else {{
                statusTitle.innerText = '🗳️ Enquete em Votação na Sala';
                statusTitle.style.color = '#eab308';
                if (badgeStatus) {{
                    badgeStatus.className = 'badge connecting';
                    badgeStatus.innerText = '🟡 Votação Aberta';
                }}
            }}

            document.getElementById('pollQuestionText').innerText = poll.question || 'Opções da Enquete';

            const optsContainer = document.getElementById('pollOptionsContainer');
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
            if (!confirm('Deseja limpar e ocultar a enquete atual?')) return;
            await fetch('/api/poll/clear', {{ method: 'POST' }});
            document.getElementById('pollDockCard').style.display = 'none';
            fetchStatus();
        }}

        function copyUrl(url) {{
            navigator.clipboard.writeText(url);
            alert('Link copiado com sucesso para o OBS Studio:\\n' + url);
        }}

        setInterval(fetchStatus, 500);
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
    {PCM_AUDIO_SCRIPT}

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
                    slideFrame.innerHTML = `<img id="stage_slide_img" src="/raw_frame/presentation?t=${{Date.now()}}">`;

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
                            <img id="stage_cam_${{mainSpeaker.id}}" src="/raw_frame/camera/${{mainSpeaker.id}}?t=${{Date.now()}}">
                            ${{showNames ? `<div class="name-tag style-${{nameStyle}}">${{mainSpeaker.name}}</div>` : ''}}
                        `;
                        camsCont.appendChild(wrapSpeaker);

                        // 2. Box do Intérprete de Libras (Exatamente do mesmo tamanho)
                        const wrapLibras = document.createElement('div');
                        wrapLibras.className = 'stage-cam-wrap is-libras-box';
                        wrapLibras.innerHTML = `
                            <img id="stage_cam_${{activeLibras.id}}" src="/raw_frame/camera/${{activeLibras.id}}?t=${{Date.now()}}">
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
                                    <img id="stage_cam_${{c.id}}" src="/raw_frame/camera/${{c.id}}?t=${{Date.now()}}">
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
                                <img id="stage_cam_${{activeLibras.id}}" src="/raw_frame/camera/${{activeLibras.id}}?t=${{Date.now()}}">
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
                                <img id="stage_cam_${{activeLibras.id}}" src="/raw_frame/camera/${{activeLibras.id}}?t=${{Date.now()}}">
                                <div class="libras-badge-overlay">🤟 Libras</div>
                                ${{showNames ? `<div class="name-tag style-${{nameStyle}}" style="font-size:11px; padding:3px 8px; bottom:6px; left:6px;">${{activeLibras.name}}</div>` : ''}}
                            `;
                            container.appendChild(librasFloating);
                        }}
                    }}

                }} else {{
                    const slideImg = document.getElementById('stage_slide_img');
                    if (slideImg) slideImg.src = '/raw_frame/presentation?t=' + Date.now();

                    cams.forEach(c => {{
                        const img = document.getElementById(`stage_cam_${{c.id}}`);
                        if (img) img.src = `/raw_frame/camera/${{c.id}}?t=` + Date.now();
                    }});
                }}
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
    {PCM_AUDIO_SCRIPT}

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

                const sig = cams.map(c => c.id + '_' + (c.name || '')).join(',') + '::' + showNames + '::' + nameStyle + '::' + layoutMode + '::' + focusedId;

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
                        img.src = '/raw_frame/camera/' + cam.id + '?t=' + Date.now();
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
                }} else {{
                    cams.forEach(cam => {{
                        const img = document.getElementById('camImg_' + cam.id);
                        if (img) img.src = '/raw_frame/camera/' + cam.id + '?t=' + Date.now();
                    }});
                }}
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
        setInterval(() => {
            img.src = '/raw_frame/presentation?t=' + Date.now();
        }, 66);
    </script>
</body>
</html>
""")

# --------------------------------------------------------------------------
# OBS OVERLAY: RECEPTOR DE ÁUDIO PCM (/audio)
# --------------------------------------------------------------------------
@app.get("/audio")
async def get_audio_receiver():
    return HTMLResponse("""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>RNP Live Audio Bridge (OBS Audio)</title>
    <style>
        body { background: transparent; color: #38bdf8; font-family: sans-serif; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; font-size: 14px; font-weight: bold; }
    </style>
</head>
<body>
    <div id="status">🎧 RNP Audio Bridge Ativo</div>
    <script>
        let audioCtx = null;
        let ws = null;

        function initAudio() {
            if (audioCtx) return;
            const AudioContext = window.AudioContext || window.webkitAudioContext;
            audioCtx = new AudioContext({ sampleRate: 48000 });
            
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            ws = new WebSocket(protocol + '//' + window.location.host + '/ws/pcm_out');
            ws.binaryType = 'arraybuffer';

            let nextStartTime = audioCtx.currentTime;

            ws.onmessage = (event) => {
                if (!(event.data instanceof ArrayBuffer)) return;
                const float32Array = new Float32Array(event.data);
                if (float32Array.length === 0) return;

                const buffer = audioCtx.createBuffer(1, float32Array.length, 48000);
                buffer.copyToChannel(float32Array, 0);

                const source = audioCtx.createBufferSource();
                source.buffer = buffer;
                source.connect(audioCtx.destination);

                const currentTime = audioCtx.currentTime;
                if (nextStartTime < currentTime) {
                    nextStartTime = currentTime;
                }
                source.start(nextStartTime);
                nextStartTime += buffer.duration;
            };

            ws.onclose = () => {
                setTimeout(() => { ws = null; initAudio(); }, 2000);
            };
        }

        window.addEventListener('click', initAudio);
        window.addEventListener('load', initAudio);
        initAudio();
    </script>
</body>
</html>
""")

# Frames Brutos
@app.get("/raw_frame/camera/{cam_id}")
async def get_raw_camera_frame(cam_id: str):
    frame = state.latest_camera_frames.get(str(cam_id))
    if not frame and state.latest_camera_frames:
        frame = list(state.latest_camera_frames.values())[0]
    if frame:
        return Response(content=frame, media_type="image/jpeg", headers={"Cache-Control": "no-store"})
    
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
        return Response(content=state.latest_presentation_frame, media_type="image/jpeg", headers={"Cache-Control": "no-store"})
    
    placeholder_svg = '''<svg xmlns="http://www.w3.org/2000/svg" width="1920" height="1080" viewBox="0 0 1920 1080">
      <rect width="1920" height="1080" fill="none"/>
      <rect x="20" y="20" width="1880" height="1040" rx="16" fill="rgba(15, 23, 42, 0.75)" stroke="rgba(56, 189, 248, 0.3)" stroke-width="2"/>
      <circle cx="960" cy="460" r="60" fill="rgba(30, 41, 59, 0.8)" stroke="#38bdf8" stroke-width="2"/>
      <text x="960" y="475" fill="#38bdf8" font-size="44" font-family="sans-serif" text-anchor="middle">📊</text>
      <text x="960" y="580" fill="#f8fafc" font-size="34" font-weight="bold" font-family="sans-serif" text-anchor="middle">Apresentação / Slides da RNP</text>
      <text x="960" y="625" fill="#94a3b8" font-size="20" font-family="sans-serif" text-anchor="middle">Aguardando compartilhamento de tela ou slides do palestrante</text>
    </svg>'''
    return Response(content=placeholder_svg, media_type="image/svg+xml", headers={"Cache-Control": "no-store"})

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=3333, log_level="warning")
