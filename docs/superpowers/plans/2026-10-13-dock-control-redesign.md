# Redesign do dock `/control` — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reconstruir o dock `/control` sobre uma camada de tokens com 2 temas (broadcast denso padrão + Ifes papel quente), reorganizado em barra de status fixa e 5 abas, com CSS/JS extraídos do `server.py`.

**Architecture:** O CSS sai da constante `CONTROL_PANEL_CSS` (~786 linhas) para `web/dock.css` e o JS inline sai para `web/dock.js`, ambos servidos como asset estático por `media_bridge.py` (padrão `FileResponse` já usado por `stage-media.js` e `audio-mixer.js`). Temas deixam de ser ~102 blocos `body.theme-X .componente` e passam a ser **apenas** override de tokens em `body.theme-X`. O HTML do `/control` vira shell (barra de status + 5 painéis de aba) e as seções existentes são redistribuídas sem perder nenhum ID de contrato.

**Tech Stack:** Python 3.14, FastAPI, Jinja-free f-strings, CSS puro com custom properties, JavaScript vanilla, Playwright + `unittest.IsolatedAsyncioTestCase`, `fastapi.testclient.TestClient`.

**Spec:** `docs/superpowers/specs/2026-10-13-dock-control-redesign-design.md` — o plano argumenta a partir do spec; leia os dois.

## Global Constraints

- **Escopo:** somente o dock `/control`. Overlays que vão ao ar (`/stage`, `/cameras/grid`, `/libras`, `/presentation`, `/chat`, `/chat/featured`, `/poll`) **não** são modificados.
- **Suíte existente:** os **79 testes** atuais devem continuar verdes **sem edição**. Nenhuma tarefa pode "consertar" teste existente alterando sua intenção.
- **Contratos do spec §7** — todos estes IDs continuam presentes no `/control`: `roomUrl`, `userName`, `userEmail`, `ytUrl`, `chkYtAutoConnect`, `selLibrasSize`, `selPollDisplayMode`, `selPollDuration`, `selPollStyle`, `selNameStyle`, `selGridLayout`, `selCamFormat`, `selChatTheme`, `selChatSize`, `selChatMax`, `chkShowNames`, `chkShowAvatars`, `rngMasterVol`, `rngMonitorVol`, `saveToast`, `logBox`, `audioDockCard`, `audioMixerRack`, `vuMasterBar`, `vuMasterPeak`, `vuMasterDbText`, `lblMasterVol`.
- **Seletores de contrato:** `[data-audio-id]`, `[data-action=mute]`, `[data-action=copy]`, `.cam-fps`, `button[title*="Copiar URL do/da <Nome>"]`.
- **Funções JS globais** chamadas por `onclick` inline ou por teste continuam globais: `copyUrl`, `updateGridConfig`, `autoSaveField`, `toggleLibras`, `testAudioVuMeter`, `setStagePreset`, `handleDisconnect`, `fetchStatus`.
- **Endpoints não mudam:** `/api/status`, `/api/config`, `/api/grid/config`, `/api/connect`, `/api/disconnect`, `/api/audio/control`, `/api/media`, `/api/libras/toggle_auto`, `/api/server/stop`.
- **`body` continua com classe `theme-<control_theme>`.**
- **Sem `window.confirm`** — dentro do dock do OBS o diálogo nativo é pouco confiável; confirmação é inline em duas etapas.
- **Contraste:** todo texto informativo ≥ 4,5:1 nos dois temas **sobre qualquer superfície** (`--bg`, `--surface`, `--surface-2`, `--surface-inset`). `--text-disabled` é exclusivo de aba inativa / controle desabilitado / separador.
- **`--text-muted` do broadcast é `#8f8f8f`** (não `#8a8a8a`, que dava 4,44:1 sobre `--surface-2`). Ver spec §6.
- **`--ok`, `--warn` e `--danger` são cores de preenchimento e borda, nunca de texto.** Rótulo de estado usa `--text`/`--text-muted`; a cor vai no ponto/quadrado ao lado.
- **Alvo de clique:** altura mínima 26px para botões e campos.
- **Como rodar um teste:** `python -u .test_runtime\run_tests.py <nome_do_teste>` (arquivo já existe; faz `sys.path.insert(0,'tests')`). Suíte inteira: `python -u .test_runtime\run_suite.py`. **Nunca** `python -m unittest tests.test_x` — `tests/` não é pacote.
- **Nunca rodar a suíte com uma instância da ponte no ar** (3333/3334): contenção de CPU faz testes aleatórios travarem. Verificar com `Get-NetTCPConnection -LocalPort 3333,3334 -State Listen` antes.
- **Commits:** um por tarefa, no fim do ciclo verde. Não usar `git add -A` cego; adicionar os arquivos nomeados na tarefa.

## Emenda 2026-10-14 (sobrepõe o texto das Tasks 4 e 5 onde conflitar)

O spec ganhou o **Adendo 2026-10-14** (UI intuitiva + responsiva, validado no companion
visual). Onde a Task 4/5 abaixo disser "5 abas", "aba Áudio" ou "VU na barra de status
(`vuBarSegments`/`vuBarDb`)", **vale o adendo**, não este plano:

- **Zona fixa:** barra de status **+ card mixer de áudio**, sempre visíveis.
- **Barra de status SEM medidor master** (conexão · fps · tema · Parar). O master vive só
  no mixer; os IDs `vuBarSegments`/`vuBarDb` não existem.
- **4 grupos** (não 5 abas): Conexão · Palco · Câmeras · Chat & Enquete. O áudio avançado
  (limiar de fala, monitor local, links individuais) fica recolhido no card fixo de áudio.
- **Responsivo:** estreito (< ~900px) = zona fixa + barra de 4 abas (só a ativa visível);
  largo (≥ ~900px) = abas desaparecem e os 4 grupos viram colunas
  `repeat(auto-fit, minmax(260px, 1fr))`; mixer vira faixa horizontal. Troca puramente CSS,
  markup único (sem DOM duplicado).
- Teste de responsividade obrigatório: largo mostra os 4 grupos e nenhuma barra de abas;
  estreito o inverso.

---

### Task 1: Infra de assets estáticos do dock

Cria `web/dock.css` e `web/dock.js`, as rotas que os servem, e liga-os no `/control`. Nenhuma mudança visual: o CSS existente continua autoritativo.

**Files:**
- Create: `web/dock.css`
- Create: `web/dock.js`
- Modify: `media_bridge.py` (dentro de `register_media_routes`, junto às rotas `/assets/*` existentes, ~linha 53-67)
- Modify: `server.py` — `<head>` do `/control` (após `{CONTROL_PANEL_CSS}`, ~linha 3214) e antes de `</body>` do `/control`
- Test: `tests/test_media_bridge.py`

**Interfaces:**
- Consome: padrão `FileResponse(WEB_DIR / '<arquivo>', media_type=..., headers={'Cache-Control': 'no-store'})` já usado em `media_bridge.py`.
- Produz: rotas `GET /assets/dock.css` e `GET /assets/dock.js`; arquivos `web/dock.css` (com a camada de tokens completa) e `web/dock.js` (IIFE com `window.RnpDock = {}`).

- [ ] **Step 1: Escrever o teste falho**

Adicionar em `tests/test_media_bridge.py`, dentro da classe `MediaRoutesTests`:

```python
    def test_assets_do_dock_servidos_com_no_store(self):
        css = self.client.get('/assets/dock.css')
        self.assertEqual(css.status_code, 200, 'web/dock.css precisa ser servido como asset.')
        self.assertIn('text/css', css.headers['content-type'])
        self.assertEqual(css.headers['cache-control'], 'no-store',
                         'O dock é recarregado dentro do OBS; não pode cachear.')
        js = self.client.get('/assets/dock.js')
        self.assertEqual(js.status_code, 200, 'web/dock.js precisa ser servido como asset.')
        self.assertIn('javascript', js.headers['content-type'])
        self.assertEqual(js.headers['cache-control'], 'no-store')

    def test_dock_referencia_os_assets(self):
        html = self.client.get('/control').text
        self.assertIn('/assets/dock.css', html)
        self.assertIn('/assets/dock.js', html)
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `python -u .test_runtime\run_tests.py` não serve (é só para `test_media_browser`). Use:
`python -u -c "import sys; sys.path.insert(0,'tests'); import unittest; r=unittest.TextTestRunner(stream=sys.stdout,verbosity=2).run(unittest.TestLoader().discover('tests',pattern='test_media_bridge.py')); sys.exit(0 if r.wasSuccessful() else 1)"`

Expected: FAIL — `/assets/dock.css` devolve 404.

- [ ] **Step 3: Criar `web/dock.css` com a camada de tokens**

Valores copiados do spec §3.2. Este arquivo cresce nas tarefas 4; por enquanto só tokens.

```css
/* Design system do dock /control.
   Tema é APENAS override de tokens em body.theme-*; nenhum componente é
   re-estilizado por tema. Ver spec 2026-10-13-dock-control-redesign-design.md */

:root {
    --radius: 2px;
    --radius-sm: 1px;
    --space-1: 4px;
    --space-2: 6px;
    --space-3: 8px;
    --space-4: 10px;
    --space-5: 12px;
    --label-size: 8.5px;
    --label-weight: 700;
    --label-tracking: .1em;
}

body.theme-broadcast {
    --bg: #1a1a1a;
    --surface: #202020;
    --surface-2: #252525;
    --surface-inset: #141414;
    --border: #2e2e2e;
    --border-strong: #3d3d3d;
    --border-soft: #2c2c2c;
    --text: #e0e0e0;
    --text-muted: #8f8f8f;   /* #8a8a8a dava 4,44:1 sobre --surface-2; ver spec §6 */
    --text-disabled: #6b6b6b;
    --primary: #2d2d2d;
    --primary-border: #3d3d3d;
    --primary-text: #e0e0e0;
    --ok: #3d8b43;
    --warn: #b5a63c;
    --danger: #c0392b;
    --danger-surface: #8b2f2f;
    --danger-border: #a13838;
    --vu-off: #2e2e2e;
    --accent-top: transparent;
    --accent-top-width: 0px;
    --label-weight: 700;
}

body.theme-ifes {
    --bg: #eae6dc;
    --surface: #f7f4ec;
    --surface-2: #f0ece1;
    --surface-inset: #f0ece1;
    --border: #cdc7b8;
    --border-strong: #c4bca6;
    --border-soft: #ded8c9;
    --text: #211f1a;
    --text-muted: #45413a;
    --text-disabled: #75705f;
    --primary: #15803d;
    --primary-border: #166534;
    --primary-text: #ffffff;
    --ok: #15803d;
    --warn: #b5a63c;
    --danger: #dc2626;
    --danger-surface: #b91c1c;
    --danger-border: #991b1b;
    --vu-off: #ded8c9;
    --accent-top: #15803d;
    --accent-top-width: 3px;
    --label-weight: 800;
}
```

- [ ] **Step 4: Criar `web/dock.js` mínimo**

```javascript
/* Lógica do dock /control. Cresce nas tarefas 4 e 6. */
(function () {
    'use strict';
    window.RnpDock = {};
})();
```

- [ ] **Step 5: Adicionar as rotas em `media_bridge.py`**

Inserir dentro de `register_media_routes(app, get_state, get_frame)`, imediatamente após a rota `/assets/stage-youtube.js`:

```python
    @app.get('/assets/dock.css')
    async def dock_style():
        return FileResponse(WEB_DIR / 'dock.css', media_type='text/css', headers={'Cache-Control': 'no-store'})

    @app.get('/assets/dock.js')
    async def dock_script():
        return FileResponse(WEB_DIR / 'dock.js', media_type='text/javascript', headers={'Cache-Control': 'no-store'})
```

- [ ] **Step 6: Ligar os assets no `/control`**

No `server.py`, no `<head>` do `/control`, imediatamente **após** a linha `{CONTROL_PANEL_CSS}` (~3214):

```html
    <link rel="stylesheet" href="/assets/dock.css">
```

E imediatamente antes do `</body>` do `/control`:

```html
    <script src="/assets/dock.js"></script>
```

- [ ] **Step 7: Rodar e confirmar que passa**

Run: mesmo comando do Step 2.
Expected: PASS — `Ran 12 tests ... OK` (10 anteriores + 2 novos).

- [ ] **Step 8: Commit**

```bash
git add web/dock.css web/dock.js media_bridge.py server.py tests/test_media_bridge.py
git commit -m "feat(dock): servir web/dock.css e web/dock.js como assets com no-store"
```

---

### Task 2: Tema `broadcast` como padrão + migração dos temas aposentados

Backend puro. `hardware`, `nordic`, `glass`, `obsidian` deixam de ser valores válidos e migram para `broadcast` na leitura — nenhum `config.json` existente quebra.

**Files:**
- Modify: `server.py` — `DEFAULT_CONFIG` (~linha 100), nova função `normalize_theme`, `BridgeState.__init__` (~linha 162), `export_config` (~linha 302), e os pontos que atribuem `state.control_theme` a partir de payload (`/api/grid/config` ~1720 e `/api/config` ~1791)
- Test: `tests/test_media_bridge.py`

**Interfaces:**
- Produz: `server.normalize_theme(value: Any) -> str` — devolve `'ifes'` para `'ifes'`, `'broadcast'` para `'broadcast'`, e `'broadcast'` para qualquer outro valor (incluindo `None`, `''` e nomes aposentados). `server.DEFAULT_CONFIG['control_theme'] == 'broadcast'`.
- Consome: nada de outras tarefas.

- [ ] **Step 1: Escrever o teste falho**

Em `tests/test_media_bridge.py`, dentro de `MediaRoutesTests`:

```python
    def test_tema_padrao_e_broadcast(self):
        self.assertEqual(server.DEFAULT_CONFIG['control_theme'], 'broadcast')

    def test_temas_aposentados_migram_para_broadcast(self):
        for antigo in ('hardware', 'nordic', 'glass', 'obsidian', '', None, 'inexistente', 123):
            self.assertEqual(server.normalize_theme(antigo), 'broadcast',
                             f'{antigo!r} deveria migrar para broadcast')

    def test_temas_validos_permanecem(self):
        self.assertEqual(server.normalize_theme('ifes'), 'ifes')
        self.assertEqual(server.normalize_theme('broadcast'), 'broadcast')

    def test_estado_normaliza_tema_vindo_do_disco(self):
        with patch.object(server, 'load_disk_config', lambda: {**server.DEFAULT_CONFIG, 'control_theme': 'glass'}):
            st = server.BridgeState()
        self.assertEqual(st.control_theme, 'broadcast',
                         'Config com tema aposentado não pode subir com valor inválido.')
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: comando do Step 2 da Task 1.
Expected: FAIL/ERROR — `AttributeError: module 'server' has no attribute 'normalize_theme'`.

- [ ] **Step 3: Implementar**

Em `server.py`, logo após a definição de `CAMERA_FORMATS` / `SPEAK_PEAK_THRESHOLD` (~linha 89-92):

```python
# Temas do dock. `hardware`, `nordic`, `glass` e `obsidian` foram aposentados no
# redesign de 2026-10; configs antigos migram para o padrão em vez de quebrar.
CONTROL_THEMES = ('broadcast', 'ifes')
DEFAULT_CONTROL_THEME = 'broadcast'


def normalize_theme(value) -> str:
    return value if value in CONTROL_THEMES else DEFAULT_CONTROL_THEME
```

Em `DEFAULT_CONFIG`, trocar:

```python
    "control_theme": "broadcast",
```

Em `BridgeState.__init__`, onde hoje está `self.control_theme = cfg.get(...)` (localizar por `control_theme`), garantir:

```python
        self.control_theme = normalize_theme(cfg.get("control_theme"))
```

Em `load_disk_config`, após `cfg.update(data)`:

```python
        cfg["control_theme"] = normalize_theme(cfg.get("control_theme"))
```

Nos dois pontos que atribuem a partir de payload (`/api/grid/config` e a rota de config), envolver com `normalize_theme(...)`:

```python
    if "control_theme" in data:
        state.control_theme = normalize_theme(data["control_theme"])
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: comando do Step 2 da Task 1.
Expected: PASS — `Ran 16 tests ... OK`.

- [ ] **Step 5: Rodar a suíte completa**

Antes: confirmar que não há ponte no ar.

```powershell
Get-NetTCPConnection -LocalPort 3333,3334 -State Listen -ErrorAction SilentlyContinue
python -u .test_runtime\run_suite.py
```

Expected: `Ran 85 tests ... OK` (16 em `test_media_bridge` + 54 em `test_media_browser` + 15 nos demais). Se algum teste de dock falhar por esperar o tema antigo, **não** mudar o teste: investigar o que ainda depende de `hardware`.

- [ ] **Step 6: Commit**

```bash
git add server.py tests/test_media_bridge.py
git commit -m "feat(dock): tema broadcast como padrao, com migracao dos temas aposentados"
```

---

### Task 3: Remover a vitrine de temas (`/catalog` e `/themes`)

As duas rotas compartilham o handler `get_themes_catalog`. O seletor compacto na barra de status (Task 4) as substitui.

**Files:**
- Modify: `server.py` — remover os dois decorators e o handler `get_themes_catalog` (~linhas 6921 em diante, localizar por `@app.get("/catalog"`)
- Test: `tests/test_media_bridge.py`

**Interfaces:**
- Consome: nada.
- Produz: `/catalog` e `/themes` devolvem 404.

- [ ] **Step 1: Escrever o teste falho**

```python
    def test_vitrine_de_temas_removida(self):
        for rota in ('/catalog', '/themes'):
            self.assertEqual(self.client.get(rota).status_code, 404,
                             f'{rota} deveria ter sido removida; o seletor de tema vive na barra de status.')
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: comando do Step 2 da Task 1.
Expected: FAIL — devolve 200.

- [ ] **Step 3: Remover as rotas**

Apagar o bloco completo: os dois `@app.get(...)` empilhados, a assinatura `async def get_themes_catalog():` e todo o `return HTMLResponse(f"""...""")` até o fechamento. Antes de apagar, confirmar com busca que nenhum outro ponto referencia `get_themes_catalog`, `/catalog` ou `/themes` (exceto docs, que são atualizadas na Task 7):

```powershell
Select-String -Path server.py -Pattern 'get_themes_catalog|/catalog|/themes'
```

- [ ] **Step 4: Validar sintaxe e rodar**

```powershell
python -m py_compile server.py
```

Run: comando do Step 2 da Task 1.
Expected: PASS — `Ran 17 tests ... OK`.

- [ ] **Step 5: Commit**

```bash
git add server.py tests/test_media_bridge.py
git commit -m "feat(dock): remover vitrine de temas /catalog e /themes"
```

---

### Task 4: Shell do dock — barra de status + 5 abas + componentes em tokens

Tarefa central. Reescreve o HTML do `/control` como shell, escreve os componentes em tokens no `dock.css`, cria a lógica de abas/status no `dock.js` e **apaga `CONTROL_PANEL_CSS`**. As seções existentes são movidas para dentro dos painéis com o markup interno preservado (IDs de contrato intactos); a limpeza de conteúdo duplicado fica para a Task 5.

**Files:**
- Modify: `server.py` — rota `/control` (~3135-4978): substituir o corpo HTML; remover a constante `CONTROL_PANEL_CSS` (~2344-3130)
- Modify: `web/dock.css` — acrescentar componentes
- Modify: `web/dock.js` — acrescentar abas, barra de status e confirmação em duas etapas
- Test: `tests/test_media_browser.py`

**Interfaces:**
- Consome: `web/dock.css` com tokens (Task 1); `normalize_theme` e `DEFAULT_CONTROL_THEME` (Task 2); `/api/status` com `connected`, `connecting`, `cameras_count`, `cameras[].fps`.
- Produz:
  - `[data-tab]` (5 botões, valores `conexao|palco|cameras|audio|chat`) e `[data-tab-panel]` (5 painéis, mesmos valores).
  - IDs novos: `connState`, `connDot`, `srcSummary`, `themeSelect`, `vuBarSegments`, `vuBarDb`, `btnStop`.
  - `window.RnpDock.selectTab(name: string): void` e `window.RnpDock.activeTab(): string`.
  - `localStorage['rnp.dock.tab']` guarda a aba ativa.
  - `data-copy-path` nos botões de copiar link (usado pela Task 5).

- [ ] **Step 1: Escrever os testes falhos**

Em `tests/test_media_browser.py`, dentro de `BrowserMediaTests`. Todos abrem o dock com `self.receiver.goto(self.url + '/control')`.

```python
    DOCK_IDS = ['roomUrl', 'userName', 'userEmail', 'ytUrl', 'chkYtAutoConnect',
                'selLibrasSize', 'selPollDisplayMode', 'selPollDuration', 'selPollStyle',
                'selNameStyle', 'selGridLayout', 'selCamFormat', 'selChatTheme',
                'selChatSize', 'selChatMax', 'chkShowNames', 'chkShowAvatars',
                'rngMasterVol', 'rngMonitorVol', 'saveToast', 'logBox', 'audioDockCard',
                'audioMixerRack', 'vuMasterBar', 'vuMasterPeak', 'vuMasterDbText',
                'lblMasterVol']

    async def open_dock(self):
        await self.receiver.goto(self.url + '/control')
        await self.receiver.wait_for_selector('[data-tab]', timeout=15000)

    async def test_dock_tem_cinco_abas_e_uma_visivel(self):
        await self.open_dock()
        self.assertEqual(await self.receiver.locator('[data-tab]').count(), 5)
        visiveis = await self.receiver.evaluate(
            "() => [...document.querySelectorAll('[data-tab-panel]')]"
            ".filter(p => p.offsetParent !== null).map(p => p.dataset.tabPanel)")
        self.assertEqual(len(visiveis), 1, 'Exatamente um painel de aba pode ficar visível.')

    async def test_dock_troca_de_aba(self):
        await self.open_dock()
        await self.receiver.locator('[data-tab="audio"]').click()
        visiveis = await self.receiver.evaluate(
            "() => [...document.querySelectorAll('[data-tab-panel]')]"
            ".filter(p => p.offsetParent !== null).map(p => p.dataset.tabPanel)")
        self.assertEqual(visiveis, ['audio'])
        self.assertEqual(await self.receiver.evaluate("() => RnpDock.activeTab()"), 'audio')

    async def test_dock_persiste_aba_ativa(self):
        await self.open_dock()
        await self.receiver.locator('[data-tab="cameras"]').click()
        self.assertEqual(await self.receiver.evaluate("() => localStorage.getItem('rnp.dock.tab')"), 'cameras')
        await self.receiver.reload()
        await self.receiver.wait_for_function(
            "() => document.querySelector('[data-tab-panel=\"cameras\"]').offsetParent !== null",
            timeout=15000)

    async def test_dock_forca_aba_conexao_sem_sala(self):
        self.state.connected = False
        await self.open_dock()
        await self.receiver.evaluate("() => localStorage.setItem('rnp.dock.tab', 'audio')")
        await self.receiver.reload()
        await self.receiver.wait_for_function(
            "() => document.querySelector('[data-tab-panel=\"conexao\"]').offsetParent !== null",
            timeout=15000,
        )

    async def test_barra_de_status_reflete_conexao(self):
        self.state.connected = False
        self.state.connecting = False
        await self.open_dock()
        await self.receiver.wait_for_function(
            "() => document.getElementById('connState').textContent.trim() === 'Desconectado'",
            timeout=15000)
        self.state.connected = True
        await self.receiver.wait_for_function(
            "() => document.getElementById('connState').textContent.trim() === 'Conectado'",
            timeout=15000)
        self.state.connected = False
        self.state.connecting = True
        await self.receiver.wait_for_function(
            "() => document.getElementById('connState').textContent.trim() === 'Conectando'",
            timeout=15000)

    async def test_parar_exige_confirmacao_em_duas_etapas(self):
        self.state.connected = True
        chamadas = []
        await self.receiver.context.route('**/api/disconnect',
            lambda route: (chamadas.append(route.request.url), route.fulfill(status=200, body='{}')))
        await self.open_dock()
        await self.receiver.locator('#btnStop').click()
        await self.receiver.wait_for_function(
            "() => document.getElementById('btnStop').dataset.armed === '1'", timeout=5000)
        self.assertEqual(chamadas, [], 'O primeiro clique não pode desconectar: precisa de confirmação.')
        await self.receiver.locator('#btnStop').click()
        await self.receiver.wait_for_function("() => (window.__armedCalls || 0) === 1", timeout=5000)
        self.assertEqual(len(chamadas), 1, 'O segundo clique deve chamar /api/disconnect.')

    async def test_dock_preserva_todos_os_ids_de_contrato(self):
        await self.open_dock()
        faltando = await self.receiver.evaluate(
            "(ids) => ids.filter(id => !document.getElementById(id))", self.DOCK_IDS)
        self.assertEqual(faltando, [], f'IDs de contrato ausentes do dock: {faltando}')

    async def test_dock_nao_tem_mais_css_inline_de_painel(self):
        await self.open_dock()
        tem_antigo = await self.receiver.evaluate(
            "() => [...document.querySelectorAll('style')]"
            ".some(s => s.textContent.includes('body.theme-hardware'))")
        self.assertFalse(tem_antigo, 'CONTROL_PANEL_CSS precisa ter sido removido do /control.')

    async def test_dock_nao_tem_emoji_decorativo_em_titulos(self):
        # Spec §3.3/§4.3: emoji em título de seção é o que mais envelhece a aparência.
        # No lugar entram micro-rótulos uppercase com letter-spacing.
        await self.open_dock()
        com_emoji = await self.receiver.evaluate(r"""() => {
            const re = /[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}\u{2B00}-\u{2BFF}\u{FE0F}]/u;
            const alvos = [...document.querySelectorAll('.section-title, .subsection-title, [data-tab]')];
            return alvos.filter(el => re.test(el.textContent)).map(el => el.textContent.trim().slice(0, 40));
        }""")
        self.assertEqual(com_emoji, [], f'Títulos ainda com emoji decorativo: {com_emoji}')

    async def test_seletor_de_tema_usa_api_theme_set_e_persiste(self):
        # Não pode postar em /api/grid/config: aquela rota ignora control_theme.
        capturas = []
        await self.receiver.context.route('**/api/theme/set', lambda route: (
            capturas.append((route.request.url, route.request.post_data)),
            route.fulfill(status=200, body='{"status":"ok","control_theme":"ifes"}')))
        await self.open_dock()
        await self.receiver.select_option('#themeSelect', 'ifes')
        await self.receiver.wait_for_function(
            "() => document.body.classList.contains('theme-ifes')", timeout=15000)
        self.assertEqual(len(capturas), 1, 'A troca de tema deve chamar /api/theme/set uma vez.')
        self.assertIn('"theme": "ifes"', capturas[0][1].replace('"theme":"ifes"', '"theme": "ifes"'))

    async def test_falha_ao_trocar_tema_nao_finge_sucesso(self):
        # Spec §6: falha de gravação não pode fingir sucesso. O seletor legado
        # aplicava a classe e mostrava "Salvo!" mesmo com o backend devolvendo 400.
        await self.receiver.context.route('**/api/theme/set',
            lambda route: route.fulfill(status=400, body='{"status":"error"}'))
        await self.open_dock()
        antes = await self.receiver.evaluate("() => document.body.className")
        await self.receiver.select_option('#themeSelect', 'ifes')
        await self.receiver.wait_for_timeout(600)
        depois = await self.receiver.evaluate("() => document.body.className")
        self.assertEqual(depois, antes,
                         'Com o backend rejeitando, o tema aplicado no body não pode mudar.')
        self.assertEqual(await self.receiver.evaluate("() => document.getElementById('themeSelect').value"),
                         antes.replace('theme-', ''), 'O seletor deve reverter para o tema vigente.')

    async def test_seletor_de_tema_legado_foi_removido(self):
        await self.open_dock()
        self.assertEqual(await self.receiver.locator('#selControlTheme').count(), 0,
                         'Dois seletores de tema não podem coexistir; o legado listava temas aposentados.')
        html = await self.receiver.content()
        self.assertNotIn('changeControlTheme', html)
        self.assertNotIn('theme-hardware', html)
```

- [ ] **Step 2: Rodar e confirmar que falham**

Run: `python -u .test_runtime\run_tests.py test_dock_tem_cinco_abas_e_uma_visivel test_dock_preserva_todos_os_ids_de_contrato`
Expected: FAIL — `[data-tab]` não existe (timeout no `wait_for_selector`). O teste de IDs deve **passar** já nesta etapa se o markup antigo for preservado; se falhar, o markup atual já perdeu algum ID e isso precisa ser corrigido junto.

- [ ] **Step 3: Escrever o CSS de componentes em `web/dock.css`**

Acrescentar após os blocos de tokens. Todos os componentes consomem tokens; **nenhum** seletor `body.theme-X .componente`.

```css
/* ---------- base ---------- */
body {
    background: var(--bg);
    color: var(--text);
    padding: var(--space-3);
    font-family: 'Segoe UI', system-ui, sans-serif;
    font-size: 12px;
}

.card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-top: var(--accent-top-width) solid var(--accent-top);
    border-radius: var(--radius);
    padding: var(--space-4);
    margin-bottom: var(--space-4);
}
/* O filete institucional do Ifes vem inteiramente dos tokens --accent-top-width /
   --accent-top. NÃO escrever `body.theme-X .card`: o teste da Task 7 proíbe qualquer
   seletor de componente por tema. */

.section-title {
    color: var(--text);
    font-size: 10px;
    font-weight: var(--label-weight);
    letter-spacing: var(--label-tracking);
    text-transform: uppercase;
    padding-bottom: var(--space-2);
    margin-bottom: var(--space-3);
    border-bottom: 1px solid var(--border);
}

.field-label {
    display: block;
    color: var(--text-muted);
    font-size: var(--label-size);
    font-weight: var(--label-weight);
    letter-spacing: var(--label-tracking);
    text-transform: uppercase;
    margin-bottom: var(--space-1);
}

input[type="text"], input[type="email"], select {
    width: 100%;
    min-height: 26px;
    background: var(--surface-inset);
    border: 1px solid var(--border-strong);
    border-radius: var(--radius);
    color: var(--text);
    padding: var(--space-1) var(--space-2);
    font-size: 11px;
}

.btn-ctrl, .btn-copy {
    min-height: 26px;
    background: var(--primary);
    border: 1px solid var(--primary-border);
    border-radius: var(--radius);
    color: var(--primary-text);
    font-size: 9px;
    font-weight: var(--label-weight);
    letter-spacing: .06em;
    text-transform: uppercase;
    padding: var(--space-2) var(--space-3);
    cursor: pointer;
}
.btn-ctrl:hover, .btn-copy:hover { border-color: var(--text-disabled); }

.btn-primary {
    background: var(--primary);
    border: 1px solid var(--primary-border);
    color: var(--primary-text);
}
.btn-danger {
    background: var(--danger-surface);
    border: 1px solid var(--danger-border);
    color: #f0dede;
}

/* ---------- barra de status ---------- */
.status-bar {
    position: sticky;
    top: 0;
    z-index: 20;
    background: var(--surface);
    border: 1px solid var(--border-soft);
    border-top: 2px solid var(--danger);
    border-radius: var(--radius);
    padding: var(--space-3);
    margin-bottom: var(--space-3);
}
.status-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-2);
}
.conn-state {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    font-size: 9px;
    font-weight: 800;
    letter-spacing: var(--label-tracking);
    text-transform: uppercase;
    color: var(--text-muted);
}
.conn-dot { width: 7px; height: 7px; border-radius: var(--radius-sm); background: var(--text-disabled); }
.conn-dot.is-on { background: var(--ok); }
.conn-dot.is-busy { background: var(--warn); }
.src-summary { color: var(--text-muted); font-size: 8px; letter-spacing: .06em; text-transform: uppercase; }

.vu-segments { display: flex; gap: 1px; height: 8px; }
.vu-segments i { flex: 1; background: var(--vu-off); }
.vu-segments i.on-1 { background: var(--ok); }
.vu-segments i.on-2 { background: var(--warn); }
.vu-segments i.on-3 { background: var(--danger); }
.vu-db { color: var(--text-muted); font-size: 8px; font-family: Consolas, monospace; }

#btnStop { white-space: nowrap; }
#btnStop[data-armed="1"] { background: var(--danger); border-color: var(--danger-border); color: #fff; }

/* ---------- abas ---------- */
.tab-bar { display: flex; gap: 1px; margin-bottom: var(--space-3); }
.tab-bar [data-tab] {
    flex: 1;
    min-height: 26px;
    background: var(--surface);
    border: 1px solid var(--border-soft);
    border-radius: var(--radius) var(--radius) 0 0;
    color: var(--text-disabled);
    font-size: 8px;
    font-weight: var(--label-weight);
    letter-spacing: .05em;
    text-transform: uppercase;
    padding: var(--space-2) 2px;
    cursor: pointer;
}
.tab-bar [data-tab][aria-selected="true"] {
    background: var(--surface-2);
    border-color: var(--border-strong);
    border-bottom-color: transparent;
    color: var(--text);
}
[data-tab-panel] { display: none; }
[data-tab-panel].is-active { display: block; }
.subsection-title {
    color: var(--text-muted);
    font-size: 9px;
    font-weight: var(--label-weight);
    letter-spacing: var(--label-tracking);
    text-transform: uppercase;
    margin: var(--space-4) 0 var(--space-2);
}
```

- [ ] **Step 4: Escrever `web/dock.js` (abas + status + confirmação)**

```javascript
/* Lógica do dock /control: abas, barra de status e confirmação em duas etapas. */
(function () {
    'use strict';

    const TABS = ['conexao', 'palco', 'cameras', 'audio', 'chat'];
    const TAB_KEY = 'rnp.dock.tab';
    const ARM_TIMEOUT_MS = 4000;

    function readTab() {
        try { return localStorage.getItem(TAB_KEY); } catch (e) { return null; }
    }
    function writeTab(name) {
        try { localStorage.setItem(TAB_KEY, name); } catch (e) { /* CEF pode bloquear */ }
    }

    function selectTab(name) {
        if (!TABS.includes(name)) name = TABS[0];
        document.querySelectorAll('[data-tab]').forEach(b =>
            b.setAttribute('aria-selected', String(b.dataset.tab === name)));
        document.querySelectorAll('[data-tab-panel]').forEach(p =>
            p.classList.toggle('is-active', p.dataset.tabPanel === name));
        writeTab(name);
    }

    function activeTab() {
        const sel = document.querySelector('[data-tab][aria-selected="true"]');
        return sel ? sel.dataset.tab : TABS[0];
    }

    function applyTabForState(connected) {
        // Sem sala conectada o operador sempre cai em Conexão, mesmo com aba persistida.
        selectTab(connected ? (readTab() || TABS[0]) : 'conexao');
    }

    function renderStatus(data) {
        const dot = document.getElementById('connDot');
        const label = document.getElementById('connState');
        const summary = document.getElementById('srcSummary');
        if (!dot || !label) return;
        dot.classList.remove('is-on', 'is-busy');
        if (data.connected) { label.textContent = 'Conectado'; dot.classList.add('is-on'); }
        else if (data.connecting) { label.textContent = 'Conectando'; dot.classList.add('is-busy'); }
        else { label.textContent = 'Desconectado'; }

        const cams = data.cameras || [];
        const fps = cams.map(c => Number(c.fps) || 0);
        const minFps = fps.length ? Math.min(...fps) : 0;
        if (summary) {
            summary.textContent = cams.length
                ? `${cams.length} cam · ${Math.round(minFps)} fps`
                : 'sem câmera';
        }
        // NÃO chamar applyTabForState aqui: renderStatus roda a cada 250 ms e isso
        // reconduziria o operador à aba Conexão 4x por segundo enquanto a sala estiver
        // desconectada, impedindo o uso de qualquer outra aba. A escolha forçada é só
        // no init() (spec §4.2: "força Conexão ao carregar").
    }

    function renderVu(peak) {
        const box = document.getElementById('vuBarSegments');
        const db = document.getElementById('vuBarDb');
        if (!box) return;
        const segs = box.children.length;
        const lit = Math.max(0, Math.min(segs, Math.round((peak || 0) * segs)));
        for (let i = 0; i < segs; i++) {
            const el = box.children[i];
            el.className = i < lit ? (i >= segs - 1 ? 'on-3' : (i >= segs - 4 ? 'on-2' : 'on-1')) : '';
        }
        if (db) {
            const v = peak > 0 ? 20 * Math.log10(peak) : -Infinity;
            db.textContent = Number.isFinite(v) ? `${v.toFixed(1)} dB` : '−∞ dB';
        }
    }

    function armStop(btn) {
        let timer = null;
        btn.addEventListener('click', async () => {
            if (btn.dataset.armed !== '1') {
                btn.dataset.armed = '1';
                btn.dataset.labelOriginal = btn.textContent;
                btn.textContent = 'Confirmar?';
                window.__armedCalls = 0;
                timer = setTimeout(() => {
                    btn.dataset.armed = '0';
                    btn.textContent = btn.dataset.labelOriginal || 'Parar';
                }, ARM_TIMEOUT_MS);
                return;
            }
            clearTimeout(timer);
            btn.dataset.armed = '0';
            btn.textContent = btn.dataset.labelOriginal || 'Parar';
            window.__armedCalls = (window.__armedCalls || 0) + 1;
            try {
                await fetch('/api/disconnect', { method: 'POST' });
            } catch (e) { /* estado chega pelo polling */ }
        });
    }

    function init() {
        document.querySelectorAll('[data-tab]').forEach(b =>
            b.addEventListener('click', () => selectTab(b.dataset.tab)));
        const stop = document.getElementById('btnStop');
        if (stop) armStop(stop);
        applyTabForState(document.body.dataset.connected === '1');
    }

    window.RnpDock = { selectTab, activeTab, renderStatus, renderVu, applyTabForState };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
```

- [ ] **Step 5: Reescrever o HTML do `/control` como shell**

Estrutura alvo. O conteúdo interno de cada seção existente é **movido intacto** para o painel correspondente (preservando IDs e `onclick`); nada é reescrito nesta tarefa além do invólucro.

```html
<body class="theme-{theme}" data-connected="{1 if state.connected else 0}">
    <link rel="stylesheet" href="/assets/dock.css">

    <div class="status-bar">
        <div class="status-row">
            <span class="conn-state">
                <span class="conn-dot" id="connDot"></span>
                <span id="connState">Desconectado</span>
            </span>
            <span class="status-row">
                <span class="src-summary" id="srcSummary">sem câmera</span>
                <select id="themeSelect" aria-label="Tema do painel" style="width:auto">
                    <option value="broadcast" {sel_broadcast}>Broadcast</option>
                    <option value="ifes" {sel_ifes}>Ifes</option>
                </select>
            </span>
        </div>
        <div class="status-row" style="margin-top:6px">
            <span style="flex:1">
                <span class="status-row">
                    <span class="field-label" style="margin:0">Master</span>
                    <span class="vu-db" id="vuBarDb">−∞ dB</span>
                </span>
                <span class="vu-segments" id="vuBarSegments">
                    <i></i><i></i><i></i><i></i><i></i><i></i>
                    <i></i><i></i><i></i><i></i><i></i><i></i>
                </span>
            </span>
            <button class="btn-danger" id="btnStop" data-armed="0">Parar</button>
        </div>
    </div>

    <div class="tab-bar" role="tablist">
        <button data-tab="conexao"  role="tab" aria-selected="true">Conexão</button>
        <button data-tab="palco"    role="tab" aria-selected="false">Palco</button>
        <button data-tab="cameras"  role="tab" aria-selected="false">Câmeras</button>
        <button data-tab="audio"    role="tab" aria-selected="false">Áudio</button>
        <button data-tab="chat"     role="tab" aria-selected="false">Chat</button>
    </div>

    <div data-tab-panel="conexao"> …Sala RNP, saúde, salvar/carregar, 7 links… </div>
    <div data-tab-panel="palco">   …presets (movidos na Task 5), Libras, slides… </div>
    <div data-tab-panel="cameras"> …grid ao vivo, foco/pin/nomes, formato…      </div>
    <div data-tab-panel="audio">   …#audioDockCard, #audioMixerRack, monitor…   </div>
    <div data-tab-panel="chat">    …4 sub-blocos: Chat, Destaque, Enquete, YouTube… </div>

    <script src="/assets/dock.js"></script>
</body>
```

No Python, substituir as 5 variáveis `sel_th_*` por apenas:

```python
    sel_broadcast = "selected" if state.control_theme == "broadcast" else ""
    sel_ifes = "selected" if state.control_theme == "ifes" else ""
    theme = normalize_theme(state.control_theme)
```

O `#themeSelect` grava via **`/api/theme/set`** — que é a rota que trata `control_theme`
e valida contra `CONTROL_THEMES`. **Não** usar `/api/grid/config`: aquela rota não trata
`control_theme` e ignoraria o campo silenciosamente. E é obrigatório checar `response.ok`
antes de sinalizar sucesso (spec §6: falha de gravação não pode fingir sucesso):

```javascript
document.getElementById('themeSelect').addEventListener('change', async (e) => {
    const escolhido = e.target.value;
    try {
        const res = await fetch('/api/theme/set', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({theme: escolhido})
        });
        if (!res.ok) throw new Error('HTTP ' + res.status);
        location.reload();          // aplica o tema já persistido
    } catch (err) {
        if (typeof showSaveNotice === 'function') showSaveNotice('Falha ao aplicar tema', true);
        // Reverte a seleção para o tema que continua valendo no servidor.
        e.target.value = document.body.className.replace('theme-', '') || 'broadcast';
    }
});
```

O `#selControlTheme` antigo (que lista os 5 temas e chama `changeControlTheme`) é **removido**
junto com a função `changeControlTheme`. Sem essa remoção, ficam dois seletores de tema e o
antigo ainda ofereceria `hardware`/`glass`, que o backend agora rejeita com 400 — e o JS
legado mostrava "Salvo!" sem checar `response.ok`.

O `<link rel="stylesheet" href="/assets/dock.css">` fica no **`<head>`** (já adicionado na
Task 1); o sketch acima o mostra junto ao `<body>` apenas para indicar ordem de cascata —
não duplicar a tag.

**Origem exata do conteúdo de cada painel** (mover o markup existente intacto, preservando
IDs e `onclick`; localizar pelos comentários HTML em caixa alta que já existem no arquivo):

| Painel | Seções de origem no `/control` atual |
| :-- | :-- |
| `conexao` | `SALA RNP` (URL/nome/e-mail + Conectar/Desconectar), `MONITOR ATIVO DE FONTES & CONEXÃO`, `SALVAR / CARREGAR CONFIGURAÇÕES`, e os 7 botões `copyUrl(...)` grandes |
| `palco` | `CONFIGURAÇÃO DE TAMANHO DA JANELA DE LIBRAS & AUTO-RECONHECIMENTO`, `SEÇÃO APRESENTAÇÃO` (parte de slides), e os 6 `btnPreset_*` — que nesta tarefa ainda estão em `SALA RNP` e só são movidos de fato na Task 5 |
| `cameras` | `CONFIGURAÇÕES DE GRID, FOCO E ESTILO DE NOMES` e o grid dinâmico de câmeras |
| `audio` | `SEÇÃO DEDICADA DE ÁUDIO DA SALA & TRANSMISSÃO OBS` (inclui `#audioDockCard`, `#audioMixerRack`, canal master e monitor local) |
| `chat` | `SEÇÃO EXCLUSIVA DE TEMAS, DESTAQUES E GERENCIAMENTO DO CHAT`, `BANNER DE MENSAGEM EM DESTAQUE ATUAL`, `LISTA INTERATIVA DE MENSAGENS PARA DESTACAR`, `SEÇÃO DE ENQUETE / POLL` e `INTEGRAÇÃO COM YOUTUBE LIVE CHAT` — cada um vira um `<div class="subsection-title">` |

**Remover emoji decorativo de todos os títulos.** Levantar a lista antes de editar, criando
`.test_runtime\find_emoji.py`:

```python
import io
import re
import sys

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
EMOJI = re.compile('[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U00002B00-\U00002BFF]')
with io.open('server.py', encoding='utf-8') as f:
    for n, line in enumerate(f, start=1):
        if 3135 <= n <= 4978 and EMOJI.search(line):
            print(n, line.strip()[:110])
```

Rodar com `python .test_runtime\find_emoji.py` e percorrer a saída linha a linha.

Cada título vira micro-rótulo uppercase via `.section-title` / `.subsection-title` (o CSS já
aplica `text-transform: uppercase` e `letter-spacing`). Botões que usam emoji como ícone
(ex.: `⏹ Desconectar`, `▶ Integrar`) perdem o emoji e ficam só com o texto. **Exceção:** o
rack de áudio e o grid de câmeras podem manter glifo se ele carregar informação de estado —
nesse caso substituir por texto (`Mudo`, `Ativo`, `Focado`), nunca por emoji decorativo.

- [ ] **Step 6: Apagar `CONTROL_PANEL_CSS`**

Remover a constante inteira de `server.py` (~linhas 2344-3130) e a referência `{CONTROL_PANEL_CSS}` no `<head>` do `/control`. Antes, garantir que nada mais a referencia:

```powershell
Select-String -Path server.py -Pattern 'CONTROL_PANEL_CSS'
```

Esperado após a remoção: nenhuma ocorrência. Validar `python -m py_compile server.py`.

- [ ] **Step 7: Ligar o polling existente à barra de status**

O `fetchStatus` inline (que já roda em `setInterval(fetchStatus, 250)`) recebe o JSON de `/api/status`. Acrescentar ao final dele, sem duplicar a requisição:

```javascript
        if (window.RnpDock) window.RnpDock.renderStatus(data);
```

E alimentar o VU da barra com o mesmo pico que já alimenta `vuMasterBar` (localizar onde `vuMasterDbText` é atualizado e chamar, no mesmo lugar):

```javascript
        if (window.RnpDock) window.RnpDock.renderVu(peak);
```

- [ ] **Step 8: Rodar os testes novos**

Run: `python -u .test_runtime\run_tests.py test_dock_tem_cinco_abas_e_uma_visivel test_dock_troca_de_aba test_dock_persiste_aba_ativa test_dock_forca_aba_conexao_sem_sala test_barra_de_status_reflete_conexao test_parar_exige_confirmacao_em_duas_etapas test_dock_preserva_todos_os_ids_de_contrato test_dock_nao_tem_mais_css_inline_de_painel test_dock_nao_tem_emoji_decorativo_em_titulos`
Expected: PASS nos 12.

- [ ] **Step 9: Rodar os testes de dock pré-existentes (regressão)**

Run: `python -u .test_runtime\run_tests.py test_dock_copia_links_da_mesma_porta test_dock_lista_audio_sem_camera_e_aplica_mute test_dock_mostra_medidor_de_fps test_dock_nao_confirma_libras_quando_gravacao_falha test_camera_format_persiste_e_rejeita_invalido`
Expected: PASS nos 5. **Se algum falhar, o contrato §7 foi violado** — restaurar o ID/seletor, não adaptar o teste.

- [ ] **Step 10: Commit**

```bash
git add server.py web/dock.css web/dock.js tests/test_media_browser.py
git commit -m "feat(dock): shell com barra de status e 5 abas sobre camada de tokens"
```

---

### Task 5: Deduplicar links de fonte e mover presets para a aba Palco

**Files:**
- Modify: `server.py` — painel `conexao` (7 links com `data-copy-path`) e remoção dos botões soltos; painel `palco` (recebe os 6 `btnPreset_*`)
- Test: `tests/test_media_browser.py`

**Interfaces:**
- Consome: shell da Task 4 (`[data-tab-panel]`, `data-copy-path`).
- Produz: exatamente um `[data-copy-path="<rota>"]` por rota; os 6 `[data-preset]` dentro do painel `palco`.

- [ ] **Step 1: Escrever os testes falhos**

```python
    SOURCE_PATHS = ['/stage', '/cameras/grid', '/libras', '/chat',
                    '/chat/featured', '/presentation', '/poll']

    async def test_cada_fonte_tem_um_unico_botao_de_copiar(self):
        await self.open_dock()
        contagem = await self.receiver.evaluate("""(paths) => {
            const btns = [...document.querySelectorAll('[data-copy-path]')];
            return paths.map(p => [p, btns.filter(b => b.dataset.copyPath === p).length]);
        }""", self.SOURCE_PATHS)
        for rota, n in contagem:
            self.assertEqual(n, 1, f'{rota} deve ter exatamente 1 botão de copiar; tem {n}.')

    async def test_nao_sobram_links_de_fonte_duplicados(self):
        await self.open_dock()
        texto = await self.receiver.content()
        for rotulo in ('Link Palco OBS', 'Link Janela Libras', 'Link Grid',
                       'Link Feed', 'Link Destaque', 'Link Slides', 'Link OBS Enquete'):
            self.assertNotIn(rotulo, texto, f'Botão duplicado "{rotulo}" deveria ter sido removido.')

    async def test_presets_de_palco_ficam_na_aba_palco(self):
        await self.open_dock()
        presets = await self.receiver.evaluate("""() => {
            const p = document.querySelector('[data-tab-panel="palco"]');
            return [...p.querySelectorAll('[data-preset]')].map(b => b.dataset.preset);
        }""")
        self.assertEqual(sorted(presets), sorted(
            ['side-by-side', 'bottom-bar', 'pip-bottom-right',
             'pip-top-right', 'split-50-50', 'slide-only']))

    async def test_presets_nao_ficam_mais_na_secao_de_sala(self):
        await self.open_dock()
        na_conexao = await self.receiver.evaluate("""() =>
            document.querySelectorAll('[data-tab-panel="conexao"] [data-preset]').length""")
        self.assertEqual(na_conexao, 0, 'Presets de layout não pertencem à aba Conexão.')
```

- [ ] **Step 2: Rodar e confirmar que falham**

Run: `python -u .test_runtime\run_tests.py test_cada_fonte_tem_um_unico_botao_de_copiar test_presets_de_palco_ficam_na_aba_palco`
Expected: FAIL — `data-copy-path` ainda não existe (contagem 0) e os presets seguem no painel `conexao`.

- [ ] **Step 3: Reescrever os 7 links no painel `conexao`**

Manter o `title` no formato que `test_dock_copia_links_da_mesma_porta` procura e acrescentar `data-copy-path`:

```html
<button class="btn-copy" data-copy-path="/stage"
        onclick="copyUrl('/stage', this)"
        title="Copiar URL do Palco Composto (1920x1080)">Palco Composto</button>
<button class="btn-copy" data-copy-path="/cameras/grid"
        onclick="copyUrl('/cameras/grid', this)"
        title="Copiar URL do Grid Dinâmico de Webcams">Grid de Câmeras</button>
<button class="btn-copy" data-copy-path="/libras"
        onclick="copyUrl('/libras', this)"
        title="Copiar URL da Janela de Libras">Janela de Libras</button>
<button class="btn-copy" data-copy-path="/chat"
        onclick="copyUrl('/chat', this)"
        title="Copiar URL do Feed de Chat">Feed de Chat</button>
<button class="btn-copy" data-copy-path="/chat/featured"
        onclick="copyUrl('/chat/featured', this)"
        title="Copiar URL do Destaque de Mensagem">Chat em Destaque</button>
<button class="btn-copy" data-copy-path="/presentation"
        onclick="copyUrl('/presentation', this)"
        title="Copiar URL dos Slides da Apresentação">Slides</button>
<button class="btn-copy" data-copy-path="/poll"
        onclick="copyUrl('/poll', this)"
        title="Copiar URL da Enquete">Enquete</button>
```

- [ ] **Step 4: Apagar os botões duplicados**

Remover todos os botões cujo `onclick` seja `copyUrl(...)` fora do bloco acima — na prática os rotulados `Link Palco OBS`, `Link Janela Libras` (as ocorrências repetidas), `Link Grid`, `Link Feed`, `Link Destaque`, `Link Slides`, `Link OBS Enquete`. Conferir:

```powershell
Select-String -Path server.py -Pattern "copyUrl\(" | Where-Object { $_.LineNumber -ge 3135 }
```

Esperado: exatamente 7 ocorrências, todas no painel `conexao`.

- [ ] **Step 5: Mover os 6 presets para o painel `palco`**

Recortar o bloco dos `btnPreset_*` (~linhas 3442-3457) de dentro da seção Sala RNP e colar no `[data-tab-panel="palco"]`, acrescentando `data-preset` e mantendo ID e `onclick`:

```html
<div class="subsection-title">Layout do palco</div>
<button id="btnPreset_side-by-side" data-preset="side-by-side" class="btn-ctrl active-preset"
        onclick="setStagePreset('side-by-side')">Lado a Lado 70/30</button>
<button id="btnPreset_bottom-bar" data-preset="bottom-bar" class="btn-ctrl"
        onclick="setStagePreset('bottom-bar')">Slide Top + Rodapé</button>
<button id="btnPreset_pip-bottom-right" data-preset="pip-bottom-right" class="btn-ctrl"
        onclick="setStagePreset('pip-bottom-right')">PIP Canto Inferior</button>
<button id="btnPreset_pip-top-right" data-preset="pip-top-right" class="btn-ctrl"
        onclick="setStagePreset('pip-top-right')">PIP Canto Superior</button>
<button id="btnPreset_split-50-50" data-preset="split-50-50" class="btn-ctrl"
        onclick="setStagePreset('split-50-50')">Split 50 / 50</button>
<button id="btnPreset_slide-only" data-preset="slide-only" class="btn-ctrl"
        onclick="setStagePreset('slide-only')">Somente Slides</button>
```

- [ ] **Step 6: Rodar testes novos + regressão de dock**

Run: `python -u .test_runtime\run_tests.py test_cada_fonte_tem_um_unico_botao_de_copiar test_nao_sobram_links_de_fonte_duplicados test_presets_de_palco_ficam_na_aba_palco test_presets_nao_ficam_mais_na_secao_de_sala test_dock_copia_links_da_mesma_porta`
Expected: PASS nos 5.

- [ ] **Step 7: Commit**

```bash
git add server.py tests/test_media_browser.py
git commit -m "refactor(dock): links de fonte uma unica vez e presets na aba Palco"
```

---

### Task 6: Extrair o JS inline do dock para `web/dock.js`

**Files:**
- Modify: `server.py` — remover o `<script>` inline do `/control`
- Modify: `web/dock.js` — receber `fetchStatus`, `copyUrl`, `updateGridConfig`, `autoSaveField`, `toggleLibras`, `testAudioVuMeter`, `setStagePreset`, `handleDisconnect` e os demais helpers
- Test: `tests/test_media_bridge.py`

**Interfaces:**
- Consome: `web/dock.js` da Task 4; `/assets/dock.js` servido (Task 1).
- Produz: `/control` sem `<script>` inline substancial; mesmas funções globais de antes.

- [ ] **Step 1: Escrever o teste falho**

Em `tests/test_media_bridge.py` (acrescentar `import re` no topo do arquivo):

```python
    def test_dock_nao_tem_js_inline_substancial(self):
        html = self.client.get('/control').text
        self.assertIn('/assets/dock.js', html)
        blocos = re.findall(r'<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>', html, re.S)
        grandes = [b.strip()[:80] for b in blocos if len(b.strip()) > 200]
        self.assertEqual(grandes, [],
                         'Todo JS substancial do dock deve viver em web/dock.js, não na f-string.')

    def test_funcoes_globais_do_dock_vivem_no_asset(self):
        js = self.client.get('/assets/dock.js').text
        for fn in ('fetchStatus', 'copyUrl', 'updateGridConfig', 'autoSaveField',
                   'toggleLibras', 'testAudioVuMeter', 'setStagePreset', 'handleDisconnect'):
            self.assertIn(fn, js, f'{fn} precisa estar em web/dock.js (é chamado por onclick inline).')
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: comando do Step 2 da Task 1.
Expected: FAIL — o `<script>` inline ainda tem milhares de caracteres.

- [ ] **Step 3: Mover o JS**

Recortar o conteúdo do `<script>` inline do `/control` para `web/dock.js`, **dentro** da IIFE existente, e expor globalmente o que o `onclick` inline chama. Como o HTML usa `onclick="setStagePreset(...)"`, essas funções precisam estar no escopo global:

```javascript
    window.fetchStatus = fetchStatus;
    window.copyUrl = copyUrl;
    window.updateGridConfig = updateGridConfig;
    window.autoSaveField = autoSaveField;
    window.toggleLibras = toggleLibras;
    window.testAudioVuMeter = testAudioVuMeter;
    window.setStagePreset = setStagePreset;
    window.handleDisconnect = handleDisconnect;
```

Duas diferenças obrigatórias ao mover:
1. **Desduplicar chaves.** Dentro da f-string Python cada `{{` vira `{` no HTML. No arquivo `.js` real escreve-se `{` simples. Não copiar `{{`/`}}` literalmente.
2. **Substituir interpolações Python.** Qualquer `{state.x}` ou `{json.dumps(...)}` no JS inline precisa virar leitura de runtime — do `data-*` no `<body>` ou de `/api/status`/`/api/config`. Exemplo: `const useWebRTC = {json.dumps(state.webrtc_only)};` vira `const useWebRTC = document.body.dataset.webrtc === '1';`, acrescentando `data-webrtc="{1 if state.webrtc_only else 0}"` no `<body>`.

Localizar todas as interpolações antes de mover:

```powershell
Select-String -Path server.py -Pattern '\{[a-z_]+|\{json\.dumps' | Where-Object { $_.LineNumber -ge 3800 -and $_.LineNumber -le 4978 }
```

- [ ] **Step 4: Rodar testes novos + regressão completa de dock**

```powershell
python -m py_compile server.py
python -u .test_runtime\run_tests.py test_dock_lista_audio_sem_camera_e_aplica_mute test_dock_mostra_medidor_de_fps test_dock_nao_confirma_libras_quando_gravacao_falha test_dock_copia_links_da_mesma_porta test_presets_de_palco_ficam_na_aba_palco
```

Run: comando do Step 2 da Task 1 (para os 2 testes novos).
Expected: tudo PASS. Se `toggleLibras` ou o autosave quebrarem, a causa mais provável é interpolação Python não convertida (Step 3, item 2).

- [ ] **Step 5: Commit**

```bash
git add server.py web/dock.js tests/test_media_bridge.py
git commit -m "refactor(dock): extrair JS inline do /control para web/dock.js"
```

---

### Task 7: Tema Ifes, suíte completa, build e documentação

**Files:**
- Modify: `docs/WEBRTC_TRANSPORTE.md`, `README.md`, `.agents/skills/rnp-to-obs/SKILL.md`, `.agents/skills/obs-stream-producer/SKILL.md`
- Modify: `web/dock.css` (se algum ajuste de contraste for necessário)
- Test: `tests/test_media_bridge.py`

**Interfaces:**
- Consome: `web/dock.css` com os dois blocos de tokens.
- Produz: garantia de que tema é só override de token; suíte verde; bundle do `.exe` com os novos assets.

- [ ] **Step 1: Escrever o teste falho**

Em `tests/test_media_bridge.py`:

```python
    def test_temas_sao_apenas_override_de_tokens(self):
        css = (Path(__file__).resolve().parents[1] / 'web' / 'dock.css').read_text(encoding='utf-8')
        for token in ('--bg', '--surface', '--text', '--text-muted', '--accent-top'):
            self.assertIn(token, css, f'Token {token} precisa existir em web/dock.css.')
        # Nenhum componente pode ser re-estilizado por tema: só tokens.
        proibidos = re.findall(r'body\.theme-\w+\s+\.[\w-]+', css)
        self.assertEqual(proibidos, [],
                         f'Tema não deve estilizar componente diretamente: {proibidos}')
        self.assertIn('#15803d', css, 'Filete institucional do Ifes precisa estar no token --accent-top.')

    def test_dock_aplica_classe_de_tema_normalizada(self):
        html = self.client.get('/control').text
        self.assertRegex(html, r'<body[^>]*class="theme-(broadcast|ifes)"')
        self.assertNotIn('theme-hardware', html)
        self.assertNotIn('theme-glass', html)
```

- [ ] **Step 2: Rodar e confirmar o estado**

Run: comando do Step 2 da Task 1.
Expected: `test_temas_sao_apenas_override_de_tokens` PASS se o `dock.css` das Tasks 1/4 estiver correto; `test_dock_aplica_classe_de_tema_normalizada` PASS se a Task 2/4 estiver correta. Se falhar, é sinal de que sobrou seletor `body.theme-X .componente` — remover e expressar via token.

- [ ] **Step 3: Verificar contraste dos pares do spec §6**

Conferir, para os dois temas, os pares ainda dependentes da superfície final: `--text` sobre `--bg` e sobre `--surface-2`, e o texto do `Parar` sobre `--danger-surface`. Se algum ficar < 4,5:1, **ajustar o token** — nunca rebaixar o requisito. Registrar os valores medidos no spec §6.

- [ ] **Step 4: Rodar a suíte completa**

```powershell
Get-NetTCPConnection -LocalPort 3333,3334 -State Listen -ErrorAction SilentlyContinue
python -u .test_runtime\run_suite.py
```

Expected: `Ran 110 tests ... OK` (79 iniciais + 31 novos: 2 na Task 1, 7 na Task 2, 2 na Task 3, 12 na Task 4, 4 na Task 5, 2 na Task 6, 2 na Task 7). Confirmar também que `config.json` **não** foi alterado pela rodada:

```powershell
git diff --stat config.json
```

Esperado: vazio (`config.json` é ignorado; se aparecer diff, o isolamento de `CONFIG_FILE` regrediu).

- [ ] **Step 5: Validar visualmente nos dois temas**

Subir o servidor e conferir o dock a 330px e 480px, em `broadcast` e `ifes`:

```powershell
python -c "import os; os.environ.update(RNP_PORT='3335'); import uvicorn, server; uvicorn.run(server.app, host='127.0.0.1', port=3335, log_level='warning')"
```

Checar: nenhuma rolagem horizontal; barra de status sempre visível ao rolar; as 5 abas cabem a 330px; filete verde no topo dos cards só no `ifes`; VU segmentado responde ao áudio. Encerrar a porta 3335 ao terminar.

- [ ] **Step 6: Rebuild do `.exe` e conferência do bundle**

```powershell
python build_exe.py
python .test_runtime\verify_bundle.py
```

Confirmar que `dist\RNP to OBS\_internal\web\dock.css` e `dock.js` existem. Se faltarem, ajustar `build_exe.py` (ele já empacota `--add-data web;web`, então a causa provável é cache do PyInstaller — limpar `build/`).

- [ ] **Step 7: Atualizar documentação**

- `docs/WEBRTC_TRANSPORTE.md`: nova seção registrando o design system do dock (tokens, 2 temas, abas), e remover menções a `/catalog` e `/themes`.
- `README.md` e `GUIA_DE_OPERACAO.md`: descrever as 5 abas e a barra de status; remover referências à vitrine de temas.
- `.agents/skills/rnp-to-obs/SKILL.md` e `obs-stream-producer/SKILL.md`: remover `/catalog` da tabela de endpoints e documentar `broadcast` como tema padrão.
- Atualizar a contagem de testes onde ela aparecer.

- [ ] **Step 8: Commit**

```bash
git add web/dock.css docs/WEBRTC_TRANSPORTE.md README.md GUIA_DE_OPERACAO.md .agents/skills tests/test_media_bridge.py
git commit -m "feat(dock): tema Ifes em tokens, validacao de contraste, build e docs"
```

---

## Verificação final

- [ ] `python -u .test_runtime\run_suite.py` → **110 testes OK**, `config.json` intacto.
- [ ] `Select-String -Path server.py -Pattern 'CONTROL_PANEL_CSS|theme-hardware|theme-nordic|theme-glass|theme-obsidian|get_themes_catalog'` → **nenhuma ocorrência**.
- [ ] `server.py` reduziu ~1.600 linhas (786 de `CONTROL_PANEL_CSS` + ~800 do HTML/JS inline do dock).
- [ ] Dock conferido a 330px e 480px nos dois temas, sem rolagem horizontal.
- [ ] `dist\RNP to OBS\_internal\web\` contém `dock.css` e `dock.js`.
