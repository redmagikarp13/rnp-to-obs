# Redesign do dock `/control` — linguagem broadcast densa + 2 temas

Data: 2026-10-13
Estado: aprovado pelo usuário em sessão de brainstorming (decisões registradas abaixo)
Escopo: **apenas** o dock `/control`. Os overlays que vão ao ar (`/stage`, `/cameras/grid`,
`/libras`, `/presentation`, `/chat`, `/chat/featured`, `/poll`) **não são tocados** nesta etapa.

---

## 1. Problema

O dock é gerado como f-string Python dentro de `server.py`:

- A rota `/control` ocupa **~1.843 linhas** (3135→4978) com **14 blocos** empilhados numa
  única coluna de rolagem: 10 cards, **47 botões**, 11 `<select>`, 8 campos, 89 IDs.
- O CSS vive na constante `CONTROL_PANEL_CSS` (**~786 linhas**, 2344→3130), injetada no `<head>`.
- **Zero variáveis CSS** (`--*`) em todo o `server.py`: cada cor está escrita por extenso.
- Os 5 temas (`hardware`, `nordic`, `glass`, `obsidian`, `ifes`) são blocos de override
  hardcoded `body.theme-X .seletor {...}`, somando **~102 seletores** (ifes 53, glass 16,
  hardware 12, nordic 11, obsidian 10). Mudar uma cor exige editar até 5 cópias.
- Estética atual: gradientes, `box-shadow` colorido de glow, `translateY` no hover,
  raio 8–12px, emoji decorativo em todo título de seção.

Consequências concretas já diagnosticadas:

- **Duplicação:** cada um dos 7 links de fonte do OBS existe em 2–3 lugares
  (botão grande "Palco Composto /stage" **e** botão solto "Link Palco OBS"; idem Grid, Feed,
  Destaque, Slides, Enquete; "Link Janela Libras" aparece 3×).
- **Má localização:** os 6 presets de layout do palco estão dentro da seção "Sala RNP",
  junto com URL/nome/e-mail.
- **Sem hierarquia para operação ao vivo:** não existe lugar fixo onde o operador veja
  "estou no ar? o som está entrando? como paro?" sem rolar a página.

Usuários: **vários operadores, inclusive novos** — o dock precisa de rótulos explícitos,
confirmação em ação destrutiva e tolerância a erro.

## 2. Decisões aprovadas

| # | Decisão | Escolha |
| :-- | :-- | :-- |
| 1 | Linguagem visual do tema padrão | **Broadcast denso** (ATEM/Resolve): cinzas neutros, superfícies planas, sem gradiente/glow/blur, raio 2px, micro-rótulos uppercase com letter-spacing, VU segmentado, indicador de estado quadrado no estilo tally — mas **sem** afirmar "no ar" (ver §4.1) |
| 2 | Segundo tema | **Ifes claro em papel quente**: canvas off-white levemente bege, verde institucional primário, vermelho reservado a perigo/ação destrutiva |
| 3 | Temas aposentados | `hardware`, `nordic`, `glass`, `obsidian` — removidos |
| 4 | Estrutura | **Barra de status funcional fixa + 5 abas** |
| 5 | Quantidade de abas | **5** (Chat e Enquete juntos, em 4 sub-blocos titulados) |
| 6 | Onde vive o CSS/JS | **Extraído** para `web/dock.css` e `web/dock.js`, servidos como asset estático |
| 7 | Transição | **Substituição direta**; rollback via git (`9eba507` tem a versão anterior) |
| 8 | Vitrine de temas | **Remover** as rotas `/catalog` e `/themes` |

## 3. Arquitetura

### 3.1 Extração

- `web/dock.css` ← substitui `CONTROL_PANEL_CSS` (constante é removida do `server.py`).
- `web/dock.js` ← o JavaScript hoje inline na f-string do `/control` (polling de
  `/api/status`, atualização do grid de câmeras, rack de áudio, toasts, autosave).
- Rotas novas em `media_bridge.py`, seguindo o padrão `FileResponse` já usado por
  `/assets/stage-media.js`, `/assets/audio-mixer.js` etc.:
  - `GET /assets/dock.css` → `web/dock.css`, `media_type='text/css'`
  - `GET /assets/dock.js` → `web/dock.js`, `media_type='text/javascript'`
  - Ambos com `Cache-Control: no-store` (consistente com os demais assets e necessário
    porque o dock é recarregado dentro do OBS).
- `server.py` mantém **apenas** o esqueleto HTML do `/control` + injeção de estado.
- `web/audio-mixer.js` continua existindo e sendo usado (rack de canais individuais);
  o `dock.js` o consome como hoje.
- `build_exe.py` **não precisa mudar**: já empacota `--add-data web;web`, então os novos
  arquivos entram no bundle automaticamente. Verificar no build.

### 3.2 Camada de tokens

Todos os componentes passam a consumir tokens. Tema é **apenas** um bloco de override de
tokens — nenhum seletor de componente por tema.

Tokens (nomes e papéis):

```
--bg, --surface, --surface-2, --surface-inset
--border, --border-strong, --border-soft
--text, --text-muted, --text-disabled
--primary, --primary-border, --primary-text
--ok, --warn, --danger, --danger-surface, --danger-border
--radius, --radius-sm
--space-1..--space-5   (4, 6, 8, 10, 12 px)
--label-size, --label-weight, --label-tracking
--vu-off
--accent-top           (filete superior do card; transparente no tema escuro)
```

**Regra de uso dos três níveis de texto** (definida após medição de contraste — ver §6):

- `--text` — todo texto primário.
- `--text-muted` — **todo** texto secundário informativo, incluindo metadados (dB, FPS,
  contagem de câmeras). É o nível mais claro que ainda garante ≥ 4,5:1 nos dois temas.
- `--text-disabled` — **somente** aba inativa, controle desabilitado e separadores. Nunca
  carrega informação que o operador precise ler para agir. Está abaixo de 4,5:1 de propósito:
  é sinalização de estado, não conteúdo, e estados desativados são isentos do requisito.

**Tema `broadcast` (padrão):**

```
--bg #1a1a1a        --surface #202020     --surface-2 #252525   --surface-inset #141414
--border #2e2e2e    --border-strong #3d3d3d  --border-soft #2c2c2c
--text #e0e0e0      --text-muted #8a8a8a  --text-disabled #6b6b6b
--primary #2d2d2d   --primary-border #3d3d3d  --primary-text #e0e0e0
--ok #3d8b43        --warn #b5a63c        --danger #c0392b
--danger-surface #8b2f2f   --danger-border #a13838
--radius 2px        --radius-sm 1px       --vu-off #2e2e2e
--label-size 8.5px  --label-weight 700    --label-tracking .1em
--accent-top transparent
```

Escala do VU segmentado (ordem fixa, do nível baixo ao clip):
`#3d8b43 → #3d8b43 → #5aa85e → #8fae4a → #b5a63c → #c9973a → #c0392b`, segmentos apagados `--vu-off`.

**Tema `ifes` (papel quente):**

```
--bg #eae6dc        --surface #f7f4ec     --surface-2 #f0ece1   --surface-inset #f0ece1
--border #cdc7b8    --border-strong #c4bca6  --border-soft #ded8c9
--text #211f1a      --text-muted #45413a  --text-disabled #75705f
--primary #15803d   --primary-border #166534  --primary-text #ffffff
--ok #15803d        --warn #b5a63c        --danger #dc2626
--danger-surface #b91c1c   --danger-border #991b1b
--radius 2px        --radius-sm 1px       --vu-off #ded8c9
--label-size 8.5px  --label-weight 800    --label-tracking .1em
--accent-top #15803d          /* filete institucional de 3px no topo do card */
```

O filete verde no topo do card é a assinatura institucional e **é mantido** — expresso pelo
token `--accent-top`, que no tema escuro vale `transparent`. Nenhum outro mecanismo por tema.

### 3.3 Removido da estética atual

Gradientes (`linear-gradient` em botões e fundos), `box-shadow` colorido de glow,
`backdrop-filter`/glassmorphism no dock, `transform: translateY(-1px)` no hover,
raio 8–12px, e **emoji decorativo em títulos de seção** (substituídos por micro-rótulos
uppercase). Ícones, se necessários, viram glifos geométricos ou texto — não emoji.

## 4. Estrutura do dock

### 4.1 Barra de status (fixa no topo, visível em todas as abas)

Conteúdo, da esquerda para a direita:

1. **Indicador de conexão** — quadrado 7×7px + rótulo uppercase, derivado de `/api/status`.
   Estados, honestos com o que a ponte de fato sabe:
   - `Conectado` (`--ok`, verde) quando `connected === true`
   - `Conectando` (`--warn`) quando `connecting === true`
   - `Desconectado` (cinza `--text-muted`) nos demais casos — é texto informativo, portanto
     usa `--text-muted` e não `--text-disabled`

   **Decisão deliberada:** não usar tally vermelho "No ar". A ponte não tem como saber se o
   OBS está transmitindo (`/api/status` expõe apenas `connected`/`connecting`); um tally
   vermelho afirmaria um estado que não existe e induziria o operador novo a erro. Vermelho
   fica reservado para ação destrutiva (`Parar`) e para alerta real.
2. **Resumo de fontes** — `<N> cam · <fps> fps`, usando `cameras_count` e o FPS já amostrado
   na origem (`cameras[].fps`).
3. **Seletor de tema compacto** — dropdown com 2 opções (`Broadcast`, `Ifes`). Substitui a
   antiga seção própria de temas. Continua gravando `control_theme` via `/api/grid/config`.
4. **Master** — rótulo + leitura `dB` + **VU segmentado compacto** (12 segmentos).
   Recebe IDs **novos**: `vuBarSegments`, `vuBarDb`. O medidor vertical completo da aba
   Áudio **mantém** os IDs existentes (`vuMasterBar`, `vuMasterPeak`, `vuMasterDbText`,
   `lblMasterVol`) e continua sendo o controle real de volume. Os dois leem do mesmo estado
   em `dock.js` — a barra é só leitura rápida, não duplica lógica nem fonte de verdade.
5. **Botão `Parar`** — ação destrutiva: chama `/api/disconnect`. Exige **confirmação**
   (ver §6).

### 4.2 Abas

Barra de 5 abas logo abaixo, fixa. Rótulos: `Conexão` · `Palco` · `Câmeras` · `Áudio` · `Chat`.
Aba ativa: `--surface` mais claro (`#2d2d2d` no broadcast), texto `--text`, sem borda inferior
(conecta visualmente ao painel). Inativa: `--surface`, texto `--text-disabled`.

Uma aba visível por vez. Aba ativa persistida em `localStorage` (`rnp.dock.tab`), para o
operador reencontrar o dock onde deixou. **Exceção:** se `connected === false`, força
`Conexão` ao carregar.

**1 · Conexão** — o que se toca antes da live
- Sala RNP: `roomUrl`, `userName`, `userEmail` (IDs preservados)
- `Conectar` / `Desconectar`
- Monitor de saúde das fontes (status + FPS por fonte)
- `Salvar` · `Recarregar` · `Padrões` (IDs `#saveToast`, `#logBox` preservados)
- **Fontes para o OBS** — os 7 links, **uma única vez**, em grade 2 colunas:
  Palco `/stage` · Grid `/cameras/grid` · Libras `/libras` · Chat `/chat` ·
  Destaque `/chat/featured` · Slides `/presentation` · Enquete `/poll`
  (mantém o contrato do teste `test_dock_copia_links_da_mesma_porta`: botões com
  `title` contendo "Copiar URL do/da <Nome>")

**2 · Palco** — composição da cena
- **Presets de layout (6)**, movidos para cá: Lado a Lado 70/30 · Slide Top + Rodapé ·
  PIP Canto Inferior · PIP Canto Superior · Split 50/50 · Somente Slides
- Libras: `selLibrasSize` · auto-reconhecimento (toggle) · limpar lista
- Apresentação / Slides

**3 · Câmeras** — grid e identidade
- Grid ao vivo com **FPS por câmera** (`.cam-fps`; verde ≥20, amarelo ≥10, vermelho <10)
- Por câmera: Focar · Fixar (pin) · Renomear · link individual
- `Restaurar Grid Equilibrado`
- `chkShowNames` · `selNameStyle` · `selGridLayout` · `selCamFormat`

**4 · Áudio** — mixer
- Master geral: `rngMasterVol` + ativo/mudo. Rótulo deve dizer que o master **sai pelo
  `/stage`** (não existe mais player em `/audio`)
- Canais individuais por participante (renderizados por `audio-mixer.js`):
  `[data-audio-id]` com VU, volume, `[data-action=mute]`, `[data-action=copy]`
- Monitor local: `rngMonitorVol` + ligado/desligado
- `Testar medidores`

**5 · Chat** — 4 sub-blocos titulados
- **Chat:** `selChatTheme` · `selChatSize` · `selChatMax` · `chkShowAvatars`
- **Destaque (lower-third):** banner atual · ocultar · lista de mensagens clicáveis
- **Enquete:** `selPollDisplayMode` · `selPollDuration` · `selPollStyle` ·
  mostrar agora · ocultar · testar · limpar
- **YouTube Live Chat:** `ytUrl` · `chkYtAutoConnect` · integrar · parar

### 4.3 Remoções

- **7 botões duplicados** de link de fonte (`Link Palco OBS`, `Link Janela Libras` ×3,
  `Link OBS Enquete`, `Link Grid`, `Link Feed`, `Link Destaque`, `Link Slides`) —
  os links passam a existir só na aba Conexão.
- Rotas **`/catalog`** e **`/themes`** (mesmo handler `get_themes_catalog`) — removidas,
  passam a 404. O seletor compacto na barra de status as substitui.
- CSS dos 4 temas aposentados (~49 seletores) e os blocos `body.theme-*` correspondentes.
- Seção própria de temas no dock.

## 5. Migração de configuração

`control_theme` em `config.json` pode conter valor aposentado. Regra, aplicada na leitura
(`load_disk_config`/`BridgeState`), não em script de migração:

```
valor em {broadcast, ifes}  → mantém
qualquer outro valor        → broadcast
```

`DEFAULT_CONFIG["control_theme"]` muda de `hardware` para **`broadcast`**.

O `config.json` do usuário em produção já contém `ifes`, que é válido — não muda.

## 6. Operador novo: requisitos de usabilidade

- **Rótulos explícitos** em todo campo; nada depende só de ícone ou de cor.
- **Confirmação em ação destrutiva:** `Parar` (desconectar) e `Padrões` (reset de config)
  abrem confirmação inline em duas etapas no próprio dock (o botão vira
  `Confirmar?` por 4s e reverte). Não usar `window.confirm` — dentro do dock do OBS o
  diálogo nativo é pouco confiável.
- **Estado nunca silencioso:** toda ação assíncrona mostra resultado no toast existente
  (`#saveToast`) — sucesso e falha. Falha de gravação de config continua **sem** fingir
  sucesso (comportamento já coberto por `test_dock_nao_confirma_libras_quando_gravacao_falha`).
- **Contraste:** ≥ 4,5:1 em todo texto informativo, nos dois temas. Valores **já medidos**
  com os tokens do §3.2 (luminância relativa WCAG):

  | Par | Broadcast | Ifes |
  | :-- | :-- | :-- |
  | `--text` sobre `--surface` | 12,3:1 ✓ | 14,9:1 ✓ |
  | `--text-muted` sobre `--surface` | 4,7:1 ✓ | 9,2:1 ✓ |
  | `--primary-text` sobre `--primary` | 10,4:1 ✓ | 5,0:1 ✓ |
  | `--text-disabled` sobre `--surface` | 3,1:1 — só estado inativo | 4,5:1 — só estado inativo |

  Pares a re-conferir na implementação (por dependerem da superfície final):
  `--text` sobre `--bg` e `--surface-2`; texto do botão `Parar` sobre `--danger-surface`.
  Se algum par informativo ficar abaixo de 4,5:1, ajustar o token — **não** rebaixar o
  requisito.
- **Alvo de clique:** altura mínima 26px para botões e campos, mesmo no tema denso.

## 7. Contratos preservados (não podem quebrar)

Testes existentes dependem destes hooks — todos devem continuar presentes e funcionais:

- IDs: `roomUrl`, `userName`, `userEmail`, `ytUrl`, `chkYtAutoConnect`, `selLibrasSize`,
  `selPollDisplayMode`, `selPollDuration`, `selPollStyle`, `selNameStyle`, `selGridLayout`,
  `selCamFormat`, `selChatTheme`, `selChatSize`, `selChatMax`, `chkShowNames`,
  `chkShowAvatars`, `rngMasterVol`, `rngMonitorVol`, `saveToast`, `logBox`,
  `audioDockCard`, `audioMixerRack`, `vuMasterBar`, `vuMasterPeak`, `vuMasterDbText`,
  `lblMasterVol`
- Seletores: `[data-audio-id]`, `[data-action=mute]`, `[data-action=copy]`, `.cam-fps`,
  `button[title*="Copiar URL do/da <Nome>"]`
- Funções JS globais chamadas por `onclick` inline ou por teste: `copyUrl`, `updateGridConfig`,
  `autoSaveField`, `toggleLibras`, `testAudioVuMeter`
- Endpoints: `/api/status`, `/api/config`, `/api/grid/config`, `/api/connect`,
  `/api/disconnect`, `/api/audio/control`, `/api/media`, `/api/libras/toggle_auto`,
  `/api/server/stop` — **nenhum muda**
- `body` continua com classe `theme-<control_theme>`

## 8. Testes

Novos (TDD — RED antes de implementar):

1. **Indicador de conexão** reflete `/api/status`: `Desconectado` com
   `connected=false`, `Conectado` com `connected=true`, `Conectando` com `connecting=true`.
2. **Troca de aba**: clicar na aba N mostra o painel N e esconde os outros quatro;
   exatamente um painel visível por vez.
3. **Aba persistida**: recarregar o dock mantém a última aba ativa (`localStorage`).
4. **Sem conectação força aba Conexão** ao carregar.
5. **Dedup de links**: cada um dos 7 caminhos (`/stage`, `/cameras/grid`, `/libras`,
   `/chat`, `/chat/featured`, `/presentation`, `/poll`) aparece em **exatamente um**
   botão de copiar no dock.
6. **Vitrine removida**: `/catalog` e `/themes` devolvem 404.
7. **Migração de tema**: `control_theme` legado (`glass`) resulta em `broadcast`;
   `ifes` permanece `ifes`.
8. **Tokens**: `web/dock.css` declara `--bg`/`--surface`/`--text` e **não** contém
   `body.theme-` com propriedades de componente (guarda contra voltar a hardcoded).
9. **Assets servidos**: `/assets/dock.css` e `/assets/dock.js` devolvem 200 com
   `text/css` / `text/javascript` e `no-store`.
10. **Confirmação em duas etapas**: `Parar` não chama `/api/disconnect` no primeiro clique.

Regresão: os **79 testes** atuais devem continuar verdes sem edição — em particular
`test_dock_copia_links_da_mesma_porta`, `test_dock_lista_audio_sem_camera_e_aplica_mute`,
`test_dock_mostra_medidor_de_fps`, `test_dock_nao_confirma_libras_quando_gravacao_falha`,
`test_camera_format_persiste_e_rejeita_invalido`.

Validação manual: dock aberto em navegador a 330px e a 480px de largura, nos dois temas,
verificando contraste, ausência de rolagem horizontal e barra de status sempre visível.

## 9. Riscos

| Risco | Mitigação |
| :-- | :-- |
| Quebrar um hook usado por teste ao reorganizar o HTML | §7 lista os contratos; teste novo nº 5 e os 4 testes de dock existentes são a rede |
| Dock dentro do OBS cachear CSS/JS antigo | assets com `Cache-Control: no-store`; orientar refresh da fonte do dock |
| Aba esconder algo que o operador precisa durante a live | barra de status fixa carrega indicador de conexão, master, FPS e `Parar` |
| Regressão no empacotamento `.exe` | `build_exe.py` já inclui `web/`; conferir `web/dock.css` e `web/dock.js` no bundle após o build |
| `localStorage` indisponível no CEF do OBS em algum contexto | acesso envolvido em try/catch com fallback para aba padrão |

## 10. Fora de escopo

- Overlays que vão ao ar (`/stage`, `/chat`, `/poll`, `/libras`, `/cameras/grid`,
  `/presentation`, `/chat/featured`) — mantêm o visual atual.
- `/` e `/dock` — ambos apenas redirecionam para `/control` (`root_redirect`), então não
  exigem mudança; só precisam continuar funcionando.
- Plugin C++ (`obs_plugin_cpp/`).
- Limpeza de `css_custom.css` e `css_custom_discord.css` (arquivos mortos, nenhum `.py` os
  referencia) — decidir à parte.
