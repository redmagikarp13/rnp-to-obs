/* Controlador de player nativo do YouTube sincronizado pelo estado farejado da sala.
   Não depende do YouTube para ser testado: recebe uma fábrica de player via opts.YT.
   decide: carregar/trocar vídeo, play/pause e correção de drift por seekTo. */
(() => {
    if (window.RnpYouTube) return;

    class RnpYouTube {
        constructor(hook, opts = {}) {
            this.hook = hook;
            this.YT = opts.YT || window.YT;
            this.tolerance = opts.tolerance ?? 2.0;   // só corrige drift acima disto
            this.hardLimit = opts.hardLimit ?? 5.0;   // acima disto corrige mesmo fora da janela
            this.minSeekMs = opts.minSeekMs ?? 3000;  // mínimo entre seeks (evita re-buffer)
            this.playerVars = opts.playerVars || {};
            this.player = null;
            this.videoId = null;
            this.rate = 1;
            this._lastSeekAt = 0;
        }

        apply(state) {
            if (!state || !state.video_id) { this.dispose(); return; }
            if (state.video_id !== this.videoId) { this._load(state.video_id); return; }
            this._sync(state);
        }

        _load(id) {
            this.dispose();
            if (!this.YT || !this.YT.Player) return;
            this.videoId = id;
            this.rate = 1;
            // A API do YouTube SUBSTITUI o elemento alvo por um iframe; por isso usamos
            // um div interno descartável e mantemos this.hook (wrapper) estável.
            const target = document.createElement('div');
            this.hook.appendChild(target);
            this.player = new this.YT.Player(target, {
                videoId: id,
                playerVars: Object.assign({autoplay: 1, controls: 0, rel: 0, modestbranding: 1,
                    playsinline: 1, iv_load_policy: 3, disablekb: 1, fs: 0}, this.playerVars),
            });
        }

        _sync(state) {
            const p = this.player;
            if (!p || !p.getCurrentTime) return;
            const now = Date.now();
            const elapsed = state.playing ? ((now - (state.updated_at || now)) / 1000) * (state.rate || 1) : 0;
            const target = (state.current_time || 0) + elapsed;
            const current = p.getCurrentTime();
            const drift = Number.isFinite(current) ? Math.abs(target - current) : 0;
            if (drift > this.tolerance && (drift > this.hardLimit || (now - this._lastSeekAt) > this.minSeekMs)) {
                p.seekTo(target, true);
                this._lastSeekAt = now;
            }
            const ps = p.getPlayerState ? p.getPlayerState() : null;
            if (state.playing && ps === 2) p.playVideo();
            else if (!state.playing && ps === 1) p.pauseVideo();
            if (state.rate && state.rate !== this.rate && p.setPlaybackRate) { p.setPlaybackRate(state.rate); this.rate = state.rate; }
        }

        dispose() {
            if (this.player && this.player.destroy) { try { this.player.destroy(); } catch (_) {} }
            this.player = null;
            this.videoId = null;
            if (this.hook) this.hook.innerHTML = '';  // remove o iframe/div interno substituído
        }
    }

    window.RnpYouTube = RnpYouTube;
})();
