---
name: realtime-web-ui
description: >-
  Guia para desenvolvimento de Front-End em Tempo Real (Real-Time Web), WebSockets de baixa latência,
  Web Audio API (VU meters, PCM Float32), manipulação de Canvas/SVG e overlays reativos para streaming.
---

# ⚡ Real-Time Web UI & Streaming Overlays - Guia Prático

Este guia aborda a construção de interfaces reativas de alto desempenho que recebem fluxos de dados contínuos (vídeo, áudio, chat, enquetes) sem recarregar a página.

---

## 🎧 1. Web Audio API & VU Meters em Tempo Real

Como processar e renderizar picos de áudio PCM Float32 com zero latência no navegador:

```javascript
// Calcular pico de volume de um chunk de Float32Array
function calculatePeak(float32Data) {
    let peak = 0;
    for (let i = 0; i < float32Data.length; i++) {
        const val = Math.abs(float32Data[i]);
        if (val > peak) peak = val;
    }
    return peak;
}

// Atualizar barra de VU Meter visual suavemente
function updateVuMeter(peak) {
    const percentage = Math.min(100, Math.round(peak * 180));
    const bar = document.getElementById('vuBar');
    if (bar) {
        bar.style.width = percentage + '%';
    }
}
```

---

## 🔄 2. Reconexão Automática de WebSockets

Padrão de reconexão resiliente para dados em tempo real:

```javascript
function connectRealtimeWebSocket(url, onMessage) {
    const ws = new WebSocket(url);
    ws.binaryType = 'arraybuffer';

    ws.onmessage = (event) => {
        onMessage(event.data);
    };

    ws.onclose = () => {
        // Tentar reconectar a cada 2 segundos se a conexão cair
        setTimeout(() => connectRealtimeWebSocket(url, onMessage), 2000);
    };

    ws.onerror = () => {
        ws.close();
    };
}
```

---

## 🖼️ 3. Transparência Perfeita para Fontes OBS (Browser Sources)

Regras para garantir que o fundo fique 100% transparente no OBS Studio:

```css
html, body {
    background: transparent !important;
    width: 100vw;
    height: 100vh;
    overflow: hidden;
    margin: 0;
    padding: 0;
}
```

---

## 🛡️ 4. Comparação de Assinatura de Estado (Evitar Re-renders desnecessários)

Para manter a interface a 60 FPS sem piscadeiras (*flickering*):

```javascript
let lastSignature = '';

function renderUI(data) {
    const signature = JSON.stringify(data);
    if (signature === lastSignature) {
        return; // Nada mudou, não reconstrói o DOM!
    }
    lastSignature = signature;
    
    // Atualiza o DOM somente quando houver mudanças reais
    rebuildDOM(data);
}
```
