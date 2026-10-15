(() => {
    if (window.RnpMedia?.version === 1) return;
    const peers = new Map();
    const cameraPublications = new Map();
    const cameraElements = new WeakMap();
    const itemSelector = '[data-test="webcamVideoItem"], [data-test="webcamItem"], [data-test="webcamItemTalkingUser"]';
    const liveTracks = (el, kind) => (el.srcObject?.getTracks?.() || [])
        .filter(t => t.kind === kind && t.readyState === 'live');
    const safeId = value => encodeURIComponent(String(value));

    // Estado do player externo (YouTube IFrame API), farejado dos postMessage que o
    // BBB recebe na janela do frame da sala. Não acesamos o iframe (cross-origin);
    // apenas observamos os eventos que já chegam à janela do BBB.
    const yt = {video_id: null, playing: false, current_time: 0, duration: 0, rate: 1, updated_at: 0};
    window.addEventListener('message', (event) => {
        const data = event.data;
        if (typeof data !== 'string' || !/playerState|video_id|onStateChange|currentTime|playbackRate/i.test(data)) return;
        let msg; try { msg = JSON.parse(data); } catch { return; }
        const info = msg && msg.info;
        if (msg.event === 'infoDelivery' && info && typeof info === 'object') {
            if (typeof info.currentTime === 'number') {
                if (info.currentTime > yt.current_time + 0.02) yt.playing = true;  // avanço => tocando
                yt.current_time = info.currentTime;
            }
            if (typeof info.duration === 'number') yt.duration = info.duration;
            if (typeof info.playbackRate === 'number') yt.rate = info.playbackRate;
            if (typeof info.playerState === 'number') yt.playing = info.playerState === 1;
            if (info.videoData && info.videoData.video_id) yt.video_id = info.videoData.video_id;
            yt.updated_at = Date.now();
        } else if (msg.event === 'onStateChange' && typeof info === 'number') {
            yt.playing = info === 1;
            yt.updated_at = Date.now();
        }
    }, true);
    function ytIframeId() {
        const f = document.querySelector('#widget2, [data-test="videoPlayer"] iframe, iframe[src*="youtu"]');
        if (!f) return null;
        const m = (f.getAttribute('src') || '').match(/\/embed\/([A-Za-z0-9_-]{6,})/);
        return m ? m[1] : null;
    }
    function youtubeState() {
        const present = !!ytIframeId();
        const id = ytIframeId() || yt.video_id || null;
        return {video_id: id, present,
            playing: yt.playing, current_time: yt.current_time, duration: yt.duration,
            rate: yt.rate, updated_at: yt.updated_at};
    }

    function participantId(video) {
        const owner = video.closest('[data-user-id], [data-participant-id]');
        const explicit = owner?.getAttribute('data-user-id') || owner?.getAttribute('data-participant-id');
        if (explicit) return explicit;
        const stream = video.closest('[data-stream]')?.getAttribute('data-stream') || '';
        // Identificador BBB antes do sufixo da publicação da webcam.
        return stream.match(/^(w_[a-z0-9]+)_/i)?.[1] || '';
    }

    function cameraId(video, pid, track) {
        if (!pid) return 'cam-' + safeId(track.id);
        const publication = video.closest('[data-stream]')?.getAttribute('data-stream') || '';
        const key = publication || track.id;
        let ids = cameraPublications.get(pid);
        if (!ids) {
            ids = new Map();
            cameraPublications.set(pid, ids);
        }
        const previous = cameraElements.get(video);
        let id = ids.get(key);
        if (!id && previous?.pid === pid && previous.publication === publication) id = previous.id;
        // A primeira publicação conserva o link antigo; as demais nunca herdam seu ID.
        if (!id) id = 'cam-' + safeId(pid) + (ids.size ? '~' + safeId(key) : '');
        ids.set(key, id);
        cameraElements.set(video, {pid, publication, id});
        return id;
    }

    function cameraEntries() {
        const found = new Map();
        for (const video of document.querySelectorAll('video')) {
            if (video.id === 'screenshareVideo' || video.closest('#screenshareContainer, [data-test="screenshareVideo"]')) continue;
            if (video.dataset.localStream === 'true' || video.dataset.currentUserStream === 'true') continue;
            const track = liveTracks(video, 'video')[0];
            if (!track) continue;
            const item = video.closest('[data-user-name]') || video.closest(itemSelector);
            if (!item) continue;
            const pid = participantId(video);
            const id = cameraId(video, pid, track);
            const name = item.getAttribute('data-user-name') ||
                item.querySelector('[data-test="dropdownWebcamButton"]')?.textContent?.trim() || 'Participante sem nome';
            if (!found.has(id)) found.set(id, {id, participant_id: pid, name, video, track});
        }
        return [...found.values()];
    }

    function audioOwner(el, track) {
        if (el.dataset.lkLocalParticipant === 'true') return null;
        // LiveKit conserva a identidade e a publicação no AudioTrack do React.
        // Só aceitamos uma referência cuja trilha coincida com a do elemento.
        const key = Object.keys(el).find(k => k.startsWith('__reactFiber$'));
        let fiber = key ? el[key] : null;
        for (let depth = 0; fiber && depth < 12; depth++, fiber = fiber.return) {
            const ref = fiber.memoizedProps?.trackRef;
            if (!ref || !['microphone', 'screen_share_audio'].includes(ref.source) || ref.participant?.isLocal) continue;
            const published = ref.publication?.track?.mediaStreamTrack;
            if (published?.id !== track.id) continue;
            if (ref.participant?.identity) return {
                id: ref.participant.identity,
                name: ref.participant.name || ref.participant.identity,
                source: ref.source
            };
        }
        // Adaptadores BBB que expõem explicitamente a identidade no elemento.
        const pid = el.getAttribute('data-participant-id') || el.getAttribute('data-user-id');
        return pid ? {id: pid, name: el.getAttribute('data-user-name') || pid} : null;
    }

    function audioEntries(includeScreen = false) {
        const entries = new Map();
        for (const el of document.querySelectorAll('audio, video')) {
            for (const track of liveTracks(el, 'audio')) {
                const owner = audioOwner(el, track);
                if (!owner) continue; // Áudio misturado nunca vira canal individual por heurística.
                const screen = owner.source === 'screen_share_audio';
                if (screen && !includeScreen) continue;
                const id = (screen ? 'screen-audio-' : 'audio-') + safeId(owner.id);
                if (!entries.has(id)) entries.set(id, {id, participant_id: owner.id, name: owner.name, track});
            }
        }
        return [...entries.values()];
    }

    function startPcm(url) {
        if (window._pcmCaptureActive) return;
        window._pcmCaptureActive = true;
        const ctx = new AudioContext({sampleRate: 48000});
        ctx.resume().catch(() => {});
        const master = ctx.createGain();
        const proc = ctx.createScriptProcessor(2048, 1, 1);
        const nodes = new Map();
        window._rnpAudioNodes = nodes;
        const ws = new WebSocket(url);
        ws.binaryType = 'arraybuffer';
        // Canal de picos por trilha: empurra o peakHold de cada canal individual na
        // cadência do sampler (60ms) para a ponte repassar à mesa do dock em tempo
        // real. Sem isto o medidor individual ficaria refém de um poll de 500ms.
        const pws = new WebSocket(url.replace('/ws/pcm_in', '/ws/peaks_in'));
        pws.onerror = () => pws.close();
        // A ponte avisa quantas mesas ouvem os picos (/ws/peaks_out). Com zero
        // ouvintes o sampler pausa: ler o AnalyserNode de toda trilha a 60 ms sem
        // ninguém olhando é CPU queimada no piso da ponte.
        let peaksWanted = false;
        pws.onmessage = ev => {
            try {
                const m = JSON.parse(ev.data);
                if (m.type === 'peaks_listeners') peaksWanted = m.n > 0;
            } catch (_) {}
        };
        proc.onaudioprocess = event => {
            if (ws.readyState === WebSocket.OPEN && ws.bufferedAmount < 48000 * 4 / 4) {
                ws.send(event.inputBuffer.getChannelData(0).buffer);
            }
        };
        master.connect(proc);
        proc.connect(ctx.destination);
        const scan = () => {
            let entries = audioEntries(true);
            // Salas sem trilhas identificadas continuam com master, nunca canais falsos.
            if (!entries.length) {
                const unique = new Map();
                for (const el of document.querySelectorAll('audio, video')) {
                    for (const track of liveTracks(el, 'audio')) unique.set(track.id, {track, id: null});
                }
                entries = [...unique.values()];
            }
            const live = new Set(entries.map(e => e.track.id));
            for (const [id, node] of nodes) {
                if (!live.has(id)) {
                    node.input.disconnect(); node.gain.disconnect(); node.analyser.disconnect();
                    nodes.delete(id);
                }
            }
            for (const entry of entries) {
                let node = nodes.get(entry.track.id);
                if (!node) {
                    const input = ctx.createMediaStreamSource(new MediaStream([entry.track]));
                    const gain = ctx.createGain(), analyser = ctx.createAnalyser();
                    input.connect(gain); gain.connect(analyser); analyser.connect(master);
                    node = {input, gain, analyser, samples: new Float32Array(analyser.fftSize), peakHold: 0, lastPeakAt: 0};
                    nodes.set(entry.track.id, node);
                }
                const control = window._rnpAudioControls?.[entry.id] || {};
                node.gain.gain.setTargetAtTime(control.muted ? 0 : (control.volume ?? 1), ctx.currentTime, .01);
                node.entryId = entry.id;
            }
        };
        window._rnpApplyAudioControls = scan;
        scan();
        const interval = setInterval(scan, 500);
        // Pico segurado com decaimento: o pico instantâneo (~42ms) some entre sílabas
        // e faz o indicador de fala piscar. Aqui seguramos o pico recente e decaímos
        // suavemente (tau=300ms), então quem fala continua "aceso" nas pausas curtas.
        const PEAK_TAU = 0.30;
        const samplePeaks = () => {
            if (!peaksWanted) return;   // ninguém ouvindo a mesa: não amostra
            const now = performance.now();
            for (const node of nodes.values()) {
                node.analyser.getFloatTimeDomainData(node.samples);
                let instant = 0;
                for (const value of node.samples) { const a = Math.abs(value); if (a > instant) instant = a; }
                const dt = node.lastPeakAt ? (now - node.lastPeakAt) / 1000 : 0;
                node.lastPeakAt = now;
                node.peakHold = Math.max(instant, node.peakHold * Math.exp(-dt / PEAK_TAU));
            }
            if (pws.readyState === WebSocket.OPEN && nodes.size) {
                const picos = {};
                for (const node of nodes.values()) {
                    if (node.entryId) picos[node.entryId] = +node.peakHold.toFixed(4);
                }
                pws.send(JSON.stringify(picos));
            }
        };
        const peakInterval = setInterval(samplePeaks, 60);
        ws.onclose = () => {
            clearInterval(interval);
            clearInterval(peakInterval);
            pws.close();
            for (const node of nodes.values()) {
                node.input.disconnect(); node.gain.disconnect(); node.analyser.disconnect();
            }
            nodes.clear(); master.disconnect(); proc.disconnect(); ctx.close();
            window._pcmCaptureActive = false;
            window._rnpApplyAudioControls = null;
            setTimeout(() => startPcm(url), 2000);
        };
        ws.onerror = () => ws.close();
    }

    function audioInfo() {
        return audioEntries().map(e => {
            const control = window._rnpAudioControls?.[e.id] || {};
            const node = window._rnpAudioNodes?.get(e.track.id);
            // Pico segurado (peak-hold) pelo sampler: mantém a fala acesa entre as
            // amostras do sync_loop e decai suavemente, sem piscar o indicador.
            const peak = node?.peakHold ?? 0;
            return {id: e.id, track_id: e.track.id, participant_id: e.participant_id, name: e.name, kind: 'audio',
                individual: true, peak, volume: control.volume ?? 1, muted: !!control.muted};
        });
    }

    function describe() {
        const audio = audioInfo();
        const sources = cameraEntries().map(e => ({id: e.id, track_id: e.track.id, participant_id: e.participant_id,
            name: e.name, kind: 'video', width: e.video.videoWidth, height: e.video.videoHeight,
            audio_source_id: audio.find(a => a.participant_id === e.participant_id)?.id || null}));
        const screen = document.querySelector('#screenshareVideo, #screenshareContainer video, [data-test="screenshareVideo"]');
        if (screen && liveTracks(screen, 'video').length) sources.push({id: 'presentation', track_id: liveTracks(screen, 'video')[0].id, name: 'Compartilhamento de tela', kind: 'video', width: screen.videoWidth, height: screen.videoHeight});
        sources.push(...audio);
        return {sources, audio_individual_available: audio.length > 0, youtube: youtubeState()};
    }

    function sourceTrack(id, kind) {
        if (kind === 'audio') return audioEntries().find(e => e.id === id)?.track;
        if (id === 'presentation') {
            const video = document.querySelector('#screenshareVideo, #screenshareContainer video, [data-test="screenshareVideo"]');
            return video ? liveTracks(video, 'video')[0] : null;
        }
        return cameraEntries().find(e => e.id === id)?.track;
    }

    function close(id) {
        const entry = peers.get(id);
        if (!entry) return;
        peers.delete(id);
        clearTimeout(entry.expiry);
        clearTimeout(entry.disconnected);
        clearInterval(entry.watch);
        entry.pc.close();
        entry.clone.stop(); // Nunca parar a trilha original da sala.
    }

    async function answer({session_id, source_id, kind, sdp}) {
        const original = sourceTrack(source_id, kind);
        if (!original) throw new Error('Fonte indisponível ou sem trilha individual.');
        const maxPeers = Number(window.RNP_MAX_PEERS) || 0;
        if (maxPeers > 0 && peers.size >= maxPeers) throw new Error(`Limite de ${maxPeers} receptores experimentais atingido.`);
        const pc = new RTCPeerConnection({iceServers: [], bundlePolicy: 'max-bundle'});
        const clone = original.clone();
        const entry = {pc, clone};
        peers.set(session_id, entry);
        entry.expiry = setTimeout(() => close(session_id), 20000);
        pc.onconnectionstatechange = () => {
            if (pc.connectionState === 'connected') {
                clearTimeout(entry.expiry);
                clearTimeout(entry.disconnected);
            } else if (pc.connectionState === 'failed' || pc.connectionState === 'closed') {
                close(session_id);
            } else if (pc.connectionState === 'disconnected') {
                clearTimeout(entry.disconnected);
                entry.disconnected = setTimeout(() => close(session_id), 10000);
            }
        };
        entry.watch = setInterval(() => {
            if (sourceTrack(source_id, kind)?.id !== original.id) close(session_id);
        }, 1000);
        try {
            await pc.setRemoteDescription({type: 'offer', sdp});
            pc.addTrack(clone, new MediaStream([clone]));
            await pc.setLocalDescription(await pc.createAnswer());
            await new Promise((resolve, reject) => {
                if (pc.iceGatheringState === 'complete') return resolve();
                const timer = setTimeout(() => {pc.removeEventListener('icegatheringstatechange', changed); reject(new Error('Tempo esgotado na negociação ICE.'));}, 7000);
                const changed = () => {
                    if (pc.iceGatheringState === 'complete') {
                        clearTimeout(timer);
                        pc.removeEventListener('icegatheringstatechange', changed);
                        resolve();
                    }
                };
                pc.addEventListener('icegatheringstatechange', changed);
            });
            return {type: pc.localDescription.type, sdp: pc.localDescription.sdp};
        } catch (error) {
            close(session_id);
            throw error;
        }
    }

    // 🛡️ Blindagem Anti-Inatividade no próprio documento da sala: modal
    // "Verificação de atividade do usuário" (RNP/Mconf) com contagem regressiva.
    // O MutationObserver confirma o botão assim que o modal monta (subsegundo),
    // antes de a notificação chegar ao screenshot do palco. Varre tambem o
    // shadow DOM raiz do player externo, pois la dentro o screenshot elementar
    // capturaria o overlay junto com o video.
    const inactivityModalRe = /inactivity|atividade|inatividade|ainda est[aá]|still.?here|sess[ãa]o/i;
    const inactivityConfirmRe = /^(verificar|confirm\w*|continuar|estou aqui|sim[,\s]*ainda estou aqui|i'?m still here|stay in meeting)$/i;
    const inactivityDangerRe = /logout|sair|leave|encerrar|disconnect/i;
    function dismissInactivityModals() {
        const roots = [document, document.querySelector('#videoPlayer')?.shadowRoot].filter(Boolean);
        for (const root of roots) {
            const hosts = [...root.querySelectorAll('[data-test], .modal, [role="dialog"]')];
            if (root === document) hosts.push(...root.querySelectorAll('#layout, #app'));
            for (const host of hosts) {
                if (!inactivityModalRe.test((host.getAttribute('data-test') || '') + ' ' + (host.textContent || ''))) continue;
                const buttons = [...host.querySelectorAll('button, a.btn, [role="button"], input[type="button"], input[type="submit"]')];
                for (const b of buttons) {
                    const dt = b.getAttribute('data-test') || '';
                    const txt = (b.textContent || b.value || '').trim();
                    if (inactivityDangerRe.test(dt) || /^(sair|logout|leave|encerrar)$/i.test(txt)) continue;
                    if (/confirm|inactivity|still/i.test(dt) || inactivityConfirmRe.test(txt)) {
                        b.click();
                        window._rnpInactivityDismissed = (window._rnpInactivityDismissed || 0) + 1;
                        break;
                    }
                }
            }
        }
    }
    dismissInactivityModals();
    let inactivitySweepQueued = false;
    new MutationObserver(() => {
        // Sala BBB muta o DOM continuamente; varre no maximo a cada 500 ms.
        if (inactivitySweepQueued) return;
        inactivitySweepQueued = true;
        setTimeout(() => { inactivitySweepQueued = false; dismissInactivityModals(); }, 500);
    }).observe(document.documentElement, {childList: true, subtree: true});
    setInterval(dismissInactivityModals, 2000);

    window.RnpMedia = {version: 1, cameraEntries, audioEntries, audioInfo, describe, answer, close, startPcm, youtubeState};
})();
