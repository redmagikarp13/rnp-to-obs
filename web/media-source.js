(() => {
    if (window.RnpMedia?.version === 1) return;
    const peers = new Map();
    // Modo de desempenho do relay ponte->OBS. 'eco' limita o encode de vídeo para
    // cortar CPU na ponte (encode) e em cada Browser Source (decode); 'full' não
    // impõe tetos. Injetado pelo server (window.RNP_PERF_MODE) e alternado ao vivo
    // por setPerfMode, que re-aplica aos peers já abertos sem reconectar.
    let perfMode = (window.RNP_PERF_MODE === 'eco') ? 'eco' : 'full';
    const ECO_MAX_FPS = 15;
    const ECO_MAX_BITRATE = 1200000;
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
        const nodes = new Map();
        window._rnpAudioNodes = nodes;
        // Canal de picos por trilha: empurra o peakHold de cada canal individual na
        // cadência do sampler (60ms) para a ponte repassar à mesa do dock em tempo
        // real. Sem isto o medidor individual ficaria refém de um poll de 500ms.
        const pws = new WebSocket(url.replace('/ws/pcm_in', '/ws/peaks_in'));
        pws.onerror = () => pws.close();
        // --- Diagnostico de cadencia (Plano A do docs/AUDIO_ESTABILIDADE_2026-11.md) ---
        // Separar GERACAO (relógio do worklet) de ENTREGA (thread que recebe o
        // postMessage e da o ws.send). Quando o Web Worker dedicado está ativo, o
        // caminho AudioWorklet -> MessageChannel -> Worker -> WebSocket não passa
        // pela main thread do BBB (eliminando buracos/rajadas quando o React trava).
        const PCM_STATS = {
            gerados: 0, enviados_ws: 0, descartados_guarda: 0,
            gaps_acum: 0,
            entrega: [], gaps_entrega: 0, t_ultimo: 0,
            t_audio_i: 0, t_audio_f: 0, w_i: 0, w_f: 0,
            via_worker: false,
        };
        window._rnpPcmStats = PCM_STATS;
        let ultimoResumoWorker = null;
        let ws = null;
        let pcmWorker = null;
        let capNode = null;
        let encerrado = false;
        let interval = null;
        let peakInterval = null;

        function encerrarCaptura() {
            if (encerrado) return;
            encerrado = true;
            if (interval) clearInterval(interval);
            if (peakInterval) clearInterval(peakInterval);
            try { pws.close(); } catch (e) {}
            if (pcmWorker) {
                try { pcmWorker.postMessage({type: 'close'}); } catch (e) {}
                try { pcmWorker.terminate(); } catch (e) {}
                pcmWorker = null;
            }
            if (ws) {
                try { ws.onclose = null; ws.close(); } catch (e) {}
                ws = null;
            }
            for (const node of nodes.values()) {
                node.input.disconnect(); node.gain.disconnect(); node.analyser.disconnect();
            }
            nodes.clear(); master.disconnect();
            if (capNode) capNode.disconnect();
            if (window._pcmProc) { window._pcmProc.disconnect(); window._pcmProc = null; }
            ctx.close();
            window._pcmCaptureActive = false;
            window._rnpApplyAudioControls = null;
            setTimeout(() => startPcm(url), 2000);
        }

        function iniciarWsMainThread() {
            if (ws || encerrado) return;
            ws = new WebSocket(url);
            ws.binaryType = 'arraybuffer';
            ws.onclose = encerrarCaptura;
            ws.onerror = () => { try { ws.close(); } catch (e) {} };
        }

        function registrarChunk(t_audio) {
            const agora = performance.now();
            PCM_STATS.gerados++;
            if (PCM_STATS.t_ultimo) {
                const d = agora - PCM_STATS.t_ultimo;
                PCM_STATS.entrega.push(d);
                if (PCM_STATS.entrega.length > 4096) PCM_STATS.entrega.shift();
                if (d > 60) { PCM_STATS.gaps_entrega++; PCM_STATS.gaps_acum++; }
            } else if (t_audio !== undefined) {
                PCM_STATS.t_audio_i = t_audio; PCM_STATS.w_i = agora / 1000;
            }
            PCM_STATS.t_ultimo = agora;
            if (t_audio !== undefined) { PCM_STATS.t_audio_f = t_audio; PCM_STATS.w_f = agora / 1000; }
        }
        function empurrar(chunk, t_audio) {
            registrarChunk(t_audio);
            if (ws && ws.readyState === WebSocket.OPEN && ws.bufferedAmount < 48000) {
                ws.send(chunk);
                PCM_STATS.enviados_ws++;
            } else {
                PCM_STATS.descartados_guarda++;
            }
        }
        // Captura em AudioWorklet: o processador roda na thread de áudio e envia cada
        // bloco de 2048 amostras por MessagePort direto ao Web Worker dedicado (sem
        // passar pela main thread do BBB). Se Worker não estiver disponível (ex.:
        // about:blank), cai automaticamente para entrega via main thread.
        const WORKLET_SRC = `class PcmCapture extends AudioWorkletProcessor {
            constructor() {
                super();
                this.buf = new Float32Array(2048);
                this.n = 0;
                this.outPort = this.port;
                this.port.onmessage = (e) => {
                    if (e.data && 'port' in e.data) {
                        this.outPort = e.data.port || this.port;
                    }
                };
            }
            process(inputs) {
                const ch = inputs[0] && inputs[0][0];
                if (ch) {
                    let off = 0;
                    while (off < ch.length) {
                        const take = Math.min(ch.length - off, this.buf.length - this.n);
                        this.buf.set(ch.subarray(off, off + take), this.n);
                        this.n += take; off += take;
                        if (this.n === this.buf.length) {
                            this.outPort.postMessage({b: this.buf, t: currentTime}, [this.buf.buffer]);
                            this.buf = new Float32Array(2048); this.n = 0;
                        }
                    }
                }
                return true;
            }
        }
        registerProcessor('pcm-capture', PcmCapture);`;

        const WORKER_SRC = `
            let ws = null;
            let port = null;
            let timer = null;
            const S = {
                gerados: 0, enviados_ws: 0, descartados_guarda: 0,
                gaps_acum: 0, entrega: [], gaps_entrega: 0, t_ultimo: 0,
                t_audio_i: 0, t_audio_f: 0, w_i: 0, w_f: 0,
            };
            function registrar(t_audio) {
                const agora = performance.now();
                S.gerados++;
                if (S.t_ultimo) {
                    const d = agora - S.t_ultimo;
                    S.entrega.push(d);
                    if (S.entrega.length > 4096) S.entrega.shift();
                    if (d > 60) { S.gaps_entrega++; S.gaps_acum++; }
                } else if (t_audio !== undefined) {
                    S.t_audio_i = t_audio; S.w_i = agora / 1000;
                }
                S.t_ultimo = agora;
                if (t_audio !== undefined) { S.t_audio_f = t_audio; S.w_f = agora / 1000; }
            }
            function resumo() {
                const v = S.entrega.slice().sort((a, b) => a - b), n = v.length;
                const r = {
                    gerados: S.gerados,
                    enviados_ws: S.enviados_ws,
                    descartados_guarda: S.descartados_guarda,
                    gaps_entrega_gt_60ms: S.gaps_entrega,
                    gaps_entrega_acumulado: S.gaps_acum,
                    via_worker: true,
                };
                if (n) {
                    r.entrega_mediana_ms = +v[n >> 1].toFixed(1);
                    r.entrega_p95_ms = +v[Math.min(n - 1, Math.floor(n * 0.95))].toFixed(1);
                    r.entrega_max_ms = +v[n - 1].toFixed(1);
                    r.entrega_amostras = n;
                }
                const spanAudio = S.t_audio_f - S.t_audio_i;
                const spanParede = S.w_f - S.w_i;
                if (spanAudio > 0 && spanParede > 0) r.clock_ratio = +(spanAudio / spanParede).toFixed(3);
                S.entrega.length = 0;
                S.gaps_entrega = 0;
                return r;
            }
            self.onmessage = (ev) => {
                const msg = ev.data || {};
                if (msg.type === 'init') {
                    port = msg.port;
                    ws = new WebSocket(msg.url);
                    ws.binaryType = 'arraybuffer';
                    ws.onclose = () => self.postMessage({type: 'closed'});
                    ws.onerror = () => { try { ws.close(); } catch (e) {} };
                    port.onmessage = (e) => {
                        const d = e.data || {};
                        registrar(d.t);
                        if (ws && ws.readyState === WebSocket.OPEN && ws.bufferedAmount < 48000) {
                            ws.send(d.b);
                            S.enviados_ws++;
                        } else {
                            S.descartados_guarda++;
                        }
                    };
                    timer = setInterval(() => {
                        self.postMessage({type: 'stats', stats: resumo()});
                    }, 1000);
                } else if (msg.type === 'close') {
                    if (timer) clearInterval(timer);
                    try { if (port) port.close(); } catch (e) {}
                    try { if (ws) { ws.onclose = null; ws.close(); } } catch (e) {}
                }
            };
        `;

        let workerChan = null;
        if (ctx.audioWorklet && typeof Worker === 'function' && typeof MessageChannel === 'function' && location.origin !== 'null') {
            try {
                const wBlob = new Blob([WORKER_SRC], {type: 'application/javascript'});
                const wUrl = URL.createObjectURL(wBlob);
                pcmWorker = new Worker(wUrl);
                URL.revokeObjectURL(wUrl);
                workerChan = new MessageChannel();
                PCM_STATS.via_worker = true;
                pcmWorker.onmessage = (ev) => {
                    const d = ev.data || {};
                    if (d.type === 'stats' && d.stats) {
                        ultimoResumoWorker = d.stats;
                        PCM_STATS.gerados = d.stats.gerados;
                        PCM_STATS.enviados_ws = d.stats.enviados_ws;
                        PCM_STATS.descartados_guarda = d.stats.descartados_guarda;
                        PCM_STATS.gaps_acum = d.stats.gaps_entrega_acumulado;
                    } else if (d.type === 'closed') {
                        encerrarCaptura();
                    }
                };
                pcmWorker.onerror = () => {
                    if (!pcmWorker) return;
                    try { pcmWorker.terminate(); } catch (e) {}
                    pcmWorker = null;
                    workerChan = null;
                    PCM_STATS.via_worker = false;
                    if (capNode) {
                        try { capNode.port.postMessage({port: null}); } catch (e) {}
                    }
                    iniciarWsMainThread();
                };
                pcmWorker.postMessage({type: 'init', url, port: workerChan.port2}, [workerChan.port2]);
            } catch (e) {
                pcmWorker = null;
                workerChan = null;
                PCM_STATS.via_worker = false;
            }
        }
        if (!pcmWorker) {
            iniciarWsMainThread();
        }

        const ligarFallback = () => {
            // Navegador sem AudioWorklet OU página fora de contexto seguro
            // (about:blank tem audioWorklet undefined): volta ao ScriptProcessor.
            if (pcmWorker) {
                try { pcmWorker.postMessage({type: 'close'}); pcmWorker.terminate(); } catch (e) {}
                pcmWorker = null;
                workerChan = null;
                PCM_STATS.via_worker = false;
            }
            iniciarWsMainThread();
            const proc = ctx.createScriptProcessor(2048, 1, 1);
            proc.onaudioprocess = ev => empurrar(ev.inputBuffer.getChannelData(0).buffer);
            master.connect(proc);
            proc.connect(ctx.destination);
            window._pcmProc = proc;
        };
        if (!ctx.audioWorklet) {
            ligarFallback();
        } else {
            const blobUrl = URL.createObjectURL(new Blob([WORKLET_SRC], {type: 'application/javascript'}));
            ctx.audioWorklet.addModule(blobUrl).then(() => {
                URL.revokeObjectURL(blobUrl);
                capNode = new AudioWorkletNode(ctx, 'pcm-capture', {
                    numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1],
                });
                capNode.port.onmessage = e => empurrar(e.data.b, e.data.t);
                if (pcmWorker && workerChan) {
                    capNode.port.postMessage({port: workerChan.port1}, [workerChan.port1]);
                }
                master.connect(capNode);
                capNode.connect(ctx.destination);
            }).catch(ligarFallback);
        }
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
        interval = setInterval(scan, 500);
        // Pico segurado com decaimento: o pico instantâneo (~42ms) some entre sílabas
        // e faz o indicador de fala piscar. Aqui seguramos o pico recente e decaímos
        // suavemente (tau=300ms), então quem fala continua "aceso" nas pausas curtas.
        const PEAK_TAU = 0.30;
        // Relatorio de diagnostico a cada ~2 s, carreado na MESMA janela dos picos
        // (nada de WebSocket nova nem de rota extra no caminho quente do audio). A
        // ponte remove a chave '_pcm' antes de repassar os picos ao dock.
        let ultimoRelato = 0;
        function resumoPcmStats() {
            if (PCM_STATS.via_worker && ultimoResumoWorker) {
                return ultimoResumoWorker;
            }
            const v = PCM_STATS.entrega.slice().sort((a, b) => a - b), n = v.length;
            const r = {
                gerados: PCM_STATS.gerados,
                enviados_ws: PCM_STATS.enviados_ws,
                descartados_guarda: PCM_STATS.descartados_guarda,
                gaps_entrega_gt_60ms: PCM_STATS.gaps_entrega,
                // Acumulado desde o inicio da captura: so assim da para comparar com
                // o gap acumulado do servidor na MESMA base (a janela de 2 s reinicia).
                gaps_entrega_acumulado: PCM_STATS.gaps_acum,
                via_worker: false,
            };
            if (n) {
                r.entrega_mediana_ms = +v[n >> 1].toFixed(1);
                r.entrega_p95_ms = +v[Math.min(n - 1, Math.floor(n * 0.95))].toFixed(1);
                r.entrega_max_ms = +v[n - 1].toFixed(1);
                r.entrega_amostras = n;
            }
            // 1,0 = worklet renderizou em tempo real Wall-clock; <1 = thread de audio
            // nao deu conta (a causa que o note suspeitava ser o sink nulo do headless).
            const spanAudio = PCM_STATS.t_audio_f - PCM_STATS.t_audio_i;
            const spanParede = PCM_STATS.w_f - PCM_STATS.w_i;
            if (spanAudio > 0 && spanParede > 0) r.clock_ratio = +(spanAudio / spanParede).toFixed(3);
            PCM_STATS.entrega.length = 0;
            PCM_STATS.gaps_entrega = 0;
            return r;
        }
        const samplePeaks = () => {
            const now = performance.now();
            for (const node of nodes.values()) {
                node.analyser.getFloatTimeDomainData(node.samples);
                let instant = 0;
                for (const value of node.samples) { const a = Math.abs(value); if (a > instant) instant = a; }
                const dt = node.lastPeakAt ? (now - node.lastPeakAt) / 1000 : 0;
                node.lastPeakAt = now;
                node.peakHold = Math.max(instant, node.peakHold * Math.exp(-dt / PEAK_TAU));
            }
            const deveRelatar = now - ultimoRelato > 2000;
            if (pws.readyState === WebSocket.OPEN && (nodes.size || deveRelatar)) {
                const picos = {};
                for (const node of nodes.values()) {
                    if (node.entryId) picos[node.entryId] = +node.peakHold.toFixed(4);
                }
                if (deveRelatar) {
                    ultimoRelato = now;
                    picos._pcm = resumoPcmStats();
                }
                pws.send(JSON.stringify(picos));
            }
        };
        peakInterval = setInterval(samplePeaks, 60);
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

    // Tetos de encode do relay. limitarClone age sobre a trilha clonada (constraints
    // de captura); limitarSender age sobre o RTCRtpSender (encodings/bitrate). Ambos
    // são tolerantes a falha: se o navegador rejeitar, o relay segue sem teto.
    function limitarClone(clone) {
        try {
            if (perfMode === 'eco') {
                clone.applyConstraints({frameRate: {max: ECO_MAX_FPS}, width: {ideal: 1280}, height: {ideal: 720}}).catch(() => {});
            } else {
                clone.applyConstraints({frameRate: {max: 60}}).catch(() => {});
            }
        } catch (e) {}
    }

    function limitarSender(pc) {
        try {
            for (const sender of pc.getSenders()) {
                if (!sender.track || sender.track.kind !== 'video') continue;
                const p = sender.getParameters();
                p.encodings = (p.encodings && p.encodings.length) ? p.encodings : [{}];
                const enc = p.encodings[0];
                if (perfMode === 'eco') {
                    enc.maxFramerate = ECO_MAX_FPS;
                    enc.maxBitrate = ECO_MAX_BITRATE;
                    p.degradationPreference = 'maintain-resolution';
                } else {
                    delete enc.maxFramerate;
                    delete enc.maxBitrate;
                    delete enc.scaleResolutionDownBy;
                    delete p.degradationPreference;
                }
                sender.setParameters(p).catch(() => {});
            }
        } catch (e) {}
    }

    function setPerfMode(mode) {
        perfMode = (mode === 'eco') ? 'eco' : 'full';
        window.RNP_PERF_MODE = perfMode;
        for (const entry of peers.values()) {
            limitarClone(entry.clone);
            limitarSender(entry.pc);
        }
    }

    async function answer({session_id, source_id, kind, sdp}) {
        const original = sourceTrack(source_id, kind);
        if (!original) throw new Error('Fonte indisponível ou sem trilha individual.');
        const maxPeers = Number(window.RNP_MAX_PEERS) || 0;
        if (maxPeers > 0 && peers.size >= maxPeers) throw new Error(`Limite de ${maxPeers} receptores experimentais atingido.`);
        const pc = new RTCPeerConnection({iceServers: [], bundlePolicy: 'max-bundle'});
        window.RnpConnection?.ignore(pc); // Relay local para o OBS, não é a entrada RNP.
        const clone = original.clone();
        limitarClone(clone);
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
            limitarSender(pc);
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
    const inactivityModalRe = /inactivityWarning|verificação de atividade|atividade do usuário|inatividade|ainda est[aá] na sessão|still.?here/i;
    const inactivityConfirmRe = /^(verificar|confirmar|estou aqui|sim[,\s]+ainda estou aqui|i'?m still here|stay in meeting)$/i;
    const inactivityDangerRe = /logout|sair|leave|encerrar|disconnect/i;
    const micPopupRe = /ativar seu microfone|unmute your microphone|activar su micr[oó]fono|abrir seu microfone/i;
    function hideMicPopups() {
        if (!document.getElementById('rnp-hide-mic-popup-style')) {
            const st = document.createElement('style');
            st.id = 'rnp-hide-mic-popup-style';
            st.textContent = `
                [data-test="muteModal"],
                [data-test="unmuteModal"],
                [class*="muteModal" i],
                [class*="muteWarning" i],
                [role="tooltip"] {
                    display: none !important;
                    visibility: hidden !important;
                    opacity: 0 !important;
                    pointer-events: none !important;
                }
            `;
            (document.head || document.documentElement).appendChild(st);
        }
        const stopSelector = 'body, html, main, #app, #layout, #presentationContainer, [data-test="presentationContainer"], [data-test="presentationArea"], [class*="presentationWrapper"], [data-test="whiteboard"], #svgContainer';
        for (const el of document.querySelectorAll('div, span, p, aside, section')) {
            if (el.matches(stopSelector) || el.closest('[data-test="chatMessages"], #chat-messages, [data-test="chatMessageItem"]')) continue;
            const txt = (el.textContent || '').replace(/\s+/g, ' ').trim();
            if (!txt || txt.length > 140 || !micPopupRe.test(txt)) continue;
            if (el.querySelector('video, #presentationContainer, [data-test="presentationContainer"], [data-test="whiteboard"]')) continue;
            let target = el;
            while (
                target.parentElement &&
                !target.parentElement.matches(stopSelector) &&
                !target.parentElement.querySelector('video, #presentationContainer, [data-test="presentationContainer"], [data-test="whiteboard"]')
            ) {
                const pTxt = (target.parentElement.textContent || '').replace(/\s+/g, ' ').trim();
                if (pTxt.length > txt.length + 10 || !micPopupRe.test(pTxt)) break;
                target = target.parentElement;
            }
            if (target.style.display !== 'none') {
                target.style.setProperty('display', 'none', 'important');
                target.style.setProperty('visibility', 'hidden', 'important');
                target.style.setProperty('opacity', '0', 'important');
                window._rnpHiddenMicPopups = (window._rnpHiddenMicPopups || 0) + 1;
            }
        }
    }
    function dismissInactivityModals() {
        hideMicPopups();
        const roots = [document, document.querySelector('#videoPlayer')?.shadowRoot].filter(Boolean);
        for (const root of roots) {
            const hosts = [...root.querySelectorAll('[data-test*="inactivity" i], [data-test="inactivityWarningModal"], .modal, [role="dialog"], [role="alertdialog"]')];
            for (const host of hosts) {
                // Blindagem estrita: NUNCA interagir com qualquer contêiner de enquete/votação!
                if (host.matches('#polling-container, [data-test*="poll" i], [class*="poll" i], aside[class*="poll" i]')) continue;
                if (host.closest('#polling-container, [data-test*="poll" i], [class*="poll" i], aside[class*="poll" i]')) continue;

                const hostAttr = host.getAttribute('data-test') || '';
                const hostTxt = host.textContent || '';
                const combined = (hostAttr + ' ' + hostTxt).toLowerCase();
                if (combined.includes('enquete') || combined.includes('polling') || combined.includes('opções da enquete') || combined.includes('pergunta da enquete')) continue;

                if (!inactivityModalRe.test(hostAttr + ' ' + hostTxt)) continue;

                const buttons = [...host.querySelectorAll('button, a.btn, [role="button"], input[type="button"], input[type="submit"]')];
                for (const b of buttons) {
                    // NUNCA clicar em opções ou botões de enquete!
                    if (b.matches('[data-test*="poll" i], [class*="poll" i], [data-test="pollAnswerOption"]')) continue;
                    if (b.closest('#polling-container, [data-test*="poll" i], [class*="poll" i], aside[class*="poll" i]')) continue;

                    const dt = b.getAttribute('data-test') || '';
                    const txt = (b.textContent || b.value || '').trim();
                    if (inactivityDangerRe.test(dt) || /^(sair|logout|leave|encerrar)$/i.test(txt)) continue;
                    if (/inactivityWarningConfirmBtn|confirmInactivity/i.test(dt) || inactivityConfirmRe.test(txt)) {
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

    window.RnpMedia = {version: 1, cameraEntries, audioEntries, audioInfo, describe, answer, close, startPcm, youtubeState, setPerfMode};
})();
