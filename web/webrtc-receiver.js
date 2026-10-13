(() => {
    const $ = id => document.getElementById(id);
    const params = new URLSearchParams(location.search);
    const requested = params.get('source');
    const requestedKind = params.get('kind') || 'video';
    const initialVolume = Math.max(0, Math.min(2, Number(params.get('volume') ?? 1) || 0));
    if (params.get('overlay') === '1') document.body.classList.add('overlay');
    let sources = [], pc = null, sessionId = null, generation = 0, desired = !!requested;
    let currentId = null, connecting = false, retryAfter = 0, refreshBusy = false;
    let audioContext = null, audioInput = null, gain = null, analyser = null;
    let lastStats = null, selectedId = null, currentTrackId = null;
    const status = text => { $('status').textContent = text; };
    const closeRemote = id => id && fetch('/api/webrtc/close/' + encodeURIComponent(id), {method: 'POST', keepalive: true}).catch(() => {});

    function disconnect() {
        generation++;
        const previous = pc;
        pc = null;
        previous?.close();
        closeRemote(sessionId);
        sessionId = null;
        currentId = null;
        currentTrackId = null;
        connecting = false;
        lastStats = null;
        audioInput?.disconnect();
        audioInput = null;
        $('media').srcObject?.getTracks().forEach(t => t.stop());
        $('media').srcObject = null;
        document.body.classList.remove('connected');
    }

    function audioGraph() {
        if (!audioContext) {
            audioContext = new AudioContext({sampleRate: 48000});
            gain = audioContext.createGain();
            analyser = audioContext.createAnalyser();
            gain.connect(analyser);
            analyser.connect(audioContext.destination);
        }
        audioContext.resume().catch(() => status('Clique em Ativar áudio para liberar a reprodução.'));
    }

    function applyGain(source) {
        if (gain && audioContext) {
            gain.gain.setTargetAtTime(source?.muted ? 0 : (source?.volume ?? 1) * initialVolume, audioContext.currentTime, 0.01);
        }
    }

    async function connect() {
        const source = sources.find(s => s.id === $('source').value);
        if (!source) { status('Fonte indisponível. Nenhum outro participante será usado no lugar dela.'); return; }
        disconnect();
        desired = true;
        connecting = true;
        currentId = source.id;
        currentTrackId = source.track_id;
        selectedId = source.id;
        const mine = generation;
        const peer = new RTCPeerConnection({iceServers: [], bundlePolicy: 'max-bundle'});
        pc = peer;
        peer.addTransceiver(source.kind, {direction: 'recvonly'});
        $('listen').hidden = source.kind !== 'audio';
        $('media').hidden = source.kind === 'audio';
        if (source.kind === 'audio') audioGraph();
        status('Negociando WebRTC local…');
        peer.ontrack = event => {
            if (mine !== generation) return;
            const stream = event.streams[0] || new MediaStream([event.track]);
            // O sink silenciado inicia o playout RTP no Chromium; só o grafo emite som.
            $('media').srcObject = stream;
            $('media').play().catch(() => status('A reprodução foi bloqueada pelo navegador.'));
            if (source.kind === 'audio') {
                audioGraph();
                audioInput?.disconnect();
                audioInput = audioContext.createMediaStreamSource(stream);
                audioInput.connect(gain);
                applyGain(source);
            }
        };
        peer.onconnectionstatechange = () => {
            if (mine !== generation) return;
            if (peer.connectionState === 'connected') {
                connecting = false;
                document.body.classList.add('connected');
                status(source.kind === 'audio' ? 'Áudio individual conectado.' : 'Vídeo WebRTC conectado.');
            } else if (['failed', 'disconnected', 'closed'].includes(peer.connectionState)) {
                disconnect();
                retryAfter = Date.now() + 3000;
                status('Conexão interrompida. Aguardando a mesma fonte…');
            }
        };
        try {
            await peer.setLocalDescription(await peer.createOffer());
            await new Promise((resolve, reject) => {
                if (peer.iceGatheringState === 'complete') return resolve();
                const timer = setTimeout(() => {peer.removeEventListener('icegatheringstatechange', changed); reject(new Error('Tempo esgotado no ICE local.'));}, 7000);
                const changed = () => {
                    if (peer.iceGatheringState === 'complete') {
                        clearTimeout(timer);
                        peer.removeEventListener('icegatheringstatechange', changed);
                        resolve();
                    }
                };
                peer.addEventListener('icegatheringstatechange', changed);
            });
            if (mine !== generation) return;
            const response = await fetch('/api/webrtc/offer', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({source_id: source.id, kind: source.kind, type: 'offer', sdp: peer.localDescription.sdp}),
                signal: AbortSignal.timeout(15000)
            });
            const answer = await response.json();
            if (mine !== generation) {closeRemote(answer.session_id); return;}
            if (!response.ok) throw new Error(typeof answer.detail === 'string' ? answer.detail : 'Falha na sinalização.');
            sessionId = answer.session_id;
            await peer.setRemoteDescription({type: answer.type, sdp: answer.sdp});
            setTimeout(() => {
                if (mine === generation && peer.connectionState !== 'connected') {
                    disconnect(); retryAfter = Date.now() + 3000;
                    status('WebRTC não conectou. Verifique o firewall e tente novamente.');
                }
            }, 15000);
        } catch (error) {
            if (mine !== generation) return;
            disconnect(); retryAfter = Date.now() + 3000;
            status(error.message);
        }
    }

    async function refresh() {
        if (refreshBusy) return;
        refreshBusy = true;
        try {
            const response = await fetch('/api/media', {cache: 'no-store', signal: AbortSignal.timeout(5000)});
            if (!response.ok) throw new Error('Sala temporariamente indisponível.');
            const data = await response.json();
            sources = requested ? data.sources.filter(s => s.kind === requestedKind) : data.sources;
            const oldValue = $('source').value;
            let selected = selectedId || oldValue;
            if (requested) {
                selected = requested;
                if (requestedKind === 'audio') {
                    selected = data.sources.find(s => s.id === requested && s.kind === 'video')?.audio_source_id || requested;
                }
            }
            const signature = JSON.stringify(sources.map(s => [s.id, s.name, s.kind]));
            if ($('source').dataset.signature !== signature) {
                $('source').replaceChildren();
                for (const s of sources) {
                    const option = document.createElement('option');
                    option.value = s.id;
                    option.textContent = `${s.kind === 'audio' ? 'Áudio individual' : 'Vídeo'} — ${s.name}`;
                    $('source').appendChild(option);
                }
                $('source').dataset.signature = signature;
            }
            if (selected) $('source').value = selected;
            $('start').disabled = !$('source').value;
            $('copy').disabled = !$('source').value;
            const current = sources.find(s => s.id === currentId);
            if (pc && (!current || current.track_id !== currentTrackId)) {
                disconnect(); status('A fonte saiu da sala. Aguardando sua reconexão…');
            }
            applyGain(current);
            if (!sources.length) status('Conecte a sala no painel deste servidor.');
            else if (desired && !$('source').value) status('Trilha solicitada indisponível. O áudio master não será usado como substituto.');
            else if (desired && !pc && !connecting && Date.now() >= retryAfter) await connect();
            else if (!desired && !pc) status('Selecione uma fonte e clique em Conectar.');
        } catch (error) {
            // Áudio individual falha fechado se não houver confirmação dos controles.
            if (gain && audioContext) gain.gain.setTargetAtTime(0, audioContext.currentTime, 0.01);
            status(error.message);
        } finally {refreshBusy = false;}
    }

    async function metrics() {
        if (!pc || pc.connectionState !== 'connected') return;
        const peer = pc;
        try {
            const reports = await peer.getStats();
            if (pc !== peer) return;
            const rows = [];
            reports.forEach(r => {
                if (r.type !== 'inbound-rtp') return;
                const codec = reports.get(r.codecId)?.mimeType || '?';
                const dt = lastStats ? (r.timestamp - lastStats.timestamp) / 1000 : 0;
                const fps = dt > 0 ? ((r.framesDecoded || 0) - (lastStats.framesDecoded || 0)) / dt : 0;
                rows.push(`${codec} · ${fps.toFixed(1)} FPS decodificados · descartados ${r.framesDropped || 0} · jitter ${((r.jitter || 0) * 1000).toFixed(1)} ms`);
                lastStats = r;
            });
            $('metrics').textContent = rows.join(' | ');
        } catch (_) {}
    }

    $('start').onclick = connect;
    $('stop').onclick = () => {desired = false; disconnect(); status('Parado.');};
    $('source').onchange = () => {selectedId = $('source').value; desired = false; disconnect();};
    $('listen').onclick = audioGraph;
    $('copy').onclick = async () => {
        const source = sources.find(s => s.id === $('source').value);
        if (!source) return;
        const url = new URL('/webrtc', location.origin);
        url.search = new URLSearchParams({source: source.id, kind: source.kind, overlay: '1'});
        try {await navigator.clipboard.writeText(url.href); status('Link copiado. No OBS, use uma fonte Navegador.');}
        catch (_) {status(url.href);}
    };
    window.addEventListener('pagehide', () => {desired = false; disconnect(); audioContext?.close();});
    setInterval(refresh, 1000);
    setInterval(metrics, 1000);
    refresh();
})();
