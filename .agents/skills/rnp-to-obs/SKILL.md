---
name: rnp-to-obs
description: >-
  Guia completo e procedimentos operacionais da ponte RNP WebConf / BigBlueButton + YouTube Live para OBS Studio.
  Use sempre que for gerenciar, estender, debugar ou operar a ponte de streaming, endpoints de vídeo, grid de câmeras,
  janela paritária de Libras, enquetes ao vivo, áudio PCM Float32 e o painel de controle (Dock do OBS).
---

# 🚀 RNP to OBS Studio Bridge - Manual Operacional e Referência Técnica

Este guia documenta toda a arquitetura, endpoints, fluxos de áudio/vídeo e procedimentos de controle da ponte de transmissão da RNP (BigBlueButton) para o OBS Studio.

---

## 🌐 Endpoints Disponíveis para o OBS Studio

Todos os links são servidos em `http://localhost:3333` com fundo 100% transparente para encaixe perfeito em cenas do OBS:

| Endpoint | Tipo no OBS | Descrição |
| :--- | :--- | :--- |
| `http://localhost:3333/control` | **Navegador Personalizado (Dock)** | Painel de controle completo para encaixar na interface do OBS. |
| `http://localhost:3333/stage` | **Navegador (Browser Source)** | Palco composto (Slides + Câmeras + Libras paritário) **e master de áudio** (PCM Float32 / 48 kHz). |
| `http://localhost:3333/cameras/grid`| **Navegador (Browser Source)** | Grid dinâmico e responsivo de todas as webcams abertas. |
| `http://localhost:3333/tv` | **Navegador (TV/monitor)** | Tela de participantes: spotlight em quem está focado, fila com os demais, Libras fora. `?chat=0` oculta a mensagem destacada. |
| `http://localhost:3333/libras` | **Navegador (Browser Source)** | Janela dedicada e isolada para Intérprete de Libras. |
| `http://localhost:3333/presentation`| **Navegador (Browser Source)** | Slides ou compartilhamento de tela puro (1080p/2.5K). |
| `http://localhost:3333/chat` | **Navegador (Browser Source)** | Feed de chat ao vivo unificado (RNP + YouTube). |
| `http://localhost:3333/chat/featured`| **Navegador (Browser Source)**| Lower-third animado para destaque de mensagens. |
| `http://localhost:3333/poll` | **Navegador (Browser Source)** | Card moderno de resultados de enquete com temporizador. |
| `http://localhost:3333/audio?source=<canal>` | **Navegador (Browser Source)** | Áudio **individual** de um participante. O master não sai mais por `/audio` (sem `source` devolve 404) — ele vem do `/stage`. |

---

## 📺 Tela de Participantes (`/tv`)

- **Só participantes**: câmeras marcadas como Libras ficam de fora (elas têm `/libras`).
- **Spotlight**: câmeras em **foco** ocupam a área grande; as demais vão para uma fila
  inferior (24vh). **Sem foco**: grade igualitária com todos.
- **Foco é um CONJUNTO** (`focused_camera_ids` no `/api/status` e no `/api/grid/config`):
  o dock permite várias câmeras focadas ao mesmo tempo (botão 🌟 alterna pertença).
  O campo legado `focused_camera_id` continua aceito (single vira lista de 1; `null`
  limpa) e é o que o `/stage` usa como "palestrante" dos presets PIP/split = primeiro focado.
- **Pin** (`pinned_camera_ids`) dá borda azul; foco dá borda verde; quem fala fica com
  nome/borda verdes. Nomes respeitam `show_camera_names`.
- **Chat destacado**: overlay inferior com a `featured_message` (mesma do `/chat/featured`);
  `?chat=0` desliga. Padrão: ligado.
- Transporte: reusa `RnpStageMedia` (stage-media.js) — as conexões WebRTC sobrevivem às
  trocas de foco/layout (reconciliação por identidade, sem renegociar).
- Fundo escuro opaco (não transparente): é tela de exibição, não overlay de OBS.

---

## 🎛️ Presets de Cena no Palco Composto (`/stage`)

- `side-by-side`: Slides em 68% à esquerda + Câmeras em 32% à direita.
- `bottom-bar`: Slides no topo + Câmeras no rodapé.
- `pip-bottom-right` / `pip-top-right`: Slides em tela cheia com palestrante flutuante em PIP (380x215).
- `split-50-50`: Divisão paritária meio a meio.
- `slide-only`: Apenas apresentação.
- `cameras-only`: **Sem slide** — todas as câmeras preenchendo o quadro; Libras como janela flutuante.
- `camera-libras`: **Sem slide** — câmera principal (focada/primeira) + Libras em paridade 50/50.
- `grid-libras`: **Sem slide** — todas as câmeras + Libras na linha, sem apresentação.

---

## 🤟 Acessibilidade em Libras (Padrão IFES / ABNT)

- **Modo `equal` (Padrão):** O intérprete de Libras divide a coluna ou rodapé com 50% de altura/largura idêntica ao palestrante.
- **Modos Flutuantes:** `huge` (560x315), `large` (480x270) e `medium` (380x214).
- **Auto-Reconhecimento:** Reconhece nomes salvos e palavras-chave (`Libras`, `Intérprete`, `ILS`). Se o intérprete cair e voltar, ele retorna automaticamente no tamanho correto.

---

## 📊 Sistema de Enquetes com Temporizador (`/poll`)

- **Modo Somente Resultados Finais:** Permanece oculto durante a votação e surge apenas quando o apresentador publica os resultados.
- **Temporizador na Tela:** 10s, 15s (padrão), 30s, 60s ou permanente.
- **Auto-Exit:** Faz transição suave de saída após a expiração do tempo.

---

## 📡 Medidor de Qualidade da Conexão (barra do `/control`)

- **Pill na barra fixa**: farol + barras (Boa / Atenção / Ruim / Sem dados) + RTT em ms; detalhe no hover/foco com RTT, jitter e perda de pacotes.
- **O que mede**: a telemetria WebRTC da mídia **recebida da sala RNP** (`getStats` dos peers de entrada), não um ping. Pingar `127.0.0.1` daria ~0 ms e ICMP é filtrado pelos nós da RNP; o relay local que entrega mídia ao OBS é ignorado (`RnpConnection.ignore`).
- **Coleta**: `web/connection-quality.js` instala um Proxy sobre `RTCPeerConnection` antes do cliente BBB nascer; `sync_loop` puxa o snapshot a cada ~2 s. Perda é calculada por **delta na janela**, nunca o acumulado da sessão.
- **Faixas**: Atenção em RTT ≥ 300 ms / jitter ≥ 30 ms / perda ≥ 2%; Ruim em RTT ≥ 600 ms / jitter ≥ 60 ms / perda ≥ 5% ou transporte interrompido. É o **pior valor** entre as trilhas recebidas.
- **API**: `/api/status` expõe `connection_quality: {level, rtt_ms, jitter_ms, loss_pct, age_ms}`. Amostra vencida (> 6 s) vira `unknown` (não reutiliza valor velho).

---

## 🔊 Arquitetura de Áudio (PCM WebRTC) e Estabilidade

- Captura as trilhas `<audio>/<video>` da conferência via **Web Audio API** na página da sala
  (`startPcm` em `web/media-source.js` → `/ws/pcm_in`), com analisador por canal.
- **Navegador de captura (`RNP_BROWSER_CHANNEL`):** `server.py` lança o navegador de captura percorrendo `["msedge", "chrome", "chromium", None]`. O Chromium completo (`channel="chromium"`) entra **antes** do fallback `None` porque `headless=True` sem `channel` no Playwright sobe o `chrome-headless-shell` (build reduzido sem pilha de áudio real, que reduz o clock do `AudioContext` para ~0,625×). A variável `RNP_BROWSER_CHANNEL` (`msedge`, `chrome`, `chromium`/`full`, `shell`) permite fixar o canal sem editar código.
- **Web Worker + `MessageChannel` (anti-stutter):** o `AudioWorkletProcessor` (`PcmCapture`, blocos de 2048 amostras ≈ 42,7 ms) transfere cada chunk por `MessagePort` diretamente para um **Web Worker dedicado** (`WORKER_SRC`), que mantém o WebSocket `/ws/pcm_in`. Assim, travamentos periódicos da *main thread* do BBB (React/SVG/DOM) não represam o envio de áudio.
- **Chat do YouTube sem bloquear o event loop (`youtube_chat_loop`):** `pytchat` é consumido via `await asyncio.to_thread(chat.get)` lendo diretamente `data.items` (lista pronta). **Nunca** iterar `data.sync_items()` na thread do event loop: no `pytchat`, `sync_items()` é um generator que executa `time.sleep(1.0)` síncrono ao final de cada lote, o que congelava o event loop por 1 s a cada 2 s e causava underrun/rajada no `/stage` do OBS.
- Transmite por WebSocket binário (`/ws/pcm_out`) em **Float32 48.000 Hz** usando filas não-bloqueantes por cliente (`_enqueue_latest`, `PCM_QUEUE_MAX=64`, drop-oldest) e jitter buffer de `TARGET_LEAD = 0.12s` no player (`PCM_AUDIO_SCRIPT`, com `bufferSource.onended -> disconnect()`).
- **Telemetria ponta a ponta (`pcm_diag` em `/api/status` e `/api/pcm_playback`):** expõe `chegada_pcm_in`, `event_loop_atraso_ms` (`loop_lag_monitor`), `captura` (`gerados`, `enviados_ws`, `via_worker`), `ouvintes` e `playback` por cliente (`/stage`, `/control`, incluindo `rearmar_colado`, `rearmar_adiantado` e `lead_ms`).
- **Master:** é reproduzido pelo `/stage` (`PCM_AUDIO_SCRIPT`) e pelo monitor local do `/control`. Não há segundo player em `/audio` — dois masters duplicavam o consumo no OBS e causavam eco.
- **No OBS:** na fonte `/stage`, marque "Controlar áudio via OBS" e **desligue**
  "Shutdown source when not visible", senão o som some em cenas sem o palco.
- A captura continua ativa mesmo sem ninguém ouvindo: é dela que vêm os picos usados
  na detecção de fala (`is_speaking` / chips de voz), com *peak-hold* τ=300 ms.
- **Padrões de economia de CPU no `/control`:**
  - Previews de vídeo (`cameras`, `presentation`, `stage`) nascem **desligados** (`previewOff = { cameras: true, presentation: true, stage: true }` em `web/dock.js`).
  - Monitor local de áudio nasce **mudo** (`savedMuted !== '0'`).
  - Medidores VU individuais nascem **desligados** (`window.RnpMeters` / `#btnToggleMeters` em `web/audio-mixer.js`, classe `.medidores-desligados` em `web/dock.css`); quando desligados, o dock não abre `/ws/peaks_out` e reduz o poll de `/api/media` de `500 ms` para `3000 ms`.
- **Card de Ajustes de Energia do Windows (`#energiaCard` no `/control`):** tutorial recolhível com 9 grupos e 29 comandos clicáveis (`copiarComando`) para `powercfg` (`PROCTHROTTLEMIN`, `PERFBOOSTMODE`, `CPMINCORES`, `ASPM` PCIe, suspensão seletiva USB), MMCSS (`NetworkThrottlingIndex`, `SystemResponsiveness`, `Tasks\Audio`) e `PowerThrottlingOff`.

---

## 🌐 Modo LAN, Segurança e Portabilidade

- **`lan_access` (`config.json` / barra do `/control`):** quando habilitado, o servidor faz bind em `0.0.0.0:3333` (requer reinício do app ao alternar) e exibe o box `#lanUrlBox` (`.lan-url-box`) com o link `http://<IP>:3333/tv` para abrir a tela de retorno em outras máquinas da rede.
- **Proteções ativas:**
  - Todas as rotas `POST` mutáveis exigem `require_same_origin(req)` (`media_bridge.py`).
  - `/api/server/stop` aceita apenas clientes loopback (`127.0.0.1`, `::1`, `localhost`).
  - `/api/connect` e `/api/config/save` validam `room_url` (`http://` ou `https://`).
  - Nomes de câmeras, mensagens de chat e opções de enquete passam por `escHtml` no `/control` (`web/dock.js`) e em todos os overlays (`server.py`).
- **Portabilidade:** `RNP_PORT` (padrão `3333`) é respeitado em `server.py`, `app.py` e `dock_window.py`.

---

## 🎨 Gerador de CSS para BBB Direto na Browser Source (`tools/gerador_css_obs.html`)

- Ferramenta standalone que consolida os seletores de `css_custom_discord.css` e `css_custom_v0.1.css` para estilizar o **DOM do cliente BBB** dentro de uma Browser Source do OBS.
- ⛔ **Não funciona no portal da RNP** (`https://conferenciaweb.rnp.br/sala/<slug>`). O OBS injeta o *CSS personalizado* **somente no frame principal** — `obs-browser/browser-client.cpp`, `BrowserClient::OnLoadEnd`: `if (frame->IsMain() && bs->css.length())` anexa um `<style>` ao `head` daquele frame. Nessa URL o documento principal é o **portal Rails** (`/conference/rooms/<slug>/invite_userid`, verificado: 0 ocorrências de `data-test`, `#cameraDock` e `html5client` no HTML) e o cliente BBB (`html5client` / host Mconf) roda num **iframe filho**, onde o CSS do OBS nunca chega. É a mesma razão pela qual `server.py` precisa de `find_active_bbb_frame` para eleger o frame real da reunião.
- **Caminho correto no portal da RNP:** usar os endpoints da ponte, que já são overlays transparentes e prontos — `/cameras/grid`, `/presentation`, `/stage` (presets `cameras-only`, `slide-only`, `grid-libras`…), `/libras`, `/chat`, `/tv`. Eles cobrem os 5 Modos de Cena sem injetar CSS.
- O CSS gerado só tem efeito quando a Browser Source carrega o **cliente BBB como documento principal** (ex.: deployment BBB em que o `html5client` é a URL de topo). O aviso consta na interface (`.aviso.bloqueio`) e no cabeçalho do próprio CSS exportado.
- **5 Modos de Cena (`#modosCena`):**
  1. `cameras-slides`: oculta o chat/sidebar e exibe apenas Câmeras + Slides/Projeção.
  2. `completo`: exibe Chat Público estilizado + Slides/Projeção + Câmeras.
  3. `so-cameras`: oculta chat e apresentação e expande o `#cameraDock` em `100vw x 100vh` em grade.
  4. `so-slides`: oculta chat e câmeras e expande a área de apresentação/compartilhamento/vídeo em `100vw x 100vh`.
  5. `so-chat`: oculta câmeras e slides e expande o chat estilizado em `100vw x 100vh`.
- **Próximo passo planejado:** integrar o editor de CSS como Dock dentro do OBS (`http://localhost:3333/css-editor`). Para ter efeito no portal da RNP a aplicação teria de ser feita **pela ponte**, injetando o CSS no frame BBB ativo via `frame.evaluate()` (a ponte já resolve esse frame), e não pelo campo *CSS personalizado* do OBS.
