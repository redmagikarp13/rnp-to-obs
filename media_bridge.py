"""Sinalização local do protótipo WebRTC; Python não transporta os pixels."""
import asyncio
import logging
import time
import urllib.parse
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

WEB_DIR = Path(__file__).resolve().parent / 'web'
MEDIA_SOURCE_JS = '\n'.join((WEB_DIR / name).read_text(encoding='utf-8')
                            for name in ('connection-quality.js', 'media-source.js'))

# Timeout dos evaluate() no frame da sala. O BBB periodicamente trava a main thread
# (render pesado, troca de layout); como o Playwright serializa os evaluate() por
# frame, uma trava vira fila. Sem timeout, /api/media chegou a pendurar 26 s — e
# como o dock/stage/webrtc consultam a cada 0,5-1 s, os pedidos se acumulavam numa
# bola de neve (metadados congelam e "somem/voltam"). Falhar rápido (503) evita isso.
_MEDIA_EVAL_TIMEOUT = 6.0
# Single-flight: um lock + cache curto faz todos os consumidores paralelos de
# /api/media compartilharem UMA única chamada de evaluate() em vez de N.
_MEDIA_CACHE_TTL = 0.25
_MEDIA_CACHE = {'ts': 0.0, 'data': None}
_MEDIA_LOCK = None


class Offer(BaseModel):
    source_id: str = Field(min_length=1, max_length=200)
    kind: Literal['video', 'audio']
    type: Literal['offer']
    sdp: str = Field(min_length=5, max_length=100_000)


class AudioControl(BaseModel):
    source_id: str = Field(min_length=1, max_length=200)
    volume: float = Field(default=1, ge=0, le=2, allow_inf_nan=False)
    muted: bool = False


def require_same_origin(request: Request):
    origin = request.headers.get('origin')
    if not origin:
        return
    base = str(request.base_url).rstrip('/')
    if origin == base:
        return
    try:
        parsed_orig = urllib.parse.urlparse(origin)
        parsed_base = urllib.parse.urlparse(base)
        orig_port = parsed_orig.port or (443 if parsed_orig.scheme == 'https' else 80)
        base_port = parsed_base.port or (443 if parsed_base.scheme == 'https' else 80)
        if orig_port == base_port:
            local_hosts = {'localhost', '127.0.0.1', '::1', '0.0.0.0', 'testclient', 'testserver'}
            orig_h = (parsed_orig.hostname or '').lower()
            base_h = (parsed_base.hostname or '').lower()
            if orig_h in local_hosts and base_h in local_hosts:
                return
            if orig_h == base_h:
                return
    except Exception:
        pass
    raise HTTPException(403, 'A sinalização só aceita a origem local do painel.')


async def ensure_media_adapter(frame):
    if not await asyncio.wait_for(frame.evaluate('() => window.RnpMedia?.version === 1'), timeout=_MEDIA_EVAL_TIMEOUT):
        await asyncio.wait_for(frame.evaluate(MEDIA_SOURCE_JS), timeout=_MEDIA_EVAL_TIMEOUT)


def register_media_routes(app, get_state, get_frame):
    async def active_frame():
        state = get_state()
        if not state.connected or not state.page:
            raise HTTPException(409, 'Conecte a sala no painel deste servidor de teste.')
        frame = get_frame(state.page)
        await ensure_media_adapter(frame)
        return frame

    @app.get('/webrtc')
    async def receiver():
        return FileResponse(WEB_DIR / 'webrtc.html', media_type='text/html', headers={'Cache-Control': 'no-store'})

    @app.get('/assets/webrtc-receiver.js')
    async def receiver_script():
        return FileResponse(WEB_DIR / 'webrtc-receiver.js', media_type='text/javascript', headers={'Cache-Control': 'no-store'})

    @app.get('/assets/stage-media.js')
    async def stage_script():
        return FileResponse(WEB_DIR / 'stage-media.js', media_type='text/javascript', headers={'Cache-Control': 'no-store'})

    @app.get('/assets/stage-youtube.js')
    async def stage_youtube_script():
        return FileResponse(WEB_DIR / 'stage-youtube.js', media_type='text/javascript', headers={'Cache-Control': 'no-store'})

    @app.get('/assets/dock.css')
    async def dock_style():
        return FileResponse(WEB_DIR / 'dock.css', media_type='text/css', headers={'Cache-Control': 'no-store'})

    @app.get('/assets/dock.js')
    async def dock_script():
        return FileResponse(WEB_DIR / 'dock.js', media_type='text/javascript', headers={'Cache-Control': 'no-store'})

    @app.get('/assets/ifes-logo.png')
    async def ifes_logo():
        return FileResponse(WEB_DIR / 'ifes-logo.png', media_type='image/png', headers={'Cache-Control': 'no-store'})

    @app.get('/assets/audio-mixer.js')
    async def mixer_script():
        return FileResponse(WEB_DIR / 'audio-mixer.js', media_type='text/javascript', headers={'Cache-Control': 'no-store'})

    @app.get('/api/media')
    async def media_sources():
        state = get_state()
        if not state.connected or not state.page:
            return {'sources': [], 'audio_individual_available': False, 'experimental': True}
        # Cache curto (single-flight): se uma consulta acabou de responder, reusa.
        cached = _MEDIA_CACHE['data']
        if cached is not None and time.monotonic() - _MEDIA_CACHE['ts'] < _MEDIA_CACHE_TTL:
            return cached
        global _MEDIA_LOCK
        if _MEDIA_LOCK is None:
            _MEDIA_LOCK = asyncio.Lock()
        async with _MEDIA_LOCK:
            # Re-checa após adquirir o lock: os pedidos que esperaram compartilham
            # o evaluate() de quem entrou primeiro (coalescência).
            cached = _MEDIA_CACHE['data']
            if cached is not None and time.monotonic() - _MEDIA_CACHE['ts'] < _MEDIA_CACHE_TTL:
                return cached
            try:
                frame = await active_frame()
                result = await asyncio.wait_for(
                    frame.evaluate('() => window.RnpMedia.describe()'),
                    timeout=_MEDIA_EVAL_TIMEOUT)
                for source in result['sources']:
                    if source['kind'] == 'audio':
                        source.update(getattr(state, 'audio_controls', {}).get(source['id'], {}))
                payload = {**result, 'experimental': True}
                _MEDIA_CACHE['ts'] = time.monotonic()
                _MEDIA_CACHE['data'] = payload
                return payload
            except HTTPException:
                raise
            except Exception:
                raise HTTPException(503, 'A mídia da sala está sendo atualizada. Tente novamente.')

    @app.post('/api/webrtc/offer')
    async def offer(body: Offer, request: Request):
        require_same_origin(request)
        frame = await active_frame()
        session_id = uuid4().hex
        try:
            result = await asyncio.wait_for(frame.evaluate(
                '(offer) => window.RnpMedia.answer(offer)',
                {**body.model_dump(), 'session_id': session_id}), timeout=12)
            return {**result, 'session_id': session_id}
        except Exception as error:
            cause = str(error).replace(body.sdp, '[SDP omitido]').splitlines()
            reason = cause[0].strip() if cause and cause[0].strip() else type(error).__name__
            logging.getLogger(__name__).warning('Falha na negociação WebRTC: %s', reason)
            try:
                await frame.evaluate('(id) => window.RnpMedia?.close(id)', session_id)
            except Exception:
                pass
            raise HTTPException(409, reason if 'Error:' not in reason else reason.split('Error:', 1)[1].strip())

    @app.post('/api/webrtc/close/{session_id}')
    async def close(session_id: str, request: Request):
        require_same_origin(request)
        state = get_state()
        if state.connected and state.page:
            try:
                await get_frame(state.page).evaluate('(id) => window.RnpMedia?.close(id)', session_id)
            except Exception:
                pass
        return {'closed': True}

    @app.post('/api/audio/control')
    async def audio_control(body: AudioControl, request: Request):
        require_same_origin(request)
        frame = await active_frame()
        sources = await asyncio.wait_for(frame.evaluate('() => window.RnpMedia.audioInfo()'), timeout=_MEDIA_EVAL_TIMEOUT)
        if body.source_id not in {s['id'] for s in sources}:
            raise HTTPException(404, 'Trilha individual indisponível; o master não será usado no lugar dela.')
        state = get_state()
        state.audio_controls[body.source_id] = {'volume': body.volume, 'muted': body.muted}
        await asyncio.wait_for(frame.evaluate('(controls) => { window._rnpAudioControls = controls; window._rnpApplyAudioControls?.(); }', state.audio_controls), timeout=_MEDIA_EVAL_TIMEOUT)
        return {'status': 'ok', **state.audio_controls[body.source_id]}
