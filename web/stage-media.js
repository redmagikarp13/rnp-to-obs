/* Receptores de vídeo do palco, preservados por identidade entre layouts. */
(() => {
    const closeRemote = id => id && fetch('/api/webrtc/close/' + encodeURIComponent(id),
        {method: 'POST', keepalive: true}).catch(() => {});

    // Prefere H264 no vídeo de saída: o CEF/Chromium do OBS decodifica H264 em
    // HARDWARE, enquanto VP8 cai em libvpx (software). A lista completa é só
    // REORDENADA (H264 primeiro, demais como fallback), então nada quebra se o H264
    // não for negociável. Custo de encode na ponte é o mesmo (validado ao vivo).
    function preferirH264(transceiver) {
        try {
            if (!transceiver || !transceiver.setCodecPreferences) return;
            const caps = (window.RTCRtpReceiver && RTCRtpReceiver.getCapabilities)
                ? RTCRtpReceiver.getCapabilities('video') : null;
            if (!caps || !caps.codecs) return;
            const eh264 = c => /h264/i.test(c.mimeType || '');
            const h264 = caps.codecs.filter(eh264);
            if (!h264.length) return;
            transceiver.setCodecPreferences([...h264, ...caps.codecs.filter(c => !eh264(c))]);
        } catch (e) {}
    }

    class RnpStageMedia {
        constructor(container) {
            this.container = container;
            this.entries = new Map();
            this.sources = new Map();
            this.youtube = null;
            this.refreshing = false;
            this.nextRefresh = 0;
            this.closed = false;
            window.addEventListener('pagehide', () => {
                this.closed = true;
                for (const entry of this.entries.values()) this.disconnect(entry);
                this.entries.clear();
            });
        }

        refresh() {
            if (this.closed || this.refreshing || Date.now() < this.nextRefresh) return;
            this.refreshing = true;
            fetch('/api/media', {cache: 'no-store', signal: AbortSignal.timeout(5000)})
                .then(async response => {
                    if (!response.ok) throw new Error('Catálogo de mídia indisponível.');
                    const data = await response.json();
                    if (this.closed) return;
                    this.youtube = data.youtube || null;
                    this.sources = new Map(data.sources.filter(s => s.kind === 'video').map(s => [s.id, s]));
                    this.reconcile();
                }).catch(() => {
                    for (const entry of this.entries.values()) {
                        if (!entry.pc) this.status(entry, 'Aguardando a mídia da sala…');
                    }
                }).finally(() => {
                    this.refreshing = false;
                    this.nextRefresh = Date.now() + 1000;
                });
        }

        status(entry, message) {
            entry.message = message;
            const parent = entry.video.parentElement;
            if (!parent) return;
            let label = parent.querySelector('.stage-media-status');
            if (!label) {
                label = document.createElement('div');
                label.className = 'stage-media-status';
                parent.appendChild(label);
            }
            label.textContent = message;
            label.hidden = !message;
        }

        disconnect(entry) {
            const previous = entry.pc;
            entry.pc = null;
            clearTimeout(entry.timeout);
            previous?.close();
            closeRemote(entry.sessionId);
            entry.sessionId = null;
            entry.video.srcObject?.getTracks().forEach(track => track.stop());
            entry.video.srcObject = null;
        }

        reconcile() {
            if (this.closed) return;
            const videos = [...this.container.querySelectorAll('video[data-source-id]')];
            const wanted = new Set(videos.map(v => v.dataset.sourceId));
            for (const [id, entry] of this.entries) {
                if (!wanted.has(id)) {
                    this.disconnect(entry);
                    this.entries.delete(id);
                }
            }
            for (const placeholder of videos) {
                const id = placeholder.dataset.sourceId;
                let entry = this.entries.get(id);
                if (!entry) {
                    entry = {video: placeholder, pc: null, sessionId: null, retryAt: 0, message: 'Conectando vídeo…'};
                    this.entries.set(id, entry);
                } else if (entry.video !== placeholder) {
                    placeholder.replaceWith(entry.video);
                    entry.video.play().catch(() => {});
                }
                const source = this.sources.get(id);
                if (entry.pc && (!source || entry.trackId !== source.track_id)) this.disconnect(entry);
                if (!source) this.status(entry, 'Aguardando esta câmera…');
                else if (!entry.pc && Date.now() >= entry.retryAt) this.connect(entry, source);
                else this.status(entry, entry.message);
            }
        }

        async connect(entry, source) {
            const peer = new RTCPeerConnection({iceServers: [], bundlePolicy: 'max-bundle'});
            entry.pc = peer;
            entry.trackId = source.track_id;
            const current = () => !this.closed && entry.pc === peer && this.entries.get(source.id) === entry;
            const failed = message => {
                if (!current()) return;
                this.disconnect(entry);
                entry.retryAt = Date.now() + 3000;
                this.status(entry, message);
            };
            this.status(entry, 'Conectando vídeo…');
            preferirH264(peer.addTransceiver('video', {direction: 'recvonly'}));
            peer.ontrack = event => {
                if (!current()) return;
                entry.video.muted = true;
                entry.video.srcObject = event.streams[0] || new MediaStream([event.track]);
                entry.video.play().catch(() => this.status(entry, 'Reprodução bloqueada pelo navegador.'));
            };
            peer.onconnectionstatechange = () => {
                if (!current()) return;
                if (peer.connectionState === 'connected') {
                    clearTimeout(entry.timeout);
                    this.status(entry, '');
                } else if (['failed', 'disconnected', 'closed'].includes(peer.connectionState)) {
                    failed('Conexão interrompida. Reconectando esta câmera…');
                }
            };
            try {
                await peer.setLocalDescription(await peer.createOffer());
                await new Promise((resolve, reject) => {
                    if (peer.iceGatheringState === 'complete') return resolve();
                    const timer = setTimeout(() => {
                        peer.removeEventListener('icegatheringstatechange', changed);
                        reject(new Error('Tempo esgotado na negociação ICE.'));
                    }, 7000);
                    const changed = () => {
                        if (peer.iceGatheringState === 'complete') {
                            clearTimeout(timer);
                            peer.removeEventListener('icegatheringstatechange', changed);
                            resolve();
                        }
                    };
                    peer.addEventListener('icegatheringstatechange', changed);
                });
                if (!current()) return;
                const response = await fetch('/api/webrtc/offer', {
                    method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({source_id: source.id, kind: 'video', type: 'offer', sdp: peer.localDescription.sdp}),
                    signal: AbortSignal.timeout(15000)
                });
                const answer = await response.json();
                if (!current()) { closeRemote(answer.session_id); return; }
                if (!response.ok) throw new Error(typeof answer.detail === 'string' ? answer.detail : 'Falha na sinalização WebRTC.');
                entry.sessionId = answer.session_id;
                await peer.setRemoteDescription({type: answer.type, sdp: answer.sdp});
                entry.timeout = setTimeout(() => {
                    if (peer.connectionState !== 'connected') failed('WebRTC não conectou. Verifique o firewall.');
                }, 15000);
            } catch (error) {
                failed(error.message);
            }
        }
    }
    window.RnpStageMedia = RnpStageMedia;
})();
