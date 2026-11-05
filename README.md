# 🚀 RNP & BigBlueButton to OBS Studio Bridge

Uma ponte de transmissão broadcast de alta fidelidade e ultra-baixa latência conectando salas da **RNP WebConf (BigBlueButton)** e transmissões do **YouTube Live** diretamente ao **OBS Studio**.

Desenvolvida especialmente para eventos institucionais, aulas magnas e transmissões do **Cefor / Ifes**, priorizando **paridade plena de acessibilidade em Libras**, **mixagem digital de áudio PCM Float32**, **enquetes dinâmicas** e **chat unificado com lower-thirds**.

---

## 🌟 Principais Recursos e Funcionalidades

### 🎛️ 1. Painel de Controle Integrado (`/control`)
- **Modo Dock OBS & Janela Desktop Nativa:** Pode ser embutido como Dock no OBS Studio ou executado como aplicativo de desktop independente via `pywebview` (com fallback para Microsoft Edge / Google Chrome em modo `--app`).
- **Conexão em 1 Clique:** Entrada automatizada na sala da RNP WebConf com bypass anti-inatividade autônomo (mantém a sessão ativa sem desconexões).
- **Integração YouTube Live:** Vinculação simultânea com qualquer transmissão ao vivo do YouTube para mesclagem de chat e super chats.
- **Seletor de Temas do Painel:** Inclui o tema escuro **Broadcast** (`broadcast`) e o tema claro institucional **Ifes** (`ifes`), alternáveis diretamente na barra de status.
- **Acesso em Rede Local (LAN) & Tela de Retorno (`/tv`):** Permite abrir a tela de retorno dos participantes em foco (`http://<IP-DO-PC>:3333/tv`) em outra máquina ou Smart TV da mesma rede.
- **Persistência Completa:** Todas as preferências, nomes personalizados, câmeras em foco, fixadas e intérpretes são salvos automaticamente no `config.json`.

### 🎚️ 2. Rack de Áudio com VU Meters Verticais e Detecção de Voz Ativa
- **Mixer Visual em Tempo Real:** Medição precisa de volume no painel de controle com rack vertical de medidores estéreo (medidores individuais, monitor local e previews de vídeo iniciam desligados por padrão para poupar CPU, podendo ser ligados a qualquer momento com 1 clique).
- **Isolamento de Áudio Ativo:** Indicadores visuais dinâmicos que destacam o participante que está falando no momento (`activeTalkers`).
- **Prevenção de Eco no OBS:** o master de áudio é transmitido por **uma única** fonte, o `/stage` (com "Controlar áudio via OBS" marcado). Os demais overlays de vídeo (`/libras`, `/cameras/grid`, `/presentation`) mantêm a saída mutada, evitando duplicação e microfonia.

### 🤟 3. Janela de Libras Paritária & Acessibilidade (`/libras`)
- **Conformidade ABNT / Cefor / Ifes:** Suporte à visualização paritária (`equal`, 50% de proporção em relação ao palestrante) ou janelas flutuantes (`huge` 560x315, `large` 480x270, `medium` 380x214).
- **Auto-Reconhecimento Inteligente:** Identifica automaticamente intérpretes por palavras-chave (`Libras`, `Intérprete`, `ILS`).
- **Marcação Manual Instantânea:** Botão seletor direto em cada card de câmera no Dock com atualização imediata em tempo real.
- **Isolamento Estrito de Fluxo:** Cada câmera recebe apenas o seu próprio sinal WebRTC, sem risco de troca de imagens.
- **Memória de Reconexão:** Se o intérprete perder a conexão momentaneamente, o sistema o reconhece e restaura na cena com as configurações salvas.

### 🎭 4. Palco Composto Inteligente (`/stage`)
Combinação dinâmica em tempo real da apresentação/slides e das câmeras dos participantes com transições suaves e 9 presets (6 com slide e 3 sem slide):
- `📺 Lado a Lado (70/30)`: Slides em 70% à esquerda + Câmeras e Libras em 30% à direita.
- `⬇️ Slide Top + Rodapé`: Apresentação no topo com grid horizontal de participantes embaixo.
- `🖼️ PIP Canto Inferior`: Slides em tela cheia com palestrante flutuando no canto inferior direito.
- `↗️ PIP Canto Superior`: Slides em tela cheia com palestrante no canto superior direito.
- `⚖️ Split 50 / 50`: Divisão meio a meio entre slides e câmeras.
- `📊 Somente Slides`: Foco exclusivo no material da apresentação.
- `🎥 Câmera + Libras (sem slide)`: Divide a tela 50/50 entre a câmera principal em foco e o intérprete de Libras.
- `👥 Todas + Libras (sem slide)`: Grade com todos os participantes + intérprete de Libras dividindo a largura.
- `📹 Só Câmeras (sem slide)`: Todas as câmeras no palco com a janela de Libras flutuante no canto.

### 📊 5. Enquetes ao Vivo BBB com 5 Temas Visuais (`/poll`)
- Extração em tempo real das enquetes abertas e resultados publicados na conferência RNP/BBB.
- **5 Temas Visuais:** `Neon Glow`, `Glassmorphism`, `Discord Bubbles`, `Cyberpunk Broadcast` e `Minimal Slate`.
- **Temporizador com Saída Animada:** Exibição configurável (10s, 15s padrão, 30s, 60s ou permanente).
- **Modo Transmissão Pura:** Overlay visual limpo sem botões de voto do BBB, ideal para o público da live.

### 💬 6. Chat Unificado & Lower-Thirds (`/chat` e `/chat/featured`)
- Agrupamento cronológico unificado do chat interno da RNP e do chat ao vivo do YouTube.
- **Lower-Third de Destaque:** 1 clique na mensagem dentro do Dock a projeta instantaneamente como tarja de destaque no OBS.
- **Isolamento Estrito de Avatares:** Participantes sem foto de perfil utilizam avatares vetoriais individuais gerados com suas iniciais, sem risco de herdar a imagem de mensagens anteriores.
- Opção para ocultar ou exibir avatares e ajuste de tamanho de fonte no painel.

### 🎥 7. Captura de Vídeos do YouTube Compartilhados na Sala (`/presentation`)
- Detecta automaticamente quando o apresentador da RNP compartilha um vídeo externo do YouTube.
- O endpoint `/presentation` transiciona perfeitamente entre os slides de PDF e o player de vídeo do YouTube em tela cheia.

---

## 📋 Catálogo Completo de Endpoints para o OBS Studio

Todos os endpoints operam em `http://localhost:3333` com transparência total (`alpha channel`) para sobreposição direta nas cenas:

| Endpoint | Tipo no OBS Studio | Dimensões Recomendadas | Descrição e Finalidade |
| :--- | :--- | :--- | :--- |
| `http://localhost:3333/control` | **Dock de Navegador** | Painel Livre | Painel de controle e monitoramento para o operador. |
| `http://localhost:3333/stage` | **Browser Source** | `1920 x 1080` | Palco principal completo (Slides + Câmeras + Libras) **e master de áudio** — marque "Controlar áudio via OBS" e desligue "Shutdown source when not visible". |
| `http://localhost:3333/cameras/grid` | **Browser Source** | `1920 x 1080` ou `1920 x 600` | Grid dinâmico com todas as webcams ativas. |
| `http://localhost:3333/libras` | **Browser Source** | `560 x 315` | Janela isolada dedicada para o Intérprete de Libras. |
| `http://localhost:3333/presentation` | **Browser Source** | `1920 x 1080` | Slides da apresentação ou vídeo compartilhado. |
| `http://localhost:3333/chat` | **Browser Source** | `460 x 700` | Feed de mensagens com rolagem automática. |
| `http://localhost:3333/chat/featured` | **Browser Source** | `1920 x 1080` | Tarja de mensagem em destaque (*Lower-Third*). |
| `http://localhost:3333/poll` | **Browser Source** | `1920 x 1080` | Card animado de resultado da enquete ao vivo. |
| `http://localhost:3333/audio?source=<canal>` | **Browser Source** | `200 x 200` | Áudio **individual** de um participante (redireciona ao receptor WebRTC). O master não fica mais aqui — sai pelo `/stage`. Links prontos no rack de áudio do `/control`. |
| `http://localhost:3333/tv` | **Navegador Web / TV** | Tela Cheia | Tela de retorno de participantes em foco (spotlight) + fila secundária e destaque de chat (`?chat=0` oculta o destaque). |

---

## 🚀 Como Executar o Projeto

### Opção 1: Executável ou Instalador Profissional (Recomendado para Produção)
O projeto conta com automação para geração de instalador `.exe` via Inno Setup e binários portáteis com PyInstaller:
- **Gerar Instalador:** Execute `compilar_instalador.bat`. O instalador `Instalador_RNP_to_OBS_Setup.exe` será gerado em `dist_installer\`.
- **Gerar Executável Portátil:** Execute `compilar_exe.bat`. A pasta autônoma `dist\RNP to OBS\` será gerada pronta para distribuição (pode ser compactada em `.zip`).
- **Instalar:** Execute o instalador gerado; ele criará atalhos no Menu Iniciar e na Área de Trabalho com ícone oficial.

### Opção 2: Modo Desktop com Python Local
1. Dê um duplo clique no atalho **`Abrir Painel.bat`** (ou execute `python app.py`).
2. O servidor iniciará em segundo plano (vigiado por watchdog automático) e abrirá uma janela nativa dedicada do painel.
3. Ao fechar a janela do painel, o servidor iniciado pelo aplicativo é encerrado automaticamente (servidores já existentes antes da abertura do painel são preservados).
4. Para forçar a parada a qualquer momento, execute **`Parar Servidor.bat`**.

### Opção 3: Modo Servidor Console Tradicional
```bash
python server.py
```
Acesse `http://localhost:3333/control` no seu navegador.

---

## 🛠️ Instalação para Desenvolvedores

### Pré-requisitos
- **Python 3.10 ou superior** instalado.
- **OBS Studio 28+** instalado.

### Passo a Passo
1. Clone este repositório:
   ```bash
   git clone https://github.com/redmagikarp13/rnp-to-obs.git
   cd rnp-to-obs
   ```
2. Instale as dependências:
   ```bash
   pip install -r requirements.txt
   playwright install chromium
   ```

---

## 🎬 Configuração Passo a Passo no OBS Studio

### 1. Inserir o Painel de Controle no OBS
1. No menu superior do OBS, clique em: **Docks** -> **Docks de Navegador Personalizados...**
2. Em **Nome do Dock**, informe: `RNP Control`.
3. Em **URL**, cole: `http://localhost:3333/control`.
4. Clique em **Aplicar** e encaixe a janela na lateral ou rodapé do OBS Studio.

### 2. Inserir as Cenas de Vídeo
1. Na lista de **Fontes** da cena, clique no **`+`** e selecione **Navegador** (*Browser Source*).
2. Marque *Criar nova* e dê um nome (ex: `Palco RNP`).
3. Cole a URL correspondente (ex: `http://localhost:3333/stage`).
4. Configure a resolução: **Largura:** `1920`, **Altura:** `1080`.
5. Nas demais fontes de vídeo (`/libras`, `/cameras/grid`, `/presentation`), deixe **desmarcado** "Controlar áudio via OBS" para evitar sobreposição sonora.

### 3. Áudio da sala (vem junto com o `/stage`)
1. Na fonte do **`/stage`**, marque **"Controlar áudio via OBS"** — é ela que entrega o master digital da sala (participantes + apresentações + som do YouTube) em PCM Float32 48 kHz.
2. Ainda na fonte do `/stage`, **desligue** "Shutdown source when not visible". Como o áudio agora é acoplado ao palco, essa opção ligada faria o som sumir sempre que o palco não estivesse visível na cena.
3. No Mixer de Áudio do OBS, o som aparecerá como uma fonte de navegador comum, aceitando ganho, compressor e monitoramento.

### 4. Áudio individual por participante (opcional)
O master já cobre a transmissão. Se precisar de uma trilha separada (ex.: gravar somente o palestrante), copie o link individual no **rack de áudio do painel** (`/control`): cada canal traz VU, volume, mute e botão de copiar, apontando para `/webrtc?source=<canal>&kind=audio`.

---

## 🏗️ Arquitetura e Engenharia de Performance

```
┌─────────────────────────────────────────────────────────────┐
│                    RNP WebConf (BigBlueButton)              │
│       Câmeras WebRTC  │  Slides SVG/Canvas  │  Áudio WebRTC │
└───────────────────────┬─────────────────────────────────────┘
                        │ CDP (Chrome DevTools Protocol)
┌───────────────────────▼─────────────────────────────────────┐
│              RNP to OBS Core (Playwright Engine)            │
│  - Loop Sequencial de Sincronização (~20 FPS)              │
│  - Extrator de Câmeras e Isolamento Estrito de IDs          │
│  - Scraping Não-Bloqueante de Chat RNP & YouTube (Pytchat)  │
│  - Transmissor de Áudio PCM Float32 via WebSocket           │
└───────────────────────┬─────────────────────────────────────┘
                        │ HTTP / WebSocket (Porta 3333)
┌───────────────────────▼─────────────────────────────────────┐
│                      OBS Studio (CEF)                       │
│  - /control (Dock)     - /stage (Palco + Master PCM 48kHz)  │
│  - /libras (Acessib.)  - /cameras/grid (Grade de Câmeras)   │
│  - /chat (Overlay)     - /poll (Enquetes BBB)               │
│  - /audio?source=N     - /chat/featured (Lower-Third)       │
└─────────────────────────────────────────────────────────────┘
```

- **Loop Único Sequencial de Sincronização:** Elimina conflitos de concorrência no protocolo CDP do Chrome, mantendo uso estável de CPU e latência mínima.
- **Cache-Busting Discrete Polling para OBS CEF:** Fontes de Browser no OBS operam com chamadas de quadros `/raw_frame/` de alta velocidade, prevenindo travamento de conexões persistentes que ocorriam com streams multipart antigos.
- **Isolamento de Avatares:** Algoritmo que garante que participantes sem foto utilizem avatares SVG dinâmicos baseados no nome, impedindo contaminação visual entre mensagens consecutivas.

---

## 📄 Licença
Desenvolvido para apoio às transmissões educacionais e institucionais.
Cefor / Ifes — Instituto Federal do Espírito Santo.
Todos os direitos reservados.
