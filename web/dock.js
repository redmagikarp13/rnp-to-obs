/* Lógica do dock /control.
   Task 4: abas (estreito) + FPS da barra de status. A troca estreito/largo é só CSS
   (media query), então aqui não há lógica de largura — só qual grupo está ativo.
   Task 6 recebe o JS que hoje está inline na f-string do /control. */
(function () {
    'use strict';

    const TAB_KEY = 'rnpDockTab';
    const tabs = document.getElementById('dockTabs');
    const groups = [...document.querySelectorAll('.dock-group')];

    function activate(nome) {
        groups.forEach(g => g.classList.toggle('is-active', g.dataset.group === nome));
        document.querySelectorAll('.dock-tab').forEach(b => {
            const on = b.dataset.tab === nome;
            b.classList.toggle('is-active', on);
            b.setAttribute('aria-selected', on ? 'true' : 'false');
        });
    }

    if (tabs) {
        tabs.addEventListener('click', (e) => {
            const btn = e.target.closest('.dock-tab');
            if (!btn) return;
            activate(btn.dataset.tab);
            try { localStorage.setItem(TAB_KEY, btn.dataset.tab); } catch (_) {}
        });
    }

    let inicial = 'conexao';
    try { inicial = localStorage.getItem(TAB_KEY) || 'conexao'; } catch (_) {}
    if (!groups.some(g => g.dataset.group === inicial)) inicial = 'conexao';
    activate(inicial);

    // Ordem dos grupos: preferência persistida no config do servidor (todos os
    // clientes veem igual). As setas do cabeçalho de cada grupo movem o grupo e a
    // aba correspondente; falha de gravação reverte em vez de fingir que salvou.
    const groupsBox = document.getElementById('dockGroups');
    const tabsBox = document.getElementById('dockTabs');

    function currentOrder() {
        return [...document.querySelectorAll('.dock-group')].map(g => g.dataset.group);
    }

    function applyGroupOrder(ordem) {
        if (!groupsBox) return;
        for (const key of ordem) {
            const g = document.getElementById('group-' + key);
            if (g) groupsBox.appendChild(g);
            const t = tabsBox && tabsBox.querySelector('[data-tab="' + key + '"]');
            if (t) tabsBox.appendChild(t);
        }
    }

    async function saveGroupOrder(ordem) {
        const anterior = currentOrder();
        try {
            const res = await fetch('/api/grid/config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ dock_group_order: ordem })
            });
            if (!res.ok) throw new Error('HTTP ' + res.status);
        } catch (_) {
            applyGroupOrder(anterior);
        }
    }

    if (groupsBox) {
        const salva = (groupsBox.dataset.dockOrder || '').split(',').filter(Boolean);
        if (salva.length === 4) applyGroupOrder(salva);
        document.addEventListener('click', (e) => {
            const btn = e.target.closest('.dock-move');
            if (!btn) return;
            const secao = btn.closest('.dock-group');
            const ordem = currentOrder();
            const i = ordem.indexOf(secao.dataset.group);
            const j = i + Number(btn.dataset.move);
            if (i < 0 || j < 0 || j >= ordem.length) return;
            [ordem[i], ordem[j]] = [ordem[j], ordem[i]];
            applyGroupOrder(ordem);
            saveGroupOrder(ordem);
        });
    }

    // Previews ao vivo (grid de câmeras, apresentação) pesam CPU. O toggle zera o src
    // (para de decodificar de verdade) e marca o contêiner para o placeholder; o estado
    // persiste por operador em localStorage. O poll que refresca o thumb da apresentação
    // consulta previewOff antes de religar (guarda no server.py).
    const PREVIEW_KEY = 'rnpDockPreviewsOff';
    const PREVIEW_ALVO = {
        cameras: 'gridPreviewIframe',
        presentation: 'thumbScreen',
    };
    const PREVIEW_BOTAO = {
        cameras: 'btnToggleCamPreview',
        presentation: 'btnToggleScreenPreview',
    };
    let previewOff = { cameras: false, presentation: false };
    try { previewOff = Object.assign(previewOff, JSON.parse(localStorage.getItem(PREVIEW_KEY) || '{}')); } catch (_) {}

    function applyPreview(kind) {
        const el = document.getElementById(PREVIEW_ALVO[kind]);
        const btn = document.getElementById(PREVIEW_BOTAO[kind]);
        if (!el) return;
        const caixa = el.parentElement;
        if (previewOff[kind]) {
            if (!el.dataset.srcOriginal) el.dataset.srcOriginal = el.getAttribute('src') || '';
            if (el.tagName === 'IFRAME') el.setAttribute('src', 'about:blank');
            else el.removeAttribute('src');
            caixa.classList.add('preview-off');
        } else {
            if (el.dataset.srcOriginal) el.setAttribute('src', el.dataset.srcOriginal);
            caixa.classList.remove('preview-off');
        }
        if (btn) btn.textContent = previewOff[kind] ? 'Mostrar preview' : 'Ocultar preview';
    }

    function togglePreview(kind) {
        if (!(kind in PREVIEW_ALVO)) return;
        previewOff[kind] = !previewOff[kind];
        try { localStorage.setItem(PREVIEW_KEY, JSON.stringify(previewOff)); } catch (_) {}
        applyPreview(kind);
    }

    Object.keys(PREVIEW_ALVO).forEach(applyPreview);

    // FPS médio das câmeras na barra de status (a origem amostra por câmera).
    const fpsEl = document.getElementById('dockFps');
    if (fpsEl) {
        setInterval(async () => {
            try {
                const st = await (await fetch('/api/status?t=' + Date.now(), { cache: 'no-store' })).json();
                const valores = (st.cameras || []).map(c => c.fps).filter(v => typeof v === 'number');
                fpsEl.textContent = valores.length
                    ? Math.round(valores.reduce((a, b) => a + b, 0) / valores.length) : '--';
            } catch (_) { /* status indisponível: mantém o último valor */ }
        }, 2000);
    }

    window.RnpDock = { activateTab: activate, togglePreview, applyGroupOrder, currentOrder,
                       get previewOff() { return previewOff; } };
})();
