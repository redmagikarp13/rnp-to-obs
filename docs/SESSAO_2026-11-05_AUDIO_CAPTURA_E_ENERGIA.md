# 🩺 Sessão 2026-11-05 — Causa raiz do áudio, navigateur de captura, OBS, CSS, energía e build 1.1.13

> **Documento de trabalho.** Registra o diagnóstico **ao vivo** feito na sala
> `https://conferenciaweb.rnp.br/sala/vila-velha`, as causas raiz encontradas para o
> "áudio travando em segundo plano", as correções aplicadas no código, o estado do
> repositório e dos artefatos, e as pendências com plano de investigação.
>
> **Resolve a hipótese em aberto** de `docs/AUDIO_ESTABILIDADE_2026-11.md` §6
> (stutter local simultâneo no dock e no OBS, rede cabeada). Complementa
> `docs/WEBRTC_TRANSPORTE.md` (vídeo) e `GUIA_DE_OPERACAO.md` (operação).

Última atualização: **2026-11-05**. Versão empacotada atual: **1.1.14**.

---

## 🎯 0. TL;DR / O que mudou de entendimento

Cinco causas distintas produzem o mesmo sintoma ("áudio trava"). Nesta sessão cada uma
foi medida e separada das outras:

| # | Causa | Status | Evidência-chave |
|---|---|---|---|
| 1 | **Navegador de captura em modo headless reduzido** (`chrome-headless-shell`) | ✅ corrigido no código | clock de áudio **0,625×** (14,65 de 23,44 chunks/s), event loop **1932 ms** |
| 2 | **Starvation de CPU do event loop** do servidor | ⚠️ causa dominante sob carga alta, mitigação parcial | atraso do loop **p95 330 ms / máx 2184 ms**, `lead_ms` do player **-24768 ms** |
| 3 | **OBS não usava o áudio da ponte** (fonte `/stage` mutada e oculta) | ⚠️ pendente de ação do operador na estação SIIES | `Navegador` `muted=true`; `Audio Webconf` (BBB direto) `muted=false`, `volume=1.41` |
| 4 | **Ajustes de energía que o slider não cobre** | 📖 documentado no `/control` | `PROCTHROTTLEMIN=5%`, `ASPM=2`, `NetworkThrottlingIndex=10` |
| 5 | **`pytchat` `data.sync_items()` bloqueando o event loop** quando o chat do YouTube está conectado (`1.1.14`) | ✅ corrigido no código (`data.items`) | `sync_items()` é generator com `time.sleep(1.0)` síncrono → loop travava **1002,9 ms a cada 2 s**; ao usar `data.items` / desconectar, caiu para **13,2 ms** com **0 underruns** |

**Veredito sobre a pergunta original** ("o áudio em segundo plano continua travando?"):
**não trava mais** — com o navigateur de captura **completo**, a janela minimizada
apresentou **menos** underruns que a visível (0,40 vs 0,89 por relato de 2 s), colchão
estável em ~115 ms (`TARGET_LEAD = 0.12`), `play_ratio = 1.0000` e **zero drops**.

E uma descoberta que reframing tudo: **o build instalado (1.1.11) não contém o
conserto**. `_internal\web\media-source.js` dele não tem `WORKER_SRC` nem `via_worker`,
e `_internal\server.py` não tem `pcm_diag`. Quem testou o `/stage` e viu travar estava
testando o código antigo.

---

## 📦 1. Estado do repositório

`git` **não está instalado** nesta máquina Windows (ausente do PATH e de todos os
locais usuais de `C:\`). O estado foi lido interpretando `.git` diretamente em Python
(reflog, refs, objetos soltos e packfile idx v2).

| Ref | SHA | Estado |
|---|---|---|
| `feat/dock-redesign` (HEAD) | `14f3935b` | branch atual |
| `origin/feat/dock-redesign` | `14f3935b` | **em sincronia — já enviado** |
| `main` = `origin/main` | `704f46aa` | **83 commits atrás**, ancestral direto |

- **Working tree == HEAD em conteúdo.** 24 arquivos divergem **apenas** por fim de linha
  (CRLF no disco vs LF no repositório); o `.gitattributes` (`* text=auto eol=lf`)
  normaliza no commit, então `git status` reporta limpo.
- **0 arquivos não rastreados.**
- Autoria: commits de `redmagikarp13` = Windows; `magikarp13` = macOS.

### Sessão do Mac (3 commits em 2026-11-05, 23 arquivos)

| Commit | Hora | Assunto |
|---|---|---|
| `666432d0` | 13:45 | `feat(bridge): web worker pcm capture, lan security hardening, ci & css generator` (22 arquivos) |
| `c006beab` | 13:55 | `feat(tools): modos de cena (cameras, slides, chat) e correcao de seletores no gerador de CSS` |
| `14f3935b` | 14:05 | `docs(skill): registra arquitetura web worker pcm, seguranca lan e gerador de css por modo de cena` |

Destaques do acumulado: `server.py` **+611 linhas**, `web/media-source.js` **+230**
(Web Worker + `MessageChannel`), `web/dock.js` **+108**, `dock_window.py` **+50**,
`media_bridge.py` **+31** (single-flight + timeouts), novos `.gitattributes`,
`.github/workflows/tests.yml`, `Liberar Rede (LAN).bat`, `tests/test_pcm_fanout.py`,
`docs/AUDIO_ESTABILIDADE_2026-11.md` (382 linhas), `tools/gerador_css_obs.html`
(980 linhas), e `installer.iss` 1.1.6 → 1.1.11.

**Testes:** as 5 suítes do CI passam no Windows — **105 testes OK**.

---

## 📐 2. Metodologia de medição (ler antes de interpretar qualquer número)

Nominal do pipeline: `48000 / 2048 = 23,44 chunks/s`, intervalo de **42,7 ms**.

Duas armadilhas que invalidam leituras ingênuas:

1. **`taxa_hz`, `clock_ratio` e `play_ratio` são cumulativos desde o início da captura.**
   Ficam permanentemente contaminados pelo ramp-up da conexão. Observado: `clock_ratio`
   parado em 0,62–0,74 enquanto o clock **real de janela** era 0,98+.
   → **Sempre derivar de delta de contadores sobre uma janela.**
2. **Os stats da captura chegam a cada ~2 s.** Amostrar a cada 3 s produz *aliasing*:
   a taxa por amostra oscila 15,6 ↔ 30,9 Hz (média 23,2).
   → **Usar `(contador_final − contador_inicial) / tempo_total`.**

Cadeia medida, estágio por estágio:

```
worklet (gerados) → Web Worker (enviados_ws) → servidor (chegada_pcm_in)
                  → por ouvinte (ouvintes) → player (playback/rearmar_colado)
```

`rearmar_colado` é **a** métrica de gap audível: cada re-armo do colchão é um underrun
naquele player. O relato é janela móvel de 2 s (`setInterval(relatarPlayback, 2000)`).

---

## 🧭 3. Causa raiz 1 — o navigateur de captura

`server.py` lança com `chromium.launch(headless=True, channel=ch, args=launch_args)`
percorrendo uma lista de canais. **Sem `channel`, o Playwright sobe o
`chrome-headless-shell`** — build reduzido, **sem pilha de áudio real** — e não o
navegador completo em `--headless=new`.

Baselines de 120 s, sem painel aberto, sala `vila-velha`:

| Métrica | **Edge** (`msedge`) | **chrome-headless-shell** | **Chromium completo** |
|---|---|---|---|
| clock de áudio (janela) | **0,9938** | **0,6251** | 0,9782 |
| worklet `gerados` | 23,29 Hz (99,4%) | **14,65 Hz (62,5%)** | 22,93 Hz (97,8%) |
| servidor `chegada` | 23,22 Hz (99,1%) | 15,08 Hz (64,4%) | 22,86 Hz (97,5%) |
| mediana de chegada | 41,9 ms | **0,0 ms** (rajada pura) | 42,1 ms |
| p95 / máx | 53,9 / 97,4 ms | 546 / **2734** ms | 55,7 / 372,5 ms |
| `gaps>60ms` no anel | ~5 | **72** | 7–20 |
| **event loop p95** | **6,6 ms** | **1932,7 ms** | 6,8 ms |
| `gaps_captura` no período | 24 | **307** | 57 |
| drops no ouvinte | 0 | **120** | 0 |

**Conclusão:** a variável não é a **marca** do navigateur, é o **modo headless**.
A hipótese §6.2 do documento anterior ("clock do *null audio sink* do headless") está
**confirmada apenas para o shell reduzido** — o Edge e o Chromium completos em
`--headless=new` têm clock saudável.

A produção estava salva **por sorte**: a ordem era `["msedge", "chrome", None]` e
Edge/Chrome existem nesta máquina. Numa máquina sem eles, cairia silenciosamente no
shell e o áudio quebraria **na fonte**.

### Correção aplicada

`python -m playwright install chromium` foi executado (Chrome for Testing
**156.0.8078.4** + headless shell + FFmpeg + Winldd em `%LOCALAPPDATA%\ms-playwright`),
e em `server.py`:

- Ordem de fallback agora é **`["msedge", "chrome", "chromium", None]`** — o Chromium
  completo entra **antes** do shell.
- Nova variável **`RNP_BROWSER_CHANNEL`**: `msedge` | `chrome` | `chromium`/`full` |
  `shell`/`playwright` (mesmo padrão de `RNP_PORT`, `RNP_HOST`, `RNP_WEBRTC_ONLY`).
- O log de boot imprime o canal escolhido **e** a `browser.version`, o que torna o
  build em execução verificável:
  - `Chromium iniciado com sucesso via playwright default (versao 156.0.8078.4)!` → shell
  - `Chromium iniciado com sucesso via chromium (versao 156.0.8078.4)!` → completo

---

## 🧭 4. Causa raiz 2 — starvation de CPU do event loop

Teste A/B com **captura via Edge** e o painel nativo (pywebview/WebView2) em `/control`:

| Fase | `rearmar_colado` | por relato (2 s) | `gaps>60` | `lead_ms` med. | `lead_ms` mín. | `drops_oldest` | `fila_max` | loop p95 / máx |
|---|---|---|---|---|---|---|---|---|
| VISÍVEL | 35 | 0,89 (32) | 245 | 179 | -816 | 0 | 50 | 6,2 / **910,9** |
| **MINIMIZADA** | 77 | **2,08** (37) | 167 | **-128** | **-24768** | **366** | **64/64** | **330,7 / 2184,0** |
| RESTAURADA | 95 | — (19) | 190 | -128 | -128 | 0 | 26 | 2,3 / 2,5 *(servidor morrendo)* |

Outros deltas da fase minimizada: `descartados_guarda` **+153** (a captura descartou
chunks porque `ws.bufferedAmount ≥ 48000`), `gaps_captura` **+303**.

**Cadeia causal:**

```
CPU satura → event loop do Python atrasa até 2,2 s → servidor para de drenar /ws/pcm_in
  → bufferedAmount da captura estoura → chunks descartados NA ORIGEM (+153)
  → filas dos ouvintes batem PCM_QUEUE_MAX=64 → drop-oldest (+366)
  → colchão do player esgota (lead_ms -24768 ms) → underrun audível
  → navigateur de captura morre: "Target page, context or browser has been closed"
  → [Bridge] Desconectado → processo do servidor sai
```

O log do servidor mostrou `TimeoutError` repetido em `frame.evaluate`
(`media_bridge.py:50`, via `/webrtc/offer` em `media_bridge.py:133`) antes da queda.

**Importante:** minimizar **piorou** o quadro aqui, mas não por throttling do WebView2 —
as flags anti-throttling estavam ativas. Foi contenção de CPU na máquina.

---

## ✅ 5. Veredito sobre "áudio em segundo plano"

Mesmo teste A/B, agora com **Chromium completo** na captura, janela `Edge --app=/stage`
com as `KEEP_ALIVE_FLAGS` reais do `dock_window.py`:

| Fase | `rearmar_colado` | relatos | **por relato (2 s)** | mediana | p95 | `lead_ms` med. | `play_ratio` | drops | `fila_max` |
|---|---|---|---|---|---|---|---|---|---|
| VISÍVEL | 25 | 28 | 0,89 | 43,6 ms | 59,0 | 99 | 0,9999 | 0 | 9/64 |
| **MINIMIZADA** | 18 | 45 | **0,40** | 42,6 ms | 55,4 | **115** | 0,9999 | **0** | 9/64 |
| RESTAURADA | 13 | 20 | 0,65 | 42,9 ms | 56,9 | 101 | **1,0000** | 0 | 9/64 |

CPU entre **92% e 100%** durante todo o teste — e ainda assim áudio limpo.

**Minimizado ficou melhor que visível.** O colchão nunca colapsou (mediana 115 ms contra
`TARGET_LEAD = 120 ms`), não houve drift de clock e nenhum drop.

Confirmação independente, com o painel do operador aberto no Chrome:

```
captura     : gerados 21058, enviados_ws 21058, descartados_guarda 0, via_worker true
              entrega_mediana_ms 41.4, p95 50.3, max 50.9
playback    : /control → rearmar_colado 0, mediana 42.3, play_ratio 0.9947, lead_ms 248
              /stage   → rearmar_colado 2, mediana 44.1, play_ratio 1.0,    lead_ms 115
connection  : level good, rtt 26–33 ms, jitter 18 ms, loss 0%
event loop  : p95 4.6 ms, max 7.5 ms
```

---

## 🎛️ 6. Causa raiz 3 — o OBS não estava usando o áudio da ponte

Coleção ativa **SIIES** (`%APPDATA%\obs-studio\basic\scenes\SIIES.json`, perfil "Sem nome"):

| Fonte | URL | Estado | Visível em |
|---|---|---|---|
| `Navegador` | `http://127.0.0.1:3333/stage` | **`muted=true`** | **nenhuma cena** (oculta em Principal 3 e Principal 3 2) |
| `Audio Webconf` | `https://conferenciaweb.rnp.br/sala/vila-velha` | **`muted=false`, `volume=1.4125`** | Principal, Principal 2, Principal 3, Principal 3 2 |
| `Navegador 4 Slide` | `https://conferenciaweb.rnp.br/sala/vila-velha` | `muted=true` | Principal 3 |
| `Navegador 3` | `http://127.0.0.1:3333/chat/featured` | `muted=false` | (oculta em Principal 3 / 3 2) |

Consequências:

1. **Nenhuma engenharia de áudio da ponte está no caminho do som** — nem Web Worker,
   nem colchão de 120 ms, nem flags anti-throttling, nem filas por cliente.
2. O que sai para a live é uma **página BBB crua dentro do CEF do OBS**, que é
   *throttleada* quando a cena não está ativa — exatamente o "trava em segundo plano".
3. São **3 clientes BBB completos** na mesma máquina (2 no OBS + 1 da captura),
   parte direta da saturação medida na seção 4.

**Ação recomendada:** desmutar `Navegador` (`/stage`), manter "Controlar áudio via OBS"
ligado e "Shutdown source when not visible" **desligado**; mutar ou remover
`Audio Webconf` e `Navegador 4 Slide`. Se quiser backup, mantê-lo **mutado** — um
backup quente reintroduz a carga que fez o `/stage` travar, e duas fontes desmutadas
ao mesmo tempo geram áudio duplicado/eco.

Assinatura diagnóstica útil: `pcm_diag.playback` é indexado por `location.pathname`.
Uma página CEF **oculta** mantém o WebSocket aberto mas tem os `setInterval`
throttleados — então **ouvinte vivo + `playback` vazio = browser source oculta**.

---

## 🎨 7. Por que o CSS do gerador não se aplica no OBS

Não é especificidade, não é falta de `!important`, não é sintaxe. É **topologia de frames**.

**Evidência 1 — o OBS injeta o Custom CSS só no frame principal**
(`obs-browser/browser-client.cpp`, `BrowserClient::OnLoadEnd`):

```cpp
if (frame->IsMain() && bs->css.length()) {
    std::string uriEncodedCSS = CefURIEncode(bs->css, false).ToString();
    script += "const obsCSS = document.createElement('style');";
    script += "obsCSS.appendChild(document.createTextNode(decodeURIComponent(\"" + uriEncodedCSS + "\")));";
    script += "document.querySelector('head').appendChild(obsCSS);";
    frame->ExecuteJavaScript(script, "", 0);
}
```

**Evidência 2 — nessa URL o frame principal não é o BBB.** `GET` em
`https://conferenciaweb.rnp.br/sala/vila-velha` termina em
`https://conferenciaweb.rnp.br/conference/rooms/vila-velha/invite_userid`
(200, 21.736 bytes, portal Rails com Turbo/GTM):

| termo no HTML | ocorrências |
|---|---|
| `data-test` | **0** |
| `cameraDock` | **0** |
| `html5client` | **0** |
| `bigbluebutton` | 4 (só links/`action` do join) |

O único `<iframe>` é o do Google Tag Manager (`height=0`, `display:none`).

**Conclusão:** o `<style>` é anexado ao portal, onde nenhum seletor do gerador existe.
O cliente BBB (`html5client` / host Mconf) roda em **frame filho**, e CSS não atravessa
fronteira de frame. É a mesma razão pela qual `server.py` precisa de
`find_active_bbb_frame` — cujo comentário diz que o portal "não tem
`#app`/`webcamVideoItem`", e o bug corrigido em `0e442a55` (eleger o portal em vez da
reunião) só é possível se os dois frames coexistem em `page.frames`.

O CSS gerado **é válido**: 5 modos de cena, 11.501 / 15.031 / 12.443 / 12.175 /
15.685 chars, chaves balanceadas, 5/5 saídas distintas, 0 erros de JS.

**Equivalência dos 5 modos de cena com os endpoints da ponte:**

| modo do gerador | endpoint / preset da ponte |
|---|---|
| `so-cameras` | `/cameras/grid`, ou `/stage` preset `cameras-only` |
| `so-slides` | `/presentation`, ou `/stage` preset `slide-only` |
| `cameras-slides` | `/stage` preset `side-by-side` |
| `completo` | `/stage` + `/chat` |
| `so-chat` | `/chat` |

**Correção aplicada:** aviso vermelho `.aviso.bloqueio` na interface do gerador com a
evidência e as duas saídas viáveis; o mesmo aviso vai no **cabeçalho do CSS exportado**
(viaja junto com o `.css`); seção do `SKILL.md` corrigida, inclusive o "próximo passo"
do `/css-editor` — para ter efeito no portal, a aplicação teria de ser **pela ponte**,
injetando no frame BBB ativo via `frame.evaluate()`.

---

## ⚡ 8. Energía e MMCSS do Windows

### Hardware

Avell **High Performance STORM BS** · Intel **i7-12650H** (híbrido P+E, 16 threads
lógicas) · **RTX 4050 Laptop** · Windows build **26200** (25H2) · bateria presente,
normalmente na tomada (`ACLineStatus=1`).

Áudio/captura: **Focusrite USB Audio Root**, Realtek HD Audio (alto-falantes / mixagem
estéreo / microfone externo), **Intel Smart Sound para áudio USB**, NVIDIA HD Audio,
**ezcap U3 capture**, 3× "NDI Webcam Audio" (virtuais). Rede: **Realtek PCIe GbE**.

### O que o slider mudou e o que não mudou

```
ActivePowerScheme         : 381b4222  Equilibrado        ← plano clássico NÃO mudou
ActiveOverlayAcPowerScheme: ded574b5  melhor desempenho  ← slider APLICADO (na tomada)
ActiveOverlayDcPowerScheme: (não definido)
```

Em notebook com Modern Standby o plano clássicofica travado em **Equilibrado**; o que o
operador muda é o **overlay**. Ele direciona o scheduler para os P-cores, mas **não
toca** nos ajustes que afetam áudio em tempo real:

| Ajuste | Valor | Por que importa |
|---|---|---|
| `PROCTHROTTLEMIN` | **CA=5 / CC=5** | CPU desce ao P-state mínimo entre rajadas e demora a subir — casa com o pior momento do A/B (fase restaurada, `lead_ms` -235) |
| `PROCTHROTTLEMAX` | CA=100 | ok |
| `ASPM` (PCIe Link State PM) | **CA=2 (máximo)** | latência em NIC Realtek (WebRTC da sala), GPU e ezcap |
| `NetworkThrottlingIndex` | **10 (0x0A)** | limita pacotes/ms durante playback — e o áudio da ponte anda por **WebSocket** |
| `SystemResponsiveness` | **20** | reserva 20% da CPU *contra* as threads de mídia |
| `Tasks\Audio` | Priority 6, **Scheduling Category Medium**, **SFIO Priority Normal**, Background Only True | prioridade de áudio abaixo do ideal |
| `Tasks\Pro Audio` | Priority 1, Scheduling High | — |
| `PowerThrottling` | chave **ausente** | EcoQoS / Modo de eficiência ativo; nenhum `PowerThrottlingOff` em IFEO para `obs64.exe`, `obs-browser-page.exe`, `msedge.exe`, `chrome.exe`, `python.exe` |

`PERFBOOSTMODE`, core parking e suspensão seletiva USB estão **ocultos** — lê-los exige
`powercfg -attributes ... -ATTRIB_HIDE`, que altera o sistema e pede admin. **Não foi
feito.**

`SmartAppControlState: Off` · `CodeIntegrityPolicyEnforcementStatus: 2` ·
`UsermodeCodeIntegrityPolicyEnforcementStatus: 0` · sem Mark-of-the-Web nos artefatos.

### Tutorial embutido no `/control`

Card recolhível `⚡ Áudio trava em segundo plano? Ajustes de energía do Windows`, logo
abaixo do card **Acesso por outra máquina (LAN)**: **9 grupos, 29 comandos**, cada um num
botão que copia para a área de transferência (`copiarComando`).

Ordem: revelar ocultos → **ler e anotar** → CPU não cair de frequência → latência
PCIe/USB → aplicar → MMCSS → power throttling do OBS → conferir → **reverter** (com os
valores medidos *antes* nesta máquina).

**Nenhum desses comandos foi aplicado.** São recomendações derivadas da medição; o
efeito deles não foi verificado.

⚠️ **Armadilha de implementação registrada:** o HTML do `/control` vive numa **f-string**
de `server.py`. Caminhos de registro precisam de barra **dobrada** no fonte
(`HKLM\\SOFTWARE`) para chegar com barra **simples** no navigateur — senão o comando
copiado falha no `reg.exe`. Barras simples numa f-string geram `SyntaxWarning`
(`invalid escape sequence`), tratado como erro na verificação.

---

## 🪶 9. Novos padrões do `/control` (menos CPU)

| Item | Antes | Agora | Onde |
|---|---|---|---|
| Preview do grid de câmeras | ligado | **desligado** | `web/dock.js` |
| Preview da apresentação (thumb) | ligado | **desligado** | `web/dock.js` |
| Preview do palco | ligado | **desligado** | `web/dock.js` |
| Monitor local de áudio | ligado | **mudo** | `web/dock.js` |
| Medidores VU individuais | ligados | **desligados** | `web/audio-mixer.js` |
| Poll de `/api/media` | 500 ms fixos | **500 ms com medidores ligados / 3000 ms desligados** | `web/audio-mixer.js` |

Detalhes de implementação:

- `previewOff = { cameras: true, presentation: true, stage: true }` — antes faltava
  `stage` no objeto padrão (ficava `undefined` → ligado).
- Monitor: `if (savedMuted !== '0') toggleMonitorMute()` — só um `'0'` explícito
  (operador ligou e ficou gravado) mantém o som.
- Medidores: `window.RnpMeters` com `toggle()` e getter `off`. **Desligado, o dock nem
  abre o `/ws/peaks_out`** (~16 msg/s a menos e sai o fan-out correspondente no
  servidor); `disconnectPeaks()` fecha o socket e zera as barras; `body` ganha a classe
  `.medidores-desligados` que esmaece barra e readout (para o operador não ler "— dB"
  e achar que a trilha está muda).
- Botão `#btnToggleMeters` no card do mixer, com rótulo já no estado padrão.
- **Preferência gravada continua valendo sobre o padrão** — quem já alternou antes não
  perde a escolha. ⚠️ Isso significa que, num navigateur com `localStorage` populado,
  o novo padrão pode não aparecer; basta alternar os botões uma vez.
- Nada disso afeta a detecção de fala nem os chips de voz: esses usam os picos que a
  página da sala amostra para o `/ws/peaks_in`, não o socket de saída.

### Verificação funcional (navigateur real, 22/22 OK)

`localStorage` vazio = padrão de fábrica. A página abriu **um único WebSocket**:

```
WebSockets abertos: ws://127.0.0.1:3333/ws/pcm_out
  /ws/peaks_out NÃO foi aberto                     OK
  /ws/pcm_out segue aberto (telemetria preservada) OK
```

`src` dos iframes em `about:blank`, thumb sem `src`, rótulos "Mostrar preview", monitor
"Mudo", `body.medidores-desligados` presente, botões religam tudo (medidores → abre
`/ws/peaks_out`; câmeras → `src` volta a `/cameras/grid?audio=0`), preferência gravada,
**0 erros de JS**.

O `/ws/pcm_out` do dock foi **mantido de propósito**: é ele que alimenta
`playback["/control"]`, a telemetria que permitiu todo este diagnóstico. Desligar o
monitor só zera o ganho.

### Medição de CPU — resultado honesto

Alternado na mesma página (30 s cada, para cancelar deriva):

| Fase | CPU mediana | CPU máx | `TaskDuration` (ms/s) | renderers |
|---|---|---|---|---|
| A: tudo desligado (novo padrão) | 95,45% | 98,73% | 1009,95 | 7 |
| B: tudo ligado (comportamento antigo) | 96,32% | 98,56% | 1015,25 | 7 |
| A2: tudo desligado de novo (controle) | 96,09% | 98,05% | 1010,32 | 7 |

**Diferença: +0,55 ponto percentual ≈ 0,09 núcleo — dentro do ruído.** A medição é
**inconclusiva** porque a máquina já estava em ~96%: com o teto atingido, o agregado não
mostra custo marginal. (Os iframes são *same-site*, então compartilham o renderer — daí
`renderers` constante em 7.)

Isto está registrado **no próprio comentário do código** para ninguém herdar uma
promessa não medida. O corte vale por outro motivo: são dois decodificadores de vídeo e
um player PCM a menos disputando o que sobra — e o que sobra é o que decide se o event
loop atrasa e o áudio picota.

### Para onde a CPU realmente vai (16 núcleos lógicos)

`PercentProcessorTime` é **% de 1 núcleo** (pode passar de 100):

| Processo | % de 1 núcleo | núcleos | % da máquina |
|---|---|---|---|
| `msedge` (5 proc., inclui o navigateur de captura) | 363 | 3,63 | **22,7%** |
| `obs64` | 343 | 3,43 | **21,4%** |
| `Qoder IDE` | 227 | 2,27 | 14,2% |
| `obs-browser-page` (5 proc.) | 162 | 1,62 | **10,1%** |
| `chrome` (5 proc.) | 157 | 1,57 | 9,8% |
| `Idle` | 100 | 1,00 | 6,25% *(→ máquina ~93,75% ocupada)* |
| `System` | 68 | 0,68 | 4,25% |
| `dwm` | 58 | 0,58 | 3,6% |
| `audiodg` | 26 | 0,26 | 1,6% |
| **`python` (a ponte)** | **10** | **0,10** | **0,6%** |

Total ≈ 1554% de 1 núcleo ≈ **15,5 de 16 núcleos**. A ponte é o componente **mais
barato** da cadeia; o orçamento está no navigateur de captura, no OBS e nas páginas de
navigateur do OBS.

Levantamento adicional: `dock.css` tem **0 animações infinitas** e **0 `@keyframes`**
(13 `transition`), então não há custo contínuo de animação. Timers do dock:
`fetchStatus` 1000 ms, `pollDockStatus` 2000 ms, `/api/media` 500→3000 ms.

---

## 🛠️ 10. Alterações de código nesta sessão

| Arquivo | Mudança |
|---|---|
| `server.py` | `RNP_BROWSER_CHANNEL` + ordem de fallback `["msedge","chrome","chromium",None]` + log do canal e da `browser.version` |
| `server.py` | Tutorial de energía no `/control` (`#energiaCard`, 9 grupos, 29 comandos) |
| `server.py` | Botão `#btnToggleMeters` no card do mixer |
| `web/dock.js` | `copiarComando()` — no escopo global, **antes** da IIFE, como os outros handlers `onclick=` |
| `web/dock.js` | Previews nascem desligados (os 3); monitor nasce mudo |
| `web/audio-mixer.js` | Medidores desligados por padrão; `window.RnpMeters`; `connectPeaks`/`disconnectPeaks`; poll `/api/media` 500/3000 ms |
| `web/dock.css` | `.energia-tut`, `.energia-aviso`, `.energia-grupo`, `.energia-cmd`, `.energia-nota` (cores via `rgba`/opacidade — funcionam nos temas claro e escuro) |
| `web/dock.css` | `.medidores-desligados` esmaece barra e readout |
| `tools/gerador_css_obs.html` | Aviso `.aviso.bloqueio` com a evidência do `IsMain()` + alternativas; aviso no cabeçalho do CSS exportado |
| `.agents/skills/rnp-to-obs/SKILL.md` | Seção do gerador corrigida (premissa do portal não funciona) + próximo passo do `/css-editor` revisto |
| `installer.iss` | `MyAppVersion` 1.1.11 → 1.1.12 → 1.1.13 → **1.1.14** |
| `server.py` | `youtube_chat_loop`: substitui `data.sync_items()` (generator que executava `time.sleep(1.0)` síncrono na main thread do `asyncio`) por `data.items` |
| `tests/test_media_bridge.py` | Testes para `youtube_chat_loop` sem `sync_items()` e para os novos padrões do dock / tutorial de energia |

`DOCK_ASSET_VERSION` é derivado dos **mtimes** de `dock.css`, `dock.js`,
`audio-mixer.js`, `media-source.js` e `connection-quality.js` — então mudar qualquer um
deles **invalida o cache sozinho** (`?v=`), sem passo manual.

---

## 🏗️ 11. Builds e artefatos

### 1.1.12 (16:32–16:40)

Primeiro build com as correções de 05/11 + fallback do navigateur + tutorial de energía.

| Artefato | Tamanho | Horário |
|---|---|---|
| `dist\RNP to OBS\RNP to OBS.exe` | 15.940.211 B | 16:32:13 |
| `dist_installer\Instalador_RNP_to_OBS_Setup.exe` | 58,5 MB | 16:37:43 (ISCC 97 s) |
| `dist_installer\RNP_to_OBS_1.1.12_portable.zip` | 82,2 MB · 672 entradas · raiz, 0 wrapper | 16:40:55 |

### 1.1.13 (17:22–17:30) — **versão atual**

Acrescenta os novos padrões do dock.

| Artefato | Tamanho | Horário |
|---|---|---|
| `dist\RNP to OBS\RNP to OBS.exe` | 15.940.468 B | 17:22:24 (PyInstaller ~327 s) |
| `dist_installer\Instalador_RNP_to_OBS_Setup.exe` | **58,49 MB** (61.333.694 B) | 17:29:06 (ISCC **308 s**) |
| `dist_installer\RNP_to_OBS_1.1.13_portable.zip` | 82,2 MB · 672 entradas · raiz, 0 wrapper | 17:29:58 |

⚠️ **Armadilha confirmada na prática:** aos ~180 s o `Setup.exe` estava com **54,13 MB**
no disco. Esse é exatamente o tamanho de instalador **truncado**. Só é confiável após
`Successful compile` no log + processo ISCC ausente + ~58,5 MB. O ISCC levou **308 s**
desta vez (vs 97 s no 1.1.12) porque a máquina estava a ~97%.

### Verificação no artefato (não no fonte)

**22/22 OK** no `dist` 1.1.13: `WORKER_SRC`, `via_worker`, `pcm_diag`,
`RNP_BROWSER_CHANNEL`, ordem de fallback, `energiaCard`, `require_same_origin`,
`copiarComando`, `.energia-cmd`, previews desligados, monitor mudo, `metersOff`,
`connectPeaks`/`disconnectPeaks`, `window.RnpMeters`, `POLL_LENTO_MS`/`POLL_RAPIDO_MS`,
ausência do `setInterval` fixo de 500 ms, `btnToggleMeters`, `.medidores-desligados`.

**Smoke test do EXE real** (porta 3399, `RNP_CONFIG_FILE` isolado para não tocar no
config do operador): subiu, `/api/status` com `pcm_diag` e as 6 chaves,
`esperado_hz = 23.4`, `/control` com 69.085 bytes contendo 1 card / 29 comandos /
9 grupos, caminhos de registro com **barra simples**, `dock.js` 75.727 B com
`copiarComando`, `dock.css` 34.952 B, `media-source.js` 32.326 B com `WORKER_SRC`.

### Comparação com o build instalado

`C:\Program Files (x86)\RNP to OBS` (build 04/11 18:05):

| Arquivo | Instalado (1.1.11) | Fonte / 1.1.13 |
|---|---|---|
| `_internal\web\media-source.js` | 22.208 B · `WORKER_SRC` **False** · `via_worker` **False** | 33.015 B · **True** · **True** |
| `_internal\server.py` | 277.075 chars · `pcm_diag` **False** · `_diag_snapshot` **False** · `loop_lag_monitor` **False** · `/api/pcm_playback` **False** · `RNP_BROWSER_CHANNEL` **False** · `resolve_bind_host` True | 294.816 B · tudo **True** |

**O build instalado não tem o Web Worker PCM nem telemetria nenhuma.**

---

## ⚠️ 12. Pendências e próximos passos

1. **Instalar o 1.1.13.** O que está em `Program Files` é 1.1.11 (04/11 18:05), sem o
   Web Worker PCM e sem telemetria. Fechar antes o app antigo — ele se anexa a um
   servidor já ativo na 3333 em vez de subir o próprio, o que confunde o diagnóstico.
2. **Reconfigurar as fontes no OBS (SIIES).** Seção 6. É a pendência de maior impacto:
   sem ela, o áudio da live continua saindo de uma página BBB crua e a máquina segue com
   3 clientes BBB.
3. **Aplicar (ou não) os ajustes de energía.** O tutorial está no `/control`; nada foi
   aplicado. Prioridade sugerida: `PROCTHROTTLEMIN` → 100 (CA), `ASPM` → 0,
   `NetworkThrottlingIndex` → `0xffffffff`. Ler e anotar antes (`PERFBOOSTMODE` e
   suspensão USB estavam ocultos).
4. **CI nunca rodou.** `.github/workflows/tests.yml` dispara só em push/PR para
   `main`/`master`, mas os 83 commits estão em `feat/dock-redesign`. Abrir PR ou
   acrescentar a branch ao trigger.
5. **CI omite 2 das 7 suítes.** `test_inactivity_guard.py` e `test_media_browser.py`
   precisam de Playwright + Chromium real, que o workflow não instala. Relevante:
   `test_media_browser.py` (97 KB) é a cobertura de integração do `media-source.js`,
   justamente o arquivo do Web Worker — no CI ele só é coberto por asserções estáticas
   de texto em `test_pcm_fanout.py`.
6. **Lacuna de release.** 1.1.7–1.1.11 foram compilados sem commits `chore(release)`;
   o pulo 1.1.6 → 1.1.11 aconteceu dentro de um `feat(bridge)`. O histórico não diz
   qual build contém qual correção — hoje só `docs/AUDIO_ESTABILIDADE_2026-11.md` §5.
7. **`clock_ratio` e `play_ratio` deveriam ser janela móvel**, não cumulativos. Do jeito
   atual não distinguem "clock lento" de "partida tardia" (seção 2). O `relatarPlayback`
   já é janela móvel para as chegadas; falta o mesmo para os clocks.
8. **`audio_channels` vem `0`** no `/api/status` mesmo com PCM fluindo e 2 câmeras.
   Investigar se é só semântica do `describe()` (trilhas que chegam por `<video>` não
   viram "audio") ou se há trilha individual não exposta.
9. **Nada foi commitado.** `git` continua ausente nesta máquina; as mudanças desta
   sessão (fallback do navigateur, tutorial, padrões do dock, `installer.iss` 1.1.13,
   aviso do gerador, `SKILL.md`) existem só no disco.
10. **CPU é o risco dominante remanescente.** Mesmo com tudo corrigido, a máquina opera
    a ~94–97%. Qualquer pico pode reproduzir a cadeia da seção 4. Vale medir de novo
    depois de remover as 2 fontes BBB diretas do OBS.

---

## 🧪 13. Como reproduzir as medições

Scripts em `.test_runtime/` (diretório ignorado pelo git):

| Script | Para quê |
|---|---|
| `audio_probe.py` | Baseline da cadeia PCM: taxas **por janela** de `gerados`/`enviados_ws`/chegada, `clock_ratio`, event loop, gaps. `--label --seconds --interval` |
| `audio_ab_painel.py` | A/B visível → minimizada → restaurada. `--via app` (pywebview, produção) ou `--via edge` (`--app=` com as `KEEP_ALIVE_FLAGS` reais); `--url` e `--pb-key` para escolher a página e a chave de `playback` |
| `validar_defaults_dock.py` | Verifica no navigateur real os novos padrões; instrumenta `WebSocket` com `Proxy` para provar que `/ws/peaks_out` **não** é aberto |
| `medir_cpu_defaults.py` | A/B de CPU com métricas CDP (`Performance.getMetrics`) e `GetSystemTimes` |
| `diag_cpu_dock.py` | CPU por processo + animações CSS + timers do dock |
| `diag_energia_final.py` / `diag_energia_registro.py` / `diag_energia_guid.py` | Plano de energía, overlay, MMCSS, power throttling |
| `diag_energia_windows.py` | Panorama geral (hardware, MMCSS, dispositivos de áudio) |
| `inspect_obs_cenas.py` | Fontes de navigateur do OBS, visibilidade por cena, mute/volume |
| `validar_gerador_css.py` | Carrega o gerador e confere os 5 modos, chaves balanceadas, avisos |
| `validar_tutorial_energia.py` | Confere o tutorial renderizado no `/control` (inclusive barra simples nos caminhos de registro) |
| `validar_dist_1113.py` / `smoketest_exe_1112.py` | Verificação do artefato e smoke test do EXE |
| `gerar_zip_1112.ps1` | ZIP portátil com layout de raiz (`includeBaseDirectory=$false`) |
| `inspect_git_changes.py`, `inspect_git_diff_real.py`, `inspect_mac_session.py`, `inspect_branch_state.py`, `inspect_mac_diff_detail.py` | Leitura do `.git` sem o CLI git |

Relatórios salvos: `probe_baseline.txt`, `mac_session_report.txt`, `git_diff_real.txt`,
`branch_state_report.txt`, `mac_diff_detail.txt`, `build_1112.log`, `build_1113.log`,
`iscc_1112.log`, `iscc_1113.log`, `server_chromium.log`, `server_chromium_full.log`,
`server_default.log`, `audio_probe_amostras.jsonl`, `audio_ab_amostras.jsonl`.

### Receitas que valem para下一次 vez

- **Ler `powercfg` em pt-BR:** os rótulos são "Índice de Configurações de **Correntes
  Alternadas/Contínuas** Atuais" e a saída vem em **cp850** — decodificar como `utf-8`
  corrompe os acentos e quebra o casamento. Ajustes ocultos não aparecem no `/query` do
  subgrupo inteiro; pedir por alias/GUID individual ou ler
  `HKLM\SYSTEM\CurrentControlSet\Control\Power\User\PowerSchemes\<esquema>\<subgrupo>\<ajuste>`
  (`AcSettingIndex`/`DcSettingIndex`) — que só guarda **deltas**.
- **Contadores de performance são localizados:** `Get-Counter -ListSet Processor` falha
  em pt-BR; tentar também `Processador`.
- **`wmic` não existe no Windows 25H2.** Usar `Get-CimInstance Win32_Process`.
- **Lançar EXE recém-compilado:** `&` pode falhar com "Uma política de Controle de
  Aplicativo bloqueou este arquivo" enquanto `Start-Process -PassThru` funciona na hora.
  Antes de culpar o Windows, conferir `SmartAppControlState` e
  `UsermodeCodeIntegrityPolicyEnforcementStatus` (aqui: Off e 0).
- **Rodar uma segunda instância do servidor:** ela sonda a porta fixa no boot e, se já
  houver uma ativa, **se anexa** em vez de subir a própria — o smoke test passa a medir
  o processo errado. Usar `RNP_PORT` distinto e `RNP_CONFIG_FILE` isolado.
- **Redirecionamento `>` em background** pode deixar arquivo de 0 byte a execução toda
  (PowerShell só libera no fim). `Tee-Object` escreve progressivamente e dá para ler
  pelo poll do terminal.

---

## 🛠️ 14. Diagnóstico ao vivo no OBS e blindagem `1.1.14` (YouTube, Chat RNP, Enquetes e Disco)

### 14.1 Causa raiz do engasgo de ~1.000 ms no `/stage` (YouTube Live Chat)
Com a sala `mconf-cefor03` conectada em produção, a telemetria `/api/status` revelou:
- `event_loop_atraso_ms`: `p95_ms = 963.4 ms`, `max_ms = 1005.3 ms` (+1 gap a cada ~2,0 s).
- `playback["/stage"]`: `rearmar_colado = 1`, `rearmar_adiantado = 2`, `max_ms = 1047 ms`.
- `youtube_connected = True` (`youtube_auto_connect = True` para o vídeo `wkSGqVpkp4Y`).

**Causa:** em `youtube_chat_loop` (`server.py`), `chat.get()` rodava em worker thread (`asyncio.to_thread`), mas logo abaixo o loop iterava `for c in data.sync_items():` **na thread do event-loop `asyncio`**. No `pytchat`, `Chatdata.sync_items()` é um generator que chama `time.sleep(wait_sec)` entre mensagens e `time.sleep(1 - stop_interval)` (~1,0 s) de forma síncrona. Isso congelava o event-loop por ~1.000 ms a cada ciclo de ~2 s, represando o repasse `/ws/pcm_in` → `/ws/pcm_out`.

**Correção (`server.py`):** leitura direta de `data.items` (lista já pronta em memória) sem jamais chamar `data.sync_items()`. Após desconectar o YouTube na instância em execução, o `max_ms` do event-loop caiu imediatamente de `1005.3 ms` para `13.2–14.3 ms` com `0` novos gaps e `0` underruns.

### 14.2 Auditoria de Chat da RNP (`extract_rnp_chat`), Enquetes (`extract_rnp_poll`) e demais loops
- **Por que Chat RNP e Enquetes não travam o áudio como o YouTube fazia:**
  1. Ambos usam `await target_frame.evaluate(...)` (assíncrono via CDP WebSocket; não bloqueia o event-loop Python).
  2. A captura de áudio na sala roda em `AudioWorkletProcessor` + `Web Worker` dedicado (`MessageChannel`), isolada da main thread do BBB.
- **Blindagens adicionais aplicadas em `server.py` (`1.1.14`):**
  1. **Chat RNP (`extract_rnp_chat`):**
     - Adicionado `asyncio.wait_for(..., timeout=SYNC_EVAL_TIMEOUT)`.
     - Cooldown de 15 s (`window._rnpLastChatOpenAttempt`) e verificação de seletores de painel aberto (`#chat-messages`, `[data-test="chatMessages"]`, `[data-test="chatInput"]`) para evitar clicar no botão de chat a cada 1 s quando a sala ainda não tem mensagens.
     - `@lru_cache(maxsize=512)` em `emojize_text` e fast-path que pula mensagens inalteradas em `state.rnp_messages_map`, evitando varreduras `O(N²)` a cada 1 s em salas com muitas mensagens.
  2. **Enquetes (`extract_rnp_poll`):**
     - Adicionado `asyncio.wait_for(..., timeout=SYNC_EVAL_TIMEOUT)`.
     - Substituído o fallback `document.querySelectorAll('aside, div, section')` com `.innerText` (que forçava reflow síncrono em todos os `<div>` do DOM do BBB a cada 2 s quando não havia enquete ativa) por seletores específicos e `textContent` em contêineres semânticos (`aside, section, [role="dialog"], [role="region"]`).
  3. **Apresentação / Slides (`sync_loop`):**
     - Adicionado `asyncio.wait_for(..., timeout=SYNC_EVAL_TIMEOUT)` na leitura de `screen_data` e `timeout=3000, animations="allow"` nos `.screenshot()` de slides/vídeo externo.
  4. **Persistência de configuração (`save_disk_config`):**
     - Mantido `f.flush()` + `os.replace()` atômico sem `os.fsync()`, evitando barreiras físicas de disco na thread do event-loop ao clicar em presets/câmeras no dock.

