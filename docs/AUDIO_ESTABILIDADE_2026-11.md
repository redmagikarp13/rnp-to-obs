# 🔊 Áudio & Estabilidade — Registro de Trabalho (sessão 2026-11-04)

> Documento de trabalho. Registra o diagnóstico e as correções de **estabilidade** e
> **áudio** aplicados na ponte RNP/BigBlueButton → OBS nesta sessão, o estado atual e
> os **próximos passos** para continuar o refinamento. Complementa o
> `docs/WEBRTC_TRANSPORTE.md` (vídeo) e o `GUIA_DE_OPERACAO.md` (operação).

Última atualização: 2026-11-04. Versão empacotada atual: **1.1.11**.

---

## 0. TL;DR / Estado atual

- **Instabilidade ("saindo e voltando")** — corrigida (1.1.8): o frame do BBB trava a
  main thread periodicamente; como o Playwright serializa `evaluate()` por frame, o
  `/api/media` pendurava (chegou a 26 s) e os pedidos se acumulavam. Resolvido com
  *single-flight* + timeouts.
- **Áudio parava ao minimizar a janela** — corrigido (1.1.9 + 1.1.10): o player
  suspendia o grafo de áudio quando a página ficava oculta, e o broadcast do servidor
  usava `asyncio.gather` que travava com um cliente parado (backpressure).
- **Acesso de outra máquina (LAN)** — corrigido (1.1.10): o config "Roaming" era
  semeado com `lan_access=false`; o servidor só escutava em `127.0.0.1`.
- **Stutter (picotado) no áudio** — **CORRIGIDO (2026-11-05)**: além do jitter buffer de
  120 ms (1.1.11), a entrega de chunks entre o `AudioWorklet` e o WebSocket `/ws/pcm_in`
  passou a fluir diretamente por `MessageChannel` para um **Web Worker dedicado**
  (`WORKER_SRC` em `web/media-source.js`), contornando a main thread do BBB (onde
  Recharts/React/DOM + `screenshot()` de slides represavam os `capNode.port.onmessage` em
  rajadas). Além disso, o `sync_loop` deixou de tirar `screenshot()` a cada 1s quando o
  slide está estático, e os `AudioBufferSourceNode` do player passaram a ser desconectados
  em `onended`.

---

## 1. Arquitetura do pipeline de áudio (PCM)

Fluxo completo, com os arquivos/símbolos de cada etapa:

```
Sala BBB (headless Chromium/Playwright)
  └─ <audio>/<video> tracks → Web Audio API
     └─ media-source.js  startPcm(url)
        • AudioContext { sampleRate: 48000 }
        • AudioWorklet "PcmCapture": buffer 2048 amostras (~42,7 ms) → postMessage
        • capNode.port.onmessage → ws.send(chunk)  se ws.bufferedAmount < 48000
        • picos por trilha → /ws/peaks_in (cadência ~60 ms)
        → WebSocket /ws/pcm_in  (Float32 48 kHz)
                 │
SERVIDOR (server.py, uvicorn/FastAPI)
  • ws_pcm_in: recebe chunk → _enqueue_latest(fila, ('bin', data))  [não-bloqueante]
  • por cliente: asyncio.Queue(maxsize=PCM_QUEUE_MAX=64) + task _client_sender
  • _client_sender: await queue.get() → websocket.send_bytes()/send_text()
  • ws_peaks_in/ws_peaks_out: mesmo padrão (PEAKS_QUEUE_MAX=32)
  • sync_loop também enfileira ('txt', speakers) para os clientes PCM
                 │
        → WebSocket /ws/pcm_out
                 │
CLIENTES DE REPRODUÇÃO (PCM_AUDIO_SCRIPT, server.py — injetado em /control e /stage)
  • AudioContext { sampleRate: 48000 } → monitorGain → destination
  • ws.onmessage(binário): createBuffer(1, 2048, 48000) → BufferSource
  • agendamento com jitter buffer: TARGET_LEAD = 0.12 s (ver §5.4)
```

Pontos-chave:
- O **vídeo** flui por WebRTC peer-to-peer (independente do `sync_loop`); o **áudio**
  flui por WebSocket PCM (este documento).
- O navegador de **captura** é headless (`chromium.launch(headless=True, channel="msedge")`),
  com várias flags anti-throttling (ver §5.2). Ele **não tem janela** para minimizar.
- O **dock** (`/control`) é uma janela **pywebview/WebView2** (`dock_window.py`); é ele
  que reproduz o monitor local de áudio e é o que o operador minimiza.

---

## 2. Sintomas relatados pelo usuário (ordem cronológica)

1. "tá bem instável, parece que fica saindo voltando" → §5.1 (1.1.8).
2. "quando a janela do aplicativo fica minimizada o áudio trava e para; ao retornar volta" → §5.2/§5.3 (1.1.9/1.1.10).
3. "ainda não consegui acessar de outra máquina" → §5.5 (1.1.10).
4. "consegui conectar de fora, mas o áudio melhorou um pouco e ainda tá dando stuttering" → §5.4 (1.1.11) + §6 (em aberto).
5. **"o stutter é na máquina local, tanto na janela do dock quanto no stage do OBS; a rede é cabo."** → §6 (pista decisiva).

---

## 3. Versões e artefatos

| Versão | Foco | Arquivos alterados |
| :-- | :-- | :-- |
| 1.1.8 | Estabilidade (`/api/media` single-flight + timeouts; broadcast de speakers; carga do `sync_loop`) | `media_bridge.py`, `server.py`, `installer.iss` |
| 1.1.9 | Áudio ao minimizar (fim do `suspend` no JS + flags WebView2) | `server.py` (`PCM_AUDIO_SCRIPT`), `dock_window.py` |
| 1.1.10 | Backpressure do broadcast (filas por cliente) + seeding do config LAN | `server.py` (`ws_pcm_*`, `ws_peaks_*`, `load_disk_config`, `BridgeState`), config Roaming da máquina |
| 1.1.11 | Jitter buffer de reprodução (`TARGET_LEAD=0.12`) | `server.py` (`PCM_AUDIO_SCRIPT`) |

Artefatos gerados em `dist_installer\`:
- `Instalador_RNP_to_OBS_Setup.exe` (58,5 MB) — Inno Setup, instala em Program Files.
- `RNP_to_OBS_<versão>_portable.zip` (82,2 MB) — conteúdo na **raiz** do zip (sem pasta).

A string de versão vive **somente** em `installer.iss` (`#define MyAppVersion`).

---

## 4. Configuração e caminhos (importante para depurar)

- **Config**: `_resolve_config_path()` (server.py) tenta `<dir_do_exe>/config.json`; se o
  diretório não for gravável (Program Files sem admin), cai para
  `%APPDATA%\RNP_to_OBS\config.json` (Roaming). `CONFIG_FILE` é resolvido **uma vez** no boot.
- **Bind de rede**: `resolve_bind_host()` lê `lan_access` do config e devolve `0.0.0.0`
  (LAN) ou `127.0.0.1`. É **cacheado no boot** (`_BIND_HOST_CACHE`) → mudar `lan_access`
  exige **reiniciar o app**.
- **Nesta máquina**: o Roaming `%APPDATA%\RNP_to_OBS\config.json` foi ajustado para
  `lan_access=true` (sala `vila-velha`). IPs LAN: `192.168.1.206` (primário, cabeado) e
  `172.28.136.190` (adaptador virtual WSL/Hyper-V — **não** usar).
- **Logs do EXE**: `%LOCALAPPDATA%\RNP_to_OBS\app_stdout.log` / `app_stderr.log`
  (via `SafeLogWriter` em `app.py`, quando não é tty).
- **Firewall**: já existem regras "RNP to OBS" (inbound Allow) — o bloqueio de LAN **não**
  era firewall, era o bind em `127.0.0.1`.

---

## 5. Correções aplicadas (detalhe técnico)

### 5.1 Estabilidade — `/api/media` e `sync_loop` (1.1.8)

**Causa raiz:** a main thread do frame do BBB trava periodicamente. O Playwright
serializa `evaluate()` por frame; sem timeout, o `/api/media`
(`frame.evaluate('() => window.RnpMedia.describe()')`) pendurava (26 s medidos) e, como o
dock/stage/webrtc consultam a cada 0,5–1 s, os pedidos se acumulavam → metadados de mídia
congelavam e "somem/voltam"; `connection_quality` ficava `unknown`.

**Correções (`media_bridge.py`):**
- `/api/media` com **single-flight**: `asyncio.Lock` + cache de `_MEDIA_CACHE_TTL=0.25 s` →
  consumidores paralelos compartilham **1** `evaluate()` em vez de N.
- `_MEDIA_EVAL_TIMEOUT=6.0 s` em todos os `evaluate()` (`/api/media`, `ensure_media_adapter`,
  `/api/audio/control`) → falha rápido (503) em vez de pendurar.

**Correções (`server.py`):**
- `sync_loop`: `cam_data = await asyncio.wait_for(target_frame.evaluate(...), timeout=SYNC_EVAL_TIMEOUT=8.0)`
  → se o frame travar, a volta é abortada e a próxima tenta de novo (degrada, não congela).
- Redução de carga no frame: extração de **chat** `0,5 s → 1,0 s`; **enquete** `1,0 s → 2,0 s`.
- Broadcast de *speakers*: o fire-and-forget `asyncio.create_task(client.send_text(...))`
  (que gerava spam "Task exception was never retrieved") foi trocado por enfileiramento
  não-bloqueante na fila do cliente (ver §5.3).

### 5.2 Áudio ao minimizar — player + WebView2 (1.1.9)

**Causa raiz (client):** `PCM_AUDIO_SCRIPT` tinha um handler `visibilitychange` que chamava
`audioCtx.suspend()` quando `document.hidden`, mais `if (pageHidden) return;` que descartava
chunks enquanto oculta. Minimizar o dock → `document.hidden=true` → áudio parava.

**Correção:** removido o `suspend`/`skip`. O grafo de áudio toca continuamente; ao voltar
para o primeiro plano, `nextStartTime=0` reconecta ao vivo (sem rajada).

**Causa raiz (engine):** o WebView2 (Chromium) throttla/backgrounding do renderer quando a
janela perde visibilidade.

**Correção (`dock_window.py`):** definir `os.environ['WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS']`
**antes** do `webview.start()` com:
```
--disable-background-timer-throttling
--disable-backgrounding-occluded-windows
--disable-renderer-backgrounding
--disable-features=CalculateNativeWinOcclusion
--autoplay-policy=no-user-gesture-required
```
O pywebview seta `AdditionalBrowserArguments` por conta própria (`edgechromium.py`), mas a
variável de ambiente **mescla** — confirmado presente na cmdline do `msedgewebview2.exe`.

### 5.3 Backpressure do broadcast — a causa real do "trava geral" (1.1.10)

**Causa raiz:** `ws_pcm_in` fazia `asyncio.gather(*[c.send_bytes(data) ...])`, que **espera
por todos** os clientes. Com o dock minimizado/suspenso, ele para de drenar o `/ws/pcm_out`;
o buffer TCP enche; aquele `send_bytes` **bloqueia**; o `gather` inteiro trava; o navegador
de captura atinge `bufferedAmount > 48000` e **descarta chunks** → áudio congelava para
**todos** (inclusive OBS) até o dock voltar a drenar.

**Correção:** broadcast **por fila, não-bloqueante**:
- `BridgeState`: `pcm_queues: Dict[WebSocket, asyncio.Queue]` e `peaks_queues`.
- `_enqueue_latest(queue, item)`: `put_nowait`; se cheia, descarta o **mais antigo** (mantém ao vivo).
- `_client_sender(websocket, queue)`: task dedicada por cliente; `await queue.get()` →
  `send_bytes`/`send_text`. Se um cliente trava, só a task **dele** espera.
- `ws_pcm_in`/`ws_peaks_in`: só fazem `_enqueue_latest` (nunca bloqueiam o loop de recepção).
- `ws_pcm_out`/`ws_peaks_out`: criam a fila + task, e no `finally` cancelam a task e removem.
- Constantes: `PCM_QUEUE_MAX=64` (~2–3 s), `PEAKS_QUEUE_MAX=32`.

### 5.4 Stutter — jitter buffer de reprodução (1.1.11)

**Causa (parcial):** o agendamento de reprodução não tinha colchão:
```js
if (nextStartTime < currentTime) nextStartTime = currentTime;  // cola no "agora"
```
Qualquer chunk que chegasse um pouco atrasado colava no `currentTime` → **underrun** (gap) → stutter.

**Correção (`PCM_AUDIO_SCRIPT`):** colchão de reprodução `const TARGET_LEAD = 0.12` (120 ms):
```js
if (nextStartTime < currentTime + 0.02) nextStartTime = currentTime + TARGET_LEAD; // re-arma o colchão
if (nextStartTime - currentTime > 0.5)  nextStartTime = currentTime + TARGET_LEAD; // burst/pós-resume
bufferSource.start(nextStartTime);
nextStartTime += audioBuffer.duration;
```
**Tradeoff:** +120 ms de latência (~170 ms total). Ajustável (subir p/ Wi-Fi ruim; descer p/ lip-sync).

⚠️ **Atenção:** como o stutter é **local + cabeado** (§6), o jitter buffer **mascara** mas
provavelmente **não elimina** a causa raiz. Manter como mitigação enquanto se investiga §6.

### 5.5 LAN — seeding do config (1.1.10)

**Causa raiz:** instalado em Program Files (não gravável) → `CONFIG_FILE` cai no Roaming;
`load_disk_config()` semeava o Roaming a partir de `DEFAULT_CONFIG` (`lan_access=False`) em
vez do `config.json` **empacotado** (`lan_access=True`) → servidor só em `127.0.0.1`.

**Correções:**
- `load_disk_config()`: quando `CONFIG_FILE` não existe, **semeia do `config.json` empacotado**
  (legível mesmo em Program Files; o PyInstaller `--add-data` o coloca em `_internal`) mesclado
  sobre `DEFAULT_CONFIG`.
- Nesta máquina: Roaming ajustado direto para `lan_access=true`. **Validado:** bind `0.0.0.0`,
  `/api/status` com `bind_host=0.0.0.0`, `lan_ips=[192.168.1.206, ...]`.

---

## 6. ✅ RESOLVIDO (2026-11-05) — Stutter local (dock + OBS), rede cabeada

### 6.0 Correção de Causa Raiz Aplicada (2026-11-05)

1. **Web Worker dedicado + `MessageChannel` (`web/media-source.js`):**
   - Antes, o `AudioWorkletProcessor` gerava blocos na thread de áudio em cadência regular
     (~42,7 ms), mas enviava `this.port.postMessage(...)` para `capNode.port.onmessage` na
     **main thread** do frame da sala BBB. Quando a main thread do BBB sofria travamentos de
     150–400 ms (renderização React/SVG, chat, ou `screenshot()` via CDP), os eventos de
     `onmessage` ficavam represados e saíam em rajada para o `/ws/pcm_in`, esgotando o
     colchão de 120 ms simultaneamente no `/control` e no `/stage`.
   - Agora, `startPcm(url)` cria um `MessageChannel` e um `Worker` dedicado (`WORKER_SRC`):
     `workerChan.port1` é transferido ao `AudioWorklet` e `workerChan.port2` ao `Worker`, que
     abre o WebSocket `/ws/pcm_in` diretamente na worker thread. O caminho quente do áudio
     (`AudioWorklet -> MessagePort -> Web Worker -> WebSocket`) não passa mais pela main thread
     da sala BBB.
2. **Eliminação de `screenshot()` redundante em slides estáticos (`server.py`):**
   - `sync_loop` agora calcula uma assinatura leve do DOM de `#presentationContainer` e só
     invoca `pres_loc.first.screenshot(type="jpeg", quality=80)` quando o slide muda (ou a
     cada 5,0 s como fallback, em vez de todo 1,0 s).
3. **Limpeza de nós WebAudio (`server.py`):**
   - Em `PCM_AUDIO_SCRIPT`, cada `AudioBufferSourceNode` agora executa
     `bufferSource.onended = () => { try { bufferSource.disconnect(); } catch (e) {} };`,
     evitando acúmulo de milhares de nós desconectados no grafo de áudio ao longo de horas.

### 6.1 Por que a pista muda o diagnóstico

- Stutter **na máquina local** e **simultaneamente no dock e no `/stage` do OBS** → os dois
  consumidores ouvem o **mesmo** áudio vindo do **mesmo** fan-out do servidor. Se ambos
  picotam **juntos**, o problema está **antes** do fan-out: na **captura** (navegador headless)
  ou na **fonte** (stream WebRTC do BBB), **não** na distribuição nem na rede.
- Rede **cabeada** → jitter/perda de rede LAN é desprezível; o jitter buffer (§5.4) não ataca
  a causa real.

### 6.2 Hipótese principal: clock de áudio do headless ("null audio sink")

O navegador de captura é **headless**. Sem dispositivo de áudio real, o Chromium pode
alimentar o `AudioContext` por um **sink nulo** cujo clock não é o de uma placa de som real,
entregando dados em **rajadas/irregular**. Isso faz o `AudioWorklet.process()` ser chamado
com timing irregular → chunks de 2048 amostras chegam ao servidor com **intervalos
irregulares** (em vez de ~42,7 ms constantes) → gap de reprodução nos dois clientes.

**É a hipótese mais consistente com "stutter local + cabeado + nos dois players".**

### 6.3 Outras hipóteses (em ordem de probabilidade)

1. **Contenção de CPU no headless**: decodificação de vídeo (câmeras/screenshare) + os
   `evaluate()` pesados do `sync_loop` podem **morfar de fome** a thread de áudio do
   navegador de captura → captura com gaps. (Mitigado em parte por §5.1, mas não eliminado.)
2. **Qualidade do stream WebRTC do BBB**: perda/jitter **da sala RNP até o host** gera áudio
   decodificado com descontinuidades **na fonte** — apareceriam em `connection_quality`
   (`/api/status`). Verificar `level`, `loss_pct`, `jitter_ms`, `rtt_ms` durante o stutter.
3. **Queda de chunks na captura**: o guarda `ws.bufferedAmount < 48000` descarta chunks se o
   servidor não ler a tempo. Com §5.3 o servidor lê rápido, mas vale **confirmar** que não há
   descarte (contar chunks/s em `ws_pcm_in`).
4. **Resampling no playback**: `AudioContext({sampleRate:48000})` vs hardware real (ex.: 44100)
   → o navegador reamostra; normalmente é limpo, mas é variável a conferir.
5. **Drift de clock** captura(48k) ↔ reprodução(48k): clocks de hardware ligeiramente
   diferentes acumulam e as redes de segurança (`>0.5 s` / `<currentTime+0.02`) re-armam
   periodicamente → pequena descontinuidade a cada X minutos.

### 6.4 Plano de investigação para amanhã (passos concretos)

**A. Medir a regularidade da chegada de chunks (diagnóstico decisivo).**
Instrumentar temporariamente o `ws_pcm_in` para registrar o intervalo entre chunks:
- esperado: ~42,7 ms constantes (2048/48000); ~23,4 chunks/s.
- contar: nº de chunks/s, min/med/max do intervalo, e quantos intervalos > ~60 ms (gaps).
- expor via `/api/status` (ex.: `pcm_in_rate`, `pcm_in_gap_ms_max`, `pcm_in_dropped`) ou logar.
Se os intervalos forem **irregulares/rajadas** → confirma §6.2/§6.1 (captura/headless). Se
forem **regulares** mas ainda picotar → o problema é no **playback** (investigar §6.3.4/6.3.5).

**B. Contar descartes por fila (drop-oldest).**
Acrescentar um contador em `_enqueue_latest` (quantas vezes descartou o mais antigo por
cliente) e expor em `/api/status`. Se `> 0` para clientes **locais**, o sender não está
dando conta (improvável local, mas vale confirmar).

**C. Testar captura NÃO headless.**
Rodar o navegador de captura **headed** (janela real, ainda que minimizada/off-screen) e
comparar o stutter. Se sumir → confirma o "null audio sink" do headless. Nesse caso, avaliar:
- manter headed com as flags anti-throttling (§5.2) já aplicadas ao Playwright, **ou**
- procurar flag/alternativa que dê clock de áudio real ao headless.
> Obs.: mudar `headless=True → False` em `run_connection` (server.py, ~linha 769/771) exige
> cuidar de throttling de janela (já há flags) e de não exibir janela ao operador (posicionar
> off-screen ou minimizada). Testar em porta separada (`RNP_PORT`) para não perturbar a sala.

**D. Correlacionar com métricas WebRTC da sala.**
Durante o stutter, ler `/api/status.connection_quality` e o `RnpConnection.snapshot()`:
se `loss_pct`/`jitter_ms` altos → a fonte (RNP→host) está degradando; o stutter é herdado.

**E. Isolar CPU.**
Medir CPU do processo de captura headless e do servidor durante o stutter (Task Manager /
`psutil`). Se o `sync_loop` (evaluate de chat/câmeras) coincide com os gaps, aumentar ainda
mais os intervalos (§5.1) ou mover trabalho pesado para fora do event-loop.

**F. Ajuste fino do jitter buffer (mitigação).**
Enquanto a raiz não é resolvida, testar `TARGET_LEAD` maior (0,18–0,25 s) — se o stutter
diminuir com colchão maior, reforça que a chegada é irregular (fonte/captura), não o player.

### 6.5 Perguntas em aberto para o usuário
- O stutter é **constante** ou em **rajadas** (piora quando alguém liga câmera/compartilha tela)?
- Acontece também quando **só áudio** (sem vídeo/câmeras) na sala?
- O medidor de **qualidade da conexão** no dock mostra "Boa" durante o stutter?
- Ocorreu também em versões **anteriores** a esta sessão (histórico), ou é novo?

---

## 7. Como reconstruir / empacotar (procedimento)

Pré-requisitos nesta estação (usuário `CeforAV`):
- Python 3.14.10: `C:\Users\CeforAV\AppData\Local\Programs\Python\Python314\python.exe`
- Inno Setup 6: `C:\Users\CeforAV\AppData\Local\Programs\Inno Setup 6\ISCC.exe`
- Deps: `requirements.txt` + `pyinstaller numpy psutil rich cryptography`. Playwright usa o
  canal `msedge` (Edge do sistema; não baixa navegador).

Passos (PowerShell; usar `;` e não `&&`):
1. **EXE**: `cd c:\Rnp\rnpToObs; python build_exe.py` → `dist\RNP to OBS\` (~80–140 s).
   - Sempre refresh PATH: `[Environment]::GetEnvironmentVariable("Path","Machine"/"User")`.
2. **Versão**: editar `installer.iss` `#define MyAppVersion "<x.y.z>"` (único lugar).
3. **Instalador**: `& "...\ISCC.exe" installer.iss` → `dist_installer\Instalador_RNP_to_OBS_Setup.exe`
   (58,5 MB). ⚠️ Rodar em **background** e aguardar `Successful compile`; não interromper
   (build anterior truncou em 41/54 MB por timeout).
4. **Portátil**:
   ```powershell
   Add-Type -AssemblyName System.IO.Compression.FileSystem
   [System.IO.Compression.ZipFile]::CreateFromDirectory("dist\RNP to OBS", "dist_installer\RNP_to_OBS_<v>_portable.zip", [System.IO.Compression.CompressionLevel]::Optimal, $false)
   ```
   (`$false` = conteúdo na raiz do zip, sem pasta; 82,2 MB).
5. **Validar** antes de testar o EXE: `python -m py_compile server.py media_bridge.py dock_window.py`;
   e, se possível, subir `python server.py` com `RNP_PORT`/`RNP_CONFIG_FILE` de teste e conferir
   `/api/status` (`bind_host`, `lan_access`, `connected`).

> Lembrete: um EXE PyInstaller **congela** o bytecode do build — editar o fonte depois não
> afeta o EXE já compilado. Sempre reconstruir antes de testar/entregar.

---

## 8. Arquivos/símbolos de referência rápida

| Área | Onde |
| :-- | :-- |
| Captura PCM (worklet, 2048, guards) | `web/media-source.js` → `startPcm`, `WORKLET_SRC`, `capNode.port.onmessage` |
| Broadcast por fila | `server.py` → `_enqueue_latest`, `_client_sender`, `ws_pcm_in`, `ws_pcm_out`, `ws_peaks_in`, `ws_peaks_out`, `PCM_QUEUE_MAX`, `PEAKS_QUEUE_MAX` |
| Player + jitter buffer | `server.py` → `PCM_AUDIO_SCRIPT` (`TARGET_LEAD`, `getAudioContext`, `connectWs`, handler `visibilitychange`) |
| Loop de sincronização | `server.py` → `sync_loop` (`SYNC_EVAL_TIMEOUT`, `SYNC_INTERVAL_WEBRTC`, intervalos de chat/enquete) |
| `/api/media` single-flight | `media_bridge.py` → `media_sources`, `ensure_media_adapter`, `_MEDIA_CACHE`, `_MEDIA_EVAL_TIMEOUT` |
| Janela/WebView2 | `dock_window.py` → `_aplicar_args_webview2`, `WEBVIEW2_KEEP_ALIVE_ARGS`, `abrir_janela_pywebview` |
| LAN/bind | `server.py` → `resolve_bind_host`, `resolve_bind_port`, `_lan_ip_candidates`, `_resolve_config_path`, `load_disk_config` |
| Watchdog/2 processos | `app.py` → `_zelo_do_servidor`, `start_server`, `_spawn_servidor` (`--server`) |

---

## 9. Decisões e trade-offs registrados

- **Jitter buffer de 120 ms**: priorizou suavidade sobre latência mínima. Como o stutter é
  local/cabeado, é **mitigação**, não cura — revisitar após §6.
- **Filas por cliente com drop-oldest**: um cliente travado perde os dados **mais antigos**
  (fica "ao vivo") em vez de travar os demais. Para áudio em tempo real, drop-oldest é o
  comportamento correto (não acumular latência).
- **LAN via config + restart**: bind é definido no boot (cacheado). O toggle no dock exige
  reiniciar o app — documentar/avisar no painel.
- **Headless na captura**: mantém a captura invisível, mas é o **principal suspeito** do
  stutter local (§6.2). Testar headed pode ser necessário.
