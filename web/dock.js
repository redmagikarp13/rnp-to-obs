/* ==========================================================================
   Handlers e estado do dock /control — extraídos do <script> inline do
   f-string em server.py (Task 6b). Rodam no escopo global de um script
   clássico: as 'function' viram window.* (usadas por onclick=...) e os 'let'
   de topo ficam no escopo léxico do script, visíveis aos handlers inline.
   Ordem preservada: este bloco vem ANTES da IIFE de setup (abas/mosaico/
   previews/fps/conexão) abaixo, exatamente como o inline corria antes.
   ========================================================================== */
        let currentCameras = [];
        let configSaveError = '';
        let currentMessages = [];
        let activeFeaturedMsg = null;
        let focusedCamIds = [];
        let pinnedCamIds = [];
        let librasCamIds = [];
        let activeLibrasSize = "equal";
        let isLocalMuted = false;
        let activeStagePreset = "side-by-side";
        let activeChatSourceFilter = "all";

        let masterPeakHold = 0;
        let masterPeakHoldTimer = null;
        let currentMasterDisplayPct = 0;
        let channelPeaks = {};

        function updatePeakHoldUI(val) {
            const el = document.getElementById('vuMasterPeak');
            if (el) el.style.bottom = Math.min(99, val) + '%';
        }

        window.onAudioPcmChunk = function(peak) {
            let db = -Infinity;
            let pct = 0;
            if (peak > 0.0001) {
                db = Math.max(-60, Math.min(0, 20 * Math.log10(peak)));
                pct = Math.min(100, Math.round(((db + 60) / 60) * 100));
            }

            if (pct >= currentMasterDisplayPct) {
                currentMasterDisplayPct = pct;
            } else {
                currentMasterDisplayPct = Math.max(0, currentMasterDisplayPct - 4);
            }

            const masterBarEl = document.getElementById('vuMasterBar');
            const masterDbEl = document.getElementById('vuMasterDbText');

            if (masterBarEl) {
                masterBarEl.style.height = currentMasterDisplayPct + '%';
            }

            if (masterDbEl) {
                if (currentMasterDisplayPct > 0) {
                    const dbStr = db.toFixed(1);
                    masterDbEl.innerText = `${dbStr} dB (${currentMasterDisplayPct}%)`;
                    masterDbEl.style.color = currentMasterDisplayPct > 85 ? '#ef4444' : (currentMasterDisplayPct > 65 ? '#f59e0b' : '#10b981');
                } else {
                    masterDbEl.innerText = '-∞ dB (0%)';
                    masterDbEl.style.color = '#94a3b8';
                }
            }

            if (pct > masterPeakHold) {
                masterPeakHold = pct;
                clearTimeout(masterPeakHoldTimer);
                masterPeakHoldTimer = setTimeout(() => {
                    const decay = setInterval(() => {
                        masterPeakHold = Math.max(0, masterPeakHold - 3);
                        updatePeakHoldUI(masterPeakHold);
                        if (masterPeakHold <= 0) clearInterval(decay);
                    }, 40);
                }, 1200);
            }
            updatePeakHoldUI(masterPeakHold);

        };

        // Atualização em tempo real de quem está falando recebida via WebSocket
        window.onSpeakersUpdate = function(speakersMap) {
            if (!currentCameras || currentCameras.length === 0) return;
            currentCameras.forEach(c => {
                const cid = String(c.id);
                if (speakersMap && speakersMap[cid] !== undefined) {
                    c.is_speaking = !!speakersMap[cid];
                }
                updateChannelVisualState(c);
            });
        };

        function updateChannelVisualState(c) {
            const cid = String(c.id);
            const strip = document.getElementById('channelStrip_cam_' + cid);
            const badge = document.getElementById('voiceBadge_cam_' + cid);
            if (!strip) return;
            
            if (c.is_speaking) {
                strip.classList.add('speaking-active');
                if (badge) {
                    badge.className = 'channel-voice-badge speaking';
                    badge.innerText = '🗣️ Falando';
                }
            } else if (c.is_muted) {
                strip.classList.remove('speaking-active');
                if (badge) {
                    badge.className = 'channel-voice-badge muted';
                    badge.innerText = '🔇 Mudo';
                }
            } else {
                strip.classList.remove('speaking-active');
                if (badge) {
                    badge.className = 'channel-voice-badge open';
                    badge.innerText = '🎙️ Aberto';
                }
            }
        }

        // Decaimento suave contínuo dos medidores verticais
        setInterval(() => {
            if (currentMasterDisplayPct > 0) {
                currentMasterDisplayPct = Math.max(0, currentMasterDisplayPct - 8);
                const masterBarEl = document.getElementById('vuMasterBar');
                const masterDbEl = document.getElementById('vuMasterDbText');
                if (masterBarEl) masterBarEl.style.height = currentMasterDisplayPct + '%';
                if (currentMasterDisplayPct === 0 && masterDbEl) {
                    masterDbEl.innerText = '-∞ dB (0%)';
                    masterDbEl.style.color = '#94a3b8';
                }
            }
            if (currentCameras && currentCameras.length > 0) {
                currentCameras.forEach(c => {
                    const cid = String(c.id);
                    const barEl = document.getElementById('vuBar_cam_' + cid);
                    const dbEl = document.getElementById('vuDb_cam_' + cid);
                    if (barEl) {
                        const cur = parseFloat(barEl.style.height || '0');
                        if (cur > 0) {
                            const nextVal = Math.max(0, cur - 8);
                            barEl.style.height = nextVal + '%';
                            if (nextVal === 0 && dbEl) {
                                dbEl.innerText = '-∞ dB (0%)';
                                dbEl.style.color = '#94a3b8';
                            }
                        }
                    }
                });
            }
        }, 120);

        window.testAudioVuMeter = function() {
            let step = 0;
            const testSteps = [0.10, 0.28, 0.52, 0.76, 0.94, 0.82, 0.60, 0.32, 0.68, 0.92, 0.98, 0.70, 0.40, 0.15, 0];
            // Para teste demonstrativo, ativa temporariamente a primeira câmera disponível
            if (currentCameras && currentCameras.length > 0) {
                currentCameras[0].is_speaking = true;
                updateChannelVisualState(currentCameras[0]);
            }
            const interval = setInterval(() => {
                if (step >= testSteps.length) {
                    clearInterval(interval);
                    if (currentCameras && currentCameras.length > 0) {
                        currentCameras[0].is_speaking = false;
                        updateChannelVisualState(currentCameras[0]);
                    }
                    return;
                }
                if (window.onAudioPcmChunk) window.onAudioPcmChunk(testSteps[step]);
                step++;
            }, 180);
        };

        window.updateMasterVol = function(val) {
            const pct = Math.round(parseFloat(val) * 100);
            const lbl = document.getElementById('lblMasterVol');
            if (lbl) lbl.innerText = pct + '%';
            if (!isLocalMuted && window.setLocalMonitorVolume) {
                window.setLocalMonitorVolume(parseFloat(val));
            }
        };

        let isMasterMuted = false;
        window.toggleMasterMute = function() {
            isMasterMuted = !isMasterMuted;
            const btn = document.getElementById('btnMuteMaster');
            if (isMasterMuted) {
                if (window.setLocalMonitorVolume) window.setLocalMonitorVolume(0);
                if (btn) {
                    btn.style.background = '#ef4444';
                    btn.style.color = '#fff';
                    btn.innerText = '🔇 Mudo';
                }
            } else {
                const val = parseFloat(document.getElementById('rngMasterVol').value || '1');
                if (window.setLocalMonitorVolume) window.setLocalMonitorVolume(val);
                if (btn) {
                    btn.style.background = '#0f172a';
                    btn.style.color = '#cbd5e1';
                    btn.innerText = '🔊 Ativo';
                }
            }
        };


        let _saveToastTimer = null;
        function showSaveNotice(ok, msg) {
            const toast = document.getElementById('saveToast');
            const sucesso = ok !== false;
            toast.textContent = sucesso ? 'Salvo!' : ('Falha: ' + (msg || 'erro ao salvar'));
            toast.className = 'badge ' + (sucesso ? 'connected' : 'disconnected');
            toast.style.display = 'inline-flex';
            clearTimeout(_saveToastTimer);
            _saveToastTimer = setTimeout(() => { toast.style.display = 'none'; }, 2600);
        }

        // Grava config e SÓ anuncia sucesso se o servidor aceitar (response.ok).
        // Modelo do saveSpeakThreshold; usado por autoSaveField, saveAllConfigurations
        // e setStagePreset, que antes mostravam 'Salvo!' mesmo com o POST falhando.
        async function postSave(endpoint, payload) {
            try {
                const res = await fetch(endpoint, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                if (!res.ok) {
                    const corpo = await res.json().catch(() => ({}));
                    throw new Error(corpo.message || ('HTTP ' + res.status));
                }
                showSaveNotice(true);
                return true;
            } catch (erro) {
                showSaveNotice(false, erro.message);
                return false;
            }
        }

        function toggleMonitorMute() {
            isLocalMuted = !isLocalMuted;
            const btn = document.getElementById('btnMuteMonitor');
            const lbl = document.getElementById('lblMonitorStatus');
            if (isLocalMuted) {
                if (window.setLocalMonitorVolume) window.setLocalMonitorVolume(0);
                if (btn) {
                    btn.style.background = '#ef4444';
                    btn.style.color = '#ffffff';
                }
                if (lbl) lbl.innerText = 'Monitor Local: Mudo 🔇';
                localStorage.setItem('rnp_monitor_muted', '1');
            } else {
                const val = parseFloat(document.getElementById('rngMonitorVol').value);
                if (window.setLocalMonitorVolume) window.setLocalMonitorVolume(val);
                if (btn) {
                    btn.style.background = '#0f172a';
                    btn.style.color = '#cbd5e1';
                }
                if (lbl) lbl.innerText = 'Monitor Local: Ligado 🔊';
                localStorage.setItem('rnp_monitor_muted', '0');
            }
        }

        function updateMonitorVol(val) {
            if (!isLocalMuted && window.setLocalMonitorVolume) {
                window.setLocalMonitorVolume(parseFloat(val));
            }
            localStorage.setItem('rnp_monitor_vol', val);
        }

        window.addEventListener('DOMContentLoaded', () => {
            const savedVol = localStorage.getItem('rnp_monitor_vol');
            if (savedVol !== null) {
                document.getElementById('rngMonitorVol').value = savedVol;
                if (window.setLocalMonitorVolume) window.setLocalMonitorVolume(parseFloat(savedVol));
            }
            // Monitor local DESLIGADO por padrão: o dock é painel de controle, e o
            // master de áudio que vai para a live sai pelo /stage, não por aqui. Só um
            // '0' explícito (o operador ligou e a preferência ficou gravada) mantém o
            // som saindo no dock — assim ninguém herda áudio duplicado nem o custo do
            // player PCM local sem pedir.
            const savedMuted = localStorage.getItem('rnp_monitor_muted');
            if (savedMuted !== '0') {
                toggleMonitorMute();
            }
        });

        async function autoSaveField(field, val) {
            const payload = {};
            payload[field] = val;
            await postSave('/api/config/save', payload);
        }

        // Handler dedicado: posta na rota que VALIDA o limiar (/api/grid/config) e, ao
        // contrário de autoSaveField, confere response.ok antes de sinalizar sucesso —
        // falha de gravação não pode fingir que salvou (spec §6).
        async function saveSpeakThreshold(el) {
            const hint = document.getElementById('speakThresholdHint');
            try {
                const res = await fetch('/api/grid/config', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ speak_peak_threshold: parseFloat(el.value) })
                });
                if (!res.ok) {
                    const corpo = await res.json().catch(() => ({}));
                    throw new Error(corpo.message || ('HTTP ' + res.status));
                }
                if (hint) hint.textContent = 'Aplicado — vale para o indicador de fala e para os chips de voz.';
                showSaveNotice();
            } catch (erro) {
                if (hint) {
                    hint.textContent = 'Não aplicado: ' + erro.message;
                    hint.style.color = '#f87171';
                }
                // Reverte para o valor que continua valendo no servidor.
                try {
                    const st = await (await fetch('/api/status?t=' + Date.now())).json();
                    if (st && st.speak_peak_threshold != null) el.value = st.speak_peak_threshold;
                } catch (_) {}
                return;
            }
            if (hint) hint.style.color = '';
        }

        async function saveAllConfigurations() {
            const config = {
                room_url: document.getElementById('roomUrl').value.trim(),
                user_name: document.getElementById('userName').value.trim(),
                user_email: document.getElementById('userEmail').value.trim(),
                youtube_url: document.getElementById('ytUrl').value.trim(),
                youtube_auto_connect: document.getElementById('chkYtAutoConnect').checked,
                show_camera_names: document.getElementById('chkShowNames').checked,
                camera_name_style: document.getElementById('selNameStyle').value,
                grid_layout_mode: document.getElementById('selGridLayout').value,
                stage_preset: activeStagePreset,
                libras_size: document.getElementById('selLibrasSize').value,
                poll_display_mode: document.getElementById('selPollDisplayMode').value,
                poll_display_duration: parseInt(document.getElementById('selPollDuration').value),
                chat_style: document.getElementById('selChatTheme').value,
                chat_font_size: document.getElementById('selChatSize').value,
                chat_max_messages: parseInt(document.getElementById('selChatMax').value),
                chat_source_filter: activeChatSourceFilter,
                libras_camera_ids: librasCamIds
            };

            await postSave('/api/config/save', config);
        }

        async function loadAllConfigurations() {
            try {
                const res = await fetch('/api/config?t=' + Date.now());
                const cfg = await res.json();
                document.getElementById('roomUrl').value = cfg.room_url || '';
                document.getElementById('userName').value = cfg.user_name || '';
                document.getElementById('userEmail').value = cfg.user_email || '';
                document.getElementById('ytUrl').value = cfg.youtube_url || '';
                document.getElementById('chkYtAutoConnect').checked = !!cfg.youtube_auto_connect;
                document.getElementById('chkShowNames').checked = !!cfg.show_camera_names;
                document.getElementById('selNameStyle').value = cfg.camera_name_style || 'glass';
                document.getElementById('selGridLayout').value = cfg.grid_layout_mode || 'auto';
                document.getElementById('selCamFormat').value = cfg.camera_format || 'original';
                document.getElementById('selLibrasSize').value = cfg.libras_size || 'equal';
                document.getElementById('selPollDisplayMode').value = cfg.poll_display_mode || 'results_only';
                document.getElementById('selPollDuration').value = (cfg.poll_display_duration !== undefined) ? cfg.poll_display_duration : 15;
                document.getElementById('selChatTheme').value = cfg.chat_style || 'glass';
                document.getElementById('selChatSize').value = cfg.chat_font_size || 'medium';
                document.getElementById('selChatMax').value = cfg.chat_max_messages || 6;
                activeStagePreset = cfg.stage_preset || 'side-by-side';
                activeLibrasSize = cfg.libras_size || 'equal';
                activeChatSourceFilter = cfg.chat_source_filter || 'all';
                librasCamIds = (cfg.libras_camera_ids || []).map(String);
                updatePresetButtonsUI(activeStagePreset);
                updateChatSourceButtonsUI(activeChatSourceFilter);
                showSaveNotice();
            } catch (e) {}
        }

        async function resetAllConfigurations() {
            if (confirm('Deseja restaurar as configurações padrão de fábrica?')) {
                await fetch('/api/config/reset', { method: 'POST' });
                await loadAllConfigurations();
            }
        }

        async function setStagePreset(presetName) {
            activeStagePreset = presetName;
            updatePresetButtonsUI(presetName);
            await postSave('/api/grid/config', { stage_preset: presetName, libras_size: activeLibrasSize });
        }

        async function setChatSourceFilter(filterVal) {
            activeChatSourceFilter = filterVal;
            updateChatSourceButtonsUI(filterVal);
            await postSave('/api/chat/config', { chat_source_filter: filterVal });
        }

        function updateChatSourceButtonsUI(filterVal) {
            const filters = ['all', 'rnp', 'youtube'];
            filters.forEach(f => {
                const btn = document.getElementById('btnChatSrc_' + f);
                if (btn) {
                    if (f === filterVal) {
                        btn.className = 'btn-ctrl active-preset';
                    } else {
                        btn.className = 'btn-ctrl';
                    }
                }
            });
        }

        async function updateLibrasSizeConfig() {
            const libras_size = document.getElementById('selLibrasSize').value;
            activeLibrasSize = libras_size;
            await postSave('/api/grid/config', { libras_size: libras_size });
        }

        function updatePresetButtonsUI(presetName) {
            const presets = ['side-by-side', 'bottom-bar', 'pip-bottom-right', 'pip-top-right', 'split-50-50', 'slide-only', 'cameras-only', 'camera-libras', 'grid-libras'];
            presets.forEach(p => {
                const btn = document.getElementById('btnPreset_' + p);
                if (btn) {
                    if (p === presetName) {
                        btn.className = 'btn-ctrl active-preset';
                    } else {
                        btn.className = 'btn-ctrl';
                    }
                }
            });
        }

        async function handleYtConnect() {
            const youtube_url = document.getElementById('ytUrl').value.trim();
            if (!youtube_url) {
                alert('Digite a URL ou ID do vídeo do YouTube!');
                return;
            }
            const badge = document.getElementById('ytStatusBadge');
            badge.className = 'badge connecting';
            badge.textContent = '● Conectando...';

            // Sucesso: o fetchStatus (poll) assume o badge. Falha: reverte e o
            // postSave já mostrou 'Falha' — não deixar o badge preso em Conectando.
            if (!await postSave('/api/youtube/connect', { youtube_url })) {
                badge.className = 'badge disconnected';
                badge.textContent = '● Não conectado';
            }
        }

        async function handleYtDisconnect() {
            await fetch('/api/youtube/disconnect', { method: 'POST' });
        }

        async function highlightMessage(msg) {
            activeFeaturedMsg = msg;
            await fetch('/api/chat/highlight', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message: msg })
            });
            renderFeaturedBox(msg);
            renderChatList(currentMessages);
        }

        async function clearFeaturedMessage() {
            activeFeaturedMsg = null;
            await fetch('/api/chat/highlight', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message: null })
            });
            renderFeaturedBox(null);
            renderChatList(currentMessages);
        }

        function escHtml(s) {
            return String(s ?? '')
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;')
                .replace(/'/g, '&#39;');
        }

        function escJsStr(s) {
            return String(s ?? '')
                .replace(/\\/g, '\\\\')
                .replace(/'/g, "\\'")
                .replace(/\r/g, '\\r')
                .replace(/\n/g, '\\n');
        }

        function renderFeaturedBox(msg) {
            const box = document.getElementById('featuredBox');
            if (msg) {
                box.style.display = 'flex';
                document.getElementById('featUser').innerText = msg.user || 'Participante';
                document.getElementById('featText').textContent = msg.text || '';
                const b = document.getElementById('featSourceBadge');
                if (msg.source === 'youtube') {
                    b.className = 'source-tag yt';
                    b.innerText = 'YouTube Live';
                } else {
                    b.className = 'source-tag rnp';
                    b.innerText = 'RNP WebConf';
                }
            } else {
                box.style.display = 'none';
            }
        }

        function renderChatList(msgs) {
            const container = document.getElementById('chatListContainer');
            document.getElementById('chatTotalCount').innerText = msgs.length + ' msgs';
            
            if (msgs.length === 0) {
                container.innerHTML = '<div style="font-size: 12px; padding: 8px">Nenhuma mensagem recebida ainda.</div>';
                return;
            }

            const reversed = [...msgs].reverse();
            container.innerHTML = '';

            reversed.forEach(m => {
                const isFeatured = activeFeaturedMsg && (activeFeaturedMsg.text === m.text && activeFeaturedMsg.user === m.user);
                const row = document.createElement('div');
                row.className = 'chat-msg-row' + (isFeatured ? ' is-featured' : '');

                const isYt = (m.source === 'youtube');
                const srcBadge = isYt ? '<span class="source-tag yt">YT</span>' : '<span class="source-tag rnp">RNP</span>';

                row.innerHTML = `
                    <div style="flex: 1; min-width: 0">
                        <div style="display: flex; align-items: center; gap: 6px; margin-bottom: 2px">
                            ${srcBadge}
                            <strong style="font-size: 12px">${escHtml(m.user)}</strong>
                            <span style="font-size: 10px">${escHtml(m.time || '')}</span>
                        </div>
                        <div style="font-size: 12px; word-break: break-word">${escHtml(m.text)}</div>
                    </div>
                    <div style="flex-shrink: 0" class="chat-msg-actions"></div>
                `;
                const btn = document.createElement('button');
                btn.className = 'btn-ctrl' + (isFeatured ? ' active-focus' : '');
                btn.style.cssText = 'padding: 4px 8px; font-size: 11px';
                btn.textContent = isFeatured ? '❌ Desfocar' : '🌟 Destacar';
                btn.onclick = isFeatured ? () => clearFeaturedMessage() : () => highlightMessage(m);
                row.querySelector('.chat-msg-actions').appendChild(btn);
                container.appendChild(row);
            });
        }

        let prevConnected = null;
        let prevCamMap = new Map();
        let prevHasPresentation = false;
        let prevLibrasCount = 0;
        let lastEventTimeout = null;

        function triggerSourceAlert(icon, text, type = 'info') {
            const banner = document.getElementById('liveSourceEventBanner');
            if (!banner) return;
            banner.style.display = 'flex';
            if (type === 'success') {
                banner.style.background = 'rgba(16, 185, 129, 0.2)';
                banner.style.border = '1px solid #10b981';
                banner.style.color = '#34d399';
            } else if (type === 'warn') {
                banner.style.background = 'rgba(239, 68, 68, 0.2)';
                banner.style.border = '1px solid #ef4444';
                banner.style.color = '#f87171';
            } else {
                banner.style.background = 'rgba(56, 189, 248, 0.2)';
                banner.style.border = '1px solid #38bdf8';
                banner.style.color = '#38bdf8';
            }
            banner.innerHTML = `<span>${icon}</span> <span>${text}</span>`;
            if (lastEventTimeout) clearTimeout(lastEventTimeout);
            lastEventTimeout = setTimeout(() => {
                banner.style.display = 'none';
            }, 4500);
        }

        async function fetchStatus() {
            if (document.hidden) return;   // aba/janela oculta não precisa de status
            try {
                const res = await fetch('/api/status?t=' + Date.now());
                if (!res.ok) {
                    document.getElementById('healthHeartbeat').innerText = '⚠️ Erro de API';
                    return;
                }
                const data = await res.json();
                document.getElementById('healthHeartbeat').innerText = '● Ao Vivo (500ms)';

                // Acesso LAN: mantém URLs e toggle sincronizados com o servidor (o IP
                // pode mudar via DHCP). Não sobrescreve enquanto o usuário opera o toggle.
                if (Array.isArray(data.lan_ips) && data.lan_ips.length) {
                    const port = location.port || '3333';
                    const base = 'http://' + data.lan_ips[0] + ':' + port;
                    window._lanBaseUrl = base;
                    const tvSpan = document.getElementById('lanUrlTv');
                    const ctrlSpan = document.getElementById('lanUrlControl');
                    if (tvSpan) tvSpan.textContent = base + '/tv';
                    if (ctrlSpan) ctrlSpan.textContent = base + '/control';
                }
                const chkLan = document.getElementById('chkLanAccess');
                if (chkLan && typeof data.lan_access === 'boolean' && document.activeElement !== chkLan) {
                    chkLan.checked = data.lan_access;
                    const lblLan = document.getElementById('lblLanAccess');
                    if (lblLan) lblLan.textContent = data.lan_access ? 'Ligado' : 'Desligado';
                    const boxLan = document.getElementById('lanUrlBox');
                    if (boxLan) boxLan.style.display = data.lan_access ? 'block' : 'none';
                }
                
                const badge = document.getElementById('statusBadge');
                if (data.connecting) {
                    badge.className = 'badge connecting';
                    badge.innerHTML = '● Conectando...';
                } else if (data.connected) {
                    badge.className = 'badge connected';
                    badge.innerHTML = '● Conectado à RNP';
                } else {
                    badge.className = 'badge disconnected';
                    badge.innerHTML = '● Desconectado';
                }


                const ytBadge = document.getElementById('ytStatusBadge');
                if (data.youtube_connected) {
                    ytBadge.className = 'badge connected';
                    ytBadge.innerHTML = '● YouTube Ativo';
                } else {
                    ytBadge.className = 'badge disconnected';
                    ytBadge.innerHTML = '● ' + (data.youtube_status || 'Desconectado');
                }

                if (data.status_message) {
                    document.getElementById('logBox').innerText = configSaveError || ('> ' + data.status_message);
                }

                focusedCamIds = (data.focused_camera_ids || (data.focused_camera_id != null ? [data.focused_camera_id] : [])).map(String);
                pinnedCamIds = (data.pinned_camera_ids || []).map(String);
                librasCamIds = (data.libras_camera_ids || []).map(String);

                const cams = data.cameras || [];
                const librasCams = cams.filter(c => librasCamIds.includes(String(c.id)) || c.is_libras);

                // --- 📡 MONITOR ATIVO DE FONTES & EVENTOS ---
                // 1. Conexão Sala RNP
                const pillRnp = document.getElementById('healthPillRnp');
                const dotRnp = document.getElementById('dotRnp');
                const stateRnp = document.getElementById('healthStateRnp');
                if (data.connecting) {
                    dotRnp.className = 'pill-dot connecting';
                    stateRnp.innerText = 'Conectando...';
                    pillRnp.className = 'source-health-pill';
                } else if (data.connected) {
                    dotRnp.className = 'pill-dot connected';
                    stateRnp.innerText = '🟢 Online';
                    pillRnp.className = 'source-health-pill is-active';
                } else {
                    dotRnp.className = 'pill-dot disconnected';
                    stateRnp.innerText = '⚪ Offline';
                    pillRnp.className = 'source-health-pill';
                }

                if (prevConnected !== null) {
                    if (!prevConnected && data.connected) {
                        triggerSourceAlert('⚡', 'Conectado à sala RNP com sucesso!', 'success');
                    } else if (prevConnected && !data.connected) {
                        triggerSourceAlert('⚠️', 'Conexão com a sala RNP foi interrompida ou caiu.', 'warn');
                    }
                }
                prevConnected = !!data.connected;

                // 2. Apresentação / Compartilhamento de Tela
                const dotSlides = document.getElementById('dotSlides');
                const stateSlides = document.getElementById('healthStateSlides');
                const pillSlides = document.getElementById('healthPillSlides');
                if (data.has_presentation) {
                    dotSlides.className = 'pill-dot connected';
                    stateSlides.innerText = '🟢 1080p Ativo';
                    pillSlides.className = 'source-health-pill is-active';
                } else {
                    dotSlides.className = 'pill-dot disconnected';
                    stateSlides.innerText = data.connected ? '⚪ Aguardando' : '⚪ Sem Slides';
                    pillSlides.className = 'source-health-pill';
                }

                if (prevConnected && data.connected) {
                    if (!prevHasPresentation && data.has_presentation) {
                        triggerSourceAlert('📊', 'Apresentação/Slides iniciados pelo palestrante!', 'success');
                    } else if (prevHasPresentation && !data.has_presentation) {
                        triggerSourceAlert('📊', 'Apresentação/Slides pausados ou finalizados.', 'info');
                    }
                }
                prevHasPresentation = !!data.has_presentation;

                // 3. Monitor de Câmeras & Detecção de Entrada/Saída
                const dotCams = document.getElementById('dotCams');
                const stateCams = document.getElementById('healthStateCams');
                const pillCams = document.getElementById('healthPillCams');
                stateCams.innerText = cams.length > 0 ? (cams.length + ' Ativa(s)') : '0 Câmeras';
                if (cams.length > 0) {
                    dotCams.className = 'pill-dot connected';
                    pillCams.className = 'source-health-pill is-active';
                } else {
                    dotCams.className = 'pill-dot disconnected';
                    pillCams.className = 'source-health-pill';
                }

                const currentCamMap = new Map(cams.map(c => [String(c.id), c.name]));
                if (prevConnected && data.connected) {
                    // Câmera entrou
                    currentCamMap.forEach((name, id) => {
                        if (!prevCamMap.has(id)) {
                            triggerSourceAlert('🎥', 'Nova câmera conectada: ' + name, 'success');
                        }
                    });
                    // Câmera saiu
                    prevCamMap.forEach((name, id) => {
                        if (!currentCamMap.has(id)) {
                            triggerSourceAlert('🔴', 'Câmera desconectada: ' + name, 'warn');
                        }
                    });
                }
                prevCamMap = currentCamMap;

                // 4. Intérprete de Libras
                const dotLibras = document.getElementById('dotLibras');
                const stateLibras = document.getElementById('healthStateLibras');
                const pillLibras = document.getElementById('healthPillLibras');
                if (librasCams.length > 0) {
                    dotLibras.className = 'pill-dot connected';
                    stateLibras.innerText = '🟢 ' + (librasCams[0].name.split(' ')[0] || 'Libras');
                    pillLibras.className = 'source-health-pill is-active';
                } else {
                    dotLibras.className = 'pill-dot disconnected';
                    stateLibras.innerText = '⚪ Nenhum';
                    pillLibras.className = 'source-health-pill';
                }

                if (prevConnected && data.connected) {
                    if (prevLibrasCount === 0 && librasCams.length > 0) {
                        triggerSourceAlert('🤟', 'Intérprete de Libras reconhecido: ' + librasCams[0].name, 'success');
                    } else if (prevLibrasCount > 0 && librasCams.length === 0) {
                        triggerSourceAlert('🤟', 'Intérprete de Libras desconectou.', 'warn');
                    }
                }
                prevLibrasCount = librasCams.length;

                // 5. YouTube Live Chat
                const dotYt = document.getElementById('dotYt');
                const stateYt = document.getElementById('healthStateYt');
                const pillYt = document.getElementById('healthPillYt');
                if (data.youtube_connected) {
                    dotYt.className = 'pill-dot connected';
                    stateYt.innerText = '🟢 Ao Vivo';
                    pillYt.className = 'source-health-pill is-active';
                } else {
                    dotYt.className = 'pill-dot disconnected';
                    stateYt.innerText = '⚪ ' + (data.youtube_status || 'Desconectado');
                    pillYt.className = 'source-health-pill';
                }

                // 6. Áudio Digital PCM
                const dotAudio = document.getElementById('dotAudio');
                const stateAudio = document.getElementById('healthStateAudio');
                const pillAudio = document.getElementById('healthPillAudio');
                const pcmListeners = data.pcm_listeners || 0;
                if (pcmListeners > 0) {
                    dotAudio.className = 'pill-dot connected';
                    stateAudio.innerText = '🟢 ' + pcmListeners + ' OBS Ouvinte(s)';
                    pillAudio.className = 'source-health-pill is-active';
                } else if (data.connected) {
                    dotAudio.className = 'pill-dot connected';
                    stateAudio.innerText = '🟢 PCM Ativo';
                    pillAudio.className = 'source-health-pill is-active';
                } else {
                    dotAudio.className = 'pill-dot disconnected';
                    stateAudio.innerText = '⚪ Standby';
                    pillAudio.className = 'source-health-pill';
                }

                const savedCount = (data.libras_saved_names || []).length;
                const elSavedCount = document.getElementById('savedInterpretersCount');
                if (elSavedCount) elSavedCount.innerText = savedCount + ' salvos';

                const elAuto = document.getElementById('btnAutoLibras');
                if (elAuto) {
                    if (data.libras_auto_detect) {
                        elAuto.className = 'btn-ctrl active-pin';
                        elAuto.innerText = '🛡️ Auto-Reconhecimento: ATIVO';
                    } else {
                        elAuto.className = 'btn-ctrl';
                        elAuto.innerText = '⚪ Auto-Reconhecimento: DESLIGADO';
                    }
                }

                if (data.stage_preset) {
                    activeStagePreset = data.stage_preset;
                    updatePresetButtonsUI(activeStagePreset);
                }

                if (data.libras_size) {
                    activeLibrasSize = data.libras_size;
                    const el = document.getElementById('selLibrasSize');
                    if (el) el.value = activeLibrasSize;
                }

                if (data.poll_display_mode) {
                    const elMode = document.getElementById('selPollDisplayMode');
                    if (elMode) elMode.value = data.poll_display_mode;
                }

                if (data.poll_display_duration !== undefined) {
                    const elDur = document.getElementById('selPollDuration');
                    if (elDur) elDur.value = String(data.poll_display_duration);
                }

                if (data.poll_style) {
                    const elStyle = document.getElementById('selPollStyle');
                    if (elStyle) elStyle.value = data.poll_style;
                }

                if (data.chat_source_filter) {
                    activeChatSourceFilter = data.chat_source_filter;
                    updateChatSourceButtonsUI(activeChatSourceFilter);
                }

                if (data.connected && !(window.RnpDock && window.RnpDock.previewOff
                        && window.RnpDock.previewOff.presentation)) {
                    document.getElementById('thumbScreen').src = '/raw_frame/presentation?t=' + Date.now();
                }

                // Poll Sync
                renderPoll(data.poll);

                // Featured Message Sync
                activeFeaturedMsg = data.featured_message || null;
                renderFeaturedBox(activeFeaturedMsg);

                // Messages sync
                currentMessages = data.chat_messages || [];
                renderChatList(currentMessages);

                // Cameras sync
                currentCameras = cams;
                document.getElementById('camTotalCount').innerText = cams.length;

                // A mesa de som usa trilhas identificadas em /api/media, inclusive sem câmera.
                
                const grid = document.getElementById('camerasGrid');
                grid.innerHTML = '';

                if (cams.length === 0) {
                    grid.innerHTML = '<div style="font-size: 12px; padding: 10px">Nenhuma câmera ligada no momento.</div>';
                } else {
                    cams.forEach(c => {
                        const cid = String(c.id);
                        const isFocus = focusedCamIds.includes(cid);
                        const isPin = pinnedCamIds.includes(cid);
                        const isLibras = (c.is_libras || librasCamIds.includes(cid));

                        const box = document.createElement('div');
                        box.className = 'mod-box';
                        if (isFocus) box.style.borderColor = '#eab308';
                        if (isLibras) box.style.borderColor = '#06b6d4';
                        if (isPin && !isFocus && !isLibras) box.style.borderColor = '#10b981';

                        const safeCidAttr = escHtml(escJsStr(cid));
                        const safeNameAttr = escHtml(escJsStr(c.name));
                        box.innerHTML = `
                            <div class="mod-title">
                                <span style="display:flex; align-items:center; gap:5px">
                                    ${isLibras ? '🤟' : (isFocus ? '🌟' : (isPin ? '📌' : '🎥'))} <strong>${escHtml(c.name)}</strong>
                                </span>
                                <span style="font-size: 10.5px">● ${Number(c.width) || 0}x${Number(c.height) || 0}</span>
                                <span class="cam-fps" style="font-size: 10.5px">${Math.round(c.fps || 0)} FPS</span>
                            </div>
                            
                            <div class="cam-controls">
                                <button class="btn-ctrl ${isFocus ? 'active-focus' : ''}" onclick="toggleFocus('${safeCidAttr}')">
                                    🌟 ${isFocus ? 'Desfocar' : 'Focar'}
                                </button>
                                <button class="btn-ctrl ${isLibras ? 'active-libras' : ''}" onclick="toggleLibras('${safeCidAttr}')">
                                    🤟 ${isLibras ? 'Libras (Ativo)' : 'Libras'}
                                </button>
                                <button class="btn-ctrl ${isPin ? 'active-pin' : ''}" onclick="togglePin('${safeCidAttr}')">
                                    📌 ${isPin ? 'Desfixar' : 'Fixar'}
                                </button>
                                <button class="btn-ctrl" onclick="renameCamera('${safeCidAttr}', '${safeNameAttr}')">
                                    ✏️ Renomear
                                </button>
                            </div>

                            <button class="btn-copy" onclick="copyUrl('/camera/${safeCidAttr}')">📋 Link Câmera ${escHtml(cid)}</button>
                        `;
                        grid.appendChild(box);
                    });
                }
            } catch (err) {
                document.getElementById('healthHeartbeat').innerText = '🔴 Servidor Offline';
            }
        }

        async function updateGridConfig() {
            const show_camera_names = document.getElementById('chkShowNames').checked;
            const camera_name_style = document.getElementById('selNameStyle').value;
            const grid_layout_mode = document.getElementById('selGridLayout').value;
            const camera_format = document.getElementById('selCamFormat').value;
            await fetch('/api/grid/config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ show_camera_names, camera_name_style, grid_layout_mode, camera_format, stage_preset: activeStagePreset, libras_camera_ids: librasCamIds, libras_size: activeLibrasSize })
            });
            showSaveNotice();
        }

        async function updateChatConfig() {
            const chat_style = document.getElementById('selChatTheme').value;
            const chat_font_size = document.getElementById('selChatSize').value;
            const chat_max_messages = document.getElementById('selChatMax').value;
            const show_chat_avatars = document.getElementById('chkShowAvatars').checked;
            await fetch('/api/chat/config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ chat_style, chat_font_size, chat_max_messages, show_chat_avatars, chat_source_filter: activeChatSourceFilter })
            });
            showSaveNotice();
        }

        async function toggleFocus(camId) {
            // Foco é um CONJUNTO: a tela /tv destaca todos os focados em spotlight.
            const targetId = String(camId);
            let newFocus = [...focusedCamIds];
            if (newFocus.includes(targetId)) {
                newFocus = newFocus.filter(id => id !== targetId);
            } else {
                newFocus.push(targetId);
            }
            await fetch('/api/grid/config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ focused_camera_ids: newFocus })
            });
            fetchStatus();
        }

        async function toggleLibras(camId) {
            const targetId = String(camId);
            let newLibras = [...librasCamIds];
            if (newLibras.includes(targetId)) {
                newLibras = newLibras.filter(id => id !== targetId);
            } else {
                newLibras.push(targetId);
            }
            try {
                const response = await fetch('/api/grid/config', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ libras_camera_ids: newLibras })
                });
                if (!response.ok) throw new Error('Não foi possível salvar Libras. Verifique a câmera e a permissão de gravação.');
                configSaveError = '';
                librasCamIds = newLibras;
                await fetchStatus();
                showSaveNotice();
            } catch (error) {
                configSaveError = error.message;
                document.getElementById('logBox').innerText = configSaveError;
            }
        }

        async function toggleLibrasAutoDetect() {
            const res = await fetch('/api/libras/toggle_auto', { method: 'POST' });
            const data = await res.json();
            const btn = document.getElementById('btnAutoLibras');
            if (btn) {
                if (data.libras_auto_detect) {
                    btn.className = 'btn-ctrl active-pin';
                    btn.innerText = '🛡️ Auto-Reconhecimento: ATIVO';
                } else {
                    btn.className = 'btn-ctrl';
                    btn.innerText = '⚪ Auto-Reconhecimento: DESLIGADO';
                }
            }
            fetchStatus();
            showSaveNotice();
        }

        async function clearSavedLibras() {
            if (!confirm('Deseja limpar todos os nomes de intérpretes salvos e desmarcar Libras de todas as câmeras?')) return;
            await fetch('/api/libras/clear_saved', { method: 'POST' });
            librasCamIds = [];
            fetchStatus();
            showSaveNotice();
        }

        async function togglePin(camId) {
            const targetId = String(camId);
            let newPins = [...pinnedCamIds];
            if (newPins.includes(targetId)) {
                newPins = newPins.filter(id => id !== targetId);
            } else {
                newPins.push(targetId);
            }
            await fetch('/api/grid/config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ pinned_camera_ids: newPins })
            });
            fetchStatus();
        }

        async function clearFocusAndPins() {
            await fetch('/api/grid/config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ focused_camera_ids: [], pinned_camera_ids: [], libras_camera_ids: [] })
            });
            librasCamIds = [];
            fetchStatus();
        }

        async function renameCamera(camId, currentName) {
            const newName = prompt('Digite o novo nome para esta câmera:\\n(Deixe em branco e clique em OK para restaurar o nome padrão da RNP)', currentName);
            if (newName !== null) {
                await fetch('/api/grid/config', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ cam_id: String(camId), custom_name: newName.trim() })
                });
                fetchStatus();
                showSaveNotice();
            }
        }

        async function handleConnect() {
            const room_url = document.getElementById('roomUrl').value.trim();
            const user_name = document.getElementById('userName').value.trim();
            const user_email = document.getElementById('userEmail').value.trim();

            document.getElementById('logBox').innerText = '> Enviando comando de conexão...';
            document.getElementById('statusBadge').className = 'badge connecting';
            document.getElementById('statusBadge').innerHTML = '● Conectando...';

            try {
                const res = await fetch('/api/connect', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ room_url, user_name, user_email })
                });
                const data = await res.json();
                document.getElementById('logBox').innerText = '> ' + data.message;
            } catch (e) {
                document.getElementById('logBox').innerText = '> Erro ao enviar requisição: ' + e;
            }
        }

        async function handleDisconnect() {
            document.getElementById('logBox').innerText = '> Desconectando...';
            try {
                await fetch('/api/disconnect', { method: 'POST' });
            } catch (e) {}
        }

        async function restartBridge() {
            if (!confirm('Reiniciar a ponte? Isso derruba a conexão atual e reconecta à sala.')) {
                return;
            }
            const btn = document.getElementById('btnBarRestart');
            if (btn) { btn.disabled = true; btn.textContent = '↻ Reiniciando...'; }
            try {
                await fetch('/api/restart', { method: 'POST' });
            } catch (e) {}
            // Recarrega após o reset agendar o reconector, para refletir o novo estado.
            setTimeout(() => location.reload(), 2000);
        }

        async function handleExitApplication() {
            if (!confirm('Deseja realmente desconectar da sala RNP e encerrar o aplicativo por completo?')) {
                return;
            }
            document.getElementById('logBox').innerText = '> Encerrando servidor e desconectando da sala...';
            try {
                await fetch('/api/server/stop', { method: 'POST' });
            } catch (e) {}
            setTimeout(() => {
                window.close();
            }, 600);
        }

        function renderPoll(poll) {
            const card = document.getElementById('pollDockCard');
            if (card) card.style.display = 'block';

            const statusTitle = document.getElementById('pollStatusTitle');
            const badgeStatus = document.getElementById('pollBadgeStatus');
            const questionText = document.getElementById('pollQuestionText');
            const optsContainer = document.getElementById('pollOptionsContainer');

            if (!poll || !poll.is_active) {
                if (statusTitle) {
                    statusTitle.innerText = 'Enquete da Sala RNP';
                }
                if (badgeStatus) {
                    badgeStatus.className = 'badge disconnected';
                    badgeStatus.innerText = '⚪ Standby (Pronto para o OBS)';
                }
                if (questionText) {
                    questionText.innerHTML = '<span style="font-weight: normal; font-size: 12px">Nenhuma enquete ativa na sala no momento. O link acima pode ser adicionado ao OBS como <strong>Navegador (Browser Source 1920x1080 transparente)</strong> antes da transmissão começar. Quando uma enquete for aberta na RNP, ela aparecerá na tela automaticamente!</span>';
                }
                if (optsContainer) optsContainer.innerHTML = '';
                return;
            }

            const isPublished = (poll.published || poll.status === 'published' || poll.status === 'results');
            if (statusTitle) {
                if (isPublished) {
                    statusTitle.innerText = 'Resultados Finais (' + (poll.total_votes || 0) + ' votos)';
                } else {
                    statusTitle.innerText = 'Enquete em Votação na Sala';
                }
            }
            if (badgeStatus) {
                if (isPublished) {
                    badgeStatus.className = 'badge connected';
                    badgeStatus.innerText = '🟢 Publicada no OBS';
                } else {
                    badgeStatus.className = 'badge connecting';
                    badgeStatus.innerText = '🟡 Votação Aberta';
                }
            }

            if (questionText) {
                questionText.innerText = poll.question || 'Opções da Enquete';
            }

            if (optsContainer) {
                optsContainer.innerHTML = '';
                const totalVotes = poll.total_votes || 0;

                (poll.options || []).forEach(opt => {
                    const row = document.createElement('div');
                    row.style.cssText = 'background: rgba(30, 41, 59, 0.7); border: 1px solid rgba(255,255,255,0.08); border-radius: 8px; padding: 6px 10px;';
                    
                    const pct = opt.percentage || (totalVotes > 0 ? Math.round((opt.votes / totalVotes) * 100) : 0);
                    const votes = opt.votes || 0;

                    row.innerHTML = `
                        <div style="display: flex; justify-content: space-between; font-size: 12px; font-weight: 700; margin-bottom: 4px">
                            <span>${escHtml(opt.text)}</span>
                            <span >${votes > 0 ? votes + ' votos (' + pct + '%)' : pct + '%'}</span>
                        </div>
                        <div style="width: 100%; height: 5px; border-radius: 3px; overflow: hidden">
                            <div style="width: ${Number(pct) || 0}%; height: 100%; transition: width 0.3s ease"></div>
                        </div>
                    `;
                    optsContainer.appendChild(row);
                });
            }
        }

        async function updatePollConfig() {
            const poll_display_mode = document.getElementById('selPollDisplayMode').value;
            const poll_display_duration = parseInt(document.getElementById('selPollDuration').value);
            const poll_style = document.getElementById('selPollStyle').value;
            await postSave('/api/poll/config', { poll_display_mode, poll_display_duration, poll_style });
        }

        async function toggleForcePoll(show) {
            // /api/poll/show|hide não recebem payload; postSave envia {}.
            await postSave(show ? '/api/poll/show' : '/api/poll/hide', {});
        }

        async function clearPoll() {
            if (!confirm('Deseja limpar e reiniciar a enquete atual?')) return;
            await fetch('/api/poll/clear', { method: 'POST' });
            fetchStatus();
        }

        async function simulatePoll() {
            await fetch('/api/poll/simulate', { method: 'POST' });
            const toast = document.getElementById('saveToast');
            if (toast) {
                toast.innerText = '🧪 Enquete de Teste Exibida no OBS!';
                toast.style.display = 'inline-block';
                setTimeout(() => { toast.style.display = 'none'; }, 2500);
            }
            fetchStatus();
        }

        async function changeControlTheme(th) {
            document.body.className = 'theme-' + th;
            await fetch('/api/theme/set', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ theme: th })
            });
            showSaveNotice();
        }

        function fallbackCopy(text) {
            const ta = document.createElement('textarea');
            ta.value = text;
            ta.style.position = 'fixed';
            ta.style.opacity = '0';
            document.body.appendChild(ta);
            ta.select();
            try { document.execCommand('copy'); } catch(e){}
            document.body.removeChild(ta);
        }

        function copyUrl(url, btnElement) {
            url = new URL(url, window.location.origin).href;
            if (navigator.clipboard && navigator.clipboard.writeText) {
                navigator.clipboard.writeText(url).catch(() => {
                    fallbackCopy(url);
                });
            } else {
                fallbackCopy(url);
            }

            const toast = document.getElementById('saveToast');
            if (toast) {
                const endpoint = url.replace('http://localhost:3333', '');
                toast.innerText = '📋 Link Copiado: ' + (endpoint || url);
                toast.style.display = 'inline-block';
                setTimeout(() => { toast.style.display = 'none'; }, 2500);
            }

            if (btnElement) {
                const orig = btnElement.innerHTML;
                btnElement.innerHTML = '✅ Copiado!';
                btnElement.style.filter = 'brightness(1.3)';
                setTimeout(() => {
                    btnElement.innerHTML = orig;
                    btnElement.style.filter = 'none';
                }, 1500);
            }
        }

        // Alterna o acesso LAN (bind 0.0.0.0). O servidor só aplica no boot, então
        // avisamos que é preciso reiniciar o app. Gravamos via /api/config/save.
        async function toggleLanAccess(checked) {
            const lbl = document.getElementById('lblLanAccess');
            const box = document.getElementById('lanUrlBox');
            const hint = document.getElementById('lanHint');
            if (lbl) lbl.textContent = checked ? 'Ligado' : 'Desligado';
            if (box) box.style.display = checked ? 'block' : 'none';
            let restart = false;
            try {
                const res = await fetch('/api/config/save', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ lan_access: checked })
                });
                const corpo = await res.json().catch(() => ({}));
                if (!res.ok) throw new Error(corpo.message || ('HTTP ' + res.status));
                restart = !!corpo.restart_needed;
                showSaveNotice(true);
            } catch (erro) {
                showSaveNotice(false, erro.message);
                // Reverte o toggle para o estado que continua valendo no servidor.
                const chk = document.getElementById('chkLanAccess');
                if (chk) chk.checked = !checked;
                if (lbl) lbl.textContent = !checked ? 'Ligado' : 'Desligado';
                if (box) box.style.display = !checked ? 'block' : 'none';
                return;
            }
            if (hint) {
                if (restart && checked) {
                    hint.innerHTML = '⚠️ <strong>Reinicie o app</strong> para o servidor passar a escutar na rede. Depois use os links acima na outra máquina e, se preciso, libere a porta no Firewall do Windows.';
                    hint.style.color = '#fbbf24';
                } else if (restart && !checked) {
                    hint.innerHTML = '⚠️ <strong>Reinicie o app</strong> para voltar a escutar só em localhost (mais seguro).';
                    hint.style.color = '#fbbf24';
                } else {
                    hint.style.color = '';
                }
            }
        }

        // Copia uma URL LAN (usa o IP da rede, não o localhost do dock). Ideal para
        // colar no navegador da TV/notebook que vai exibir /tv, /stage etc.
        function copyLanUrl(path, btnElement) {
            const base = window._lanBaseUrl;
            if (!base) {
                showSaveNotice(false, 'IP da rede indisponível');
                return;
            }
            const url = base + path;
            if (navigator.clipboard && navigator.clipboard.writeText) {
                navigator.clipboard.writeText(url).catch(() => fallbackCopy(url));
            } else {
                fallbackCopy(url);
            }
            const toast = document.getElementById('saveToast');
            if (toast) {
                toast.innerText = '📋 Link LAN Copiado: ' + url;
                toast.style.display = 'inline-block';
                setTimeout(() => { toast.style.display = 'none'; }, 2500);
            }
            if (btnElement) {
                const orig = btnElement.innerHTML;
                btnElement.innerHTML = '✅ Copiado!';
                setTimeout(() => { btnElement.innerHTML = orig; }, 1500);
            }
        }

        // Copia um comando do tutorial de energia do Windows (card #energiaCard).
        // O texto sai do <code> filho, então não há duplicação entre o que é exibido
        // e o que é copiado — e os caminhos de registro chegam com a barra invertida
        // única que o reg.exe/CMD espera. Fica no escopo global de propósito: o
        // onclick= inline do f-string em server.py precisa enxergar window.copiarComando.
        function copiarComando(btnElement) {
            const code = btnElement && btnElement.querySelector('code');
            const texto = code ? code.textContent.trim() : '';
            if (!texto) {
                showSaveNotice(false, 'Comando vazio');
                return;
            }
            if (navigator.clipboard && navigator.clipboard.writeText) {
                navigator.clipboard.writeText(texto).catch(() => fallbackCopy(texto));
            } else {
                fallbackCopy(texto);
            }
            const toast = document.getElementById('saveToast');
            if (toast) {
                toast.innerText = '📋 Comando copiado — cole num terminal como Administrador';
                toast.style.display = 'inline-block';
                setTimeout(() => { toast.style.display = 'none'; }, 2500);
            }
            if (btnElement) {
                btnElement.classList.add('copiado');
                setTimeout(() => { btnElement.classList.remove('copiado'); }, 1200);
            }
        }

        setInterval(fetchStatus, 1000);
        fetchStatus();

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
    //
    // PADRÃO DESLIGADO nos três. Não é cosmético: cada preview é uma página inteira a
    // mais dentro do dock — o grid de câmeras e o palco abrem os PRÓPRIOS receptores
    // WebRTC (decodificando vídeo), e o palco ainda roda um segundo player PCM; o
    // thumb da apresentação força um /raw_frame/presentation no servidor.
    // Uma preferência já gravada em localStorage continua valendo sobre o padrão,
    // então quem ligou antes não perde a escolha.
    //
    // Honestidade sobre o ganho: medido ao vivo com a sala conectada, a diferença no
    // CPU agregado da máquina ficou em ~0,5 ponto percentual — dentro do ruído, porque
    // a máquina já estava em ~97% (navegador de captura ~23%, OBS ~21%, páginas de
    // navegador do OBS ~10%, IDE ~14%). Com o teto de CPU atingido, o agregado não
    // mostra custo marginal, então isto NÃO está aqui como "a" economia de CPU. O corte
    // vale mesmo assim: são dois decodificadores de vídeo e um player PCM a menos
    // disputando o que sobra — e o que sobra é justamente o que decide se o event loop
    // do servidor atrasa e o áudio picota.
    const PREVIEW_KEY = 'rnpDockPreviewsOff';
    const PREVIEW_ALVO = {
        cameras: 'gridPreviewIframe',
        presentation: 'thumbScreen',
        stage: 'stagePreviewIframe',
    };
    const PREVIEW_BOTAO = {
        cameras: 'btnToggleCamPreview',
        presentation: 'btnToggleScreenPreview',
        stage: 'btnToggleStagePreview',
    };
    let previewOff = { cameras: true, presentation: true, stage: true };
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

    // FPS e conexão compartilham a consulta já existente; não há ping ao localhost.
    const fpsEl = document.getElementById('dockFps');
    const connectionMeter = document.getElementById('connectionMeter');
    const connectionDetails = document.getElementById('connectionDetails');
    const qualityLabels = {good: 'Boa', fair: 'Atenção', poor: 'Ruim',
        unknown: 'Sem dados', disconnected: 'Desconectado', connecting: 'Conectando',
        unavailable: 'Ponte indisponível'};
    const metric = (n, suffix) => typeof n === 'number' && Number.isFinite(n)
        ? n.toLocaleString('pt-BR', {maximumFractionDigits: 1}) + suffix : '—';

    function showConnectionDetails(show) {
        if (!connectionMeter || !connectionDetails) return;
        connectionMeter.setAttribute('aria-expanded', String(show));
        if (show) {
            const rect = connectionMeter.getBoundingClientRect();
            connectionDetails.style.top = (rect.bottom + 6) + 'px';
            connectionDetails.style.left = Math.max(12,
                Math.min(rect.left, window.innerWidth - connectionDetails.offsetWidth - 12)) + 'px';
        }
    }
    if (connectionMeter) {
        const wrap = connectionMeter.parentElement;
        connectionMeter.addEventListener('focus', () => showConnectionDetails(true));
        connectionMeter.addEventListener('click', () => showConnectionDetails(true));
        connectionMeter.addEventListener('blur', () => showConnectionDetails(false));
        wrap.addEventListener('mouseenter', () => showConnectionDetails(true));
        wrap.addEventListener('mouseleave', () => {
            if (document.activeElement !== connectionMeter) showConnectionDetails(false);
        });
        document.addEventListener('keydown', e => {
            if (e.key === 'Escape') showConnectionDetails(false);
        });
        window.addEventListener('resize', () => showConnectionDetails(false));
    }

    function renderConnection(quality) {
        if (!connectionMeter) return;
        const q = quality || {};
        const level = Object.hasOwn(qualityLabels, q.level) ? q.level : 'unknown';
        const label = qualityLabels[level];
        connectionMeter.dataset.level = level;
        document.getElementById('connectionLabel').textContent = label;
        document.getElementById('connectionRtt').textContent = q.rtt_ms == null ? '' : metric(q.rtt_ms, ' ms');
        connectionMeter.setAttribute('aria-label', 'Qualidade da conexão RNP: ' + label);
        const lit = {good: 4, fair: 2, poor: 1}[level] || 0;
        connectionMeter.querySelectorAll('.connection-bars i').forEach((bar, i) => bar.classList.toggle('is-lit', i < lit));
        const reason = {
            unknown: 'Sem estatísticas recentes. Aguardando mídia WebRTC da sala.',
            disconnected: 'Conecte a sala para medir a mídia recebida.',
            connecting: 'Aguardando a conexão com a sala RNP.',
            unavailable: 'O painel não conseguiu consultar a ponte local. A conexão RNP não pôde ser verificada.'
        }[level] || ('Latência (RTT): ' + metric(q.rtt_ms, ' ms') + '\nJitter: ' + metric(q.jitter_ms, ' ms') +
            '\nPerda de pacotes: ' + metric(q.loss_pct, '%'));
        connectionDetails.textContent = 'Entrada RNP · ' + label + '\n' + reason +
            '\nPior valor entre as trilhas recebidas; atualização a cada 2 s.' +
            '\nAtenção: RTT ≥ 300 ms, jitter ≥ 30 ms ou perda ≥ 2%.' +
            '\nRuim: RTT ≥ 600 ms, jitter ≥ 60 ms ou perda ≥ 5%, ou transporte interrompido.' +
            '\nNão mede velocidade da internet nem a saída do OBS.';
    }

    let statusPending = false;
    async function pollDockStatus() {
        if (document.hidden || statusPending) return;
        statusPending = true;
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 3000);
        try {
            const response = await fetch('/api/status?t=' + Date.now(), {cache: 'no-store', signal: controller.signal});
            if (!response.ok) throw new Error('Status indisponível');
            const st = await response.json();
            if (fpsEl) {
                const valores = (st.cameras || []).map(c => c.fps).filter(v => typeof v === 'number');
                fpsEl.textContent = valores.length
                    ? Math.round(valores.reduce((a, b) => a + b, 0) / valores.length) : '--';
            }
            renderConnection(st.connection_quality || {level: st.connecting ? 'connecting' : st.connected ? 'unknown' : 'disconnected'});
        } catch (_) {
            if (fpsEl) fpsEl.textContent = '--';
            renderConnection({level: 'unavailable'});
        } finally {
            clearTimeout(timeout);
            statusPending = false;
        }
    }
    if (fpsEl || connectionMeter) {
        pollDockStatus();
        setInterval(pollDockStatus, 2000);
        document.addEventListener('visibilitychange', () => {
            if (!document.hidden) { renderConnection({level: 'unknown'}); pollDockStatus(); }
        });
    }

    window.RnpDock = {
        activateTab,
        applyBlockOrder,
        currentOrder,
        togglePreview,
        get previewOff() { return previewOff; },
    };
})();
