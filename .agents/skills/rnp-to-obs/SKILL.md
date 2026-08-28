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
| `http://localhost:3333/stage` | **Navegador (Browser Source)** | Palco composto (Slides + Câmeras + Libras paritário). |
| `http://localhost:3333/cameras/grid`| **Navegador (Browser Source)** | Grid dinâmico e responsivo de todas as webcams abertas. |
| `http://localhost:3333/libras` | **Navegador (Browser Source)** | Janela dedicada e isolada para Intérprete de Libras. |
| `http://localhost:3333/presentation`| **Navegador (Browser Source)** | Slides ou compartilhamento de tela puro (1080p/2.5K). |
| `http://localhost:3333/chat` | **Navegador (Browser Source)** | Feed de chat ao vivo unificado (RNP + YouTube). |
| `http://localhost:3333/chat/featured`| **Navegador (Browser Source)**| Lower-third animado para destaque de mensagens. |
| `http://localhost:3333/poll` | **Navegador (Browser Source)** | Card moderno de resultados de enquete com temporizador. |
| `http://localhost:3333/audio` | **Navegador (Browser Source)** | Áudio mixado de alta fidelidade (PCM Float32 / 48 kHz). |

---

## 🎛️ Presets de Cena no Palco Composto (`/stage`)

- `side-by-side`: Slides em 68% à esquerda + Câmeras em 32% à direita.
- `bottom-bar`: Slides no topo + Câmeras no rodapé.
- `pip-bottom-right` / `pip-top-right`: Slides em tela cheia com palestrante flutuante em PIP (380x215).
- `split-50-50`: Divisão paritária meio a meio.
- `slide-only`: Apenas apresentação.

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

## 🔊 Arquitetura de Áudio (PCM WebRTC)

- Captura o elemento `<audio>` da conferência via **Web Audio API**.
- Transmite por WebSocket binário (`/ws/pcm_out`) em **Float32 48.000 Hz**.
- Latência sub-50ms e zero compressão adicional.
