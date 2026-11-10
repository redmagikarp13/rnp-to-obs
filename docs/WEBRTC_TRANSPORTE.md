# 🔄 Transporte WebRTC — Estado e Registro

> Documento de trabalho. Descreve o transporte WebRTC, que **substitui** o pipeline
> JPEG legado relatado no `README.md` como caminho padrão. Desde 2026-09-30 o modo
> WebRTC é o **padrão de produção** em qualquer caminho de boot (`start.bat`,
> `app.pyw`, `.exe`); o legado JPEG permanece disponível apenas por opt-out
> explícito com `RNP_WEBRTC_ONLY=0`.

Última atualização: 2026-09-30.

---

## 1. Objetivo

Reduzir o gargalo de desempenho da ponte RNP/BigBlueButton → OBS. O transporte
legado envia cada câmera como `vídeo BBB → canvas → JPEG80 → Base64 → Python →
/raw_frame/ → polling no overlay`, o que sobrecarrega CPU e limita o FPS.

O experimento entrega as trilhas reais da sala por `RTCPeerConnection` direta ao
receptor no OBS, sem passar JPEG pelos microserviços. O Python faz apenas
**sinalização SDP**; ele não transporta os pixels do vídeo.

Isso **não** é encaminhamento RTP por SFU, nem comprova zero-copy ou ausência de
recodificação. É uma conexão peer-to-peer local (`iceServers: []`,
`bundlePolicy: 'max-bundle'`), sem STUN/TURN.

---

## 2. Como rodar

### Produção (porta 3333)

`start.bat` → `python server.py`. Sobe em `127.0.0.1:3333`, carrega o
`config.json` da raiz do workspace e **já entra em modo WebRTC** (não precisa de
variável de ambiente). `Parar Servidor.bat` encerra via `/api/server/stop`.

### Instância de teste / A-B (porta 3334)

Servidor separado, para não contaminar a sala oficial. Comando de desenvolvimento
usado nesta estação:

```powershell
python -c "import os; os.environ.update(RNP_PORT='3334',RNP_WEBRTC_ONLY='1',RNP_CONFIG_FILE=r'r:\rnpToObs\.test_runtime\config.json'); import uvicorn,server; uvicorn.run(server.app,host='127.0.0.1',port=3334,log_level='warning')"
```

Variáveis:
- `RNP_PORT=3334` — porta isolada da produção.
- `RNP_WEBRTC_ONLY=1` — força WebRTC (hoje redundante: já é o padrão). Use
  **`RNP_WEBRTC_ONLY=0`** para comparar contra o caminho legado JPEG.
- `RNP_CONFIG_FILE` — config isolada em `.test_runtime/` (ignorada pelo Git).

> Rodar a suíte de testes **enquanto uma instância ao vivo está no ar** causa
> contenção de CPU e a suíte trava em testes aleatórios (que passam isolados em
> 4-10 s). Para validar, encerre a instância ou rode em lotes direcionados.

As fontes no OBS precisam apontar para a **mesma porta** do servidor conectado.
Mudar só a porta da URL sem reconectar nesta instância não traz as câmeras.

### O que o modo WebRTC muda
- Webcams e compartilhamento de tela **com trilha** não geram JPEG.
- `/stage`, `/cameras/grid` e o endpoint independente `/libras` usam WebRTC quando
  `state.webrtc_only` é verdadeiro. Slides estáticos e players externos **sem
  trilha** voltam a usar captura pontual (~1×/s), então "somente WebRTC" não
  significa ausência absoluta de JPEG.
- Loop de metadados em ~250 ms.
- Os três overlays de vídeo reaproveitam o **mesmo** gerenciador
  `web/stage-media.js` (`RnpStageMedia`).
- **YouTube compartilhado (Fase 3):** como é um iframe de outra origem (sem
  `MediaStreamTrack`), o `/stage` reproduz o vídeo **nativamente** e o
  **sincroniza** pelo estado farejado (play/pause/seek), em vez do screenshot a
  ~1 fps. O **som** do vídeo sai pelo próprio `/stage` nesse caso.

---

## 3. Arquitetura e arquivos

| Arquivo | Papel |
| :--- | :--- |
| `media_bridge.py` | Rotas de sinalização: `/webrtc`, `/api/media`, `/api/webrtc/offer`, `/api/webrtc/close/{id}`, `/api/audio/control`, e serve os JS de receptor/palco. |
| `web/media-source.js` | Adaptador injetado no navegador da sala (versão 1). Cataloga fontes, clona trilhas, negocia as respostas SDP e fareja os `postMessage` do YouTube IFrame API, expondo `youtube={video_id,playing,current_time,duration,rate,updated_at}` via `describe()` → `/api/media`. |
| `web/webrtc-receiver.js` | Receptor individual (`/webrtc`), vídeo ou um canal de áudio. |
| `web/stage-media.js` | Gerenciador de receptores WebRTC reutilizado por `/stage`, `/cameras/grid` e `/libras`. Casa placeholders `video[data-source-id]` com `/api/media`, mantém 1 peer por fonte visível e reinsere o `<video>` vivo ao reconstruir o DOM. Expõe `this.youtube` (estado do player). |
| `web/stage-youtube.js` | `RnpYouTube`: player nativo do YouTube sincronizado pelo estado farejado — carrega/troca vídeo, play/pause e corrige drift com `seekTo`; a API do YouTube é carregada sob demanda. Usado pelo `/stage`. |
| `web/audio-mixer.js` | Mixer de canais individuais no painel. |
| `server.py` (`/stage`, `/cameras/grid`, `/libras`) | Monta os overlays, escolhendo JPEG ou WebRTC conforme `webrtc_only`; o `/stage` abre o player nativo do YouTube quando há vídeo ativo (senão, volta ao screenshot). |

Sinalização real de mídia é feita via `frame.evaluate(RnpMedia.answer)` com
timeout de 12 s; a origem rejeita `Origin` divergente (não é autenticação
completa). O **teto de receptores é configurável** via `RNP_MAX_PEERS`, lido em
`media-source.js` por `window.RNP_MAX_PEERS`; o servidor injeta o valor do
ambiente no navegador da sala em `run_connection`. **Por padrão não há teto**
(`RNP_MAX_PEERS` ausente ou `0` = ilimitado); defina um número só se quiser um
backstop.

> Importante: esse teto é **global por sala**, somando palco + grid + `/libras` +
> `/webrtc` individual + áudio de cada pessoa. Não é "8 câmeras por endpoint". O
> teto real de câmeras é limitado por **CPU** (1 encode na origem + 1 decode no
> OBS por trilha), não por esse número.

---

## 4. Identidade das câmeras (correção de 2026-09-25)

Cada publicação de vídeo do **mesmo participante** agora recebe um ID próprio,
em `media-source.js` (`cameraId`):

- Sem `participant_id`: cai para `cam-<track.id>`.
- Primeira publicação do participante: preserva o link antigo `cam-<participante>`.
- Publicações adicionais: `cam-<participante>~<publicação>`, onde a publicação usa
  `data-stream` (fallback `track.id`).
- O mapeamento é estável por elemento: reordenar ou remover a primária **não**
  transfere o ID nem a marcação de Libras para outra câmera.

O áudio continua **um canal por participante**; as duas câmeras do mesmo usuário
compartilham o mesmo `audio_source_id`. Libras é salvo pelo nome original
normalizado (NFC + casefold + compactação de espaços), correspondência exata.

Diagnóstico: quando a negociação falha, o 409 agora devolve o **motivo real**
(ex.: "Limite de N receptores experimentais atingido." ou "Fonte indisponível…"),
sanitizado para não expor o SDP, e registrado no logger `media_bridge`
(`test_sinalizacao_registra_causa_sem_expor_sdp`, `test_limite_de_receptores_e_configuravel`).

---

## 5. Áudio

**O master sai pelo `/stage`.** Desde 2026-09-30 não existe mais um segundo player
PCM em `/audio`: `/audio` sem `source` devolve **404**, porque manter dois masters
duplicava o consumo no OBS (uma Browser Source a mais, com grafo de áudio e
superfície de render) e causava eco quando ambos eram usados juntos.

- `/stage` → reproduz o master PCM Float32 mono 48 kHz via `PCM_AUDIO_SCRIPT`
  (`/ws/pcm_out`): participantes + apresentações + som do YouTube nativo.
- `/audio?source=...` → **mantido**: redireciona (307) para o receptor WebRTC
  individual `/webrtc?source=...&kind=audio&overlay=1`.
- `/control` também carrega `PCM_AUDIO_SCRIPT`, mas o guarda
  `window.self !== window.top` impede duplicação quando o painel roda iframado no
  dock do OBS.
- Microfones individuais usam `participant.identity`; `screen_share_audio` entra
  só no master, sem virar falso canal individual.
- Links dos áudios **individuais** ficam no rack do dock (`web/audio-mixer.js`):
  cada canal tem VU, volume, mute e botão que copia o link `/webrtc?...kind=audio`.

> **Configuração obrigatória no OBS para a fonte `/stage`:** marcar
> **"Controlar áudio via OBS"** e **desligar** "Shutdown source when not visible".
> Sem isso o som some quando o palco não está visível na cena — o áudio agora é
> acoplado ao palco. Se uma cena precisar de som sem o palco, use os links
> individuais (`/audio?source=...`).

---

## 5.5 YouTube compartilhado (Fase 3)

O vídeo externo do YouTube na sala é um `<iframe>` de outra origem: sem trilha
capturável, não vai por WebRTC. A ponte **fareja os `postMessage` do YouTube IFrame
API** na janela do frame da sala (`media-source.js`) e extrai
`{video_id, playing, current_time, duration, rate, updated_at, present}`, exposto em
`/api/media` **e** `/api/status` (chave `youtube`). Detalhes que só a sala real
revelou:

- **`video_id`/`present` vêm do `src` do iframe** (`/embed/<id>`), não só do
  `postMessage` — assim o id existe mesmo pausado/sem `videoData`, e a presença do
  iframe diz se "há YouTube agora" (não depende de frescura de mensagem).
- **`playing` também é inferido pelo avanço de `currentTime`**, porque o BBB nem
  sempre reenvia `playerState` a cada tick.

Com `present` verdadeiro, o `/stage` abre um **player nativo do YouTube**
(`RnpYouTube`, `web/stage-youtube.js`) no slot de apresentação e o sincroniza:
troca de vídeo por id, play/pause por estado, e correção de drift por `seekTo`
**conservadora** (só com drift > ~2 s; acima de ~5 s corrige imediato; mínimo ~3 s
entre seeks) — seek a cada poll fazia o YouTube re-bufferar e "travar".

**Armadilha de DOM corrigida:** a API do YouTube **substitui** o elemento-alvo por um
iframe. Por isso o `RnpYouTube` mantém um **wrapper estável** (`#ytStageHost`, que o
palco move entre rebuilds de layout) e entrega à API um **div interno descartável**;
guardar referência ao div substituído fazia o palco re-anexar um elemento vazio
(tela preta) a cada rebuild. O iframe é ancorado `position:absolute; inset:0` no
`slide-frame` para preencher o slot (a API cria o iframe com tamanho próprio e ele é
*flex item*). Teste de regressão garante que trocar o layout não recria/destrói o
player.

Sem YouTube presente, o palco volta ao screenshot/`presentation` normal.

**Prioridade do slot de apresentação:** tela compartilhada real (WebRTC) >
YouTube nativo sincronizado > screenshot de slides.

## 5.6 Formatos de câmera (`camera_format`)

Opção global persistida (`config.json`, editável no painel em "Formato Câmera" e via
`/api/grid/config`), com sobrescrita por URL (`?camfmt=`) em `/stage`,
`/cameras/grid` e `/libras`. Valores:

| Valor | Caixa | `object-fit` | Efeito |
| :-- | :-- | :-- | :-- |
| `original` (padrão) | **abraça o conteúdo**: `aspect-ratio` = proporção real do vídeo, fundo transparente (mantém borda e drop shadow da base) | `contain` | moldura acompanha o vídeo, sem caixa preta nem corte |
| `paisagem` | `aspect-ratio 16/9` | `cover` | caixa 16:9, corta se a fonte for vertical |
| `quadrada` | `aspect-ratio 1/1` | `cover` | caixa 1:1, corta o excedente |
| `retrato` | `aspect-ratio 9/16` | `cover` | caixa vertical, corta laterais |
| `esticado` | flexível | `fill` | estica para preencher (distorce) |
| `auto` | flexível | `cover` | comportamento antigo (corte para preencher) |

Implementação: o wrapper da câmera recebe `data-camfmt` e o CSS compartilhado
(`CAM_FORMAT_CSS` em `server.py`) define `object-fit`/`aspect-ratio`. Valores fora da
lista são rejeitados no endpoint.

## 5.7 Sinalização de quem está falando

O `sync_loop` calcula `is_speaking` por câmera combinando duas fontes: os
indicadores de fala do DOM do BBB (nomes dos "talking indicators") **e o pico real
do microfone** (`audioInfo().peak` por `participant_id`, limiar
`SPEAK_PEAK_THRESHOLD = 0.05`). O pico é o sinal robusto: independe de o BBB marcar
ou não o participante no DOM. Os overlays (`/stage`, `/cameras/grid`, `/libras`)
aplicam a classe `is-speaking` **na name-tag já existente** da câmera (borda verde +
brilho + ponto verde pulsante via `SPEAKING_CSS`), atualizada a cada poll **sem
reconstruir o DOM** — não há um segundo elemento de "fulano está falando", evitando
duplicar a identificação de quem já aparece com câmera. O rack de áudio do dock
mantém seus badges próprios (Falando/Mudo/Aberto), que são outra superfície
(monitoramento, não overlay de transmissão).

**Pico segurado (peak-hold).** O `analyser` devolve um pico instantâneo de ~42 ms;
como o `sync_loop` amostra a cada ~250 ms, a fala contínua era capturada de forma
esparsa e o indicador **piscava** (verdadeiro nos polls com pico alto, falso nos vãos
entre sílabas). Em `startPcm` (`web/media-source.js`) um sampler a cada 60 ms guarda
`peakHold = max(pico_instantâneo, peakHold · e^(−dt/τ))` com `τ = 300 ms`, e
`audioInfo()` passa a devolver esse pico segurado. Assim quem fala continua "aceso"
nas pausas curtas e o estado decai suavemente ~0,9 s após parar — sem piscar. A
mesma suavização beneficia os chips de voz e qualquer VU que leia `peak`.

**Chips de voz (quem fala sem câmera).** O `sync_loop` popula `state.voice_speakers`
com participantes cujo pico supera o limiar **e que não têm câmera visível**
(excluindo `screen-audio-*`), exposto em `/api/status.voice_speakers`. O `/stage`
renderiza esses nomes em `#voiceChips`, um overlay **no topo central**
(`position:absolute; top:14px; left:50%; translateX(-50%); z-index:300`), sempre
visível — inclusive sobre os slides e sobre o player do YouTube — e reconstruído
somente quando a lista de nomes muda (assinatura em `dataset.sig`).

> **Armadilha de CSS no `/stage`:** a regra universal
> `* { … overflow: hidden; }` (necessária para transparência/sem scroll no OBS) se
> aplica a **todos** os elementos. Um contêiner `position:absolute` sem largura
> encolhe ao tamanho do conteúdo, então ele **recorta o `box-shadow` dos filhos no
> retângulo do elemento** — a sombra sai "retangular" em vez de sangrar. Overlays
> com brilho precisam declarar `overflow: visible` explicitamente (`#voiceChips` e
> `.voice-chip`). Exceção deliberada: `.stage-cam-wrap` mantém `overflow:hidden`
> porque precisa recortar o vídeo nos cantos arredondados. Verificado por
> `test_sombra_do_chip_de_voz_sangra_para_fora`, que compara o alfa de um pixel
> 3 px fora da borda do chip **com e sem** `box-shadow` (diferencial, imune ao
> conteúdo que estiver atrás).

## 6. Validação

**Sala real** (URL omitida por privacidade; validada numa sala autorizada, porta 3334):
- `/api/media` listou **2 fontes de vídeo** do mesmo `participant_id`, com
  `track_id` distintos.
- No `/stage`, dois elementos `video[data-source-id^="cam-"]` reproduziram com
  `totalVideoFrames` avançando em ambos e ofertas WebRTC HTTP 200. Sem requests
  `/raw_frame/camera/`.

**Automação:** `python -m unittest discover -s tests -p "test_*.py"` → **76 OK**
(~189 s, com nenhuma instância ao vivo no ar).
Cobertura de multicâmera: IDs estáveis sob reordenação/remoção, DOM
duplicado sem fonte extra, remontagem preserva ID, palco reproduz as duas trilhas,
e Libras sem herança entre publicações. Cobertura nova destas etapas: teto de
receptores configurável; `/cameras/grid` e `/libras` reproduzindo via WebRTC sem
JPEG; grid preservando conexões ao trocar layout e liberando peer ao remover
câmera; FPS por câmera amostrado na origem (`sync_loop`) e medidor no dock; a
Fase 3 (controlador `RnpYouTube` de play/pause/seek/troca + montagem/desmontagem do
player nativo no `/stage`); e a sinalização de fala (detecção pelo pico real do
microfone, **peak-hold com decaimento suave** que não pisca, e **chip de voz** no
topo central do `/stage` para quem fala sem câmera).

### Como descobrir o seu limite real de câmeras
No **dock de controle** (`/control`), cada card de câmera mostra o **FPS entregue
pela origem** (cor verde ≥ 20, amarelo ≥ 10, vermelho < 10). Vá adicionando
webcams na sala e observe quando o FPS começa a cair — esse é o teto real da sala
+ máquina da ponte, não um limite de código. Por padrão não há teto de receptores;
se quiser um backstop, defina `RNP_MAX_PEERS=<número>` na linha de comando do
servidor. (Medição do OBS em si segue pendente.)

### Modo Econômico e teto de encode (2026-11)
Para rodar em notebooks/máquinas fracas, o campo `performance_mode` (`full`|`eco`,
padrão `full`) limita o relay ponte→OBS **ao vivo**, sem reconectar:
- Em `eco`, `web/media-source.js` aplica `limitarClone` (`applyConstraints`
  frameRate≤15, 1280x720) e `limitarSender` (`encodings.maxFramerate=15`,
  `maxBitrate=1.2Mbps`, `degradationPreference='maintain-resolution'`) em cada peer
  de vídeo. Corta CPU de **encode na ponte** e de **decode em cada Browser Source**
  do OBS. `setPerfMode(mode)` re-aplica aos peers já abertos; o server injeta
  `window.RNP_PERF_MODE` no `add_init_script` e chama `setPerfMode` no
  `/api/grid/config` ao alternar (botão `#btnPerfMode` no dock).
- Em `eco`, o `sync_loop` cai para **1 Hz** (`sync_interval()` / `SYNC_INTERVAL_ECO`),
  metade dos `evaluate()` por câmera. O legado JPEG mantém 0,05 s.
- O piso dominante continua sendo o **decode das trilhas de entrada** na página da
  sala (a ponte é um participante BBB). Para confirmar se esse decode está na GPU,
  use `tools/diag_gpu_decode.py` (mesmos `BROWSER_LAUNCH_ARGS`/`browser_channels()`
  da produção; reporta o renderer WebGL e `decoderImplementation`/`powerEfficientDecoder`).

### Codecs: VP8 na entrada (software), H264 na saída (hardware no OBS)
Diagnóstico ao vivo (getStats das trilhas + sonda de SDP):
- **Entrada (sala BBB → ponte):** o servidor Mconf/RNP entrega **VP8-only** (SDP `m=video`
  só com VP8 PT 96 + RTX 97; sem VP9/H264/AV1). O assinante não tem codec alternativo
  para negociar. E **nenhum browser mainstream (Chrome/Edge/Firefox) faz HW decode de VP8**
  no Windows — cai em **libvpx (software)**, medido como `decoderImplementation="libvpx"`,
  `powerEfficientDecoder=false`. A GPU até poderia (o NVDEC decodifica VP8, segundo o doc
  NVIDIA Video Codec SDK), mas o Chromium não roteia VP8 para o NVDEC. Esse é o **piso de
  CPU da ponte** (~245% no gpu-process) e só cai se a RNP habilitar VP9/H264 no servidor
  (aí o Chromium faz HW decode) ou com um pipeline nativo (FFmpeg `vp8_cuvid`).
- **Saída (ponte → OBS):** a perna negociava **VP8** por padrão, e o CEF do OBS decodifica
  VP8 em software. Como ambos os lados são Chromium e o SDP de resposta da ponte oferece
  H264/VP9/AV1/VP8, os receptores (`web/stage-media.js`, `web/webrtc-receiver.js`) passaram
  a aplicar `setCodecPreferences` **preferindo H264** (demais como fallback). Com isso o
  **OBS decodifica em hardware** (Chromium faz HW decode de H264), sem custo extra de encode
  na ponte (medido: H264 372,7% vs VP8 374,8%, ruído). Diagnóstico permanente em
  `/api/status.inbound_video` (codec/decoder/powerEfficient da entrada).

**Limites das evidências:**
- A melhora de fluidez é relato qualitativo do usuário no OBS; não houve medição
  controlada de CPU/GPU, latência total ou A/B dentro do OBS.
- FPS observado não isola se o teto vem da publicação, da assinatura ou do relay.
- **Falha intermitente conhecida:** `test_audio_isolado_e_mute_real` já mediu
  pico em ~1289 Hz em vez de 440 Hz dentro do delta; passa em repetição. Não é
  bloqueante, mas **não está resolvido**.

---

## 7. Fora deste escopo (ainda não feito)

- Plugin C++ (`obs_plugin_cpp/`) — não integrado ao novo transporte.
- **Instalação em `Program Files` ainda pendente de ação do usuário.** Os
  artefatos foram recompilados em 2026-09-30 na versão **1.1.0**
  (`dist\RNP to OBS\RNP to OBS.exe` e
  `dist_installer\Instalador_RNP_to_OBS_Setup.exe`, 57,8 MB), com o código novo
  conferido no bundle (`PEAK_TAU`/`samplePeaks`/`peakHold` em `web/media-source.js`
  e o default WebRTC + `voiceChips` em `server.py`). Mas a cópia **instalada** em
  `C:\Program Files (x86)\RNP to OBS\` segue na 1.0.0 (25/09) até o instalador ser
  executado — o que exige elevação. A produção em uso hoje é o `server.py` do
  workspace na 3333, não o `.exe` instalado.
- Otimizações gerais de preview/captura fora deste transporte.
- Players externos sem `MediaStreamTrack` via WebRTC.
- Testes prolongados com muitos receptores simultâneos.

Nenhuma cena do OBS foi alterada por mim; nenhum commit ou push foi feito.

---

## 8. Próximos passos sugeridos

1. Resolver a medição de frequência intermitente do `test_audio_isolado_e_mute_real`.
2. Fazer medição controlada de CPU/GPU e FPS (A/B legado × WebRTC) dentro do OBS,
   usando o medidor de FPS do dock como ponto de partida.
3. Determinar empiricamente o teto de câmeras da estação e escolher um default
   sensato para `RNP_MAX_PEERS`.
4. ~~Recompilar o `.exe`/instalador com o novo transporte~~ **feito em 2026-09-30**:
   o WebRTC virou o padrão de produção (`RNP_WEBRTC_ONLY=0` para optar pelo legado),
   a produção roda na 3333 a partir do workspace e o `.exe`/instalador foram
   regenerados na versão **1.1.0**.
5. ~~Corrida do `run_connection`~~ **corrigida**: `/api/connect` usa `start_connection`,
   que cancela e aguarda a tarefa anterior antes de criar a nova; `/api/disconnect`
   chama `cancel_connection`. `run_connection` trata `CancelledError` fechando o
   navegador. Teste `test_segundo_connect_cancela_o_anterior`.