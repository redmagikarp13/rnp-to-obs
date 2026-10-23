/* Telemetria de entrada da RNP. Instalada antes do cliente BBB, sem depender do DOM.
   Nunca publica SDP, endereços ICE ou identificadores dos participantes. */
(() => {
    if (window.RnpConnection) return;
    const peers = new Map();
    const NativePeer = window.RTCPeerConnection;
    const empty = () => ({rtt_ms: null, jitter_ms: null, loss_pct: null, degraded: false});
    const valid = n => typeof n === 'number' && Number.isFinite(n) && n >= 0;
    let latest = empty(), measuredAt = null, lastAttempt = -Infinity, pending = null;

    if (NativePeer) {
        const TrackedPeer = new Proxy(NativePeer, {
            construct(target, args, newTarget) {
                const pc = Reflect.construct(target, args, newTarget);
                const entry = {previous: new Map(), receiving: false};
                peers.set(pc, entry);
                pc.addEventListener('track', () => { entry.receiving = true; });
                pc.addEventListener('connectionstatechange', () => {
                    if (pc.connectionState === 'closed') peers.delete(pc);
                });
                return pc;
            }
        });
        window.RTCPeerConnection = TrackedPeer;
        if (window.webkitRTCPeerConnection === NativePeer) window.webkitRTCPeerConnection = TrackedPeer;
    }

    async function collect() {
        const result = empty();
        const maximum = (key, value) => {
            if (valid(value)) result[key] = Math.max(result[key] ?? 0, value);
        };
        await Promise.all([...peers].map(async ([pc, entry]) => {
            if (pc.connectionState === 'closed') { peers.delete(pc); return; }
            if (['disconnected', 'failed'].includes(pc.connectionState)) {
                if (entry.receiving) result.degraded = true;
                entry.previous.clear();
                return;
            }
            let timer;
            try {
                const reports = await Promise.race([
                    Promise.resolve().then(() => pc.getStats()),
                    new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('stats timeout')), 1000); })
                ]);
                if (!peers.has(pc) || pc.connectionState === 'closed') return;
                const next = new Map();
                for (const report of reports.values()) {
                    if (report.type !== 'inbound-rtp' || !['audio', 'video'].includes(report.kind || report.mediaType)) continue;
                    entry.receiving = true;
                    const previous = entry.previous.get(report.id);
                    next.set(report.id, {timestamp: report.timestamp,
                        received: report.packetsReceived, lost: report.packetsLost});
                    if (!valid(report.timestamp) || !valid(report.packetsReceived)) continue;
                    if (previous && report.timestamp <= previous.timestamp) continue;
                    const received = previous ? report.packetsReceived - previous.received : report.packetsReceived;
                    // Sem pacotes novos, jitter/RTT antigos não atestam mídia saudável.
                    const lost = previous && Number.isFinite(report.packetsLost) && Number.isFinite(previous.lost)
                        ? Math.max(0, report.packetsLost - previous.lost) : null;
                    if (received === 0 && !(lost > 0)) continue;
                    if (!previous && received === 0) continue;
                    if (valid(report.jitter)) maximum('jitter_ms', report.jitter * 1000);
                    const transport = reports.get(report.transportId);
                    const pair = reports.get(transport?.selectedCandidatePairId);
                    if (pair?.state === 'succeeded' && valid(pair.currentRoundTripTime)) {
                        maximum('rtt_ms', pair.currentRoundTripTime * 1000);
                    }
                    // Perda na janela, nunca o percentual acumulado da sessão.
                    // Uma trilha ruim não pode ser diluída pelas demais: usamos o pior valor.
                    if (previous && received >= 0 && lost !== null && received + lost > 0) {
                        maximum('loss_pct', lost / (received + lost) * 100);
                    }
                }
                entry.previous = next;
            } catch (_) {
                if (peers.has(pc)) result.unavailable = true;
            } finally {
                clearTimeout(timer);
            }
        }));
        latest = result;
        measuredAt = performance.now();
        return {...latest};
    }

    function sample() {
        if (!pending) {
            lastAttempt = performance.now();
            pending = collect().finally(() => { pending = null; });
        }
        return pending;
    }

    function snapshot() {
        if (performance.now() - lastAttempt >= 2000) sample();
        return {...latest, age_ms: measuredAt === null ? null : performance.now() - measuredAt};
    }

    window.RnpConnection = {sample, snapshot, ignore: pc => peers.delete(pc)};
})();
