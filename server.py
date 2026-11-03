import asyncio
import base64
import json
import math
import os
import re
import sys
import time
import urllib.parse
import unicodedata
from typing import Dict, List, Optional, Set
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, Response, FileResponse, RedirectResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Frame
import pytchat
import emoji
from media_bridge import MEDIA_SOURCE_JS, ensure_media_adapter, register_media_routes

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

CONFIG_FILE = os.environ.get('RNP_CONFIG_FILE', os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json"))

app = FastAPI(title="RNP & YouTube to OBS Bridge Server")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

CAMERA_FORMATS = ('auto', 'original', 'retrato', 'paisagem', 'quadrada', 'esticado')

# Campos de enumeração aceitos pelo painel. O valor é refletido no HTML dos Browser
# Sources (presets de palco, tamanho de Libras, estilo de nome, layout do grid), então
# um valor fora da lista não pode ser gravado nem devolvido ao navegador.
LIBRAS_SIZES = ('equal', 'huge', 'large', 'medium')
CAMERA_NAME_STYLES = ('glass', 'pill', 'minimal', 'glow')
GRID_LAYOUT_MODES = ('auto', 'row', 'column', 'grid')
STAGE_PRESETS = ('side-by-side', 'bottom-bar', 'pip-bottom-right', 'pip-top-right',
                 'split-50-50', 'slide-only', 'cameras-only', 'camera-libras', 'grid-libras')
_ENUM_CAMPOS = {
    'libras_size': LIBRAS_SIZES,
    'camera_name_style': CAMERA_NAME_STYLES,
    'grid_layout_mode': GRID_LAYOUT_MODES,
    'stage_preset': STAGE_PRESETS,
}


def erro_enum(data: dict):
    """Mensagem de erro se algum campo de enumeração presente em `data` tiver valor
    fora da lista; None se todos ausentes ou válidos. Chamar ANTES de mutar qualquer
    campo, para um payload inválido não aplicar metade das mudanças."""
    for campo, permitidos in _ENUM_CAMPOS.items():
        if campo in data and data[campo] not in permitidos:
            return f"{campo} deve ser um de {list(permitidos)}"
    return None

# Pico de áudio (0..1) acima do qual consideramos que o participante está falando.
# É configurável por sala/operador (campo `speak_peak_threshold` em config.json, ajustável
# no dock) porque o ganho do microfone varia muito: medição ao vivo com um participante
# real deu mediana 0,063 e piso de silêncio 0,0013 — o valor antigo 0,05 ficava exatamente
# na mediana, então o indicador acendia em só metade das amostras de fala.
DEFAULT_SPEAK_PEAK_THRESHOLD = 0.02
SPEAK_PEAK_MIN = 0.005
SPEAK_PEAK_MAX = 0.5


def parse_speak_threshold(value):
    """Devolve o limiar como float dentro da faixa, ou None se inválido."""
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not (SPEAK_PEAK_MIN <= parsed <= SPEAK_PEAK_MAX):
        return None
    return parsed


# Blocos do mosaico do dock (a unidade de reordenação e de span). A ordem é
# preferência persistida no config do servidor, para que todos os clientes vejam igual.
DOCK_BLOCKS = ('mixer', 'fontes', 'palco-preview', 'cameras', 'chat',
               'palco-presets', 'palco-libras', 'enquete', 'slides')


def _dock_asset_version() -> int:
    """Soma dos mtimes dos assets do dock: muda quando qualquer um muda.

    Clientes de browser-source (OBS/CEF) seguram CSS/JS velho mesmo com
    Cache-Control no-store, porque o href não muda entre deploys. Um ?v= derivado
    dos mtimes força refetch no próximo load da página, sem depender de o operador
    lembrar de hard-reload.
    """
    total = 0
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web')
    for nome in ('dock.css', 'dock.js', 'audio-mixer.js', 'media-source.js', 'connection-quality.js'):
        try:
            total += int(os.path.getmtime(os.path.join(base, nome)))
        except OSError:
            pass
    return total


DOCK_ASSET_VERSION = _dock_asset_version()


def parse_block_order(value):
    """Devolve a ordem como lista válida (permutação de DOCK_BLOCKS), ou None."""
    if not isinstance(value, (list, tuple)):
        return None
    if sorted(value) != sorted(DOCK_BLOCKS):
        return None
    return list(value)

# Temas do dock /control. `hardware`, `nordic`, `glass` e `obsidian` foram aposentados no
# redesign de 2026-10; configs antigos migram para o padrão em vez de quebrar.
CONTROL_THEMES = ('broadcast', 'ifes')
DEFAULT_CONTROL_THEME = 'broadcast'


def normalize_theme(value) -> str:
    """Devolve um tema válido; qualquer valor desconhecido vira o padrão."""
    return value if value in CONTROL_THEMES else DEFAULT_CONTROL_THEME

DEFAULT_CONFIG = {
    "room_url": "https://conferenciaweb.rnp.br/ifes/ifes-cefor-01",
    "user_name": "OBS Studio",
    "user_email": "obs@obs.com",
    "youtube_url": "",
    "youtube_auto_connect": False,
    "control_theme": DEFAULT_CONTROL_THEME,
    "show_camera_names": True,
    "camera_name_style": "glass",
    "grid_layout_mode": "auto",
    "camera_format": "original",
    "speak_peak_threshold": DEFAULT_SPEAK_PEAK_THRESHOLD,
    "dock_block_order": list(DOCK_BLOCKS),
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
                # Tema aposentado em config.json antigo não pode subir como valor inválido.
                cfg["control_theme"] = normalize_theme(cfg.get("control_theme"))
                return cfg
        except Exception as e:
            print(f"[Config] Erro ao carregar config.json: {e}")
    return DEFAULT_CONFIG.copy()

def save_disk_config(cfg: dict):
    try:
        with open(CONFIG_FILE + '.tmp', "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=4, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(CONFIG_FILE + '.tmp', CONFIG_FILE)
        print(f"[Config] Configurações salvas com sucesso em {CONFIG_FILE}")
    except Exception as e:
        print(f"[Config] Erro ao salvar config.json: {e}")
        raise

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

def normalized_name(name: str) -> str:
    return ' '.join(unicodedata.normalize('NFC', name).casefold().split())


class BridgeState:
    def __init__(self):
        cfg = load_disk_config()
        self.connected = False
        self.connecting = False
        self.connection_stats: Optional[dict] = None
        self.connection_stats_received_at: float = 0
        # WebRTC é o modo de produção (validado ao vivo em vídeo, YouTube nativo e
        # compartilhamento com áudio): liga por padrão em qualquer caminho de boot
        # (start.bat, app.pyw, .exe). Só o opt-out explícito mantém o legado JPEG.
        self.webrtc_only = os.environ.get('RNP_WEBRTC_ONLY', '1') != '0'
        self.status_message = "Pronto para conectar."
        self.room_url = cfg.get("room_url", "https://conferenciaweb.rnp.br/ifes/ifes-cefor-01")
        self.user_name = cfg.get("user_name", "OBS Studio")
        self.user_email = cfg.get("user_email", "obs@obs.com")
        self.youtube_url = cfg.get("youtube_url", "")
        self.youtube_auto_connect = bool(cfg.get("youtube_auto_connect", False))
        self.youtube_connected = False
        self.youtube_status = "Desconectado"
        self.control_theme = normalize_theme(cfg.get("control_theme"))
        
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
        self.peaks_clients: Set[WebSocket] = set()
        self.last_speaker_map: Dict[str, bool] = {}
        self.audio_channels: List[dict] = []
        self.audio_controls: Dict[str, dict] = {}
        self.youtube: Optional[dict] = None
        self.voice_speakers: List[dict] = []
        self.connection_task: Optional[asyncio.Task] = None
        
        # Opções de Grid, Câmeras e Libras
        self.focused_camera_id: Optional[str] = None
        self.pinned_camera_ids: List[str] = []
        # IDs da reunião não são persistentes; versões antigas usavam posições.
        self.libras_camera_ids: List[str] = []
        self.libras_disabled_camera_ids: Set[str] = set()
        self.libras_saved_names: List[str] = list(cfg.get("libras_saved_names", []))
        self.libras_disabled_names: List[str] = list(cfg.get("libras_disabled_names", []))
        self.libras_auto_detect: bool = cfg.get("libras_auto_detect", True)
        self.libras_size: str = cfg.get("libras_size", "equal")
        self.show_camera_names: bool = cfg.get("show_camera_names", True)
        self.camera_name_style: str = cfg.get("camera_name_style", "glass")
        _fmt = cfg.get("camera_format", "original")
        self.camera_format: str = _fmt if _fmt in CAMERA_FORMATS else "original"
        _limiar = parse_speak_threshold(cfg.get("speak_peak_threshold"))
        self.speak_peak_threshold: float = (
            _limiar if _limiar is not None else DEFAULT_SPEAK_PEAK_THRESHOLD)
        _salva = cfg.get("dock_block_order")
        if isinstance(_salva, list):
            # Migração: ordens salvas antes de um bloco ser fundido/removido (ex.:
            # 'aviso' dentro de 'fontes') perdem a chave órfã em vez de invalidar tudo.
            _salva = [k for k in _salva if k in DOCK_BLOCKS]
        _ordem = parse_block_order(_salva)
        self.dock_block_order: list = _ordem if _ordem is not None else list(DOCK_BLOCKS)
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

    def set_libras_cameras(self, ids):
        requested = list(dict.fromkeys(str(cid) for cid in ids))
        cameras = {str(c['id']): c for c in self.cameras_list}
        if any(cid not in cameras for cid in requested):
            raise HTTPException(409, 'Uma câmera saiu da sala. Atualize o painel antes de marcar Libras.')
        selected_names = {normalized_name(cameras[cid].get('original_name', cameras[cid].get('name', ''))) for cid in requested}
        selected_participants = {cameras[cid]['participant_id'] for cid in requested if cameras[cid].get('participant_id')}
        for cid, cam in cameras.items():
            original = cam.get('original_name', cam.get('name', '')).strip()
            key = normalized_name(original)
            if cid in requested:
                self.libras_disabled_camera_ids.discard(cid)
                self.libras_disabled_names = [n for n in self.libras_disabled_names if normalized_name(n) != key]
                if key and key not in {normalized_name(n) for n in self.libras_saved_names}:
                    self.libras_saved_names.append(original)
            elif cid in self.libras_camera_ids or cam.get('participant_id') in selected_participants:
                self.libras_disabled_camera_ids.add(cid)
                if key not in selected_names:
                    self.libras_saved_names = [n for n in self.libras_saved_names if normalized_name(n) != key]
                    if key and key not in {normalized_name(n) for n in self.libras_disabled_names}:
                        self.libras_disabled_names.append(original)
            cam['is_libras'] = cid in requested
        self.libras_camera_ids = requested

    def reconcile_cameras(self, captured):
        live_ids = {str(c['id']) for c in captured}
        selected = set(self.libras_camera_ids) & live_ids
        selected_participants = {c['participant_id'] for c in captured if str(c['id']) in selected and c.get('participant_id')}
        self.libras_disabled_camera_ids.intersection_update(live_ids)
        self.pinned_camera_ids = [cid for cid in self.pinned_camera_ids if cid in live_ids]
        if self.focused_camera_id not in live_ids:
            self.focused_camera_id = None
        saved = {normalized_name(n) for n in self.libras_saved_names if normalized_name(n)}
        disabled = {normalized_name(n) for n in self.libras_disabled_names if normalized_name(n)}
        frames, cameras = {}, []
        for cam in captured:
            cid, original = str(cam['id']), cam['name'].strip()
            key = normalized_name(original)
            pid = cam.get('participant_id')
            if pid and pid in selected_participants and cid not in selected:
                self.libras_disabled_camera_ids.add(cid)
            blocked = cid in self.libras_disabled_camera_ids or key in disabled
            auto = self.libras_auto_detect and bool(re.search(r'\b(?:libras|intérprete|interprete|ils)\b', key))
            is_libras = not blocked and (cid in selected or key in saved or auto)
            if is_libras:
                selected.add(cid)
                if pid:
                    selected_participants.add(pid)
            else:
                selected.discard(cid)
            if cam.get('frame'):
                frames[cid] = base64.b64decode(cam['frame'])
            cameras.append({k: v for k, v in cam.items() if k != 'frame'} | {
                'id': cid, 'original_name': original,
                'name': self.custom_camera_names.get(cid, original),
                'is_libras': is_libras, 'is_focused': cid == self.focused_camera_id,
                'is_pinned': cid in self.pinned_camera_ids})
        self.latest_camera_frames = frames
        self.cameras_list = cameras
        self.libras_camera_ids = [c['id'] for c in cameras if c['id'] in selected]

    def connection_quality(self) -> dict:
        """Qualidade de entrada, usando a pior métrica disponível na janela de 2 s."""
        result = {'level': 'unknown', 'rtt_ms': None, 'jitter_ms': None,
                  'loss_pct': None, 'age_ms': None}
        if self.connecting:
            return dict(result, level='connecting')
        if not self.connected:
            return dict(result, level='disconnected')
        sample = self.connection_stats or {}
        age = sample.get('age_ms')
        if type(age) not in (int, float) or not math.isfinite(age) or age < 0:
            return result
        age += max(0, time.monotonic() - self.connection_stats_received_at) * 1000
        if age > 6000:
            return result
        result['age_ms'] = round(age)
        if sample.get('unavailable') and not sample.get('degraded'):
            return result
        severity = 2 if sample.get('degraded') else -1
        # Faixas operacionais, não um teste de velocidade nem diagnóstico da causa.
        for key, warning, poor in (('rtt_ms', 300, 600), ('jitter_ms', 30, 60), ('loss_pct', 2, 5)):
            value = sample.get(key)
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                continue
            if key == 'loss_pct' and value > 100:
                continue
            result[key] = round(value, 2)
            severity = max(severity, 2 if value >= poor else 1 if value >= warning else 0)
        if severity >= 0:
            result['level'] = ('good', 'fair', 'poor')[severity]
        return result

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
            "camera_format": self.camera_format,
            "speak_peak_threshold": self.speak_peak_threshold,
            "dock_block_order": self.dock_block_order,
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
            "libras_disabled_names": self.libras_disabled_names
        }

state = BridgeState()

def log(msg: str):
    state.status_message = msg
    print(f"[Bridge] {msg}", flush=True)

def find_active_bbb_frame(page: Page) -> Frame:
    # O cliente real da reunião é o BBB html5 client (ou o endpoint bigbluebutton).
    frames = page.frames
    for f in frames:
        if "html5client" in f.url or "/bigbluebutton/" in f.url:
            return f
    # Hosts de reunião Mconf (ex.: live-awsNNN.mconf.rnp.br/...), mas NUNCA o portal
    # conferenciaweb.rnp.br/sala/<slug>: o slug da sala pode conter "mconf" (ex.:
    # 'mconf-cefor03') e o predicado amplo escolhia o portal externo, que não tem
    # #app/webcamVideoItem -> o adaptador via zero mídia e o dock travava em 'conectando'.
    for f in frames:
        if "mconf" in f.url and "conferenciaweb" not in f.url and "/sala/" not in f.url:
            return f
    return page.main_frame

register_media_routes(app, lambda: state, find_active_bbb_frame)

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
        # interruptable=False: o default do pytchat registra signal.signal(SIGINT),
        # que estoura ("signal only works in main thread") porque criamos o chat via
        # asyncio.to_thread (worker). O ciclo de vida já é gerenciado pelo cancelamento
        # da task asyncio, então não precisamos do handler de SIGINT do pytchat.
        chat = await asyncio.to_thread(pytchat.create, video_id=video_id, interruptable=False)
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
async def start_connection(room_url: str, user_name: str, user_email: str):
    """Inicia uma conexão cancelando e aguardando qualquer tentativa anterior (evita duas salas ativas)."""
    previous = state.connection_task
    if previous is not None and not previous.done():
        previous.cancel()
        try:
            await previous
        except BaseException:
            pass
    state.connection_task = asyncio.create_task(run_connection(room_url, user_name, user_email))


async def cancel_connection():
    task = state.connection_task
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except BaseException:
            pass
    state.connection_task = None


async def run_connection(room_url: str, user_name: str, user_email: str):
    if state.connected or state.connecting:
        await stop_connection()

    state.connecting = True
    state.connected = False
    state.connection_stats = None
    state.connection_stats_received_at = 0
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
        try:
            max_peers = int(os.environ.get('RNP_MAX_PEERS', '0'))
        except ValueError:
            max_peers = 0
        await state.context.add_init_script(f'window.RNP_MAX_PEERS = {max_peers};')
        await state.context.add_init_script(MEDIA_SOURCE_JS)
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
        await ensure_media_adapter(target_frame)
        await target_frame.evaluate('(url) => window.RnpMedia.startPcm(url)',
                                    f'ws://127.0.0.1:{os.environ.get("RNP_PORT", "3333")}/ws/pcm_in')

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

    except asyncio.CancelledError:
        # Cancelada por um connect/disconnect novo: libera o navegador e sai sem marcar como conectado.
        state.connecting = False
        if state.browser:
            try:
                await state.browser.close()
            except Exception:
                pass
            state.browser = None
        raise
    except Exception as e:
        state.connected = False
        state.connecting = False
        log(f"Erro na conexão: {e}")

async def stop_connection():
    log("Desconectando da sala...")
    state.connected = False
    state.connecting = False
    state.connection_stats = None
    state.connection_stats_received_at = 0
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
    state.audio_channels.clear()
    state.audio_controls.clear()
    state.libras_camera_ids.clear()
    state.libras_disabled_camera_ids.clear()
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
                'button:has-text("Verificar")',
                'button:has-text("Continuar")',
                'button:has-text("Estou aqui")',
                'button:has-text("Sim")',
                'button:has-text("Sim, ainda estou aqui")',
                'button:has-text("I\'m still here")',
                'button:has-text("Stay in meeting")'
            ]

            # O modal "Verificação de atividade do usuário" da RNP pode montar tanto no
            # frame do BigBlueButton quanto no documento externo da sala; varre todos.
            candidate_frames = [target_frame]
            try:
                candidate_frames = state.page.frames
            except Exception:
                pass
            for frame in candidate_frames:
                dismissed = False
                for sel in inactivity_selectors:
                    try:
                        btn = frame.locator(sel)
                        if await btn.count() > 0 and await btn.first.is_visible():
                            await btn.first.click()
                            state.inactivity_bypassed_count += 1
                            log(f"🛡️ Modal de inatividade da RNP interceptado e confirmado automaticamente ({state.inactivity_bypassed_count}x)!")
                            dismissed = True
                            break
                    except Exception:
                        pass
                if dismissed:
                    break

            # Contabiliza confirmações feitas pelo guard em página (MutationObserver do
            # media-source.js), que resolvem o modal em subsegundo sem passar por aqui.
            try:
                dismissed_in_page = await target_frame.evaluate('() => window._rnpInactivityDismissed || 0')
                if dismissed_in_page > state.inactivity_bypassed_count:
                    state.inactivity_bypassed_count = dismissed_in_page
            except Exception:
                pass
        except Exception:
            pass

        await asyncio.sleep(25)

# Cadência do sync_loop em produção (webrtc_only): cada volta faz evaluate por
# câmera (fps/fala), então 4 Hz custava caro no piso da ponte. 2 Hz mantém os
# chips de fala e o fps da barra responsivos pela metade do custo. O legado
# (JPEG) continua em 0.05 s porque o mosaico de frames depende dessa cadência.
SYNC_INTERVAL_WEBRTC = 0.5


async def sync_loop():
    last_slide_check = 0.0
    last_chat_check = 0.0
    last_poll_check = 0.0
    while state.connected and state.page:
        t_start = time.time()
        try:
            target_frame = find_active_bbb_frame(state.page)
            state.bbb_frame = target_frame
            await ensure_media_adapter(target_frame)

            # 1. Capturar todas as Webcams com aceleracao, canvas persistente e deteccao de audio/fala
            cam_data = await target_frame.evaluate('''(captureJpeg) => {
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

                const videoEntries = window.RnpMedia.cameraEntries();
                videoEntries.forEach((entry) => {
                    const video = entry.video;
                    if (video.id === 'screenshareVideo' || video.closest('#screenshareContainer') || video.closest('[data-test="screenshareVideo"]')) {
                        return;
                    }
                    if (seenVideos.has(video)) return;
                    seenVideos.add(video);

                    const parent = video.closest('[data-test="webcamVideoItem"]') || video.closest('[data-test="webcamItem"]') || video.closest('div[class*="VideoListItem"]') || video.parentElement;
                    
                    let name = entry.name;
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
                    
                    let frame = null;
                    if (captureJpeg) {
                        const camKey = entry.id;
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
                        frame = canvas.toDataURL('image/jpeg', 0.80).slice(23);
                    }

                    window._fpsSamples = window._fpsSamples || new Map();
                    const q = (video.getVideoPlaybackQuality ? video.getVideoPlaybackQuality() : null);
                    const nowMs = performance.now();
                    const prevFps = window._fpsSamples.get(entry.id);
                    let fps = (prevFps && q && nowMs > prevFps.t) ? (q.totalVideoFrames - prevFps.f) / ((nowMs - prevFps.t) / 1000) : 0;
                    if (fps < 0) fps = 0;
                    window._fpsSamples.set(entry.id, {t: nowMs, f: q ? q.totalVideoFrames : 0});

                    list.push({
                        id: entry.id,
                        participant_id: entry.participant_id,
                        name: name,
                        width: w,
                        height: h,
                        fps: Math.round(fps * 10) / 10,
                        frame: frame,
                        is_speaking: isSpeaking,
                        is_muted: isMuted
                    });
                });

                const liveIds = new Set(list.map(c => c.id));
                for (const id of Object.keys(window._camCanvasMap)) {
                    if (!liveIds.has(id)) delete window._camCanvasMap[id];
                }
                if (window._fpsSamples) {
                    for (const id of [...window._fpsSamples.keys()]) {
                        if (!liveIds.has(id)) window._fpsSamples.delete(id);
                    }
                }
                return {cameras: list, audio: window.RnpMedia.audioInfo(), youtube: window.RnpMedia.youtubeState(),
                    connection_stats: window.RnpConnection?.snapshot() || null};
            }''', not state.webrtc_only)

            state.audio_channels = cam_data['audio']
            state.youtube = cam_data.get('youtube')
            state.connection_stats = cam_data.get('connection_stats')
            state.connection_stats_received_at = time.monotonic()

            # Fala por pico real do microfone (independe dos indicadores de DOM do BBB).
            # O limiar vem do estado, não de constante: cada sala/operador tem um ganho
            # de microfone diferente e o dock permite ajustar em runtime.
            limiar_fala = state.speak_peak_threshold
            peaks = {}
            for a in cam_data.get('audio', []):
                pid = a.get('participant_id')
                if pid and not str(a.get('id', '')).startswith('screen-audio-'):
                    peaks[pid] = max(peaks.get(pid, 0.0), float(a.get('peak') or 0.0))
            for cam in cam_data['cameras']:
                pid = cam.get('participant_id')
                if pid and peaks.get(pid, 0.0) > limiar_fala:
                    cam['is_speaking'] = True
            cam_pids = {c.get('participant_id') for c in cam_data['cameras'] if c.get('participant_id')}
            state.voice_speakers = [
                {'id': a.get('id'), 'participant_id': a.get('participant_id'), 'name': a.get('name')}
                for a in cam_data.get('audio', [])
                if a.get('participant_id')
                and not str(a.get('id', '')).startswith('screen-audio-')
                and float(a.get('peak') or 0.0) > limiar_fala
                and a['participant_id'] not in cam_pids
            ]

            state.reconcile_cameras(cam_data['cameras'])
            cams_info = state.cameras_list

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
            screen_data = await target_frame.evaluate('''(captureJpeg) => {
                const vid = document.querySelector('#screenshareVideo, #screenshareContainer video, [data-test="screenshareVideo"]');
                if (vid && vid.videoWidth > 0) {
                    if (!captureJpeg) return { mode: 'screenshare', frame: null };
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
            }''', not state.webrtc_only)

            if screen_data:
                state.latest_presentation_frame = base64.b64decode(screen_data['frame']) if screen_data['frame'] else None
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
        sleep_time = max(0.015, (SYNC_INTERVAL_WEBRTC if state.webrtc_only else 0.05) - elapsed)
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

@app.websocket("/ws/peaks_in")
async def ws_peaks_in(websocket: WebSocket):
    # Picos por trilha empurrados pelo adaptador (media-source.js) na cadência do
    # sampler (60ms). A ponte só repassa o pacote aos clientes da mesa do dock,
    # sem interpretar: o medidor individual deixa de depender de poll de 500ms.
    # O sampler não pode pausar por falta de ouvintes da mesa: o próprio servidor
    # lê os picos (audioInfo) no sync_loop para detecção de fala e chips de voz.
    await websocket.accept()
    try:
        while True:
            msg = await websocket.receive_text()
            for client in list(state.peaks_clients):
                try:
                    await client.send_text(msg)
                except Exception:
                    state.peaks_clients.discard(client)
    except (WebSocketDisconnect, Exception):
        pass

@app.websocket("/ws/peaks_out")
async def ws_peaks_out(websocket: WebSocket):
    await websocket.accept()
    state.peaks_clients.add(websocket)
    try:
        while True:
            await websocket.receive_text()
    except (WebSocketDisconnect, Exception):
        state.peaks_clients.discard(websocket)

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

@app.post("/api/restart")
async def api_restart():
    # Reinício forçado da ponte: derruba o estado, cancela as tarefas SEM esperar
    # preso por uma task travada, fecha o navegador no melhor esforço e reconecta
    # com a config salva. Recupera uma ponte engasgada onde o 'Conectar' comum
    # (que só chama start_connection) não consegue subir de novo.
    log("Reiniciando a ponte (reset completo)...")
    # Derruba os wait-loops (ex.: 'sala ainda não começou') que testam state.connecting.
    state.connecting = False
    state.connected = False
    alvos = [state.connection_task, state.sync_task, state.content_sync_task,
             state.keep_alive_task, state.youtube_task]
    for t in alvos:
        if t and not t.done():
            t.cancel()
    vivos = [t for t in alvos if t and not t.done()]
    if vivos:
        await asyncio.wait(vivos, timeout=3)
    # Fecha navegador/playwright com timeout para não vazar um Edge nem travar aqui.
    for recurso in (state.context, state.browser):
        try:
            if recurso:
                await asyncio.wait_for(recurso.close(), timeout=5)
        except Exception:
            pass
    try:
        if state.playwright:
            await asyncio.wait_for(state.playwright.stop(), timeout=5)
    except Exception:
        pass
    state.browser = None
    state.context = None
    state.page = None
    state.bbb_frame = None
    state.playwright = None
    state.cameras_list = []
    state.audio_channels = []
    state.voice_speakers = []
    # Reconecta do zero, em background, com a config salva.
    state.connection_task = asyncio.create_task(
        run_connection(state.room_url, state.user_name, state.user_email))
    return JSONResponse({"status": "restarting"})


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
    await start_connection(room_url, user_name, user_email)
    return JSONResponse({"status": "connecting", "message": "Iniciando conexão com a sala..."})

@app.post("/api/disconnect")
async def api_disconnect():
    await cancel_connection()
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
    # Valida antes de mutar qualquer campo: um valor inválido não pode deixar metade
    # do payload aplicada e a outra metade salva em disco.
    if "speak_peak_threshold" in data:
        limiar = parse_speak_threshold(data["speak_peak_threshold"])
        if limiar is None:
            return JSONResponse(
                {"status": "error",
                 "message": f"speak_peak_threshold deve ser um número entre {SPEAK_PEAK_MIN} e {SPEAK_PEAK_MAX}"},
                status_code=400)
        data["speak_peak_threshold"] = limiar
    if "dock_block_order" in data:
        ordem = parse_block_order(data["dock_block_order"])
        if ordem is None:
            return JSONResponse(
                {"status": "error",
                 "message": f"dock_block_order deve ser uma permutação de {list(DOCK_BLOCKS)}"},
                status_code=400)
        data["dock_block_order"] = ordem
    erro = erro_enum(data)
    if erro:
        return JSONResponse({"status": "error", "message": erro}, status_code=400)
    if "custom_name" in data:
        nome = data["custom_name"]
        if not isinstance(nome, str) or len(nome) > 80:
            return JSONResponse(
                {"status": "error", "message": "custom_name deve ser texto de até 80 caracteres"},
                status_code=400)
    if "focused_camera_id" in data:
        state.focused_camera_id = str(data["focused_camera_id"]) if data["focused_camera_id"] is not None else None
    if "pinned_camera_ids" in data:
        state.pinned_camera_ids = [str(x) for x in data["pinned_camera_ids"]]
    if "libras_camera_ids" in data:
        state.set_libras_cameras(data["libras_camera_ids"])
    if "libras_size" in data:
        state.libras_size = data["libras_size"]
    if "show_camera_names" in data:
        state.show_camera_names = bool(data["show_camera_names"])
    if "camera_name_style" in data:
        state.camera_name_style = data["camera_name_style"]
    if "grid_layout_mode" in data:
        state.grid_layout_mode = data["grid_layout_mode"]
    if "camera_format" in data:
        fmt = str(data["camera_format"])
        if fmt in CAMERA_FORMATS:
            state.camera_format = fmt
    if "speak_peak_threshold" in data:
        state.speak_peak_threshold = data["speak_peak_threshold"]
    if "dock_block_order" in data:
        state.dock_block_order = data["dock_block_order"]
    if "stage_preset" in data:
        state.stage_preset = data["stage_preset"]
    if "custom_name" in data and "cam_id" in data:
        c_name = data["custom_name"].strip()
        cid_str = str(data["cam_id"])
        if c_name:
            state.custom_camera_names[cid_str] = c_name
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
    state.libras_disabled_names = []
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
    theme = data.get("theme")
    if theme is None:
        # Sem campo explícito não se aplica o padrão: isso sobrescreveria a
        # preferência vigente do operador numa chamada acidental.
        return JSONResponse({"status": "error", "message": "Campo 'theme' obrigatório"}, status_code=400)
    if theme in CONTROL_THEMES:
        state.control_theme = theme
        save_disk_config(state.export_config())
        return JSONResponse({"status": "ok", "control_theme": state.control_theme})
    return JSONResponse({"status": "error", "message": "Tema inválido"}, status_code=400)

@app.post("/api/config/save")
async def api_config_save(req: Request):
    data = await req.json()
    erro = erro_enum(data)
    if erro:
        return JSONResponse({"status": "error", "message": erro}, status_code=400)
    if "room_url" in data: state.room_url = data["room_url"].strip()
    if "user_name" in data: state.user_name = data["user_name"].strip()
    if "user_email" in data: state.user_email = data["user_email"].strip()
    if "youtube_url" in data: state.youtube_url = data["youtube_url"].strip()
    if "youtube_auto_connect" in data: state.youtube_auto_connect = bool(data["youtube_auto_connect"])
    if "control_theme" in data: state.control_theme = normalize_theme(data["control_theme"])
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
    if "libras_camera_ids" in data: state.set_libras_cameras(data["libras_camera_ids"])

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
    state.camera_format = cfg.get("camera_format", "original")
    state.stage_preset = cfg["stage_preset"]
    state.libras_size = cfg["libras_size"]
    state.libras_saved_names = []
    state.libras_disabled_names = []
    state.libras_disabled_camera_ids.clear()
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
            "connection_quality": state.connection_quality(),
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
            "webrtc_only": state.webrtc_only,
            "youtube": state.youtube,
            "voice_speakers": state.voice_speakers,
            "speak_peak_threshold": state.speak_peak_threshold,
            "focused_camera_id": state.focused_camera_id,
            "pinned_camera_ids": state.pinned_camera_ids,
            "libras_camera_ids": state.libras_camera_ids,
            "libras_saved_names": state.libras_saved_names,
            "libras_auto_detect": state.libras_auto_detect,
            "libras_size": state.libras_size,
            "show_camera_names": state.show_camera_names,
            "camera_name_style": state.camera_name_style,
            "camera_format": state.camera_format,
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

        // Janela oculta não reproduz monitor local: suspende o grafo de áudio em vez
        // de deixar o WebAudio consumindo CPU em segundo plano.
        document.addEventListener('visibilitychange', () => {
            if (!audioCtx) return;
            if (document.hidden) audioCtx.suspend();
            else audioCtx.resume();
        });

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
CAM_FORMAT_CSS = """
        /* Formatos de câmera (data-camfmt no wrapper): proporção e ajuste da imagem. */
        [data-camfmt="original"] > video, [data-camfmt="original"] > img { object-fit: contain !important; }
        [data-camfmt="esticado"] > video, [data-camfmt="esticado"] > img { object-fit: fill !important; }
        [data-camfmt="auto"] > video, [data-camfmt="auto"] > img,
        [data-camfmt="retrato"] > video, [data-camfmt="retrato"] > img,
        [data-camfmt="paisagem"] > video, [data-camfmt="paisagem"] > img,
        [data-camfmt="quadrada"] > video, [data-camfmt="quadrada"] > img { object-fit: cover !important; }
        .stage-cam-wrap[data-camfmt="retrato"], .cam-card[data-camfmt="retrato"] {
            aspect-ratio: 9 / 16; flex: 0 1 auto; margin: auto; max-width: 100%; max-height: 100%;
        }
        .stage-cam-wrap[data-camfmt="paisagem"], .cam-card[data-camfmt="paisagem"] {
            aspect-ratio: 16 / 9; flex: 0 1 auto; margin: auto; max-width: 100%; max-height: 100%;
        }
        .stage-cam-wrap[data-camfmt="quadrada"], .cam-card[data-camfmt="quadrada"] {
            aspect-ratio: 1 / 1; flex: 0 1 auto; margin: auto; max-width: 100%; max-height: 100%;
        }
        /* "original": a moldura abraça o conteúdo e fica sem fundo preto,
           mas MANTÉM borda e drop shadow da base contornando o vídeo. */
        .stage-cam-wrap[data-camfmt="original"], .cam-card[data-camfmt="original"], .libras-card[data-camfmt="original"] {
            background: transparent !important;
            flex: 0 1 auto;
            margin: auto;
            max-width: 100%;
            max-height: 100%;
        }
"""

SPEAKING_CSS = """
/* Sinalização de quem está falando, aplicada à name-tag EXISTENTE (sem segundo elemento). */
.name-tag.is-speaking {
    border-color: #22c55e !important;
    box-shadow: 0 0 0 1px rgba(34, 197, 94, .7), 0 0 14px rgba(34, 197, 94, .55) !important;
}
.name-tag.is-speaking::after {
    content: '';
    width: 7px; height: 7px; border-radius: 50%;
    background: #22c55e;
    box-shadow: 0 0 8px #22c55e;
    animation: pulseLive 1.2s infinite;
    margin-left: 2px;
}
@keyframes pulseLive {
    0%, 100% { transform: scale(1); opacity: 1; }
    50% { transform: scale(1.3); opacity: 0.6; }
}
"""

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
    sel_fmt_auto = "selected" if state.camera_format == "auto" else ""
    sel_fmt_original = "selected" if state.camera_format == "original" else ""
    sel_fmt_retrato = "selected" if state.camera_format == "retrato" else ""
    sel_fmt_paisagem = "selected" if state.camera_format == "paisagem" else ""
    sel_fmt_quadrada = "selected" if state.camera_format == "quadrada" else ""
    sel_fmt_esticado = "selected" if state.camera_format == "esticado" else ""

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

    sel_th_broadcast = "selected" if state.control_theme == "broadcast" else ""
    sel_th_ifes = "selected" if state.control_theme == "ifes" else ""
    # Marca institucional no lugar do emoji do título, só no tema Ifes; no broadcast
    # o título fica sem ícone (emoji decorativo sai de cena, ver spec §3.3).
    ifes_logo = (
        '<img src="/assets/ifes-logo.png" alt="Ifes" '
        'title="Instituto Federal do Espírito Santo" '
        'style="height: 30px; width: auto; margin-left: 9px; vertical-align: -9px">'
        if state.control_theme == "ifes" else '')
    chk_yt_auto_connect = "checked" if state.youtube_auto_connect else ""
    dock_order_csv = ",".join(state.dock_block_order)

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
    <!-- Design system do dock: tokens + base migrado + componentes + shell. -->
    <link rel="stylesheet" href="/assets/dock.css?v={DOCK_ASSET_VERSION}">
</head>
<body class="theme-{state.control_theme}">
    <div class="dock-statusbar" id="dockStatusbar">
        <h1><span>RNP to OBS</span>{ifes_logo}</h1>
        <span id="statusBadge" class="badge disconnected">● Desconectado</span>
        <span id="shieldBadge" class="badge" title="Blindagem anti-inatividade: confirma sozinho o modal de verificação da RNP">🛡️ Anti-Inatividade</span>
        <span class="dock-statusbar-fps"><span id="dockFps">--</span> fps</span>
        <span class="connection-meter-wrap">
            <button type="button" class="connection-meter" id="connectionMeter" data-level="unknown"
                    aria-label="Qualidade da conexão RNP: sem dados" aria-describedby="connectionDetails"
                    aria-expanded="false" aria-controls="connectionDetails">
                <span class="connection-bars" aria-hidden="true"><i></i><i></i><i></i><i></i></span>
                <span>RNP <span id="connectionLabel">Sem dados</span></span>
                <span id="connectionRtt" class="connection-rtt"></span>
            </button>
            <span id="connectionDetails" class="connection-details" role="tooltip">
                Qualidade da mídia recebida da RNP. Aguardando estatísticas WebRTC.
            </span>
        </span>
        <label class="dock-statusbar-theme" for="selControlTheme">Estilo
            <select id="selControlTheme" onchange="changeControlTheme(this.value)">
                <option value="broadcast" {sel_th_broadcast}>Broadcast</option>
                <option value="ifes" {sel_th_ifes}>Ifes</option>
            </select>
        </label>
        <span class="dock-bar-right">
        <span class="dock-bar-config">
            <button id="btnBarSave" class="btn btn-ctrl" onclick="saveAllConfigurations()" title="Salvar todas as configurações em disco">Salvar</button>
            <button id="btnBarReload" class="btn btn-ctrl" onclick="loadAllConfigurations()" title="Recarregar configurações do disco">Recarregar</button>
            <button id="btnBarDefaults" class="btn btn-ctrl" onclick="resetAllConfigurations()" title="Restaurar configurações padrão">Padrões</button>
        </span>
        <span id="saveToast" class="badge" style="display:none">Salvo!</span>
        <button id="btnBarRestart" onclick="restartBridge()" class="btn btn-ctrl" title="Reiniciar a ponte: reset completo e reconectar (recupera quando 'Conectar' não sobe)">↻ Reiniciar</button>
        <button onclick="handleExitApplication()" class="btn btn-danger" title="Desconectar da sala e encerrar o servidor e aplicativo">Parar</button>
        <span class="dock-connect-wrap">
            <button id="btnConnectMenu" class="btn btn-ctrl" aria-expanded="false" aria-controls="connectPopover" title="Configuração de conexão com a sala RNP">Conexões ▾</button>
            <div class="dock-popover" id="connectPopover" hidden>
    <div class="card" id="connectPanelCard">
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
    <!-- INTEGRAÇÃO COM YOUTUBE LIVE CHAT -->
    <div class="card" >
        <div class="section-title">
            <span style="display: flex; align-items: center; gap: 6px">
                <span>🔴 YouTube Live Chat</span>
            </span>
            <div id="ytStatusBadge" class="badge disconnected" style="font-size: 10.5px">● Desconectado</div>
        </div>
        <div class="input-group">
            <label>Link ou ID da Transmissão do YouTube:</label>
            <input type="text" id="ytUrl" placeholder="Ex: https://youtube.com/live/xxxx ou watch?v=xxxx" value="{state.youtube_url}" onchange="autoSaveField('youtube_url', this.value)">
        </div>
        <div style="margin-top: 8px; margin-bottom: 8px; display: flex; align-items: center; gap: 8px">
            <input type="checkbox" id="chkYtAutoConnect" {chk_yt_auto_connect} onchange="autoSaveField('youtube_auto_connect', this.checked)" style="width: 16px; height: 16px; cursor: pointer">
            <label for="chkYtAutoConnect" style="margin: 0; font-size: 12px; cursor: pointer; user-select: none">
                Conectar chat do YouTube automaticamente ao conectar à sala
            </label>
        </div>
        <div class="actions">
            <button id="btnYtConnect" class="btn btn-danger" style="flex:1" onclick="handleYtConnect()">▶️ Integrar Chat do YouTube</button>
            <button id="btnYtDisconnect" class="btn btn-ctrl" onclick="handleYtDisconnect()">⏹ Parar</button>
        </div>
    </div>
        <div class="actions">
            <button id="btnConnect" class="btn btn-primary" onclick="handleConnect()">⚡ Conectar à RNP</button>
            <button id="btnDisconnect" class="btn btn-danger" onclick="handleDisconnect()">⏹ Desconectar</button>
        </div>
        <div class="log-box" id="logBox">> Pronto para conectar.</div>
    </div>

    <!-- 🎭 PRESETS DE CENA AO VIVO (SLIDES + CÂMERAS) -->
            </div>
        </span>
        </span>
    </div>
    <div class="dock-monitor-fixed">
    <div class="card" style="padding: 10px 14px">
        <div class="monitor-head">
            <span style="font-weight: 800; font-size: 12.5px; display: flex; align-items: center; gap: 6px">
                <span class="live-pulse-dot"></span>
                <span class="monitor-title-text">Monitor de Fontes em Tempo Real</span>
            </span>
            <span id="healthHeartbeat" style="font-size: 10.5px; font-family: monospace">● Ativo (500ms)</span>
        </div>

        <div class="sources-health-grid">
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
        <div id="liveSourceEventBanner" style="display: none; margin-top: 8px; padding: 6px 10px; border-radius: 6px; font-size: 11.5px; font-weight: 700; align-items: center; gap: 6px"></div>
    </div>

    <!-- SALVAR / CARREGAR CONFIGURAÇÕES -->
    </div>
    <nav class="dock-tabs" id="dockTabs" role="tablist" aria-label="Seções do dock">
        <button class="dock-tab is-active" role="tab" aria-selected="true" data-tab="conexao"><span class="tab-long">Conexão</span><span class="tab-short">Conex</span></button>
        <button class="dock-tab" role="tab" aria-selected="false" data-tab="palco"><span class="tab-long">Palco</span><span class="tab-short">Palco</span></button>
        <button class="dock-tab" role="tab" aria-selected="false" data-tab="cameras"><span class="tab-long">Câmeras</span><span class="tab-short">Câm</span></button>
        <button class="dock-tab" role="tab" aria-selected="false" data-tab="audio"><span class="tab-long">Áudio</span><span class="tab-short">Áudio</span></button>
        <button class="dock-tab" role="tab" aria-selected="false" data-tab="chat"><span class="tab-long">Chat</span><span class="tab-short">Chat</span></button>
    </nav>
    <div class="dock-mosaico" id="dockMosaico" data-block-order="{dock_order_csv}">
    <section class="dock-bloco" data-bloco="mixer" data-cat="audio" data-span="2" data-rows="2">
        <div class="dock-bloco-head">
            <span class="dock-bloco-move">
                <button class="dock-move" data-move="-1" title="Mover bloco para a esquerda" aria-label="Mover bloco mixer para a esquerda">‹</button>
                <button class="dock-move" data-move="1" title="Mover bloco para a direita" aria-label="Mover bloco mixer para a direita">›</button>
            </span>
        </div>
    <div class="card" id="audioDockCard">
        <div class="section-title">
            <span style="display: flex; align-items: center; gap: 8px">
                <span>🔊</span> Transmissão de Áudio da Sala & Mixer OBS
            </span>
            <div style="display: flex; gap: 6px; flex-wrap: wrap">
                <button class="btn-ctrl" onclick="testAudioVuMeter()" style="font-size: 11.5px; padding: 4px 10px" title="Testar animação de todos os medidores de áudio">
                    🧪 Testar Medidores
                </button>
            </div>
        </div>

        <!-- Informação do Mixer Unificado -->
        <div class="mixer-info">
            <div style="display: flex; align-items: center; gap: 8px; font-size: 12px">
                <span class="source-health-dot ok"></span>
                <span><strong>Mixer Broadcast:</strong> Medidores verticais calibrados para cada fonte. O master geral sai pelo <strong>/stage</strong> (PCM Float32 48kHz) — marque "Controlar áudio via OBS" nessa fonte.</span>
            </div>
            <span class="badge connected" style="font-size: 11px">OBS: /stage</span>
        </div>

        <!-- SENSIBILIDADE DA DETECÇÃO DE FALA: recolhido por padrão (ajuste raro,
             não operação; no card fixo ele não pode empurrar as abas da dobra). -->
        <details class="dock-advanced">
            <summary>Sensibilidade da detecção de fala</summary>
            <div style="border: 1px solid; border-radius: 8px; padding: 9px 12px; margin-top: 8px; margin-bottom: 12px">
                <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap">
                    <label for="speakThreshold" style="font-size: 11.5px; font-weight: 700">Limiar (0,005 a 0,5)</label>
                    <input type="number" id="speakThreshold" min="0.005" max="0.5" step="0.005"
                           value="{state.speak_peak_threshold}" onchange="saveSpeakThreshold(this)"
                           style="width: 96px">
                    <span id="speakThresholdHint" style="font-size: 11px">
                        Pico mínimo do microfone para marcar alguém como falando.
                    </span>
                </div>
                <div style="font-size: 10.5px; margin-top: 6px; line-height: 1.55">
                    O indicador acende pouco enquanto a pessoa fala? <strong>Baixe</strong> o valor.
                    Acende sozinho com ruído da sala? <strong>Aumente</strong>.
                    Referência medida em sala real: silêncio ≈ 0,001 · fala ≈ 0,02 a 0,15.
                    Vale para o indicador na name-tag e para os chips de voz de quem fala sem câmera.
                </div>
            </div>
        </details>

        <!-- MESA DE SOM BROADCAST COM MEDIDORES VERTICAIS -->
        <div class="audio-mixer-rack" id="audioMixerRack">
            <!-- CANAL MASTER (GERAL) -->
            <div class="audio-channel-strip master-channel" id="channelStripMaster">
                <div class="channel-header">
                    <span style="font-size: 18px">🎛️</span>
                    <span class="channel-title">Master Geral</span>
                    <span class="channel-tag" >OBS: /stage</span>
                </div>

                <div class="channel-meter-box">
                    <div class="meter-scale-vertical">
                        <span style="font-weight:bold">0</span>
                        <span >-3</span>
                        <span >-6</span>
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

                <div class="channel-tag" title="O master é reproduzido pela fonte /stage no OBS">Master via /stage</div>

                <div class="channel-fader-box">
                    <div style="display:flex; justify-content:space-between; width:100%; font-size:10px">
                        <span>Vol:</span>
                        <span id="lblMasterVol">100%</span>
                    </div>
                    <input type="range" id="rngMasterVol" min="0" max="1.5" step="0.05" value="1" style="width:100%; cursor:pointer" oninput="updateMasterVol(this.value)">
                    <button class="btn-ctrl" id="btnMuteMaster" style="width:100%; padding:3px 6px; font-size:10.5px; margin-top:2px" onclick="toggleMasterMute()">
                        🔊 Ativo
                    </button>
                </div>
            </div>

            <!-- FONTES INDIVIDUAIS (GERADAS DINAMICAMENTE PARA CADA CÂMERA / PARTICIPANTE) -->
            <div id="individualChannelsContainer" style="display: flex; gap: 12px; align-items: stretch"></div>

            <!-- PLACEHOLDER QUANDO NÃO HÁ FONTES ADICIONAIS -->
            <div class="audio-channel-strip" id="noIndividualChannelsPlaceholder" style="border-style: dashed; opacity: 0.7; justify-content: center; min-width: 140px; text-align: center">
                <span style="font-size: 24px; margin-bottom: 6px">🎙️</span>
                <span style="font-size: 11px; font-weight: 700">Fontes Individuais</span>
                <p style="font-size: 10px; margin-top: 4px; line-height: 1.3">Aguardando participantes entrarem com câmera/voz na sala RNP.</p>
            </div>
        </div>

        <!-- Monitor Local de Áudio (Para ouvir ou mutar no computador da live) -->
        <div class="audio-monitor-controls-bar" style="display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap">
            <div style="display: flex; align-items: center; gap: 10px">
                <button id="btnMuteMonitor" class="btn-ctrl" style="padding: 6px 12px; font-size: 12px; border-radius: 6px" onclick="toggleMonitorMute()">
                    🔊 <span id="lblMonitorStatus">Monitor Local: Ligado</span>
                </button>
                <div style="display: flex; align-items: center; gap: 6px">
                    <span style="font-size: 11.5px; font-weight: 600">Volume Local:</span>
                    <input type="range" id="rngMonitorVol" min="0" max="1" step="0.05" value="1" style="width: 85px; cursor: pointer" oninput="updateMonitorVol(this.value)">
                </div>
            </div>
            <div style="font-size: 11px">
                💡 <em>Dica OBS: Cole a URL em uma Fonte de Navegador e marque "Controlar áudio via OBS" nas propriedades.</em>
            </div>
        </div>
    </div>
    </section>
    <section class="dock-bloco" data-bloco="fontes" data-cat="conexao" data-span="2" data-rows="2">
        <div class="dock-bloco-head">
            <span class="dock-bloco-move">
                <button class="dock-move" data-move="-1" title="Mover bloco para a esquerda" aria-label="Mover bloco fontes para a esquerda">‹</button>
                <button class="dock-move" data-move="1" title="Mover bloco para a direita" aria-label="Mover bloco fontes para a direita">›</button>
            </span>
        </div>
    <div class="card" style="padding: 12px 14px">
        <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; flex-wrap: wrap; gap: 6px">
            <div style="display: flex; align-items: center; gap: 8px">
                <span style="font-size: 16px">🔗</span>
                <div>
                    <strong style="font-size: 13px">Fontes para o OBS Studio (Montagem Prévia de Cenas)</strong>
                    <div style="font-size: 11px">Copie os links abaixo e adicione no OBS como <strong>Navegador (Browser Source)</strong> a qualquer momento:</div>
                </div>
            </div>
        </div>

        <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(185px, 1fr)); gap: 8px">
            <!-- Enquete -->
            <button class="btn-copy" style="justify-content: flex-start; text-align: left; padding: 8px 10px" onclick="copyUrl('/poll', this)" title="Copiar URL da Enquete (1920x1080 transparente)">
                <span style="font-size: 18px; margin-right: 6px">📊</span>
                <div style="line-height: 1.2">
                    <div style="font-weight: 800; font-size: 11.5px">Enquete / Votação</div>
                    <div style="font-size: 10px; opacity: 0.8; font-family: monospace">/poll (1920x1080)</div>
                </div>
            </button>

            <!-- Palco Composto -->
            <button class="btn-copy" style="justify-content: flex-start; text-align: left; padding: 8px 10px" onclick="copyUrl('/stage', this)" title="Copiar URL do Palco Composto (1920x1080)">
                <span style="font-size: 18px; margin-right: 6px">🎭</span>
                <div style="line-height: 1.2">
                    <div style="font-weight: 800; font-size: 11.5px">Palco Composto</div>
                    <div style="font-size: 10px; opacity: 0.8; font-family: monospace">/stage (1920x1080)</div>
                </div>
            </button>

            <!-- Janela Libras -->
            <button class="btn-copy" style="justify-content: flex-start; text-align: left; padding: 8px 10px" onclick="copyUrl('/libras', this)" title="Copiar URL da Janela de Libras (PIP)">
                <span style="font-size: 18px; margin-right: 6px">🤟</span>
                <div style="line-height: 1.2">
                    <div style="font-weight: 800; font-size: 11.5px">Janela de Libras</div>
                    <div style="font-size: 10px; opacity: 0.8; font-family: monospace">/libras (PIP / Paritária)</div>
                </div>
            </button>

            <!-- Chat Feed -->
            <button class="btn-copy" style="justify-content: flex-start; text-align: left; padding: 8px 10px" onclick="copyUrl('/chat', this)" title="Copiar URL do Feed de Chat RNP + YouTube">
                <span style="font-size: 18px; margin-right: 6px">💬</span>
                <div style="line-height: 1.2">
                    <div style="font-weight: 800; font-size: 11.5px">Feed de Chat (RNP+YT)</div>
                    <div style="font-size: 10px; opacity: 0.8; font-family: monospace">/chat (Transparente)</div>
                </div>
            </button>

            <!-- Chat Destaque -->
            <button class="btn-copy" style="justify-content: flex-start; text-align: left; padding: 8px 10px" onclick="copyUrl('/chat/featured', this)" title="Copiar URL do Destaque de Mensagem">
                <span style="font-size: 18px; margin-right: 6px">⭐</span>
                <div style="line-height: 1.2">
                    <div style="font-weight: 800; font-size: 11.5px">Chat em Destaque</div>
                    <div style="font-size: 10px; opacity: 0.8; font-family: monospace">/chat/featured (Lower Third)</div>
                </div>
            </button>

            <!-- Grid de Câmeras -->
            <button class="btn-copy" style="justify-content: flex-start; text-align: left; padding: 8px 10px" onclick="copyUrl('/cameras/grid', this)" title="Copiar URL do Grid Dinâmico de Webcams">
                <span style="font-size: 18px; margin-right: 6px">👥</span>
                <div style="line-height: 1.2">
                    <div style="font-weight: 800; font-size: 11.5px">Grid de Câmeras</div>
                    <div style="font-size: 10px; opacity: 0.8; font-family: monospace">/cameras/grid (1920x1080)</div>
                </div>
            </button>

            <!-- Slides / Apresentação -->
            <button class="btn-copy" style="justify-content: flex-start; text-align: left; padding: 8px 10px" onclick="copyUrl('/presentation', this)" title="Copiar URL dos Slides da Apresentação">
                <span style="font-size: 18px; margin-right: 6px">📽️</span>
                <div style="line-height: 1.2">
                    <div style="font-weight: 800; font-size: 11.5px">Slides Apresentação</div>
                    <div style="font-size: 10px; opacity: 0.8; font-family: monospace">/presentation (Slides Puros)</div>
                </div>
            </button>

            <!-- O master de áudio agora vem do /stage (ver botão "Palco Composto");
                 os áudios individuais ficam no rack de áudio abaixo, com link próprio. -->
        </div>
    </div>
        <div class="card dock-notice">
            {'<p >Modo WebRTC: use /stage para câmeras, Libras e compartilhamento de tela. O master de áudio também sai pelo /stage (marque "Controlar áudio via OBS" e desligue "Shutdown source when not visible"). Slides estáticos mantêm captura pontual; previews JPEG de câmeras e grid legado permanecem desativados.</p>' if state.webrtc_only else ''}
        </div>
    </section>
    <section class="dock-bloco" data-bloco="palco-preview" data-cat="palco" data-span="2" data-rows="2">
        <div class="dock-bloco-head">
            <span class="dock-bloco-move">
                <button class="dock-move" data-move="-1" title="Mover bloco para a esquerda" aria-label="Mover bloco palco-preview para a esquerda">‹</button>
                <button class="dock-move" data-move="1" title="Mover bloco para a direita" aria-label="Mover bloco palco-preview para a direita">›</button>
            </span>
        </div>
    <div class="card" data-block="palco-preview">
        <div class="section-title">
            <span style="display: flex; align-items: center; gap: 6px">
                <span>🎭 Palco Composto (Slides + Câmeras)</span>
            </span>
            <div style="display: flex; gap: 6px; flex-wrap: wrap">
                <button id="btnToggleStagePreview" class="btn-ctrl" onclick="RnpDock.togglePreview('stage')">Ocultar preview</button>
            </div>
        </div>

        <div class="obs-grid-preview-box" style="height: 180px">
            <iframe id="stagePreviewIframe" src="/stage?audio=0"></iframe>
        </div>
    </div>
    </section>
    <section class="dock-bloco" data-bloco="cameras" data-cat="cameras" data-span="2" data-rows="2">
        <div class="dock-bloco-head">
            <span class="dock-bloco-move">
                <button class="dock-move" data-move="-1" title="Mover bloco para a esquerda" aria-label="Mover bloco cameras para a esquerda">‹</button>
                <button class="dock-move" data-move="1" title="Mover bloco para a direita" aria-label="Mover bloco cameras para a direita">›</button>
            </span>
        </div>
    <div class="card">
        <div class="section-title">
            <span>🎥 Grid de Câmeras OBS (Ao Vivo)</span>
            <div style="display: flex; gap: 6px; flex-wrap: wrap">
                <button id="btnToggleCamPreview" class="btn-ctrl" onclick="RnpDock.togglePreview('cameras')">Ocultar preview</button>
            </div>
        </div>

        <div class="obs-grid-preview-box">
            <iframe id="gridPreviewIframe" src="/cameras/grid?audio=0"></iframe>
        </div>

        <div class="grid-options-bar">
            <div class="grid-opt-group">
                <label style="margin: 0; display: flex; align-items: center; gap: 6px; cursor: pointer; font-size: 12px">
                    <input type="checkbox" id="chkShowNames" {chk_show_names} onchange="updateGridConfig()">
                    <span>Exibir Nomes</span>
                </label>
            </div>

            <div class="grid-opt-group">
                <label style="margin: 0; font-size: 12px">Estilo:</label>
                <select id="selNameStyle" style="width: auto; padding: 4px 8px; font-size: 12px" onchange="updateGridConfig()">
                    <option value="glass" {sel_glass}>Glass (Vidro)</option>
                    <option value="pill" {sel_pill}>Pill (Pílula)</option>
                    <option value="minimal" {sel_min}>Minimal Dark</option>
                    <option value="glow" {sel_glow}>Neon Glow</option>
                </select>
            </div>

            <div class="grid-opt-group">
                <label style="margin: 0; font-size: 12px">Layout:</label>
                <select id="selGridLayout" style="width: auto; padding: 4px 8px; font-size: 12px" onchange="updateGridConfig()">
                    <option value="auto" {sel_layout_auto}>🤖 Auto (1920x600)</option>
                    <option value="row" {sel_layout_row}>➡️ Linha Horizontal</option>
                    <option value="column" {sel_layout_col}>⬇️ Coluna Vertical</option>
                    <option value="grid" {sel_layout_grid}>🔲 Grid Quadrantes</option>
                </select>
            </div>

            <div class="grid-opt-group">
                <label style="margin: 0; font-size: 12px">Formato Câmera:</label>
                <select id="selCamFormat" style="width: auto; padding: 4px 8px; font-size: 12px" onchange="updateGridConfig()">
                    <option value="original" {sel_fmt_original}>📐 Original (sem corte)</option>
                    <option value="paisagem" {sel_fmt_paisagem}>🖼️ Paisagem 16:9</option>
                    <option value="quadrada" {sel_fmt_quadrada}>⬜ Quadrada 1:1</option>
                    <option value="retrato" {sel_fmt_retrato}>📱 Retrato 9:16</option>
                    <option value="esticado" {sel_fmt_esticado}>↔️ Esticado (preencher)</option>
                    <option value="auto" {sel_fmt_auto}>🤖 Auto (corte atual)</option>
                </select>
            </div>

            <div class="grid-opt-group" style="width: 100%">
                <button class="btn-ctrl" style="width: 100%" onclick="clearFocusAndPins()">🔄 Restaurar Grid Equilibrado</button>
            </div>
        </div>

        <div class="section-title">
            <span>Câmeras Sincronizadas (<span id="camTotalCount">0</span>)</span>
        </div>

        <div class="preview-grid" id="camerasGrid">
            <div style="font-size: 12px; padding: 10px">Nenhuma câmera ligada no momento.</div>
        </div>
    </div>
    </section>
    <section class="dock-bloco" data-bloco="chat" data-cat="chat" data-span="2" data-rows="2">
        <div class="dock-bloco-head">
            <span class="dock-bloco-move">
                <button class="dock-move" data-move="-1" title="Mover bloco para a esquerda" aria-label="Mover bloco chat para a esquerda">‹</button>
                <button class="dock-move" data-move="1" title="Mover bloco para a direita" aria-label="Mover bloco chat para a direita">›</button>
            </span>
        </div>
    <div class="card">
        <div class="section-title">
            <span>💬 Chat & Destaque (Lower-Third)</span>
            <div style="display: flex; gap: 6px; flex-wrap: wrap">
            </div>
        </div>

        <!-- BANNER DE MENSAGEM EM DESTAQUE ATUAL -->
        <div id="featuredBox" class="featured-active-banner" style="display: none">
            <div style="flex: 1">
                <div style="display: flex; align-items: center; gap: 6px; margin-bottom: 2px">
                    <span style="font-size: 11px; font-weight: 800">🌟 EM DESTAQUE NA TRANSMISSÃO:</span>
                    <span id="featSourceBadge" class="source-tag rnp">RNP</span>
                </div>
                <div style="font-size: 12.5px; font-weight: 700" id="featUser">Nome</div>
                <div style="font-size: 12px" id="featText">Texto da mensagem em destaque...</div>
            </div>
            <button class="btn btn-danger" style="padding: 6px 10px; font-size: 11.5px" onclick="clearFeaturedMessage()">❌ Ocultar Destaque</button>
        </div>

        <div class="grid-options-bar">
            <div class="grid-opt-group">
                <label style="margin: 0; font-size: 12px">Tema:</label>
                <select id="selChatTheme" style="width: auto; padding: 4px 8px; font-size: 12px" onchange="updateChatConfig()">
                    <option value="glass" {sel_chat_glass}>💎 StreamYard Glass</option>
                    <option value="streamer" {sel_chat_streamer}>🔴 Streamer Avatar</option>
                    <option value="broadcast" {sel_chat_bcast}>🎬 Broadcast News</option>
                    <option value="neon" {sel_chat_neon}>⚡ Neon Glow</option>
                    <option value="minimal" {sel_chat_min}>📄 Minimal Legenda</option>
                    <option value="bubbles" {sel_chat_bubbles}>💬 Discord Bubbles</option>
                </select>
            </div>

            <div class="grid-opt-group">
                <label style="margin: 0; font-size: 12px">Fonte:</label>
                <select id="selChatSize" style="width: auto; padding: 4px 8px; font-size: 12px" onchange="updateChatConfig()">
                    <option value="small" {sel_size_small}>Pequena</option>
                    <option value="medium" {sel_size_med}>Média</option>
                    <option value="large" {sel_size_large}>Grande</option>
                </select>
            </div>

            <div class="grid-opt-group">
                <label style="margin: 0; font-size: 12px">Máx:</label>
                <select id="selChatMax" style="width: auto; padding: 4px 8px; font-size: 12px" onchange="updateChatConfig()">
                    <option value="4" {sel_max_4}>4 msgs</option>
                    <option value="6" {sel_max_6}>6 msgs</option>
                    <option value="8" {sel_max_8}>8 msgs</option>
                    <option value="12" {sel_max_12}>12 msgs</option>
                </select>
            </div>

            <div class="grid-opt-group" style="width: 100%; margin-top: 4px">
                <label style="margin: 0; display: flex; align-items: center; gap: 6px; cursor: pointer; font-size: 12px">
                    <input type="checkbox" id="chkShowAvatars" {chk_chat_avatars} onchange="updateChatConfig()" style="width: 15px; height: 15px">
                    <span>👤 Exibir Avatares dos Usuários (YouTube / RNP)</span>
                </label>
            </div>
        </div>

        <div class="chat-preview-container">
            <iframe id="chatPreviewIframe" src="/chat"></iframe>
        </div>

        <!-- LISTA INTERATIVA DE MENSAGENS PARA DESTACAR COM 1 CLIQUE -->
        <div style="margin-top: 14px">
            <div style="display: flex; align-items: center; justify-content: space-between">
                <label style="font-size: 12px; font-weight: 700">Mensagens Recentes (Clique para Destacar na Tela):</label>
                <span id="chatTotalCount" style="font-size: 11px">0 msgs</span>
            </div>
            <div class="chat-manager-list" id="chatListContainer">
                <div style="font-size: 12px; padding: 8px">Nenhuma mensagem recebida ainda.</div>
            </div>
        </div>
    </div>
    </section>
    <section class="dock-bloco" data-bloco="palco-presets" data-cat="palco" data-span="2" data-rows="2">
        <div class="dock-bloco-head">
            <span class="dock-bloco-move">
                <button class="dock-move" data-move="-1" title="Mover bloco para a esquerda" aria-label="Mover bloco palco-presets para a esquerda">‹</button>
                <button class="dock-move" data-move="1" title="Mover bloco para a direita" aria-label="Mover bloco palco-presets para a direita">›</button>
            </span>
        </div>
    <div class="card" data-block="palco-presets">
        <div class="section-title"><span>Presets de Cena</span></div>

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
            <button id="btnPreset_cameras-only" class="btn-ctrl" onclick="setStagePreset('cameras-only')">
                👥 Somente Câmeras
            </button>
            <button id="btnPreset_camera-libras" class="btn-ctrl" onclick="setStagePreset('camera-libras')">
                🤟 Câmera + Libras
            </button>
            <button id="btnPreset_grid-libras" class="btn-ctrl" onclick="setStagePreset('grid-libras')">
                🔳 Grid + Libras
            </button>
        </div>

        <!-- CONFIGURAÇÃO DE TAMANHO DA JANELA DE LIBRAS & AUTO-RECONHECIMENTO -->
    </div>
    </section>
    <section class="dock-bloco" data-bloco="palco-libras" data-cat="palco" data-span="2" data-rows="2">
        <div class="dock-bloco-head">
            <span class="dock-bloco-move">
                <button class="dock-move" data-move="-1" title="Mover bloco para a esquerda" aria-label="Mover bloco palco-libras para a esquerda">‹</button>
                <button class="dock-move" data-move="1" title="Mover bloco para a direita" aria-label="Mover bloco palco-libras para a direita">›</button>
            </span>
        </div>
    <div class="card" data-block="palco-libras">
        <div class="section-title"><span>Libras</span></div>
        <div class="grid-options-bar" style="margin-top: 8px">
            <div class="grid-opt-group" style="width: 100%; justify-content: space-between">
                <label style="margin: 0; font-size: 12px; font-weight: 700">🤟 Tamanho Libras:</label>
                <select id="selLibrasSize" style="width: auto; padding: 4px 8px; font-size: 12px" onchange="updateLibrasSizeConfig()">
                    <option value="equal" {sel_lib_equal}>👥 Igual ao Palestrante (Padrão IFES)</option>
                    <option value="huge" {sel_lib_huge}>📐 Gigante Flutuante (560x315)</option>
                    <option value="large" {sel_lib_large}>📺 Grande Flutuante (480x270)</option>
                    <option value="medium" {sel_lib_med}>▫️ Médio Flutuante (380x214)</option>
                </select>
            </div>
            <div style="width: 100%; display: flex; align-items: center; justify-content: space-between; margin-top: 6px; font-size: 11px; flex-wrap: wrap; gap: 6px">
                <button id="btnAutoLibras" class="btn-ctrl active-pin" style="padding: 3px 8px; font-size: 11px" onclick="toggleLibrasAutoDetect()">
                    🛡️ Auto-Reconhecimento: ATIVO
                </button>
                <div style="display: flex; align-items: center; gap: 6px">
                    <span id="savedInterpretersCount" style="font-size: 11px">0 salvos</span>
                    <button class="btn-ctrl" style="padding: 2px 6px; font-size: 10.5px" onclick="clearSavedLibras()" title="Limpar lista de nomes de intérpretes salvos">🗑️ Limpar</button>
                </div>
            </div>
        </div>
    </div>
    </section>
    <section class="dock-bloco" data-bloco="enquete" data-cat="palco" data-span="1" data-rows="2">
        <div class="dock-bloco-head">
            <span class="dock-bloco-move">
                <button class="dock-move" data-move="-1" title="Mover bloco para a esquerda" aria-label="Mover bloco enquete para a esquerda">‹</button>
                <button class="dock-move" data-move="1" title="Mover bloco para a direita" aria-label="Mover bloco enquete para a direita">›</button>
            </span>
        </div>
    <div id="pollDockCard" class="card poll-card" >
        <div class="poll-header" style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; flex-wrap: wrap; gap: 6px">
            <div style="display: flex; align-items: center; gap: 6px">
                <span style="font-size: 14px">📊</span>
                <strong style="font-size: 13px" id="pollStatusTitle">Enquete da Sala RNP</strong>
                <span id="pollBadgeStatus" class="badge disconnected" style="font-size: 10px; padding: 2px 6px">⚪ Standby</span>
            </div>
            <div style="display: flex; gap: 6px; flex-wrap: wrap">
                <button class="btn btn-ctrl" style="padding: 3px 8px; font-size: 11px" onclick="simulatePoll()" title="Simular uma enquete de teste no OBS para testar o posicionamento">🧪 Testar no OBS</button>
                <button class="btn btn-ctrl" style="padding: 3px 8px; font-size: 11px" onclick="clearPoll()">🗑️ Limpar</button>
            </div>
        </div>

        <div class="grid-options-bar" style="margin-bottom: 10px">
            <div class="grid-opt-group" style="width: 100%; justify-content: space-between">
                <label style="margin: 0; font-size: 12px; font-weight: 700">🎯 Exibir no OBS:</label>
                <select id="selPollDisplayMode" style="width: auto; padding: 4px 8px; font-size: 12px" onchange="updatePollConfig()">
                    <option value="results_only" {sel_pm_results}>📊 Somente Resultados Finais (Recomendado)</option>
                    <option value="always" {sel_pm_always}>🗳️ Votação Ao Vivo + Resultados</option>
                    <option value="hidden" {sel_pm_hidden}>❌ Ocultar no OBS</option>
                </select>
            </div>
            <div class="grid-opt-group" style="width: 100%; justify-content: space-between; margin-top: 4px">
                <label style="margin: 0; font-size: 12px; font-weight: 600">⏱️ Tempo na Tela:</label>
                <select id="selPollDuration" style="width: auto; padding: 4px 8px; font-size: 12px" onchange="updatePollConfig()">
                    <option value="10" {sel_pd_10}>⏱️ 10 segundos</option>
                    <option value="15" {sel_pd_15}>⏱️ 15 segundos (Padrão)</option>
                    <option value="30" {sel_pd_30}>⏱️ 30 segundos</option>
                    <option value="60" {sel_pd_60}>⏱️ 60 segundos (1 min)</option>
                    <option value="0" {sel_pd_inf}>♾️ Fixo (Até Fechar)</option>
                </select>
            </div>
            <div class="grid-opt-group" style="width: 100%; justify-content: space-between; margin-top: 4px">
                <label style="margin: 0; font-size: 12px; font-weight: 600">🎨 Estilo da Enquete:</label>
                <select id="selPollStyle" style="width: auto; padding: 4px 8px; font-size: 12px" onchange="updatePollConfig()">
                    <option value="neon" {sel_pstyle_neon}>⚡ Neon Glow</option>
                    <option value="glass" {sel_pstyle_glass}>💎 Glassmorphism</option>
                    <option value="bubbles" {sel_pstyle_bubbles}>💬 Discord Bubbles</option>
                    <option value="cyber" {sel_pstyle_cyber}>🎬 Cyberpunk Broadcast</option>
                    <option value="minimal" {sel_pstyle_min}>📄 Minimal Slate</option>
                </select>
            </div>
            <div style="width: 100%; display: flex; gap: 6px; margin-top: 6px">
                <button id="btnForcePollShow" class="btn-ctrl" style="flex:1" onclick="toggleForcePoll(true)">
                    ✨ Mostrar no OBS Agora
                </button>
                <button id="btnForcePollHide" class="btn-ctrl" style="flex:1" onclick="toggleForcePoll(false)">
                    ❌ Ocultar Agora
                </button>
            </div>
        </div>

        <div style="font-size: 11px; margin-bottom: 6px; display: flex; align-items: center; gap: 4px">
            <span>📡 <strong>Dica OBS:</strong> Cole o link acima em uma fonte de <em>Navegador (1920x1080 transparente)</em>. Ela se ativará automaticamente quando houver enquete.</span>
        </div>

        <div class="poll-question" id="pollQuestionText" style="font-size: 13px; font-weight: 800; margin-bottom: 8px">Aguardando início de enquete...</div>
        <div class="poll-options-grid" id="pollOptionsContainer" style="display: flex; flex-direction: column; gap: 6px"></div>
    </div>
    </section>
    <section class="dock-bloco" data-bloco="slides" data-cat="palco" data-span="2" data-rows="2">
        <div class="dock-bloco-head">
            <span class="dock-bloco-move">
                <button class="dock-move" data-move="-1" title="Mover bloco para a esquerda" aria-label="Mover bloco slides para a esquerda">‹</button>
                <button class="dock-move" data-move="1" title="Mover bloco para a direita" aria-label="Mover bloco slides para a direita">›</button>
            </span>
        </div>
    <div class="card">
        <div class="section-title">
            <span>📊 Apresentação / Slides</span>
            <button id="btnToggleScreenPreview" class="btn-ctrl" onclick="RnpDock.togglePreview('presentation')">Ocultar preview</button>
        </div>
        <div class="thumb-box" style="height: 200px">
            <img id="thumbScreen" src="/raw_frame/presentation" onerror="this.style.opacity=0.3" onload="this.style.opacity=1">
        </div>
    </div>
    </section>
    </div>
    <!-- handlers e estado do dock vivem em web/dock.js -->
    {PCM_AUDIO_SCRIPT}
    <script src="/assets/audio-mixer.js?v={DOCK_ASSET_VERSION}"></script>
    <script src="/assets/dock.js?v={DOCK_ASSET_VERSION}"></script>
</body>
</html>
    """
    return HTMLResponse(content=html_content, headers=headers)

# --------------------------------------------------------------------------
# OBS OVERLAY: PALCO COMPOSTO (SLIDES + CÂMERAS + LIBRAS INTEGRADO) (/stage)
# --------------------------------------------------------------------------
@app.get("/stage")
async def get_stage_overlay(preset: Optional[str] = None, libras_size: Optional[str] = None, camfmt: Optional[str] = None):
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
        .slide-frame img, .slide-frame video {{
            width: 100% !important;
            height: 100% !important;
            object-fit: contain !important;
        }}

        /* O player nativo do YouTube vira um iframe (flex item) com tamanho próprio;
           ancora absoluto no slide-frame (position:relative) para preencher de verdade. */
        #ytStageHost, #ytStageHost *, .slide-frame iframe, .slide-frame #ytStageHost {{
            position: absolute !important;
            inset: 0 !important;
            width: 100% !important;
            height: 100% !important;
            display: block;
            border: 0 !important;
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
        .stage-cam-wrap img, .stage-cam-wrap video {{
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
        .libras-floating-box img, .libras-floating-box video {{
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

        /* 7. PRESETS SEM SLIDE (cameras-only / camera-libras / grid-libras): sem
           apresentação, a linha de câmeras preenche todo o quadro; cada palco é
           flex:1 (herdado de .stage-cam-wrap), então N câmeras dividem a largura em
           partes iguais. É o inverso do slide-only. */
        .preset-cameras-only .cameras-row,
        .preset-camera-libras .cameras-row,
        .preset-grid-libras .cameras-row {{
            flex: 1 !important;
            width: 100% !important;
            height: 100% !important;
        }}

        {NAME_TAG_CSS}
        {CAM_FORMAT_CSS}
        {SPEAKING_CSS}
    </style>
</head>
<body>
    <style>
        .stage-media-status {{ position: absolute; inset: 40% 8% auto; color: #cbd5e1; font: 14px sans-serif; text-align: center; pointer-events: none; }}
        /* O seletor universal acima aplica `overflow: hidden` a tudo; aqui é preciso
           sobrescrever, senão a caixa do contêiner (que encolhe ao tamanho do chip)
           recorta o box-shadow e a sombra sai retangular em vez de sangrar. */
        #voiceChips {{ position: absolute; top: 14px; left: 50%; transform: translateX(-50%); display: flex; gap: 8px; z-index: 300; pointer-events: none; overflow: visible; }}
        .voice-chip {{ display: flex; align-items: center; gap: 7px; background: rgba(2, 6, 23, .82); border: 1px solid rgba(34, 197, 94, .65); color: #e2e8f0; font: 700 13px/1.2 sans-serif; padding: 6px 14px; border-radius: 999px; box-shadow: 0 0 14px rgba(34, 197, 94, .45); backdrop-filter: blur(6px); overflow: visible; }}
        .voice-chip::before {{ content: '🎙'; font-size: 12px; }}
    </style>
    <script src="/assets/stage-media.js"></script>
    <script src="/assets/stage-youtube.js"></script>
    <div id="stage-container" class="preset-side-by-side"></div>
    <div id="voiceChips"></div>
    <!-- O master de áudio (participantes + apresentações) é reproduzido AQUI, via
         PCM_AUDIO_SCRIPT no fim desta página. No OBS, marque "Controlar áudio via
         OBS" nesta fonte e desligue "Shutdown source when not visible", senão o
         som some quando o palco não estiver visível na cena. -->

    <script>
        const container = document.getElementById('stage-container');
        let currentStageSignature = '';
        let stageBusy = false, lastSlideRefresh = 0;
        const useWebRTC = {json.dumps(state.webrtc_only)};
        const stageMedia = useWebRTC ? new RnpStageMedia(container) : null;
        let ytHost = null, ytPlayer = null, ytApiLoading = false, stageYt = null;
        let camFmt = 'original';
        window.onYouTubeIframeAPIReady = () => {{ window.__ytReady = true; }};
        function loadYtApi() {{
            if (ytApiLoading || window.__ytReady) return;
            ytApiLoading = true;
            const s = document.createElement('script'); s.src = 'https://www.youtube.com/iframe_api';
            document.head.appendChild(s);
        }}
        function ensureYt() {{
            if (!ytHost) {{ ytHost = document.createElement('div'); ytHost.id = 'ytStageHost'; ytHost.style.cssText = 'width:100%;height:100%;background:#000;'; }}
            if (!window.RnpYouTube) return;
            loadYtApi();
            if (!ytPlayer && window.__ytReady && window.YT && window.YT.Player) ytPlayer = new RnpYouTube(ytHost, {{}});
        }}
        function cameraMarkup(id) {{
            if (useWebRTC) return `<video id="stage_cam_${{id}}" data-source-id="${{id}}" autoplay muted playsinline></video>`;
            return `<img id="stage_cam_${{id}}" src="/raw_frame/camera/${{encodeURIComponent(id)}}">`;
        }}
        const urlOverridePreset = "{preset or ''}";
        const urlOverrideLibrasSize = "{libras_size or ''}";
        const urlOverrideCamFmt = "{camfmt or ''}";

        async function updateStage() {{
            if (stageBusy) return;
            stageBusy = true;
            stageMedia?.refresh();
            try {{
                const res = await fetch('/api/status?t=' + Date.now(), {{signal: AbortSignal.timeout(5000)}});
                if (!res.ok) return;
                const data = await res.json();
                stageYt = data.youtube || null;
                camFmt = urlOverrideCamFmt || data.camera_format || 'original';
                const screenVideo = !!stageMedia?.sources.has('presentation');
                const yt = stageYt;
                const ytActive = !!(useWebRTC && yt && yt.present && yt.video_id);
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
                    screenVideo,
                    hasPresentation: !!data.has_presentation,
                    ytActive,
                    ytVideoId: yt ? yt.video_id : '',
                    camFmt,
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
                    if (screenVideo) {{
                        slideFrame.innerHTML = '<video id="stage_slide_video" data-source-id="presentation" autoplay muted playsinline></video>';
                    }} else if (ytActive) {{
                        ensureYt();
                        slideFrame.appendChild(ytHost);
                    }} else if (!useWebRTC || data.has_presentation) {{
                        slideFrame.innerHTML = '<img id="stage_slide_img" src="/raw_frame/presentation">';
                    }}

                    // Identificar Câmeras de Libras e Outras Câmeras Reais
                    const librasCams = cams.filter(c => c.is_libras || librasIds.includes(String(c.id)));
                    let speakerCams = cams.filter(c => !c.is_libras && !librasIds.includes(String(c.id)));

                    if (pinnedIds.length > 0) {{
                        speakerCams = speakerCams.filter(c => pinnedIds.includes(String(c.id)));
                    }}

                    // PRESETS SEM SLIDE: sem apresentação, as câmeras preenchem o
                    // quadro inteiro. camera-libras = principal + Libras (50/50); grid-libras
                    // = todas as câmeras + Libras; cameras-only = todas as câmeras com a
                    // Libras como janela flutuante. Cada palco é flex:1, dividindo a largura.
                    if ((activePreset === 'cameras-only' || activePreset === 'camera-libras' || activePreset === 'grid-libras')
                            && (speakerCams.length > 0 || librasCams.length > 0)) {{
                        container.className = 'preset-' + activePreset;
                        const camsCont = document.createElement('div');
                        camsCont.className = 'cameras-row';
                        const box = (c, isLibras) => {{
                            const w = document.createElement('div');
                            w.className = 'stage-cam-wrap' + (isLibras ? ' is-libras-box' : '');
                            w.innerHTML = cameraMarkup(c.id)
                                + (isLibras ? '<div class="libras-badge-overlay">🤟 Libras</div>' : '')
                                + (showNames ? `<div class="name-tag style-${{nameStyle}}">${{c.name}}</div>` : '');
                            return w;
                        }};
                        if (activePreset === 'camera-libras') {{
                            const main = (focusedId ? speakerCams.find(c => String(c.id) === focusedId) : null) || speakerCams[0];
                            const lb = librasCams[0];
                            if (main) camsCont.appendChild(box(main, false));
                            if (lb) camsCont.appendChild(box(lb, true));
                        }} else {{
                            speakerCams.forEach(c => camsCont.appendChild(box(c, false)));
                            if (activePreset === 'grid-libras' && librasCams[0]) camsCont.appendChild(box(librasCams[0], true));
                        }}
                        container.appendChild(camsCont);
                        if (activePreset === 'cameras-only' && librasCams[0]) {{
                            const lb = librasCams[0];
                            const fl = document.createElement('div');
                            fl.className = 'libras-floating-box';
                            fl.style.cssText = 'width:380px;height:214px;bottom:24px;right:24px;';
                            fl.innerHTML = cameraMarkup(lb.id) + '<div class="libras-badge-overlay">🤟 Libras</div>';
                            container.appendChild(fl);
                        }}
                        return;
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
                        wrapSpeaker.style.flex = '0 0 calc(50% - 4px)';
                        wrapSpeaker.innerHTML = `
                            ${{cameraMarkup(mainSpeaker.id)}}
                            ${{showNames ? `<div class="name-tag style-${{nameStyle}}">${{mainSpeaker.name}}</div>` : ''}}
                        `;
                        camsCont.appendChild(wrapSpeaker);

                        // 2. Box do Intérprete de Libras (Exatamente do mesmo tamanho)
                        const wrapLibras = document.createElement('div');
                        wrapLibras.className = 'stage-cam-wrap is-libras-box';
                        wrapLibras.style.flex = '0 0 calc(50% - 4px)';
                        wrapLibras.innerHTML = `
                            ${{cameraMarkup(activeLibras.id)}}
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
                                    ${{cameraMarkup(c.id)}}
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
                                ${{cameraMarkup(activeLibras.id)}}
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
                                ${{cameraMarkup(activeLibras.id)}}
                                <div class="libras-badge-overlay">🤟 Libras</div>
                                ${{showNames ? `<div class="name-tag style-${{nameStyle}}" style="font-size:11px; padding:3px 8px; bottom:6px; left:6px;">${{activeLibras.name}}</div>` : ''}}
                            `;
                            container.appendChild(librasFloating);
                        }}
                    }}

                }}

                // Atualizar frames ao vivo diretamente da memória
                const slideImg = document.getElementById('stage_slide_img');
                if (slideImg && (!useWebRTC || Date.now() - lastSlideRefresh >= 1000)) {{
                    slideImg.src = '/raw_frame/presentation?t=' + Date.now();
                    lastSlideRefresh = Date.now();
                }}

                // Sinaliza quem fala na name-tag existente (sem duplicar identificação).
                cams.forEach(c => {{
                    const cid = String(c.id);
                    const media = container.querySelector(`video[data-source-id="${{cid}}"]`) || document.getElementById('stage_cam_' + cid);
                    const wrap = media ? media.closest('.stage-cam-wrap') : null;
                    const tag = wrap ? wrap.querySelector('.name-tag') : null;
                    if (tag) tag.classList.toggle('is-speaking', !!c.is_speaking);
                }});

                // Chips de voz para quem fala SEM câmera (topo central, sempre visível).
                const chipBox = document.getElementById('voiceChips');
                if (chipBox) {{
                    const names = (data.voice_speakers || []).map(v => v.name || 'Participante');
                    const chipSig = names.join('|');
                    if (chipBox.dataset.sig !== chipSig) {{
                        chipBox.dataset.sig = chipSig;
                        chipBox.innerHTML = '';
                        names.forEach(n => {{
                            const d = document.createElement('div');
                            d.className = 'voice-chip';
                            d.textContent = n;
                            chipBox.appendChild(d);
                        }});
                    }}
                }}

                if (!useWebRTC) cams.forEach(c => {{
                    const img = document.getElementById(`stage_cam_${{c.id}}`);
                    if (img) img.src = `/raw_frame/camera/${{encodeURIComponent(c.id)}}?t=` + Date.now();
                }});
            }} catch (e) {{}}
            finally {{
                if (useWebRTC) {{
                    const y = stageYt;
                    const active = !!(y && y.present && y.video_id);
                    if (active) {{
                        ensureYt();
                        if (ytPlayer) {{ ytPlayer.apply(y); const sf = document.querySelector('.slide-frame'); if (sf && ytHost && !sf.contains(ytHost)) sf.appendChild(ytHost); }}
                    }} else if (ytPlayer) {{
                        ytPlayer.apply(null);
                    }}
                }}
                container.querySelectorAll('.stage-cam-wrap').forEach(w => {{
                    w.dataset.camfmt = camFmt;
                    const m = w.querySelector('video, img');
                    const nw = m && (m.videoWidth || m.naturalWidth || 0);
                    const nh = m && (m.videoHeight || m.naturalHeight || 0);
                    w.style.aspectRatio = (camFmt === 'original' && nw && nh) ? (nw + ' / ' + nh) : '';
                }});
                stageMedia?.reconcile();
                stageBusy = false;
            }}
        }}

        setInterval(updateStage, useWebRTC ? 250 : 66);
        updateStage();
    </script>
    {PCM_AUDIO_SCRIPT}
</body>
</html>
""")

# --------------------------------------------------------------------------
# OBS OVERLAY: JANELA DEDICADA DE LIBRAS (/libras)
# --------------------------------------------------------------------------
@app.get("/libras")
async def get_libras_overlay(cam_id: Optional[str] = None, camfmt: Optional[str] = None):
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

        img, .libras-card video {{
            width: 100% !important;
            height: 100% !important;
            object-fit: cover !important;
        }}

        .stage-media-status {{ position: absolute; inset: 40% 8% auto; color: #cbd5e1; font: 14px sans-serif; text-align: center; pointer-events: none; }}

        {NAME_TAG_CSS}
        {CAM_FORMAT_CSS}
        {SPEAKING_CSS}
    </style>
</head>
<body>
    <div class="libras-card">
        {'' if state.webrtc_only else '<img id="librasFeed" src="/raw_frame/camera/1">'}
        <div class="libras-badge-overlay" style="font-size: 13px; padding: 4px 10px;">🤟 Intérprete de Libras</div>
        <div id="librasNameTag" class="name-tag style-glass" style="display: none;"></div>
    </div>
    <!-- Áudio desativado no /libras de propósito: quem reproduz o master é o /stage.
         Ligar aqui também causaria eco/duplicação no OBS Studio. -->

    <script src="/assets/stage-media.js"></script>
    <script>
        const card = document.querySelector('.libras-card');
        const img = document.getElementById('librasFeed');
        const nameTag = document.getElementById('librasNameTag');
        const urlOverrideCam = "{cam_id or ''}";
        const urlOverrideCamFmt = "{camfmt or ''}";
        const useWebRTC = {json.dumps(state.webrtc_only)};
        const librasMedia = useWebRTC ? new RnpStageMedia(card) : null;
        let currentTargetCamId = urlOverrideCam || '';
        let librasBusy = false;

        async function updateLibras() {{
            if (librasBusy) return;
            librasBusy = true;
            if (librasMedia) librasMedia.refresh();
            try {{
                const res = await fetch('/api/status?t=' + Date.now(), {{signal: AbortSignal.timeout(5000)}});
                const data = await res.json();
                const cams = data.cameras || [];
                const librasIds = (data.libras_camera_ids || []).map(String);
                const showNames = data.show_camera_names;
                const nameStyle = data.camera_name_style || 'glass';
                const camFmt = urlOverrideCamFmt || data.camera_format || 'original';

                let targetCam = null;
                if (urlOverrideCam) {{
                    targetCam = cams.find(c => String(c.id) === urlOverrideCam);
                }} else if (librasIds.length > 0) {{
                    targetCam = cams.find(c => c.is_libras || librasIds.includes(String(c.id)));
                }} else {{
                    targetCam = cams.find(c => c.is_libras);
                }}

                if (!targetCam) {{
                    card.style.display = 'none';
                    nameTag.style.display = 'none';
                    if (useWebRTC) {{ const v = card.querySelector('video[data-source-id]'); if (v) v.remove(); }}
                    else if (img) img.style.display = 'none';
                    return;
                }}
                card.style.display = 'flex';
                card.dataset.camfmt = camFmt;
                const media = card.querySelector('video, img');
                const nw = media && (media.videoWidth || media.naturalWidth || 0);
                const nh = media && (media.videoHeight || media.naturalHeight || 0);
                card.style.aspectRatio = (camFmt === 'original' && nw && nh) ? (nw + ' / ' + nh) : '';
                currentTargetCamId = String(targetCam.id);

                if (useWebRTC) {{
                    let video = card.querySelector('video[data-source-id]');
                    if (!video) {{
                        video = document.createElement('video');
                        video.autoplay = true; video.muted = true;
                        video.setAttribute('playsinline', '');
                        card.insertBefore(video, card.firstChild);
                    }}
                    if (video.dataset.sourceId !== String(targetCam.id)) video.dataset.sourceId = String(targetCam.id);
                }} else if (img) {{
                    img.style.display = '';
                    img.src = '/raw_frame/camera/' + targetCam.id + '?t=' + Date.now();
                }}

                if (showNames && targetCam.name) {{
                    nameTag.className = 'name-tag style-' + nameStyle;
                    nameTag.innerText = targetCam.name;
                    nameTag.style.display = 'flex';
                }} else {{
                    nameTag.style.display = 'none';
                }}
                nameTag.classList.toggle('is-speaking', !!targetCam.is_speaking);
            }} catch (e) {{
            }} finally {{
                if (librasMedia) librasMedia.reconcile();
                librasBusy = false;
            }}
        }}

        setInterval(updateLibras, useWebRTC ? 250 : 66);
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
    style: Optional[str] = None,
    camfmt: Optional[str] = None
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

        .cam-card img, .cam-card video {{
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

        .stage-media-status {{
            position: absolute; inset: 40% 8% auto; width: 84%;
            color: #cbd5e1; font: 14px sans-serif; text-align: center; pointer-events: none;
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
        {CAM_FORMAT_CSS}
        {SPEAKING_CSS}
    </style>
</head>
<body>
    <div class="grid-container" id="gridContainer"></div>

    <script src="/assets/stage-media.js"></script>
    <script>
        const container = document.getElementById('gridContainer');
        const overrideLayout = "{layout or ''}";
        const overrideNames = {names if names is not None else 'null'};
        const overrideStyle = "{style or ''}";
        const overrideCamFmt = "{camfmt or ''}";
        const useWebRTC = {json.dumps(state.webrtc_only)};
        let currentSig = '';
        let gridBusy = false;
        const gridMedia = useWebRTC ? new RnpStageMedia(container) : null;

        function gridLayout(n, mode) {{
            if (mode === 'row') return [n, 1];
            if (mode === 'column') return [1, n];
            if (mode === 'grid') {{ const cols = Math.ceil(Math.sqrt(n)); return [cols, Math.ceil(n / cols)]; }}
            if (n === 1) return [1, 1];
            if (n === 2) return [2, 1];
            if (n <= 4) return [2, 2];
            if (n <= 6) return [3, 2];
            if (n <= 9) return [3, 3];
            return [4, Math.ceil(n / 4)];
        }}

        async function updateGrid() {{
            if (gridBusy) return;
            gridBusy = true;
            if (gridMedia) gridMedia.refresh();
            try {{
                const res = await fetch('/api/status?t=' + Date.now(), {{signal: AbortSignal.timeout(5000)}});
                const data = await res.json();
                const cams = data.cameras || [];
                const showNames = (overrideNames !== null) ? !!overrideNames : (data.show_camera_names !== undefined ? data.show_camera_names : true);
                const nameStyle = overrideStyle || data.camera_name_style || 'glass';
                const layoutMode = overrideLayout || data.grid_layout_mode || 'auto';
                const camFmt = overrideCamFmt || data.camera_format || 'original';
                const focusedId = data.focused_camera_id;
                const librasIds = (data.libras_camera_ids || []).map(String);

                if (cams.length === 0) {{
                    if (currentSig !== 'empty') {{
                        currentSig = 'empty';
                        container.innerHTML = '';
                    }}
                    return;
                }}

                const sig = cams.map(c => c.id + '_' + (c.name || '') + '_' + (librasIds.includes(String(c.id)) ? '1' : '0')).join(',') + '::' + showNames + '::' + nameStyle + '::' + layoutMode + '::' + focusedId + '::' + camFmt;

                if (sig !== currentSig) {{
                    currentSig = sig;
                    const dims = gridLayout(cams.length, layoutMode);
                    container.style.gridTemplateColumns = `repeat(${{dims[0]}}, 1fr)`;
                    container.style.gridTemplateRows = `repeat(${{dims[1]}}, 1fr)`;
                    container.innerHTML = '';

                    cams.forEach(cam => {{
                        const isLibras = librasIds.includes(String(cam.id));
                        const isFocused = (focusedId && String(cam.id) === String(focusedId));

                        const card = document.createElement('div');
                        card.className = 'cam-card' + (isLibras ? ' is-libras' : '') + (isFocused ? ' is-focused' : '');
                        card.id = 'camCard_' + cam.id;
                        card.dataset.camfmt = camFmt;

                        if (useWebRTC) {{
                            const video = document.createElement('video');
                            video.dataset.sourceId = cam.id;
                            video.autoplay = true; video.muted = true;
                            video.setAttribute('playsinline', '');
                            card.appendChild(video);
                        }} else {{
                            const img = document.createElement('img');
                            img.id = 'camImg_' + cam.id;
                            img.src = '/raw_frame/camera/' + cam.id;
                            img.onerror = () => {{
                                setTimeout(() => {{ if (img) img.src = '/raw_frame/camera/' + cam.id + '?t=' + Date.now(); }}, 1500);
                            }};
                            card.appendChild(img);
                        }}

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

                if (!useWebRTC) {{
                    cams.forEach(cam => {{
                        const img = document.getElementById('camImg_' + cam.id);
                        if (img) img.src = '/raw_frame/camera/' + cam.id + '?t=' + Date.now();
                    }});
                }}

                container.querySelectorAll('.cam-card').forEach(c => {{
                    c.dataset.camfmt = camFmt;
                    const m = c.querySelector('video, img');
                    const nw = m && (m.videoWidth || m.naturalWidth || 0);
                    const nh = m && (m.videoHeight || m.naturalHeight || 0);
                    c.style.aspectRatio = (camFmt === 'original' && nw && nh) ? (nw + ' / ' + nh) : '';
                }});

                // Sinaliza quem fala na name-tag existente (sem duplicar identificação).
                cams.forEach(c => {{
                    const tag = document.querySelector('#camCard_' + c.id + ' .name-tag');
                    if (tag) tag.classList.toggle('is-speaking', !!c.is_speaking);
                }});
            }} catch (e) {{
            }} finally {{
                if (gridMedia) gridMedia.reconcile();
                gridBusy = false;
            }}
        }}

        setInterval(updateGrid, useWebRTC ? 250 : 66);
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
# ÁUDIO: apenas o alias para o receptor WebRTC individual.
# O master (participantes + apresentações) é reproduzido pelo /stage via
# PCM_AUDIO_SCRIPT. Não existe mais um segundo player PCM aqui: dois masters
# ligados ao mesmo tempo duplicavam o consumo no OBS e causavam eco.
# Os links individuais ficam no rack de áudio do painel (/control).
# --------------------------------------------------------------------------
@app.get("/audio")
async def get_audio_receiver(source: Optional[str] = None, vol: Optional[float] = 1.0):
    if not source:
        raise HTTPException(404, 'O master de áudio agora vem do /stage; use os links individuais do painel.')
    query = urllib.parse.urlencode({'source': source, 'kind': 'audio', 'volume': vol, 'overlay': '1'})
    return RedirectResponse('/webrtc?' + query)


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


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get('RNP_PORT', '3333')), log_level="warning")

