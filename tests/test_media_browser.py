"""Integração local com mídia sintética; nunca entra em salas reais."""
import asyncio
import io
import os
import socket
import struct
import json
import tempfile
import time
import unittest
from unittest.mock import patch

import httpx
import uvicorn
from PIL import Image
from websockets import connect as connect_websocket
from playwright.async_api import async_playwright

import server
from media_bridge import MEDIA_SOURCE_JS


FIXTURE = """() => {
    const ctx = new AudioContext({sampleRate: 48000});
    window.fixtureTracks = [];
    window.fixtureAudioContext = ctx;
    window.fixtureOscs = {};
    for (const [id, frequency] of [['w_a', 440], ['w_b', 880]]) {
        const container = document.createElement('div');
        container.dataset.test = 'webcamVideoItem';
        container.dataset.userId = id;
        container.dataset.userName = id === 'w_a' ? 'Intérprete Teste' : 'Participante Teste';
        const canvas = document.createElement('canvas');
        canvas.width = 320; canvas.height = 180;
        const paint = canvas.getContext('2d');
        let n = 0;
        setInterval(() => {paint.fillStyle = n++ % 2 ? '#f00' : '#00f'; paint.fillRect(0, 0, 320, 180);}, 40);
        const video = document.createElement('video');
        video.autoplay = true; video.muted = true;
        video.srcObject = canvas.captureStream(25);
        container.appendChild(video); document.body.appendChild(container);
        window.fixtureTracks.push(video.srcObject.getVideoTracks()[0]);
        const osc = ctx.createOscillator(); osc.frequency.value = frequency;
        window.fixtureOscs[id] = osc;
        const dest = ctx.createMediaStreamDestination(); osc.connect(dest); osc.start();
        const audio = document.createElement('audio');
        audio.dataset.userId = id; audio.dataset.userName = container.dataset.userName;
        audio.srcObject = dest.stream; audio.muted = true;
        document.body.appendChild(audio);
    }
    const anonymous = document.createElement('audio');
    anonymous.id = 'remote-media';
    anonymous.srcObject = ctx.createMediaStreamDestination().stream;
    document.body.appendChild(anonymous);
}"""


class BrowserMediaTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Isola a persistência: rotas como /api/grid/config chamam save_disk_config
        # e, sem isto, sobrescreveriam o config.json real (produção) a cada teste.
        self._cfg_dir = tempfile.TemporaryDirectory()
        self.cfg_patch = patch.object(server, 'CONFIG_FILE',
                                      os.path.join(self._cfg_dir.name, 'config.json'))
        self.cfg_patch.start()
        self.state = server.BridgeState()
        self.patch = patch.object(server, 'state', self.state)
        self.patch.start()
        self.sock = socket.socket()
        self.sock.bind(('127.0.0.1', 0))
        port = self.sock.getsockname()[1]
        self.url = f'http://127.0.0.1:{port}'
        self.http = uvicorn.Server(uvicorn.Config(server.app, log_level='error', lifespan='off'))
        self.task = asyncio.create_task(self.http.serve(sockets=[self.sock]))
        while not self.http.started:
            await asyncio.sleep(.01)
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(channel='msedge', headless=True,
            args=['--autoplay-policy=no-user-gesture-required', '--mute-audio'])
        self.producer = await self.browser.new_page()
        await self.producer.set_content('<body></body>')
        await self.producer.evaluate(FIXTURE)
        await self.producer.evaluate(MEDIA_SOURCE_JS)
        self.state.page = self.producer
        self.state.connected = True
        self.receiver = await self.browser.new_page()
        self.offers = []
        self.receiver.on('request', lambda r: self.offers.append(r.post_data_json['source_id'])
            if r.url.endswith('/api/webrtc/offer') else None)

    async def asyncTearDown(self):
        await self.browser.close()
        await self.pw.stop()
        self.http.should_exit = True
        await self.task
        self.patch.stop()
        self.cfg_patch.stop()
        self._cfg_dir.cleanup()

    async def test_sinalizacao_registra_causa_sem_expor_sdp(self):
        async with httpx.AsyncClient() as client:
            with self.assertLogs('media_bridge', level='WARNING') as logs:
                response = await client.post(self.url + '/api/webrtc/offer', json={
                    'source_id': 'cam-ausente', 'kind': 'video', 'type': 'offer', 'sdp': 'v=0\r\n'})
        self.assertEqual(response.status_code, 409)
        self.assertIn('Fonte indisponível', '\n'.join(logs.output))
        self.assertNotIn('v=0', '\n'.join(logs.output))

    async def same_participant_cameras(self):
        await self.producer.evaluate("""() => {
            const first = document.querySelector('[data-test=webcamVideoItem][data-user-id=w_a]');
            first.dataset.stream = 'w_a_primary';
            RnpMedia.cameraEntries();
            const second = first.cloneNode(false); second.dataset.stream = 'w_a_secondary';
            const canvas = document.createElement('canvas'); canvas.width = 160; canvas.height = 90;
            const paint = canvas.getContext('2d'); let n = 0;
            setInterval(() => { paint.fillStyle = n++ % 2 ? '#0f0' : '#ff0'; paint.fillRect(0, 0, 160, 90); }, 40);
            const video = document.createElement('video'); video.autoplay = true; video.muted = true;
            video.srcObject = canvas.captureStream(25); second.appendChild(video); document.body.appendChild(second);
            window.secondCameraTrack = video.srcObject.getVideoTracks()[0];
        }""")
        sources = await self.producer.evaluate("() => RnpMedia.describe().sources.filter(s => s.kind === 'video' && s.participant_id === 'w_a')")
        self.assertEqual(len(sources), 2, 'Duas publicações do mesmo participante precisam aparecer separadamente.')
        self.assertEqual(len({s['id'] for s in sources}), 2)
        return sources

    async def test_multicamera_ids_estaveis_apos_reordenar_ou_remover_primaria(self):
        sources = await self.same_participant_cameras()
        original = {s['track_id']: s['id'] for s in sources}
        self.assertEqual(sources[0]['id'], 'cam-w_a')
        await self.producer.evaluate("() => document.body.prepend(document.querySelector('[data-stream=w_a_secondary]'))")
        reordered = await self.producer.evaluate("() => RnpMedia.describe().sources.filter(s => s.kind === 'video' && s.participant_id === 'w_a')")
        self.assertEqual({s['track_id']: s['id'] for s in reordered}, original)
        await self.producer.evaluate("() => document.querySelector('[data-stream=w_a_primary]').remove()")
        remaining = await self.producer.evaluate("() => RnpMedia.describe().sources.filter(s => s.kind === 'video' && s.participant_id === 'w_a')")
        self.assertEqual([s['id'] for s in remaining], [sources[1]['id']])
        self.assertNotEqual(remaining[0]['id'], 'cam-w_a')

    async def test_multicamera_dom_duplicado_nao_cria_fonte_extra(self):
        sources = await self.same_participant_cameras()
        await self.producer.evaluate("""() => {
            const original = document.querySelector('[data-stream=w_a_secondary]');
            const copy = original.cloneNode(true);
            copy.querySelector('video').srcObject = original.querySelector('video').srcObject;
            document.body.appendChild(copy);
        }""")
        current = await self.producer.evaluate("() => RnpMedia.describe().sources.filter(s => s.kind === 'video' && s.participant_id === 'w_a')")
        self.assertEqual([s['id'] for s in current], [s['id'] for s in sources])
        self.assertTrue(all(s['audio_source_id'] == 'audio-w_a' for s in current))
        self.assertEqual(len(await self.producer.evaluate("() => RnpMedia.audioInfo().filter(s => s.participant_id === 'w_a')")), 1)

    async def test_multicamera_remontagem_da_publicacao_preserva_id(self):
        sources = await self.same_participant_cameras()
        await self.producer.evaluate("""() => {
            const old = document.querySelector('[data-stream=w_a_secondary]');
            const replacement = old.cloneNode(true);
            replacement.querySelector('video').srcObject = new MediaStream([secondCameraTrack.clone()]);
            old.replaceWith(replacement);
        }""")
        current = await self.producer.evaluate("() => RnpMedia.describe().sources.filter(s => s.kind === 'video' && s.participant_id === 'w_a')")
        self.assertEqual([s['id'] for s in current], [s['id'] for s in sources])
        self.assertNotEqual(current[1]['track_id'], sources[1]['track_id'])

    async def test_multicamera_stage_reproduz_as_duas_trilhas(self):
        sources = await self.same_participant_cameras()
        self.state.webrtc_only = True
        self.state.libras_auto_detect = False
        self.state.libras_saved_names = []
        self.state.stage_preset = 'side-by-side'
        self.state.reconcile_cameras([{'id': s['id'], 'participant_id': s['participant_id'], 'name': s['name']} for s in sources])
        await self.receiver.goto(self.url + '/stage')
        await self.receiver.wait_for_function("document.querySelectorAll('video[data-source-id^=\"cam-\"]').length === 2")
        await self.receiver.wait_for_function("[...document.querySelectorAll('video[data-source-id^=\"cam-\"]')].every(v => v.getVideoPlaybackQuality().totalVideoFrames > 5)", timeout=25000)
        sizes = await self.receiver.locator('video[data-source-id^="cam-"]').evaluate_all('(videos) => videos.map(v => [v.videoWidth,v.videoHeight])')
        self.assertCountEqual(sizes, [[320, 180], [160, 90]])
        self.assertCountEqual(self.offers, [s['id'] for s in sources])

    async def _real_video_offer(self, source_id):
        sdp = await self.receiver.evaluate("""() => {
            const pc = new RTCPeerConnection({iceServers: []});
            pc.addTransceiver('video', {direction: 'recvonly'});
            return pc.createOffer().then(o => pc.setLocalDescription(o)).then(() => pc.localDescription.sdp);
        }""")
        async with httpx.AsyncClient() as client:
            return await client.post(self.url + '/api/webrtc/offer',
                json={'source_id': source_id, 'kind': 'video', 'type': 'offer', 'sdp': sdp})

    async def test_limite_de_receptores_e_configuravel(self):
        await self.producer.evaluate("window.RNP_MAX_PEERS = 1")
        first = await self._real_video_offer('cam-w_a')
        self.assertEqual(first.status_code, 200)
        second = await self._real_video_offer('cam-w_b')
        self.assertEqual(second.status_code, 409)
        self.assertIn('Limite de 1 receptores', second.json()['detail'])
        await self.producer.evaluate("window.RNP_MAX_PEERS = 8")
        third = await self._real_video_offer('cam-w_b')
        self.assertEqual(third.status_code, 200)

    async def test_captura_estado_youtube_da_sala(self):
        await self.producer.evaluate("""() => {
            window.dispatchEvent(new MessageEvent('message', {data: JSON.stringify({event:'infoDelivery',
                info:{playerState:1, currentTime:12.5, duration:100, playbackRate:1, videoData:{video_id:'abc123'}}, channel:'widget', id:1})}));
        }""")
        d = await self.producer.evaluate('() => RnpMedia.describe()')
        self.assertEqual(d['youtube']['video_id'], 'abc123')
        self.assertTrue(d['youtube']['playing'])
        self.assertAlmostEqual(d['youtube']['current_time'], 12.5, delta=0.1)
        await self.producer.evaluate("""() => {
            window.dispatchEvent(new MessageEvent('message', {data: JSON.stringify({event:'onStateChange', info:2, channel:'widget', id:1})}));
        }""")
        d2 = await self.producer.evaluate('() => RnpMedia.describe()')
        self.assertFalse(d2['youtube']['playing'])
        self.assertEqual(d2['youtube']['video_id'], 'abc123')

    async def open_grid(self, path='/cameras/grid'):
        self.state.webrtc_only = True
        self.state.libras_auto_detect = False
        self.state.libras_saved_names = []
        self.state.libras_disabled_names = []
        self.state.custom_camera_names = {}
        self.state.grid_layout_mode = 'auto'
        captured = await self.producer.evaluate('() => RnpMedia.cameraEntries().map(e => ({id:e.id, name:e.name}))')
        self.state.reconcile_cameras(captured)
        await self.receiver.goto(self.url + path)
        await self.receiver.wait_for_function("document.querySelectorAll('video[data-source-id^=\"cam-\"]').length === 2")
        await self.receiver.wait_for_function("[...document.querySelectorAll('video[data-source-id^=\"cam-\"]')].every(v => v.getVideoPlaybackQuality().totalVideoFrames > 5)", timeout=25000)

    async def test_grid_webrtc_reproduz_cameras_sem_jpeg(self):
        requests = []
        self.receiver.on('request', lambda r: requests.append(r.url))
        await self.open_grid()
        videos = await self.receiver.locator('video[data-source-id^="cam-"]').evaluate_all(
            '(vs) => vs.map(v => ({width:v.videoWidth, muted:v.muted, audio:v.srcObject.getAudioTracks().length}))')
        self.assertEqual(videos, [{'width': 320, 'muted': True, 'audio': 0}] * 2)
        self.assertFalse(any('/raw_frame/camera/' in url for url in requests))
        self.assertCountEqual(self.offers, ['cam-w_a', 'cam-w_b'])

    async def test_grid_preserva_conexoes_ao_trocar_layout(self):
        await self.open_grid()
        await self.receiver.evaluate("window.savedVideo = document.querySelector('[data-source-id=\"cam-w_a\"]')")
        self.state.grid_layout_mode = 'grid'
        await self.receiver.wait_for_function("document.querySelector('[data-source-id=\"cam-w_a\"]')")
        self.assertTrue(await self.receiver.evaluate("savedVideo === document.querySelector('[data-source-id=\"cam-w_a\"]')"))
        frames = await self.receiver.evaluate('savedVideo.getVideoPlaybackQuality().totalVideoFrames')
        await self.receiver.wait_for_function('(n) => savedVideo.getVideoPlaybackQuality().totalVideoFrames > n + 5', arg=frames, timeout=5000)
        self.assertCountEqual(self.offers, ['cam-w_a', 'cam-w_b'])

    async def test_grid_fecha_peer_ao_remover_camera(self):
        await self.producer.evaluate("() => { const Peer = RTCPeerConnection; window.testPeers = []; window.RTCPeerConnection = class extends Peer { constructor(...a) { super(...a); testPeers.push(this); } }; }")
        await self.open_grid()
        await self.producer.wait_for_function("testPeers.length === 2")
        self.state.reconcile_cameras([{'id': 'cam-w_b', 'name': 'Participante Teste'}])
        await self.receiver.wait_for_function("document.querySelectorAll('video[data-source-id^=\"cam-\"]').length === 1")
        await self.producer.wait_for_function("testPeers.filter(p => p.connectionState !== 'closed').length === 1", timeout=8000)
        self.assertTrue(await self.producer.evaluate("fixtureTracks.every(t => t.readyState === 'live')"))

    async def test_grid_legado_mantem_fontes_jpeg(self):
        self.state.webrtc_only = False
        self.state.cameras_list = [{'id': 'cam-w_b', 'name': 'Participante', 'is_libras': False, 'original_name': 'Participante'}]
        self.state.libras_camera_ids = []
        self.state.pinned_camera_ids = []
        await self.receiver.goto(self.url + '/cameras/grid')
        await self.receiver.locator('#camImg_cam-w_b').wait_for()
        self.assertEqual(await self.receiver.locator('#camImg_cam-w_b').evaluate('el => el.tagName'), 'IMG')
        self.assertEqual(self.offers, [])

    async def test_libras_webrtc_mostra_video_do_interprete(self):
        requests = []
        self.receiver.on('request', lambda r: requests.append(r.url))
        self.state.webrtc_only = True
        self.state.libras_auto_detect = False
        captured = await self.producer.evaluate('() => RnpMedia.cameraEntries().map(e => ({id:e.id, name:e.name}))')
        self.state.reconcile_cameras(captured)
        self.state.set_libras_cameras(['cam-w_a'])
        await self.receiver.goto(self.url + '/libras')
        await self.receiver.wait_for_function("document.querySelector('.libras-card video[data-source-id=\"cam-w_a\"]')")
        await self.receiver.wait_for_function("document.querySelector('.libras-card video').getVideoPlaybackQuality().totalVideoFrames > 5", timeout=25000)
        self.assertTrue(await self.receiver.evaluate("!!document.querySelector('.libras-card video')?.muted"))
        self.assertFalse(any('/raw_frame/camera/' in u for u in requests))
        self.assertEqual(self.offers, ['cam-w_a'])

    async def test_libras_legado_mantem_img(self):
        self.state.webrtc_only = False
        self.state.cameras_list = [{'id': 'cam-w_b', 'name': 'Participante', 'is_libras': True, 'original_name': 'Participante'}]
        self.state.libras_camera_ids = ['cam-w_b']
        await self.receiver.goto(self.url + '/libras')
        await self.receiver.locator('#librasFeed').wait_for()
        self.assertEqual(await self.receiver.evaluate("document.getElementById('librasFeed').tagName"), 'IMG')
        self.assertEqual(self.offers, [])

    async def test_sync_loop_amostra_fps_por_camera(self):
        self.state.webrtc_only = True
        task = asyncio.create_task(server.sync_loop())
        try:
            for _ in range(80):
                cams = self.state.cameras_list
                if len(cams) == 2 and all('fps' in c for c in cams) and any(c['fps'] > 0 for c in cams):
                    break
                await asyncio.sleep(.1)
            cams = self.state.cameras_list
            self.assertEqual(len(cams), 2)
            self.assertTrue(all('fps' in c for c in cams), 'Cada câmera precisa expor fps no /api/status.')
            self.assertGreater(max(c['fps'] for c in cams), 0)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_dock_mostra_medidor_de_fps(self):
        self.state.webrtc_only = False
        self.state.cameras_list = [{'id': 'cam-w_b', 'name': 'Participante', 'width': 320, 'height': 180,
            'fps': 23.5, 'is_libras': False, 'original_name': 'Participante'}]
        await self.receiver.goto(self.url + '/control')
        text = ''
        # O dock reconstrói o grid a cada poll; lemos de forma atômica via evaluate.
        for _ in range(40):
            text = await self.receiver.evaluate("document.querySelector('.cam-fps')?.textContent || ''")
            if text:
                break
            await asyncio.sleep(.05)
        self.assertIn('FPS', text)
        self.assertIn('24', text)

    async def test_stage_youtube_controlador_play_pause_seek_troca(self):
        await self.receiver.goto(self.url + '/webrtc')
        await self.receiver.add_script_tag(url=self.url + '/assets/stage-youtube.js')
        calls = await self.receiver.evaluate(r"""() => {
            const calls = [];
            function FakePlayer(el, opts){ this.v = opts.videoId; this._t = 0; this._ps = 1; calls.push(['create', this.v]); }
            FakePlayer.prototype.destroy = function(){ calls.push(['destroy', this.v]); };
            FakePlayer.prototype.getCurrentTime = function(){ return this._t; };
            FakePlayer.prototype.getPlayerState = function(){ return this._ps; };
            FakePlayer.prototype.playVideo = function(){ calls.push(['play', this.v]); this._ps = 1; };
            FakePlayer.prototype.pauseVideo = function(){ calls.push(['pause', this.v]); this._ps = 2; };
            FakePlayer.prototype.seekTo = function(t){ calls.push(['seek', this.v, Math.round(t)]); this._t = t; };
            FakePlayer.prototype.setPlaybackRate = function(r){ calls.push(['rate', this.v, r]); };
            const hook = document.createElement('div'); document.body.appendChild(hook);
            const c = new RnpYouTube(hook, {YT: {Player: FakePlayer}, tolerance: 0.5});
            const now = Date.now();
            c.apply({video_id:'a', playing:true,  current_time:5,  duration:100, rate:1, updated_at:now});
            c.player._t = 5; c.player._ps = 1;
            c.apply({video_id:'a', playing:false, current_time:20, duration:100, rate:1, updated_at:now});
            c.player._ps = 2; c.player._t = 80;
            c.apply({video_id:'a', playing:true,  current_time:80.2, duration:100, rate:1, updated_at:now});
            c.apply({video_id:'b', playing:true,  current_time:0, duration:100, rate:1, updated_at:now});
            return calls;
        }""")
        self.assertEqual(calls[0], ['create', 'a'])
        self.assertIn(['pause', 'a'], calls)
        self.assertIn(['seek', 'a', 20], calls)
        self.assertIn(['play', 'a'], calls)
        self.assertNotIn(['seek', 'a', 80], calls)
        self.assertIn(['destroy', 'a'], calls)
        self.assertIn(['create', 'b'], calls)

    async def test_stage_monta_youtube_e_desmonta_ao_parar(self):
        self.state.webrtc_only = True
        self.state.libras_auto_detect = False
        self.state.libras_saved_names = []
        self.state.stage_preset = 'side-by-side'
        captured = await self.producer.evaluate('() => RnpMedia.cameraEntries().map(e => ({id:e.id, name:e.name}))')
        self.state.reconcile_cameras(captured)
        await self.receiver.goto(self.url + '/stage')
        await self.receiver.evaluate("""() => {
            window.__yt = {created: [], disposed: []};
            window.__ytReady = true;
            window.YT = { Player: function(el, opts){
                const vid = opts.videoId; window.__yt.created.push(vid);
                const d = document.createElement('div'); d.setAttribute('data-yt', vid); el.appendChild(d);
                this.destroy = () => { window.__yt.disposed.push(vid); el.innerHTML = ''; };
                this.getCurrentTime = () => 0; this.getPlayerState = () => 1;
                this.playVideo = this.pauseVideo = this.seekTo = this.setPlaybackRate = () => {};
            } };
        }""")
        # Presença do iframe autoriza a montagem; o /stage não depende de frescura da mensagem.
        self.state.youtube = {'video_id': 'ytABCD', 'present': True, 'playing': True, 'current_time': 10,
            'duration': 100, 'rate': 1, 'updated_at': int(time.time() * 1000)}
        await self.receiver.wait_for_function("window.__yt && window.__yt.created.includes('ytABCD')", timeout=40000)
        self.assertTrue(await self.receiver.evaluate("!!document.querySelector('.slide-frame #ytStageHost [data-yt=\"ytABCD\"]')"))
        dims = await self.receiver.evaluate("""() => {
            const sf = document.querySelector('.slide-frame').getBoundingClientRect();
            const y = document.querySelector('.slide-frame #ytStageHost [data-yt="ytABCD"]').getBoundingClientRect();
            return {sf: [sf.width, sf.height], y: [y.width, y.height]};
        }""")
        self.assertGreater(dims['y'][0], 0, 'O player do YouTube deve ocupar o slot de apresentação.')
        self.assertAlmostEqual(dims['y'][0], dims['sf'][0], delta=2)
        self.assertAlmostEqual(dims['y'][1], dims['sf'][1], delta=2)
        # Reconstruir o layout não deve destruir/recriar o player (wrapper estável).
        self.state.stage_preset = 'bottom-bar'
        await self.receiver.wait_for_function("document.querySelector('.preset-bottom-bar')", timeout=10000)
        await asyncio.sleep(1)
        counts = await self.receiver.evaluate("() => ({c: window.__yt.created.length, d: window.__yt.disposed.length})")
        self.assertEqual(counts, {'c': 1, 'd': 0}, 'Trocar o layout não deve recriar nem destruir o player.')
        self.assertTrue(await self.receiver.evaluate("!!document.querySelector('.slide-frame #ytStageHost [data-yt=\"ytABCD\"]')"))
        # Video parou: some o iframe -> o palco desmonta e libera o player.
        self.state.youtube = None
        await self.receiver.wait_for_function("window.__yt.disposed.includes('ytABCD')", timeout=15000)
        await self.receiver.wait_for_function("!document.querySelector('.slide-frame #ytStageHost')", timeout=15000)

    async def test_stage_youtube_seek_somente_quando_compensa(self):
        await self.receiver.goto(self.url + '/webrtc')
        await self.receiver.add_script_tag(url=self.url + '/assets/stage-youtube.js')
        seeks = await self.receiver.evaluate(r"""() => {
            const calls = [];
            function FakePlayer(el, opts){ this.v = opts.videoId; this._t = 0; this._ps = 1; }
            FakePlayer.prototype.destroy=function(){};
            FakePlayer.prototype.getCurrentTime=function(){return this._t;};
            FakePlayer.prototype.getPlayerState=function(){return this._ps;};
            FakePlayer.prototype.playVideo=function(){}; FakePlayer.prototype.pauseVideo=function(){};
            FakePlayer.prototype.seekTo=function(t){calls.push(Math.round(t)); this._t=t;};
            FakePlayer.prototype.setPlaybackRate=function(){};
            const hook=document.createElement('div'); document.body.appendChild(hook);
            const c=new RnpYouTube(hook, {YT:{Player:FakePlayer}});   // usa os padrões conservadores
            const now=Date.now();
            const S=(ct)=>({video_id:'a',playing:true,current_time:ct,duration:100,rate:1,updated_at:now});
            c.apply(S(1.5));                 // cria 'a'
            c.player._t=0; c.apply(S(1.5));  // drift 1.5 < tolerância -> sem seek
            c.player._t=0; c.apply(S(20));   // drift 20 > limite duro -> seek permitido
            c.player._t=20; c.apply(S(23));  // drift 3 (>tolerância, <limite duro) dentro da janela mínima -> NÃO seek
            return calls;
        }""")
        self.assertEqual(seeks, [20], 'Deve buscar só uma vez: drift pequeno não corrige e drift médio dentro da janela mínima não rebusca.')

    async def test_youtube_id_vem_do_iframe_sem_mensagem(self):
        await self.producer.evaluate("""() => {
            const box = document.createElement('div'); box.setAttribute('data-test','videoPlayer');
            const f = document.createElement('iframe'); f.id = 'widget2';
            f.src = 'https://www.youtube-nocookie.com/embed/XYZxyz12?controls=0&enablejsapi=1';
            box.appendChild(f); document.body.appendChild(box);
        }""")
        y = await self.producer.evaluate('() => RnpMedia.describe().youtube')
        self.assertEqual(y['video_id'], 'XYZxyz12', 'O id deve vir do src do iframe, mesmo sem postMessage.')

    async def test_youtube_play_detectado_pelo_avanco_do_tempo(self):
        await self.producer.evaluate("""() => {
            window.dispatchEvent(new MessageEvent('message', {data: JSON.stringify({event:'infoDelivery', info:{currentTime:10, videoData:{video_id:'TT'}}, channel:'widget', id:1})}));
            window.dispatchEvent(new MessageEvent('message', {data: JSON.stringify({event:'infoDelivery', info:{currentTime:12, progressState:{current:12}}, channel:'widget', id:1})}));
        }""")
        y = await self.producer.evaluate('() => RnpMedia.describe().youtube')
        self.assertEqual(y['video_id'], 'TT')
        self.assertTrue(y['playing'], 'Se o currentTime avança, está tocando, mesmo sem playerState.')

    async def test_formato_original_usa_contain_no_palco(self):
        self.state.camera_format = 'original'
        await self.open_stage()
        attr = await self.receiver.evaluate("() => document.querySelector('.stage-cam-wrap').dataset.camfmt")
        fit = await self.receiver.evaluate("() => getComputedStyle(document.querySelector('.stage-cam-wrap video')).objectFit")
        self.assertEqual(attr, 'original')
        self.assertEqual(fit, 'contain')

    async def test_formato_original_frame_acompanha_conteudo(self):
        self.state.camera_format = 'original'
        await self.open_stage()
        info = await self.receiver.evaluate("""() => {
            const w = document.querySelector('.stage-cam-wrap');
            return {bg: getComputedStyle(w).backgroundColor, ratio: w.style.aspectRatio, fmt: w.dataset.camfmt,
                shadow: getComputedStyle(w).boxShadow};
        }""")
        self.assertEqual(info['fmt'], 'original')
        self.assertIn(info['bg'], ('rgba(0, 0, 0, 0)', 'transparent'), 'No formato original a moldura não deve pintar caixa preta.')
        self.assertNotEqual(info['shadow'], 'none', 'O drop shadow deve permanecer contornando o conteúdo.')
        self.assertEqual(info['ratio'].replace(' ', ''), '320/180', 'A moldura deve adotar a proporção real do vídeo.')

    async def test_formato_esticado_sobrepoe_via_url(self):
        self.state.camera_format = 'original'
        await self.open_stage(path='/stage?camfmt=esticado')
        fit = await self.receiver.evaluate("() => getComputedStyle(document.querySelector('.stage-cam-wrap video')).objectFit")
        self.assertEqual(fit, 'fill')

    async def test_grid_aplica_formato_retrato(self):
        self.state.webrtc_only = True
        self.state.libras_auto_detect = False
        self.state.camera_format = 'retrato'
        captured = await self.producer.evaluate('() => RnpMedia.cameraEntries().map(e => ({id:e.id, name:e.name}))')
        self.state.reconcile_cameras(captured)
        await self.receiver.goto(self.url + '/cameras/grid')
        await self.receiver.wait_for_function("document.querySelectorAll('.cam-card[data-camfmt=\"retrato\"]').length === 2")
        fit = await self.receiver.evaluate("() => getComputedStyle(document.querySelector('.cam-card video')).objectFit")
        ratio = await self.receiver.evaluate("() => getComputedStyle(document.querySelector('.cam-card')).aspectRatio")
        self.assertEqual(fit, 'cover')
        self.assertIn('9 / 16', ratio.replace('0.5625', '9 / 16'))

    async def test_grid_aplica_formato_quadrada(self):
        self.state.webrtc_only = True
        self.state.libras_auto_detect = False
        self.state.camera_format = 'quadrada'
        captured = await self.producer.evaluate('() => RnpMedia.cameraEntries().map(e => ({id:e.id, name:e.name}))')
        self.state.reconcile_cameras(captured)
        await self.receiver.goto(self.url + '/cameras/grid')
        await self.receiver.wait_for_function("document.querySelectorAll('.cam-card[data-camfmt=\"quadrada\"]').length === 2")
        fit = await self.receiver.evaluate("() => getComputedStyle(document.querySelector('.cam-card video')).objectFit")
        ratio = await self.receiver.evaluate("() => getComputedStyle(document.querySelector('.cam-card')).aspectRatio")
        self.assertEqual(fit, 'cover')
        self.assertTrue(ratio.startswith('1 / 1') or ratio in ('1', '1/1'), f'aspect-ratio quadrado, obtido: {ratio}')

    async def test_libras_aplica_formato(self):
        self.state.webrtc_only = True
        self.state.libras_auto_detect = False
        self.state.camera_format = 'original'
        captured = await self.producer.evaluate('() => RnpMedia.cameraEntries().map(e => ({id:e.id, name:e.name}))')
        self.state.reconcile_cameras(captured)
        self.state.set_libras_cameras(['cam-w_a'])
        await self.receiver.goto(self.url + '/libras')
        await self.receiver.wait_for_function("document.querySelector('.libras-card video')")
        attr = await self.receiver.evaluate("() => document.querySelector('.libras-card').dataset.camfmt")
        fit = await self.receiver.evaluate("() => getComputedStyle(document.querySelector('.libras-card video')).objectFit")
        self.assertEqual(attr, 'original')
        self.assertEqual(fit, 'contain')

    async def test_palco_sinaliza_fala_no_nome_sem_duplicar(self):
        await self.open_stage()
        for c in self.state.cameras_list:
            c['is_speaking'] = (str(c['id']) == 'cam-w_b')
        await self.receiver.wait_for_function(
            "document.querySelectorAll('.stage-cam-wrap .name-tag.is-speaking').length === 1", timeout=10000)
        owner = await self.receiver.evaluate(
            "() => document.querySelector('.stage-cam-wrap .name-tag.is-speaking').closest('.stage-cam-wrap').querySelector('video').dataset.sourceId")
        self.assertEqual(owner, 'cam-w_b')
        self.assertEqual(await self.receiver.evaluate(
            "() => document.querySelectorAll('.stage-cam-wrap .is-speaking').length"), 1,
            'A sinalização deve ficar só na name-tag, sem segundo elemento.')
        for c in self.state.cameras_list:
            c['is_speaking'] = False
        await self.receiver.wait_for_function(
            "document.querySelectorAll('.stage-cam-wrap .name-tag.is-speaking').length === 0", timeout=10000)

    async def test_grid_sinaliza_fala_no_nome(self):
        self.state.webrtc_only = True
        self.state.libras_auto_detect = False
        self.state.libras_saved_names = []
        captured = await self.producer.evaluate('() => RnpMedia.cameraEntries().map(e => ({id:e.id, name:e.name}))')
        self.state.reconcile_cameras(captured)
        await self.receiver.goto(self.url + '/cameras/grid')
        await self.receiver.wait_for_function("document.querySelectorAll('.cam-card').length === 2")
        for c in self.state.cameras_list:
            c['is_speaking'] = (str(c['id']) == 'cam-w_a')
        await self.receiver.wait_for_function(
            "document.querySelectorAll('.cam-card .name-tag.is-speaking').length === 1", timeout=10000)
        owner = await self.receiver.evaluate(
            "() => document.querySelector('.cam-card .name-tag.is-speaking').closest('.cam-card').id")
        self.assertEqual(owner, 'camCard_cam-w_a')
        for c in self.state.cameras_list:
            c['is_speaking'] = False
        await self.receiver.wait_for_function(
            "document.querySelectorAll('.cam-card .name-tag.is-speaking').length === 0", timeout=10000)

    async def test_fala_detectada_pelo_pico_do_microfone(self):
        await self.producer.evaluate('(url) => RnpMedia.startPcm(url)', self.url.replace('http:', 'ws:') + '/ws/pcm_in')
        await self.producer.wait_for_function("RnpMedia.audioInfo().every(a => a.peak > 0.05)", timeout=10000)
        task = asyncio.create_task(server.sync_loop())
        try:
            for _ in range(100):
                cams = self.state.cameras_list
                if len(cams) == 2 and all(c.get('is_speaking') for c in cams):
                    break
                await asyncio.sleep(.1)
            self.assertTrue(all(c['is_speaking'] for c in self.state.cameras_list),
                            'Microfone com pico deve marcar is_speaking mesmo sem indicador do BBB.')
            await self.producer.evaluate("() => { window._rnpAudioControls = {'audio-w_a': {muted: true}}; window._rnpApplyAudioControls?.(); }")
            for _ in range(100):
                a = [c for c in self.state.cameras_list if c['id'] == 'cam-w_a']
                if a and not a[0]['is_speaking']:
                    break
                await asyncio.sleep(.1)
            self.assertFalse([c for c in self.state.cameras_list if c['id'] == 'cam-w_a'][0]['is_speaking'],
                             'Microfone mudo não pode continuar marcado como falando.')
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_limiar_de_fala_vem_do_estado_nao_da_constante(self):
        # O ganho do microfone muda por sala e por operador; o limiar precisa ser
        # ajustável em runtime. Aqui o tom do fixture tem pico ~1.0, então um limiar
        # acima disso prova que o sync_loop está lendo o estado.
        await self.producer.evaluate('(url) => RnpMedia.startPcm(url)', self.url.replace('http:', 'ws:') + '/ws/pcm_in')
        await self.producer.wait_for_function("RnpMedia.audioInfo().every(a => a.peak > 0.5)", timeout=10000)
        self.state.speak_peak_threshold = 1.5
        task = asyncio.create_task(server.sync_loop())
        try:
            for _ in range(60):
                if len(self.state.cameras_list) == 2:
                    break
                await asyncio.sleep(.1)
            self.assertEqual(len(self.state.cameras_list), 2)
            self.assertFalse(any(c.get('is_speaking') for c in self.state.cameras_list),
                             'Com limiar acima do pico real, ninguém pode ser marcado como falando.')
            self.assertEqual(self.state.voice_speakers, [],
                             'Chip de voz também deve respeitar o limiar configurado.')
            self.state.speak_peak_threshold = 0.001
            for _ in range(60):
                if all(c.get('is_speaking') for c in self.state.cameras_list):
                    break
                await asyncio.sleep(.1)
            self.assertTrue(all(c.get('is_speaking') for c in self.state.cameras_list),
                            'Com limiar baixo, o mesmo pico real deve marcar fala.')
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_pico_de_fala_decai_suavemente_ao_silenciar(self):
        # O pico instantâneo (~42ms) some entre sílabas e faz o indicador piscar.
        # audioInfo() precisa segurar o pico recente e decair suavemente.
        await self.producer.evaluate('(url) => RnpMedia.startPcm(url)', self.url.replace('http:', 'ws:') + '/ws/pcm_in')
        await self.producer.wait_for_function("RnpMedia.audioInfo().every(a => a.peak > 0.05)", timeout=10000)
        read_w_a = "() => RnpMedia.audioInfo().find(a => a.participant_id === 'w_a').peak"
        # Silencia w_a (fim da fala) mantendo a trilha viva.
        await self.producer.evaluate("() => { window._rnpAudioControls = {'audio-w_a': {muted: true}}; window._rnpApplyAudioControls?.(); }")
        await self.producer.evaluate("() => new Promise(r => setTimeout(r, 220))")
        pico_recente = await self.producer.evaluate(read_w_a)
        self.assertGreater(pico_recente, 0.05,
                           '~220ms após parar de falar o pico ainda deve refletir a fala recente (peak-hold), não zerar.')
        # E deve decair abaixo do limiar (não fica travado para sempre).
        await self.producer.wait_for_function("RnpMedia.audioInfo().find(a => a.participant_id === 'w_a').peak < 0.05", timeout=3000)

    async def test_chip_de_voz_sem_camera_no_palco(self):
        await self.producer.evaluate('(url) => RnpMedia.startPcm(url)', self.url.replace('http:', 'ws:') + '/ws/pcm_in')
        await self.producer.wait_for_function("RnpMedia.audioInfo().every(a => a.peak > 0.05)", timeout=10000)
        self.state.webrtc_only = True
        self.state.libras_auto_detect = False
        self.state.libras_saved_names = []
        await self.producer.evaluate("() => document.querySelector('[data-user-id=w_b]').remove()")
        task = asyncio.create_task(server.sync_loop())
        try:
            for _ in range(100):
                if any(v.get('participant_id') == 'w_b' for v in (self.state.voice_speakers or [])):
                    break
                await asyncio.sleep(.1)
            names = [v['name'] for v in (self.state.voice_speakers or [])]
            self.assertIn('Participante Teste', names, 'Voz sem câmera deve virar falante avulso.')
            self.assertNotIn('Intérprete Teste', names, 'Quem tem câmera não deve duplicar como chip de voz.')
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await self.receiver.goto(self.url + '/stage')
        await self.receiver.wait_for_function("document.querySelectorAll('.voice-chip').length === 1", timeout=15000)
        self.assertIn('Participante Teste', await self.receiver.locator('.voice-chip').first.inner_text())

    async def test_chip_de_voz_aparece_sobre_slides_sem_nenhuma_camera(self):
        # "Aparecendo sempre mesmo em cima dos slides": sem nenhuma câmera o palco
        # cai no ramo slide-only, que não pode pular a renderização dos chips de voz.
        self.state.webrtc_only = True
        self.state.libras_auto_detect = False
        self.state.libras_saved_names = []
        self.state.reconcile_cameras([])
        self.state.voice_speakers = [{'id': 'audio-w_b', 'participant_id': 'w_b', 'name': 'Participante Teste'}]
        await self.receiver.goto(self.url + '/stage')
        await self.receiver.wait_for_function(
            "document.querySelectorAll('.voice-chip').length === 1", timeout=15000)
        self.assertIn('Participante Teste', await self.receiver.locator('.voice-chip').first.inner_text())

    async def test_sombra_do_chip_de_voz_sangra_para_fora(self):
        # A sombra é tinta fora da caixa do chip: precisa sangrar para o entorno,
        # não ser recortada no retângulo do contêiner.
        self.state.webrtc_only = True
        self.state.libras_auto_detect = False
        self.state.libras_saved_names = []
        self.state.reconcile_cameras([])
        self.state.voice_speakers = [{'id': 'audio-w_b', 'participant_id': 'w_b', 'name': 'Participante Teste'}]
        await self.receiver.goto(self.url + '/stage')
        await self.receiver.wait_for_function("document.querySelectorAll('.voice-chip').length === 1", timeout=15000)

        pad = 12
        box = await self.receiver.evaluate(
            "() => { const r = document.querySelector('.voice-chip').getBoundingClientRect();"
            " return {x: r.x, y: r.y, w: r.width, h: r.height}; }")
        clip = {'x': box['x'] - pad, 'y': box['y'] - pad,
                'width': box['w'] + 2 * pad, 'height': box['h'] + 2 * pad}
        # Ponto 3px à direita da borda do chip, no meio da altura: só a sombra pinta ali.
        px, py = pad + int(box['w']) + 3, pad + int(box['h']) // 2

        async def alfa_no_ponto():
            shot = await self.receiver.screenshot(omit_background=True, clip=clip)
            return Image.open(io.BytesIO(shot)).convert('RGBA').getpixel((px, py))[3]

        alfa_com_sombra = await alfa_no_ponto()
        await self.receiver.evaluate("() => document.querySelector('.voice-chip').style.boxShadow = 'none'")
        alfa_sem_sombra = await alfa_no_ponto()
        self.assertGreater(alfa_com_sombra, alfa_sem_sombra,
                           'A sombra do chip de voz precisa sangrar para fora do elemento, sem recorte retangular.')

    async def open_stage(self, path='/stage'):
        self.state.webrtc_only = True
        self.state.libras_auto_detect = False
        self.state.libras_saved_names = []
        self.state.libras_disabled_names = []
        self.state.custom_camera_names = {}
        self.state.stage_preset = 'side-by-side'
        self.state.libras_size = 'equal'
        captured = await self.producer.evaluate('() => RnpMedia.cameraEntries().map(e => ({id:e.id, name:e.name}))')
        self.state.reconcile_cameras(captured)
        self.state.set_libras_cameras(['cam-w_a'])
        await self.receiver.goto(self.url + path)
        await self.receiver.wait_for_function("document.querySelector('#stage-container').children.length > 0")
        self.assertEqual(await self.receiver.locator('video[data-source-id^="cam-"]').count(), 2,
                         'O palco precisa receber duas câmeras WebRTC, não imagens JPEG.')
        await self.receiver.wait_for_function("[...document.querySelectorAll('video[data-source-id^=\"cam-\"]')].every(v => v.getVideoPlaybackQuality().totalVideoFrames > 5)", timeout=25000)

    async def test_stage_recebe_video_real_e_mestre_de_audio(self):
        requests, ws_urls = [], []
        self.receiver.on('request', lambda r: requests.append(r.url))
        self.receiver.on('websocket', lambda ws: ws_urls.append(ws.url))
        await self.open_stage()
        videos = await self.receiver.locator('video[data-source-id^="cam-"]').evaluate_all(
            '(videos) => videos.map(v => ({width:v.videoWidth, muted:v.muted, audio:v.srcObject.getAudioTracks().length}))')
        self.assertEqual(videos, [{'width': 320, 'muted': True, 'audio': 0}] * 2)
        self.assertFalse(any('/raw_frame/camera/' in url for url in requests))
        self.assertTrue(any('/ws/pcm_out' in u for u in ws_urls),
                        'O palco precisa reproduzir o master de áudio (participantes + apresentações).')
        self.assertCountEqual(self.offers, ['cam-w_a', 'cam-w_b'])

    async def test_stage_preserva_conexoes_ao_trocar_layout_e_libras(self):
        await self.open_stage()
        await self.receiver.evaluate("window.savedVideo = document.querySelector('[data-source-id=\"cam-w_a\"]')")
        self.state.stage_preset = 'bottom-bar'
        await self.receiver.wait_for_function("document.querySelector('.preset-bottom-bar .cameras-row')")
        boxes = await self.receiver.locator('.stage-cam-wrap').evaluate_all(
            '(els) => els.map(el => ({w:el.getBoundingClientRect().width,h:el.getBoundingClientRect().height}))')
        self.assertAlmostEqual(boxes[0]['w'], boxes[1]['w'], delta=1)
        self.assertAlmostEqual(boxes[0]['h'], boxes[1]['h'], delta=1)
        self.state.libras_size = 'large'
        await self.receiver.wait_for_function("document.querySelector('.libras-floating-box video')")
        self.assertTrue(await self.receiver.evaluate("savedVideo === document.querySelector('.libras-floating-box video')"))
        self.assertCountEqual(self.offers, ['cam-w_a', 'cam-w_b'])
        self.assertEqual(await self.receiver.locator('.libras-floating-box').evaluate('(el) => el.getBoundingClientRect().width'), 480)
        frames = await self.receiver.evaluate('savedVideo.getVideoPlaybackQuality().totalVideoFrames')
        await self.receiver.wait_for_function('(n) => savedVideo.getVideoPlaybackQuality().totalVideoFrames > n + 5', arg=frames, timeout=5000)

    async def test_stage_reconecta_trilha_sem_trocar_identidade(self):
        await self.open_stage()
        await self.producer.evaluate("() => { const a = document.querySelector('[data-user-id=w_a] video'); a.srcObject = new MediaStream([a.srcObject.getVideoTracks()[0].clone()]); }")
        for _ in range(100):
            if self.offers.count('cam-w_a') > 1:
                break
            await asyncio.sleep(.1)
        self.assertEqual(self.offers.count('cam-w_a'), 2)
        self.assertEqual(self.offers.count('cam-w_b'), 1)
        await self.receiver.wait_for_function("document.querySelector('[data-source-id=\"cam-w_a\"]').getVideoPlaybackQuality().totalVideoFrames > 5", timeout=15000)

    async def test_stage_fecha_cameras_ocultas_e_no_pagehide(self):
        await self.producer.evaluate("() => { const Peer = RTCPeerConnection; window.testPeers = []; window.RTCPeerConnection = class extends Peer { constructor(...args) { super(...args); testPeers.push(this); } }; }")
        await self.open_stage()
        self.state.stage_preset = 'slide-only'
        await self.receiver.wait_for_function("document.querySelectorAll('video[data-source-id^=\"cam-\"]').length === 0")
        await self.producer.wait_for_function("testPeers.every(p => p.connectionState === 'closed')", timeout=5000)
        self.state.stage_preset = 'side-by-side'
        await self.receiver.wait_for_function("document.querySelectorAll('video[data-source-id^=\"cam-\"]').length === 2")
        await self.receiver.wait_for_function("[...document.querySelectorAll('video[data-source-id^=\"cam-\"]')].every(v => v.videoWidth > 0)", timeout=25000)
        await self.receiver.goto('about:blank')
        await self.producer.wait_for_function("testPeers.every(p => p.connectionState === 'closed')", timeout=5000)
        self.assertTrue(await self.producer.evaluate("fixtureTracks.every(t => t.readyState === 'live')"))

    async def test_stage_compartilhamento_usa_webrtc(self):
        await self.producer.evaluate("() => { const v = document.createElement('video'); v.id = 'screenshareVideo'; v.autoplay = true; v.muted = true; v.srcObject = new MediaStream([fixtureTracks[0].clone()]); document.body.appendChild(v); }")
        await self.open_stage()
        await self.receiver.wait_for_function("document.querySelector('.slide-frame video')?.videoWidth === 320", timeout=25000)
        self.assertIn('presentation', self.offers)
        self.assertTrue(await self.receiver.locator('.slide-frame video').evaluate('(v) => v.muted'))

    async def test_stage_falha_de_sinalizacao_visivel_e_recuperavel(self):
        await self.receiver.route('**/api/webrtc/offer', lambda route: route.fulfill(
            status=409, content_type='application/json', body='{"detail":"Limite de receptores atingido"}'))
        self.state.webrtc_only = True
        self.state.cameras_list = [{'id': 'cam-w_b', 'name': 'Participante', 'is_libras': False}]
        await self.receiver.goto(self.url + '/stage')
        for _ in range(80):
            if 'Limite de receptores' in await self.receiver.locator('body').inner_text():
                break
            await asyncio.sleep(.1)
        self.assertIn('Limite de receptores', await self.receiver.locator('body').inner_text())
        await self.receiver.unroute('**/api/webrtc/offer')
        await self.receiver.wait_for_function("document.querySelector('video[data-source-id=\"cam-w_b\"]')?.videoWidth === 320", timeout=25000)

    async def test_stage_legado_mantem_fontes_jpeg(self):
        self.state.webrtc_only = False
        self.state.cameras_list = [{'id': 'cam-w_b', 'name': 'Participante', 'is_libras': False}]
        self.state.libras_camera_ids = []
        self.state.pinned_camera_ids = []
        self.state.stage_preset = 'side-by-side'
        await self.receiver.goto(self.url + '/stage')
        await self.receiver.locator('#stage_cam_cam-w_b').wait_for()
        self.assertEqual(await self.receiver.locator('#stage_cam_cam-w_b').evaluate('(el) => el.tagName'), 'IMG')
        self.assertEqual(self.offers, [])

    async def test_stage_fecha_resposta_atrasada_de_camera_removida(self):
        ready, release = asyncio.Event(), asyncio.Event()
        await self.producer.evaluate("() => { const Peer = RTCPeerConnection; window.testPeers = []; window.RTCPeerConnection = class extends Peer { constructor(...args) { super(...args); testPeers.push(this); } }; }")
        async def delayed_offer(route):
            response = await route.fetch()
            ready.set()
            await release.wait()
            await route.fulfill(response=response)
        await self.receiver.route('**/api/webrtc/offer', delayed_offer)
        self.state.webrtc_only = True
        self.state.cameras_list = [{'id': 'cam-w_b', 'name': 'Participante', 'is_libras': False}]
        self.state.stage_preset = 'side-by-side'
        self.state.pinned_camera_ids = []
        self.state.libras_camera_ids = []
        try:
            await self.receiver.goto(self.url + '/stage')
            await asyncio.wait_for(ready.wait(), timeout=15000)
            self.state.stage_preset = 'slide-only'
            await self.receiver.wait_for_function("!document.querySelector('video[data-source-id^=\"cam-\"]')")
            release.set()
            await self.producer.wait_for_function("testPeers.length > 0 && testPeers.every(p => p.connectionState === 'closed')", timeout=5000)
            self.assertEqual(self.offers, ['cam-w_b'])
        finally:
            release.set()

    async def test_dock_copia_links_da_mesma_porta(self):
        await self.receiver.context.grant_permissions(['clipboard-read', 'clipboard-write'])
        await self.receiver.goto(self.url + '/control')
        for path, title in [('/stage', 'Palco Composto'), ('/poll', 'Enquete'), ('/chat', 'Feed de Chat'), ('/libras', 'Janela de Libras')]:
            await self.receiver.locator(f'button[title*="Copiar URL do {title}"], button[title*="Copiar URL da {title}"]').click()
            self.assertEqual(await self.receiver.evaluate('navigator.clipboard.readText()'), self.url + path)

    async def test_slides_continuam_disponiveis_sem_jpeg_de_cameras(self):
        self.state.webrtc_only = True
        await self.producer.evaluate("() => { const slide = document.createElement('div'); slide.id = 'presentationContainer'; slide.textContent = 'Slide de teste'; slide.style.cssText = 'width:320px;height:180px;background:white'; document.body.appendChild(slide); }")
        task = asyncio.create_task(server.sync_loop())
        try:
            for _ in range(50):
                if self.state.latest_presentation_frame:
                    break
                await asyncio.sleep(.1)
            self.assertIsNotNone(self.state.latest_presentation_frame, 'Slides estáticos precisam continuar disponíveis no palco.')
            self.assertEqual(self.state.latest_camera_frames, {})
            await self.receiver.goto(self.url + '/stage')
            await self.receiver.wait_for_function("document.querySelector('.slide-frame img')?.naturalWidth > 0", timeout=8000)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_modo_webrtc_nao_codifica_jpeg_em_paralelo(self):
        self.state.webrtc_only = True
        await self.producer.evaluate("() => { window.encodeCalls = 0; HTMLCanvasElement.prototype.toDataURL = () => { window.encodeCalls++; return ''; }; }")
        task = asyncio.create_task(server.sync_loop())
        try:
            for _ in range(40):
                if self.state.cameras_list:
                    break
                await asyncio.sleep(.05)
            self.assertEqual(await self.producer.evaluate('window.encodeCalls'), 0)
            self.assertEqual(len(self.state.cameras_list), 2)
            self.assertEqual(self.state.latest_camera_frames, {})
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_captura_legada_usa_mesma_identidade_do_webrtc(self):
        task = asyncio.create_task(server.sync_loop())
        try:
            for _ in range(60):
                if self.state.cameras_list:
                    break
                await asyncio.sleep(.05)
            self.assertEqual({c['id'] for c in self.state.cameras_list}, {'cam-w_a', 'cam-w_b'})
            self.assertEqual(self.state.cameras_list[0]['original_name'], 'Intérprete Teste')
            await self.producer.evaluate("() => document.querySelector('[data-user-id=w_a]').remove()")
            for _ in range(60):
                if len(self.state.cameras_list) == 1:
                    break
                await asyncio.sleep(.05)
            self.assertEqual([c['id'] for c in self.state.cameras_list], ['cam-w_b'])
            self.assertNotIn('cam-w_a', self.state.latest_camera_frames)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_master_preserva_audio_do_compartilhamento_sem_criar_canal_falso(self):
        await self.producer.evaluate("""() => {
            const ctx = fixtureAudioContext;
            const dest = ctx.createMediaStreamDestination();
            const osc = ctx.createOscillator(); osc.connect(dest); osc.start();
            const el = document.createElement('audio'); el.srcObject = dest.stream;
            el.__reactFiber$fixture = {memoizedProps:{trackRef:{source:'screen_share_audio',
                participant:{identity:'w_screen',name:'Tela',isLocal:false},
                publication:{track:{mediaStreamTrack:dest.stream.getAudioTracks()[0]}}}}};
            document.body.appendChild(el);
            window.screenAudioTrack = dest.stream.getAudioTracks()[0];
        }""")
        await self.producer.evaluate("() => { window._rnpAudioControls = {'audio-w_a':{muted:true}, 'audio-w_b':{muted:true}}; }")
        await self.producer.evaluate('(url) => RnpMedia.startPcm(url)', self.url.replace('http:', 'ws:') + '/ws/pcm_in')
        await asyncio.sleep(.3)
        async with connect_websocket(self.url.replace('http:', 'ws:') + '/ws/pcm_out') as socket_out:
            data = await asyncio.wait_for(socket_out.recv(), timeout=3)
            samples = struct.unpack('<' + 'f' * (len(data) // 4), data)
            self.assertGreater(max(abs(v) for v in samples), .1)
        self.assertEqual(len(await self.producer.evaluate('() => RnpMedia.audioInfo()')), 2)

    async def test_picos_individuais_sao_empurrados_por_websocket(self):
        # O medidor individual não pode esperar um poll de 500ms: os picos por trilha
        # devem ser EMPURRADOS na cadência do sampler (60ms) via /ws/peaks_in -> out.
        await self.producer.evaluate('(url) => RnpMedia.startPcm(url)',
                                     self.url.replace('http:', 'ws:') + '/ws/pcm_in')
        await self.producer.wait_for_function("RnpMedia.audioInfo().every(a => a.peak > 0.05)", timeout=10000)
        async with connect_websocket(self.url.replace('http:', 'ws:') + '/ws/peaks_out') as sock:
            mensagem = await asyncio.wait_for(sock.recv(), timeout=3)
        picos = json.loads(mensagem)
        self.assertGreater(picos.get('audio-w_a', 0), 0.05,
                           'O pico empurrado deve refletir a trilha tocando, não zerar.')

    async def test_mesa_do_dock_atualiza_vu_por_push(self):
        # A mesa individual deve assinar /ws/peaks_out e erguer o VU por push,
        # independente do poll de /api/media (que só cuida de estrutura/volume/mute).
        ws_urls = []
        self.receiver.on('websocket', lambda ws: ws_urls.append(ws.url))
        await self.producer.evaluate('(url) => RnpMedia.startPcm(url)',
                                     self.url.replace('http:', 'ws:') + '/ws/pcm_in')
        await self.producer.wait_for_function("RnpMedia.audioInfo().every(a => a.peak > 0.05)", timeout=10000)
        await self.receiver.goto(self.url + '/control')
        await self.receiver.evaluate("() => RnpDock.activateTab('audio')")
        await self.receiver.wait_for_selector('[data-audio-id="audio-w_a"]', timeout=10000)
        await self.receiver.wait_for_function(
            "parseFloat(document.querySelector('[data-audio-id=audio-w_a] .vu-fill-vertical').style.height || '0') > 5",
            timeout=5000)
        self.assertTrue(any('/ws/peaks_out' in u for u in ws_urls),
                        'A mesa precisa assinar o canal de picos empurrados.')

    async def test_toggle_desliga_preview_de_webcams_e_apresentacao(self):
        # Desligar o preview precisa PARAR de carregar a fonte (src about:blank),
        # e o poll que refresca o thumb da apresentação não pode ressuscitá-lo.
        await self.receiver.goto(self.url + '/control')
        await self.receiver.click('#btnToggleCamPreview')
        await self.receiver.wait_for_timeout(300)
        estado = await self.receiver.evaluate("""() => ({
            src: document.getElementById('gridPreviewIframe').getAttribute('src'),
            off: document.getElementById('gridPreviewIframe').parentElement.classList.contains('preview-off'),
        })""")
        self.assertEqual(estado['src'], 'about:blank', 'Preview de webcams desligado não deve carregar o grid.')
        self.assertTrue(estado['off'], 'O contêiner deve marcar preview-off para o placeholder.')
        await self.receiver.click('#btnToggleCamPreview')
        await self.receiver.wait_for_timeout(300)
        self.assertIn('/cameras/grid', await self.receiver.evaluate(
            "() => document.getElementById('gridPreviewIframe').getAttribute('src')"),
            'Religar o preview deve restaurar a fonte.')
        await self.receiver.click('#btnToggleScreenPreview')
        await self.receiver.wait_for_timeout(1600)
        src = await self.receiver.evaluate("() => document.getElementById('thumbScreen').getAttribute('src') || ''")
        self.assertNotIn('/raw_frame', src, 'O poll não pode religar o preview desativado.')

    async def test_mixer_canais_com_largura_fixa(self):
        # Mesa de verdade: cada canal tem largura constante, não estica com a tela.
        # Sem isso, o master virava um bloco de 500px quando o Áudio ocupava a linha.
        await self.receiver.goto(self.url + '/control')
        await self.receiver.evaluate("() => RnpDock.activateTab('audio')")
        await self.receiver.set_viewport_size({'width': 1400, 'height': 900})
        await self.receiver.wait_for_timeout(600)
        w = await self.receiver.evaluate(
            "() => document.getElementById('channelStripMaster').getBoundingClientRect().width")
        self.assertGreater(w, 140, 'Strip estreito demais não caberia o fader.')
        self.assertLess(w, 220, 'O strip do master não pode esticar com a largura da tela.')

    async def test_mosaico_adapta_colunas_por_largura(self):
        # Mosaico: 4 trilhos no largo (grupos span 2 = 2x2), auto-fit no médio
        # (2 colunas) e coluna única no estreito — sem vãos mortos.
        await self.receiver.goto(self.url + '/control')
        trilhas = ("(sel) => getComputedStyle(document.querySelector(sel))"
                   ".gridTemplateColumns.split(' ').length")
        await self.receiver.set_viewport_size({'width': 1200, 'height': 900})
        await self.receiver.wait_for_timeout(600)
        self.assertEqual(await self.receiver.evaluate(trilhas, '.dock-groups'), 4,
                         'No largo o mosaico tem 4 trilhos.')
        self.assertEqual(await self.receiver.evaluate(trilhas, '.dock-group.is-active .dock-group-body'), 2,
                         'Cada grupo ocupa 2 trilhos (2x2).')
        await self.receiver.set_viewport_size({'width': 780, 'height': 900})
        await self.receiver.wait_for_timeout(600)
        self.assertEqual(await self.receiver.evaluate(trilhas, '.dock-group.is-active .dock-group-body'), 2,
                         'No médio o grupo ativo usa 2 colunas (auto-fit).')
        await self.receiver.set_viewport_size({'width': 400, 'height': 900})
        await self.receiver.wait_for_timeout(600)
        self.assertEqual(await self.receiver.evaluate(trilhas, '.dock-group.is-active .dock-group-body'), 1,
                         'No estreito o grupo ativo vira coluna única.')
        await self.receiver.set_viewport_size({'width': 2200, 'height': 900})
        await self.receiver.wait_for_timeout(600)
        self.assertEqual(await self.receiver.evaluate(trilhas, '.dock-groups'), 6,
                         'No ultra-largo o mosaico sobe para 6 trilhos.')
        await self.receiver.set_viewport_size({'width': 1200, 'height': 900})
        await self.receiver.wait_for_timeout(600)
        frac = await self.receiver.evaluate("""() => {
            const g = document.getElementById('group-audio').getBoundingClientRect();
            const c = document.getElementById('dockGroups').getBoundingClientRect();
            return g.width / c.width;
        }""")
        self.assertLess(frac, 0.6, 'O bloco de Áudio não pode ocupar a linha inteira.')

    async def test_menu_de_conexao_abre_e_fecha(self):
        # Popover: fechado por padrão, abre no clique do botão da barra, fecha ao
        # clicar fora. Por ser overlay, não ocupa fluxo em nenhuma largura.
        await self.receiver.goto(self.url + '/control')
        await self.receiver.wait_for_timeout(800)
        fechado = "() => document.getElementById('connectPopover').hidden"
        self.assertTrue(await self.receiver.evaluate(fechado), 'Popover começa fechado.')
        await self.receiver.click('#btnConnectMenu')
        await self.receiver.wait_for_timeout(200)
        self.assertFalse(await self.receiver.evaluate(fechado), 'O botão da barra abre o popover.')
        await self.receiver.click('h1')
        await self.receiver.wait_for_timeout(200)
        self.assertTrue(await self.receiver.evaluate(fechado), 'Clicar fora fecha o popover.')

    async def test_reordenar_grupos_persiste_apos_reload(self):
        # As setas do cabeçalho de cada grupo movem o grupo (e a aba correspondente)
        # e a ordem persiste no config do servidor — sobrevive a reload.
        await self.receiver.goto(self.url + '/control')
        antes = await self.receiver.evaluate(
            "() => [...document.querySelectorAll('.dock-group')].map(g => g.dataset.group)")
        await self.receiver.click('#group-conexao .dock-move[data-move="1"]')
        await self.receiver.wait_for_timeout(500)
        depois = await self.receiver.evaluate(
            "() => [...document.querySelectorAll('.dock-group')].map(g => g.dataset.group)")
        self.assertEqual(depois, antes[1:2] + antes[:1] + antes[2:],
                         'Mover para a direita deve trocar o grupo de lugar.')
        abas = await self.receiver.evaluate(
            "() => [...document.querySelectorAll('.dock-tab')].map(b => b.dataset.tab)")
        self.assertEqual(abas, depois, 'As abas acompanham a ordem dos grupos.')
        await self.receiver.reload(wait_until='domcontentloaded')
        await self.receiver.wait_for_timeout(800)
        recarregado = await self.receiver.evaluate(
            "() => [...document.querySelectorAll('.dock-group')].map(g => g.dataset.group)")
        self.assertEqual(recarregado, depois, 'A ordem deve persistir após reload.')

    async def test_monitor_comprime_no_estreito(self):
        # C híbrida: no estreito o monitor vira chips (some o estado detalhado);
        # no largo mostra o estado completo. Mesmo DOM, troca por CSS.
        await self.receiver.goto(self.url + '/control')
        probe = ("() => getComputedStyle(document.querySelector("
                 "'.dock-monitor-fixed .pill-state')).display")
        await self.receiver.set_viewport_size({'width': 400, 'height': 800})
        await self.receiver.wait_for_timeout(600)
        estreito = await self.receiver.evaluate(probe)
        await self.receiver.set_viewport_size({'width': 1200, 'height': 800})
        await self.receiver.wait_for_timeout(600)
        largo = await self.receiver.evaluate(probe)
        self.assertEqual(estreito, 'none', 'No estreito o estado detalhado some (chips).')
        self.assertNotEqual(largo, 'none', 'No largo o monitor mostra o estado completo.')

    async def test_abas_acima_da_dobra_no_estreito(self):
        # No dock estreito a zona fixa não pode empurrar as abas para fora da tela:
        # o operador precisa ver status + mixer + abas sem rolar.
        await self.receiver.set_viewport_size({'width': 400, 'height': 800})
        await self.receiver.goto(self.url + '/control')
        await self.receiver.wait_for_timeout(1200)
        top = await self.receiver.evaluate(
            "() => document.getElementById('dockTabs').getBoundingClientRect().top")
        self.assertLess(top, 800, 'No estreito a barra de abas deve ficar acima da dobra.')

    async def test_responsivo_largo_mostra_grupos_sem_abas(self):
        # Estreito: barra de abas visível e só o grupo ativo; largo: os 4 grupos
        # visíveis e nenhuma barra de abas (disclosure progressivo).
        await self.receiver.goto(self.url + '/control')
        estado = """() => ({
            abas: getComputedStyle(document.getElementById('dockTabs')).display !== 'none',
            visiveis: [...document.querySelectorAll('.dock-group')]
                .filter(g => getComputedStyle(g).display !== 'none').length,
        })"""
        await self.receiver.set_viewport_size({'width': 400, 'height': 900})
        await self.receiver.wait_for_timeout(600)
        estreito = await self.receiver.evaluate(estado)
        self.assertTrue(estreito['abas'], 'No estreito a barra de abas deve aparecer.')
        self.assertEqual(estreito['visiveis'], 1, 'No estreito só o grupo ativo fica visível.')
        await self.receiver.set_viewport_size({'width': 1200, 'height': 900})
        await self.receiver.wait_for_timeout(600)
        largo = await self.receiver.evaluate(estado)
        self.assertFalse(largo['abas'], 'No largo as abas desaparecem.')
        self.assertEqual(largo['visiveis'], 5, 'No largo os 5 grupos ficam visíveis.')

    async def test_mixer_controla_canal_real_sem_afetar_outro(self):
        await self.producer.evaluate('(url) => RnpMedia.startPcm(url)', self.url.replace('http:', 'ws:') + '/ws/pcm_in')
        await self.producer.wait_for_function("RnpMedia.audioInfo().every(a => a.peak > 0.1)")
        async with httpx.AsyncClient() as client:
            response = await client.post(self.url + '/api/audio/control',
                json={'source_id': 'audio-w_a', 'volume': .5, 'muted': True})
            self.assertEqual(response.status_code, 200)
        await self.producer.wait_for_function("RnpMedia.audioInfo().find(a => a.id === 'audio-w_a').peak < .001")
        channels = await self.producer.evaluate('() => RnpMedia.audioInfo()')
        self.assertGreater(channels[1]['peak'], .1)
        self.assertTrue(channels[0]['muted'])
        self.assertEqual(channels[0]['volume'], .5)
        self.assertEqual(len(channels), 2)

    async def test_dock_nao_confirma_libras_quando_gravacao_falha(self):
        await self.receiver.goto(self.url + '/control')
        await self.receiver.route('**/api/grid/config', lambda route: route.fulfill(status=500, body='Falha de escrita'))
        await self.receiver.evaluate("toggleLibras('cam-w_a')")
        self.assertFalse(await self.receiver.locator('#saveToast').is_visible())
        self.assertIn('salvar Libras', await self.receiver.locator('#logBox').inner_text())

    async def test_dock_lista_audio_sem_camera_e_aplica_mute(self):
        await self.producer.evaluate("() => document.querySelectorAll('video').forEach(v => v.remove())")
        await self.receiver.goto(self.url + '/control')
        await self.receiver.evaluate("() => RnpDock.activateTab('audio')")
        await self.receiver.locator('[data-audio-id="audio-w_a"] button[data-action=mute]').click(timeout=6000)
        for _ in range(40):
            if self.state.audio_controls.get('audio-w_a', {}).get('muted'):
                break
            await asyncio.sleep(.05)
        self.assertTrue(self.state.audio_controls.get('audio-w_a', {}).get('muted'))
        self.assertEqual(await self.receiver.locator('[data-audio-id]').count(), 2)

    async def test_link_audio_sem_trilha_nao_negocia_video(self):
        await self.producer.evaluate("() => document.querySelector('audio[data-user-id=w_a]').remove()")
        await self.receiver.goto(self.url + '/webrtc?source=cam-w_a&kind=audio')
        await asyncio.sleep(3)
        self.assertEqual(self.offers, [])

    async def test_video_real_sem_jpeg_e_identidade_resiste_a_reordenacao(self):
        before = await self.producer.evaluate('() => RnpMedia.describe()')
        self.assertEqual([s['id'] for s in before['sources']],
            ['cam-w_a', 'cam-w_b', 'audio-w_a', 'audio-w_b'])
        await self.producer.evaluate("() => document.body.prepend(document.querySelector('[data-user-id=\"w_b\"]'))")
        after = await self.producer.evaluate('() => RnpMedia.describe()')
        self.assertEqual({s['id']: s['name'] for s in before['sources']},
                         {s['id']: s['name'] for s in after['sources']})
        raw_requests = []
        self.receiver.on('request', lambda r: raw_requests.append(r.url) if '/raw_frame/' in r.url else None)
        await self.receiver.goto(self.url + '/webrtc?source=cam-w_a&kind=video')
        await self.receiver.wait_for_function("document.querySelector('video').getVideoPlaybackQuality().totalVideoFrames > 10", timeout=25000)
        self.assertEqual(await self.receiver.locator('video').evaluate('(v) => v.videoWidth'), 320)
        self.assertEqual(raw_requests, [])
        await self.receiver.locator('#stop').click()
        self.assertTrue(await self.producer.evaluate("() => fixtureTracks.every(t => t.readyState === 'live')"))

    async def test_audio_isolado_e_mute_real(self):
        await self.receiver.add_init_script("""{
            const Peer = window.RTCPeerConnection;
            window.RTCPeerConnection = class extends Peer {
                constructor(...args) { super(...args); window.testPeer = this; }
            };
            const Native = window.AudioContext;
            window.AudioContext = class extends Native {
                createAnalyser() { const a = super.createAnalyser(); window.testAnalyser = a; return a; }
            };
        }""")
        await self.receiver.goto(self.url + '/webrtc?source=audio-w_a&kind=audio')
        await self.receiver.wait_for_function("document.body.classList.contains('connected')", timeout=25000)
        try:
            await self.receiver.wait_for_function("""() => {
                if (!window.testAnalyser) return false;
                const x = new Float32Array(testAnalyser.fftSize); testAnalyser.getFloatTimeDomainData(x);
                return Math.max(...x) > 0.1;
            }""", timeout=10000)
        except Exception:
            origin = await self.producer.evaluate('() => fixtureAudioContext.state')
            received = await self.receiver.evaluate("""() => {
                const a = window.testAnalyser; const x = new Float32Array(a.fftSize); a.getFloatTimeDomainData(x);
                return {state:a.context.state, peak:Math.max(...x), status:document.querySelector('#status').textContent, metrics:document.querySelector('#metrics').textContent};
            }""")
            stats = await self.receiver.evaluate("async () => [...(await testPeer.getStats()).values()].filter(r => r.type === 'inbound-rtp')")
            self.fail(f'Áudio: origem={origin}; receptor={received}; RTP={stats}')
        frequency_js = """() => {
            const a = testAnalyser, bins = new Float32Array(a.frequencyBinCount);
            a.getFloatFrequencyData(bins);
            let best = 0; for(let i=1;i<bins.length;i++) if(bins[i]>bins[best]) best=i;
            return best * a.context.sampleRate / a.fftSize;
        }"""
        # A primeira FFT pode pegar o transiente de partida do grafo de áudio, então
        # amostra até estabilizar e afirma sobre o valor estabilizado (a retry antiga
        # coletava amostras que nunca entravam na asserção — era decorativa).
        peak_hz = await self.receiver.evaluate(frequency_js)
        samples = [peak_hz]
        for _ in range(10):
            if abs(peak_hz - 440) <= 30:
                break
            await asyncio.sleep(.1)
            peak_hz = await self.receiver.evaluate(frequency_js)
            samples.append(peak_hz)
        self.assertAlmostEqual(peak_hz, 440, delta=30, msg=f'Frequências a cada 100 ms: {samples}')
        async with httpx.AsyncClient() as client:
            response = await client.post(self.url + '/api/audio/control',
                json={'source_id': 'audio-w_a', 'volume': 1, 'muted': True})
        self.assertEqual(response.status_code, 200)
        await self.receiver.wait_for_function("""() => {
            const x = new Float32Array(testAnalyser.fftSize); testAnalyser.getFloatTimeDomainData(x);
            return Math.max(...x.map(Math.abs)) < 0.001;
        }""", timeout=10000)
        self.assertEqual(self.offers, ['audio-w_a'])

    async def test_substituicao_da_trilha_reconecta_o_mesmo_participante(self):
        await self.receiver.goto(self.url + '/webrtc?source=cam-w_a&kind=video')
        await self.receiver.wait_for_function("document.querySelector('video').getVideoPlaybackQuality().totalVideoFrames > 10", timeout=25000)
        await self.producer.evaluate("""() => {
            const a = document.querySelector('[data-user-id=w_a] video');
            const b = document.querySelector('[data-user-id=w_b] video');
            a.srcObject = new MediaStream([b.srcObject.getVideoTracks()[0].clone()]);
        }""")
        for _ in range(60):
            if len(self.offers) >= 2:
                break
            await asyncio.sleep(.1)
        self.assertEqual(self.offers, ['cam-w_a', 'cam-w_a'])
        await self.receiver.wait_for_function("document.querySelector('video').getVideoPlaybackQuality().totalVideoFrames > 10", timeout=15000)

    async def test_fonte_manual_removida_nao_troca_para_outra_pessoa(self):
        await self.receiver.goto(self.url + '/webrtc')
        await self.receiver.locator('#source').select_option('cam-w_a')
        await self.receiver.locator('#start').click()
        await self.receiver.wait_for_function("document.body.classList.contains('connected')", timeout=25000)
        await self.producer.evaluate("() => document.querySelector('[data-test=webcamVideoItem][data-user-id=w_a]').remove()")
        await asyncio.sleep(6)
        self.assertTrue(all(source == 'cam-w_a' for source in self.offers), self.offers)
        self.assertEqual(await self.receiver.locator('#source').input_value(), '')


if __name__ == '__main__':
    unittest.main()
