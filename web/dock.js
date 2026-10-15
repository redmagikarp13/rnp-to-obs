/* Lógica do dock /control — modelo de BLOCOS (Task 4d).
   A unidade do mosaico é o bloco (card), não o grupo: cada bloco tem categoria
   (filtro das abas no estreito), span próprio e alça de reordenação. A ordem é
   persistida no config do servidor; a aba ativa, no localStorage. */
(function () {
    'use strict';

    const TAB_KEY = 'rnpDockTab';
    const tabs = document.getElementById('dockTabs');
    const mosaico = document.getElementById('dockMosaico');
    const blocos = [...document.querySelectorAll('.dock-bloco')];

    // Abas = filtros de categoria no estreito; no largo o CSS mostra tudo.
    function activateTab(cat) {
        blocos.forEach(b => b.classList.toggle('is-off', b.dataset.cat !== cat));
        document.querySelectorAll('.dock-tab').forEach(t => {
            const on = t.dataset.tab === cat;
            t.classList.toggle('is-active', on);
            t.setAttribute('aria-selected', on ? 'true' : 'false');
        });
    }
    if (tabs) {
        tabs.addEventListener('click', (e) => {
            const btn = e.target.closest('.dock-tab');
            if (!btn) return;
            activateTab(btn.dataset.tab);
            try { localStorage.setItem(TAB_KEY, btn.dataset.tab); } catch (_) {}
        });
    }
    let cat = 'conexao';
    try { cat = localStorage.getItem(TAB_KEY) || 'conexao'; } catch (_) {}
    const cats = [...new Set(blocos.map(b => b.dataset.cat))];
    if (!cats.includes(cat)) cat = cats[0] || 'conexao';
    activateTab(cat);

    // Painel de conexões como popover ancorado na barra: overlay, não ocupa fluxo em
    // largura nenhuma (não empurra a dobra no estreito). Abre no botão, fecha fora/Esc.
    const connectMenu = document.getElementById('btnConnectMenu');
    const connectPop = document.getElementById('connectPopover');
    function setConnectMenu(aberto) {
        if (!connectPop || !connectMenu) return;
        connectPop.hidden = !aberto;
        connectMenu.setAttribute('aria-expanded', aberto ? 'true' : 'false');
    }
    if (connectMenu) {
        connectMenu.addEventListener('click', (e) => {
            e.stopPropagation();
            setConnectMenu(connectPop.hidden);
        });
    }
    document.addEventListener('click', (e) => {
        if (connectPop && !connectPop.hidden && !connectPop.contains(e.target)) setConnectMenu(false);
    });
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') setConnectMenu(false);
    });

    // Ordem dos blocos: persistida no servidor (todos os clientes veem igual).
    function currentOrder() {
        return [...mosaico.querySelectorAll('.dock-bloco')].map(b => b.dataset.bloco);
    }
    function applyBlockOrder(ordem) {
        if (!mosaico) return;
        for (const key of ordem) {
            const b = mosaico.querySelector('.dock-bloco[data-bloco="' + key + '"]');
            if (b) mosaico.appendChild(b);
        }
    }
    async function saveBlockOrder(ordem) {
        const anterior = currentOrder();
        try {
            const res = await fetch('/api/grid/config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ dock_block_order: ordem })
            });
            if (!res.ok) throw new Error('HTTP ' + res.status);
        } catch (_) {
            applyBlockOrder(anterior);   // falha de gravação não finge que salvou
        }
    }
    if (mosaico) {
        const salva = (mosaico.dataset.blockOrder || '').split(',').filter(Boolean);
        const presentes = blocos.map(b => b.dataset.bloco);
        if (salva.length === presentes.length && presentes.every(k => salva.includes(k))) {
            applyBlockOrder(salva);
        }
        document.addEventListener('click', (e) => {
            const btn = e.target.closest('.dock-move');
            const bloco = btn && btn.closest('.dock-bloco');
            if (!btn || !bloco) return;
            const ordem = currentOrder();
            const i = ordem.indexOf(bloco.dataset.bloco);
            const j = i + Number(btn.dataset.move);
            if (i < 0 || j < 0 || j >= ordem.length) return;
            [ordem[i], ordem[j]] = [ordem[j], ordem[i]];
            applyBlockOrder(ordem);
            saveBlockOrder(ordem);
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

    window.RnpDock = {
        activateTab,
        applyBlockOrder,
        currentOrder,
        togglePreview,
        get previewOff() { return previewOff; },
    };
})();
