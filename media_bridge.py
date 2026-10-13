"""Sinalização local do protótipo WebRTC; Python não transporta os pixels."""
import asyncio
import logging
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

WEB_DIR = Path(__file__).resolve().parent / 'web'
MEDIA_SOURCE_JS = (WEB_DIR / 'media-source.js').read_text(encoding='utf-8')


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
    if origin and origin != str(request.base_url).rstrip('/'):
        raise HTTPException(403, 'A sinalização só aceita a origem local do painel.')


async def ensure_media_adapter(frame):
    if not await frame.evaluate('() => window.RnpMedia?.version === 1'):
        await frame.evaluate(MEDIA_SOURCE_JS)


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

    @app.get('/assets/audio-mixer.js')
    async def mixer_script():
        return FileResponse(WEB_DIR / 'audio-mixer.js', media_type='text/javascript', headers={'Cache-Control': 'no-store'})

    @app.get('/api/media')
    async def media_sources():
        state = get_state()
        if not state.connected or not state.page:
            return {'sources': [], 'audio_individual_available': False, 'experimental': True}
        try:
            frame = await active_frame()
            result = await frame.evaluate('() => window.RnpMedia.describe()')
            for source in result['sources']:
                if source['kind'] == 'audio':
                    source.update(getattr(state, 'audio_controls', {}).get(source['id'], {}))
            return {**result, 'experimental': True}
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
        sources = await frame.evaluate('() => window.RnpMedia.audioInfo()')
        if body.source_id not in {s['id'] for s in sources}:
            raise HTTPException(404, 'Trilha individual indisponível; o master não será usado no lugar dela.')
        state = get_state()
        state.audio_controls[body.source_id] = {'volume': body.volume, 'muted': body.muted}
        await frame.evaluate('(controls) => { window._rnpAudioControls = controls; window._rnpApplyAudioControls?.(); }', state.audio_controls)
        return {'status': 'ok', **state.audio_controls[body.source_id]}
