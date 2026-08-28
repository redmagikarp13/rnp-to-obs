# RNP Webconferência Bridge - Plugin Nativo para OBS Studio (C++)

Plugin nativo para **OBS Studio 30+ (Windows 64-bit)** que permite integrar a **Webconferência RNP (BigBlueButton / Mconf)** diretamente no pipeline de vídeo do OBS com altíssimo desempenho, transparência nativa e divisão modular de fontes.

---

## 🌟 Recursos do Plugin

Ao instalar a DLL no OBS Studio, novas fontes nativas ficam disponíveis no menu **Adicionar Fonte (+)**:

1. **🎥 RNP - Câmera de Participante (Webcam)**:
   - Permite selecionar no menu de propriedades: *Todas em Grid Dinâmico*, *Orador Ativo (Quem fala)* ou a câmera de um participante específico.
   - Renderização direta na GPU (DirectX 11) com máxima fluidez e baixa latência.
2. **📊 RNP - Apresentação / Slides (Tela Compartilhada)**:
   - Captura dos slides / tela em 60 fps nativos com resolução ajustável (1080p/4K).
   - Sem barras de navegação, anotações ou menus da interface web.
3. **💬 RNP - Chat Overlay (StreamYard Style)**:
   - Feed de mensagens translúcido com cards de autor em ciano e texto branco puro.
   - Suporte a transparência nativa e efeito de fade gradiente no topo/base.

---

## 📁 Estrutura do Projeto

```
r:\rnpToObs\obs_plugin_cpp\
  ├── include\
  │     ├── obs_headers.h           # Declarações da C API do OBS Studio (libobs)
  │     ├── rnp_session.h           # Gerenciador de sessão e WebRTC
  │     └── rnp_sources.h           # Registros das fontes nativas
  ├── src\
  │     ├── plugin_main.cpp         # Ponto de entrada do módulo OBS (load/unload)
  │     ├── rnp_camera_source.cpp   # Fonte nativa de Câmeras
  │     ├── rnp_presentation_source.cpp # Fonte nativa de Slides
  │     ├── rnp_chat_source.cpp     # Fonte nativa de Chat
  │     └── rnp_session.cpp         # Coordenação da sala RNP
  ├── bin\64bit\
  │     └── obs-rnp-bridge.dll      # Arquivo binário compilado para o OBS
  ├── build.bat                     # Script de compilação automatizada
  ├── install.bat                   # Instalador automático para a pasta do OBS
  └── README.md
```

---

## 🚀 Como Instalar e Usar

### Passo 1: Instalação no OBS
1. Execute o arquivo [`install.bat`](file:///r:/rnpToObs/obs_plugin_cpp/install.bat) como Administrador (ou copie manualmente `bin\64bit\obs-rnp-bridge.dll` para `C:\Program Files\obs-studio\obs-plugins\64bit\`).
2. Abra ou reinicie o **OBS Studio**.

### Passo 2: Adicionando as Fontes na Cena
1. No painel de **Fontes** do OBS, clique no botão **`+`**.
2. Escolha qualquer uma das fontes RNP:
   - `RNP - Câmera de Participante`
   - `RNP - Apresentação / Slides`
   - `RNP - Chat Overlay`
3. Nas propriedades da fonte, informe a **URL da Sala** (ex: `https://conferenciaweb.rnp.br/sala/sua-sala`) e clique em **OK**.
