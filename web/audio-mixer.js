(() => {
    const container = document.getElementById('individualChannelsContainer');
    const placeholder = document.getElementById('noIndividualChannelsPlaceholder');
    if (!container) return;
    const rows = new Map();
    let busy = false;
    const link = document.createElement('a');
    link.href = '/webrtc'; link.target = '_blank'; link.rel = 'noopener';
    link.textContent = 'Abrir teste WebRTC (vídeo e áudio individual)';
    container.parentElement.insertBefore(link, container);

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
                const db = channel.peak > .0001 ? 20 * Math.log10(channel.peak) : -Infinity;
                row.querySelector('.vu-fill-vertical').style.height = Math.max(0, Math.min(100, (db + 60) / 60 * 100)) + '%';
                row.querySelector('output').textContent = Number.isFinite(db) ? db.toFixed(1) + ' dB' : '−∞ dB';
            }
        } catch (e) {
            for (const state of rows.values()) {state.row.remove();}
            rows.clear();
            if (placeholder) {placeholder.style.display = 'flex'; placeholder.textContent = e.message;}
        } finally {busy = false;}
    }
    setInterval(refresh, 500);
    refresh();
})();
