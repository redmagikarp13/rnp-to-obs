# 🚀 RNP & BigBlueButton to OBS Studio Bridge

Uma ponte de nível broadcast em tempo real que conecta salas da **RNP WebConf (BigBlueButton)** e transmissões do **YouTube Live** diretamente ao **OBS Studio**.

Desenvolvida com foco em ultra-baixa latência, fidelidade visual sem perda de qualidade, total paridade de acessibilidade (Libras), integração completa de enquetes ao vivo, chat unificado com lower-thirds e mixagem digital de áudio PCM.

---

## 🌟 Principais Recursos

### 🎛️ 1. Dock de Controle Integrado no OBS (`/control`)
- Painel moderno em tema escuro com suporte nativo a **Docks Personalizados do OBS Studio** (`Docks -> Docks de Navegador Personalizados`).
- Controle em 1 clique para conectar/desconectar da sala RNP e vincular lives do YouTube.
- Troca em tempo real de presets de palco, foco de câmeras, fixação de palestrantes e temas visuais.
- **Persistência em Disco:** todas as configurações são salvas em `config.json` e restauradas automaticamente.

### 🎭 2. Palco Composto Inteligente (`/stage`)
- Combina dinamicamente apresentação/slides e câmeras dos participantes com transições suaves.
- **6 Presets de Transmissão:**
  - `📺 Lado a Lado (70/30)`: Slides em 70% à esquerda + Câmeras e Libras em 30% à direita.
  - `⬇️ Slide Top + Rodapé`: Apresentação no topo com grid horizontal de participantes embaixo.
  - `🖼️ PIP Canto Inferior`: Slides em tela cheia com palestrante flutuando no canto inferior direito.
  - `↗️ PIP Canto Superior`: Slides em tela cheia com palestrante no canto superior direito.
  - `⚖️ Split 50 / 50`: Divisão meio a meio para debates e mesas-redondas.
  - `📊 Somente Slides`: Foco exclusivo na apresentação.

### 🤟 3. Janela de Libras Paritária & Acessibilidade (`/libras`)
- **Padrão IFES / ABNT:** Janela com proporção idêntica ao palestrante (`equal`) ou flutuante nos tamanhos `huge` (560x315), `large` (480x270) e `medium` (380x214).
- **Auto-Reconhecimento de Intérprete:** Detecta automaticamente palavras-chave como `Libras`, `Intérprete`, `ILS` no nome do participante.
- **Persistência de Conexão:** Se o intérprete cair e reconectar, o sistema o identifica e restaura na cena com a configuração exata.

### 📊 4. Enquetes ao Vivo com Temporizador e 5 Temas (`/poll`)
- Extração de enquetes abertas e resultados publicados diretamente do BigBlueButton.
- **Modos de Exibição:** Somente Resultados Finais (Recomendado) ou Votação ao Vivo.
- **Temporizador com Auto-Exit:** 10s, 15s (padrão), 30s, 60s ou permanente, com animação suave de saída.
- **5 Temas Visuais:**
  - `⚡ Neon Glow`: Borda cyan neon com gradientes luminosos.
  - `💎 Glassmorphism`: Vidro fosco moderno (*frosted glass*).
  - `💬 Discord Bubbles`: Card escuro com acentos em índigo.
  - `🎬 Cyberpunk Broadcast`: Estilo angular com bordas douradas.
  - `📄 Minimal Slate`: Minimalista escuro e discreto.
- **Modo Transmissão Pura:** Monitoramento seguro sem intervenção ou botões de votação interna.

### 💬 5. Chat Unificado & Lower-Thirds (`/chat` & `/chat/featured`)
- Agregação em tempo real de mensagens do chat da RNP e do SuperChat/Live Chat do YouTube.
- **Destaque em 1 Clique:** Clique em qualquer mensagem no Dock para exibi-la como um banner de destaque (*lower-third*) no OBS.
- Emojis convertidos e renderizados com alta nitidez.

### 🔊 6. Áudio Digital de Alta Fidelidade (`/audio`)
- Transmissão direta de áudio sem compressão via **WebSocket PCM Float32 (48.000 Hz)**.
- Latência sub-50ms e monitor com VU Meter em tempo real no painel de controle.

### 🛡️ 7. Bypass Anti-Inatividade Automático
- Sistema autônomo que intercepta e fecha modais de inatividade da RNP, mantendo o bot conectado indefinidamente sem interrupções na live.

---

## 📋 Lista de Endpoints para o OBS Studio

Todos os endpoints operam em `http://localhost:3333` com fundo 100% transparente para encaixe direto nas cenas:

| Endpoint | Tipo no OBS | Resolução Recomendada | Descrição |
| :--- | :--- | :--- | :--- |
| `http://localhost:3333/control` | **Dock de Navegador** | — | Painel de controle para o operador. |
| `http://localhost:3333/stage` | **Browser Source** | `1920 x 1080` | Palco composto (Slides + Câmeras + Libras). |
| `http://localhost:3333/cameras/grid` | **Browser Source** | `1920 x 600` ou `1920 x 1080` | Grid responsivo de todas as webcams abertas. |
| `http://localhost:3333/libras` | **Browser Source** | `560 x 315` | Janela isolada para Intérprete de Libras. |
| `http://localhost:3333/presentation` | **Browser Source** | `1920 x 1080` | Apresentação ou compartilhamento de tela puro. |
| `http://localhost:3333/chat` | **Browser Source** | `460 x 600` | Feed de mensagens unificado (RNP + YouTube). |
| `http://localhost:3333/chat/featured` | **Browser Source** | `1920 x 1080` | Banner de mensagem em destaque (Lower-Third). |
| `http://localhost:3333/poll` | **Browser Source** | `1920 x 1080` | Card animado de resultados de enquetes. |
| `http://localhost:3333/audio` | **Browser Source** | `200 x 200` | Receptor de áudio digital sem perdas. |

---

## ⚙️ Instalação e Execução

### Pré-requisitos
- **Python 3.10 ou superior** instalado ([python.org](https://www.python.org/downloads/)).
- **OBS Studio 28+** instalado.

### 1. Instalação Rápida (Windows)
1. Clone ou baixe este repositório:
   ```bash
   git clone https://github.com/redmagikarp13/rnp-to-obs.git
   cd rnp-to-obs
   ```
2. Execute o arquivo **`install.bat`** (ele instalará o `FastAPI`, `Playwright`, `Pytchat` e o motor do Chromium automaticamente).

### 2. Instalação Manual
```bash
pip install -r requirements.txt
playwright install chromium
```

### 3. Como Iniciar o Servidor
Dê um duplo clique em **`start.bat`** ou rode no terminal:
```bash
python server.py
```
O servidor estará pronto em `http://localhost:3333`.

---

## 🎬 Configurando no OBS Studio

### Adicionando o Painel de Controle no OBS
1. No menu superior do OBS, acesse: **Docks** -> **Docks de Navegador Personalizados...**
2. No campo *Nome do Dock*, digite: `RNP Control`.
3. No campo *URL*, digite: `http://localhost:3333/control`.
4. Clique em **Aplicar** e encaixe a janela onde preferir na sua interface do OBS.

### Adicionando Cenas e Overlays
1. Em qualquer cena, clique no botão **`+`** em *Fontes* e escolha **Navegador** (*Browser Source*).
2. Cole o link desejado (ex: `http://localhost:3333/stage`).
3. Defina a largura e altura como `1920` x `1080`.
4. Marque a opção: `Controlar áudio via OBS` (opcional caso utilize a fonte de áudio).

---

## 🛠️ Tecnologias Utilizadas
- **Backend:** Python 3, FastAPI, Uvicorn, WebSockets, Asyncio.
- **Automação WebRTC:** Playwright (Chromium Headless).
- **Front-End & Overlays:** Vanilla HTML5, CSS3 Glassmorphism/Neon Design System, Web Audio API.
- **Integração YouTube:** Pytchat (Live Stream Chat API).

---

## 📄 Licença
Distribuído sob licença proprietária/privada. Todos os direitos reservados.
