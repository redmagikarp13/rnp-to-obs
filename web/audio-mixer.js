(() => {
    const container = document.getElementById('individualChannelsContainer');
    const placeholder = document.getElementById('noIndividualChannelsPlaceholder');
    if (!container) return;
    const rows = new Map();
    let busy = false;

    function makeRow(channel) {
        const row = document.createElement('div');
        row.className = 'audio-channel-strip';
        row.dataset.audioId = channel.id;
        row.innerHTML = `<div class="channel-header"><span class="channel-title"></span><span class="channel-tag">Áudio individual real</span></div>
            <div class="channel-meter-box"><div class="vu-track-vertical"><div class="vu-fill-vertical"></div></div></div>
            <output class="meter-db-readout">−∞ dB</output>
            <label>Volume <input type="range" min="0" max="2" step="0.05" value="1" aria-label="Volume individual"></label>
            <button class="btn-ctrl" data-action="mute">Silenciar</button>
            <button class="channel-btn-copy" data-action="copy">Copiar link OBS</button>
            <span role="status"></span>`;
        const state = {row, channel, pending: false};
        const slider = row.querySelector('input');
        const mute = row.querySelector('[data-action=mute]');
        async function update(muted, volume) {
            state.pending = true; slider.disabled = true; mute.disabled = true;
            const message = row.querySelector('[role=status]');
            try {
                const response = await fetch('/api/audio/control', {method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({source_id: channel.id, muted, volume}),
                    signal: AbortSignal.timeout(5000)});
                if (!response.ok) throw new Error('Não foi possível aplicar o controle.');
                state.channel = {...state.channel, muted, volume};
                message.textContent = '';
            } catch (e) {message.textContent = e.message;}
            finally {state.pending = false; slider.disabled = false; mute.disabled = false; refresh();}
        }
        slider.onchange = () => update(!!state.channel.muted, Number(slider.value));
        mute.onclick = () => update(!state.channel.muted, state.channel.volume ?? 1);
        row.querySelector('[data-action=copy]').onclick = async () => {
            const url = new URL('/webrtc', location.origin);
            url.search = new URLSearchParams({source: channel.id, kind: 'audio', overlay: '1'});
            try {await navigator.clipboard.writeText(url.href); row.querySelector('[role=status]').textContent = 'Link copiado.';}
            catch (_) {row.querySelector('[role=status]').textContent = url.href;}
        };
        container.appendChild(row);
        return state;
    }

    async function refresh() {
        if (busy) return;
        busy = true;
        try {
            const response = await fetch('/api/media', {cache: 'no-store', signal: AbortSignal.timeout(5000)});
            if (!response.ok) throw new Error('Mídia temporariamente indisponível.');
            const data = await response.json();
            const channels = data.sources.filter(c => c.kind === 'audio' && c.individual);
            for (const [id, state] of rows) {
                if (!channels.some(c => c.id === id)) {state.row.remove(); rows.delete(id);}
            }
            if (placeholder) {
                placeholder.style.display = channels.length ? 'none' : 'flex';
                placeholder.textContent = 'Sem trilhas individuais identificadas. O master não é um canal individual.';
            }
            for (const channel of channels) {
                let state = rows.get(channel.id);
                if (!state) {state = makeRow(channel); rows.set(channel.id, state);}
                const row = state.row;
                row.querySelector('.channel-title').textContent = channel.name;
                if (!state.pending) {
                    state.channel = channel;
                    const slider = row.querySelector('input');
                    if (document.activeElement !== slider) slider.value = channel.volume ?? 1;
                    row.querySelector('[data-action=mute]').textContent = channel.muted ? 'Reativar áudio' : 'Silenciar';
                }
            }
        } catch (e) {
            for (const state of rows.values()) {state.row.remove();}
            rows.clear();
            if (placeholder) {placeholder.style.display = 'flex'; placeholder.textContent = e.message;}
        } finally {busy = false;}
    }
    // Medidores por push: os picos por trilha chegam na cadência do sampler (60ms)
    // via /ws/peaks_out. O poll de /api/media acima cuida de estrutura, volume e mute.
    //
    // DESLIGADOS POR PADRÃO. O socket chega ~16x/s e repinta o DOM de cada trilha; é
    // o corte mais barato do dock. Desligado, o dock NEM ABRE o /ws/peaks_out, então
    // sai também o fan-out correspondente no servidor. O operador liga no botão
    // "Medidores" do card de áudio e a preferência fica em localStorage. Nada disso
    // afeta a detecção de fala nem os chips de voz: esses usam os picos que a página
    // da sala já amostra para o /ws/peaks_in, não este socket de saída.
    const METERS_KEY = 'rnpDockMetersOff';
    let metersOff = localStorage.getItem(METERS_KEY) !== '0';   // ausente => desligado
    let pws = null;

    function pintarPicos(picos) {
        for (const [id, peak] of Object.entries(picos)) {
            const estado = rows.get(id);
            if (!estado) continue;
            const db = peak > .0001 ? 20 * Math.log10(peak) : -Infinity;
            const fill = estado.row.querySelector('.vu-fill-vertical');
            if (fill) fill.style.height = Math.max(0, Math.min(100, (db + 60) / 60 * 100)) + '%';
            const out = estado.row.querySelector('output');
            if (out) out.textContent = Number.isFinite(db) ? db.toFixed(1) + ' dB' : '−∞ dB';
        }
    }

    function zerarMedidores() {
        for (const estado of rows.values()) {
            const fill = estado.row.querySelector('.vu-fill-vertical');
            if (fill) fill.style.height = '0%';
            const out = estado.row.querySelector('output');
            if (out) out.textContent = '— dB';
        }
    }

    function connectPeaks() {
        if (pws) return;
        pws = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws/peaks_out');
        pws.onmessage = (event) => {
            if (document.hidden) return;   // janela oculta não repinta medidores
            let picos;
            try { picos = JSON.parse(event.data); } catch (_) { return; }
            pintarPicos(picos);
        };
        pws.onclose = () => {
            pws = null;
            // só reconecta se ainda estiver ligado; senão o toggle volta a abrir
            if (!metersOff) setTimeout(connectPeaks, 2000);
        };
    }

    function disconnectPeaks() {
        if (!pws) return;
        const socket = pws;
        pws = null;
        try { socket.onclose = null; socket.close(); } catch (_) {}
        zerarMedidores();
    }

    // Cadência do poll de /api/media. Com os medidores ligados nada muda (500 ms,
    // como antes). Desligados — o novo padrão — a lista de canais pode refrescar bem
    // mais devagar: cada /api/media pode forçar um frame.evaluate() na página da
    // sala, que é o mesmo caminho que já chegou a pendurar 26 s e a atrasar o áudio.
    const POLL_RAPIDO_MS = 500;
    const POLL_LENTO_MS = 3000;
    let pollTimer = null;

    function scheduleRefresh() {
        clearTimeout(pollTimer);
        pollTimer = setTimeout(() => {
            if (!document.hidden) refresh();
            scheduleRefresh();
        }, metersOff ? POLL_LENTO_MS : POLL_RAPIDO_MS);
    }

    function applyMeters() {
        if (metersOff) disconnectPeaks(); else connectPeaks();
        document.body.classList.toggle('medidores-desligados', metersOff);
        const btn = document.getElementById('btnToggleMeters');
        if (btn) btn.textContent = metersOff ? 'Ligar medidores' : 'Desligar medidores';
        scheduleRefresh();   // a cadência do poll acompanha o estado dos medidores
    }

    window.RnpMeters = {
        toggle() {
            metersOff = !metersOff;
            try { localStorage.setItem(METERS_KEY, metersOff ? '1' : '0'); } catch (_) {}
            applyMeters();
        },
        get off() { return metersOff; },
    };

    applyMeters();
    refresh();
})();
