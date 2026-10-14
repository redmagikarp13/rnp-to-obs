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

    window.RnpDock = { activateTab: activate };
})();
