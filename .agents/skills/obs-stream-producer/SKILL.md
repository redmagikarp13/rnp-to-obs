---
name: obs-stream-producer
description: >-
  Guia de produção de lives, configuração avançada do OBS Studio, Browser Sources transparentes,
  criação de cenas broadcast, mixagem de áudio digital e boas práticas de acessibilidade e engajamento.
---

# 🎬 OBS Studio Stream Producer - Guia de Produção e Melhores Práticas

Este guia fornece orientações para engenharia e produção de transmissões ao vivo com o OBS Studio integrando aplicações web, fontes de navegador e dashboards interativos.

---

## 🖥️ Configuração Ideal de Browser Sources no OBS

Para obter 60 FPS estáveis e transparência perfeita:

1. **Aceleração de Hardware:**
   - Em *Configurações > Avançado > Fontes*, mantenha **"Habilitar aceleração de hardware no navegador"** marcado.
2. **Resoluções Recomendadas por Fonte:**
   - **Palco Composto (`/stage`):** `1920x1080` (60 FPS).
   - **Grid de Câmeras (`/cameras/grid`):** `1920x600` ou `1920x1080`.
   - **Janela Dedicada de Libras (`/libras`):** `480x270` ou `560x315`.
   - **Enquetes (`/poll`):** `1920x1080` (fica ancorado no canto inferior direito).
   - **Chat Feed (`/chat`):** `450x800`.
   - **Lower-Third / Destaque (`/chat/featured`):** `1920x250`.

---

## 🎛️ Configuração do Dock no OBS Studio

1. No menu superior do OBS Studio, clique em **Painéis (Docks) > Painéis com Navegador Personalizados...**
2. Crie um novo painel:
   - **Nome do Painel:** `RNP Bridge Control`
   - **URL:** `http://localhost:3333/control`
3. Clique em **Aplicar** e arraste a janela flutuante para encaixar na barra lateral ou inferior do OBS.

---

## 🎧 Roteamento de Áudio

- O master de áudio da conferência sai **apenas** pela Browser Source do `/stage`: marque **"Controlar áudio via OBS"** nessa fonte e deixe **desmarcado** nas demais (`/libras`, `/cameras/grid`, `/presentation`, `/chat`). Duas fontes com áudio ligado geram eco.
- Na mesma fonte do `/stage`, **desligue** "Shutdown source when not visible". Como o áudio é acoplado ao palco, essa opção ligada suspende a página e derruba o som em cenas sem o palco.
- Assim, o canal de som da conferência RNP aparecerá diretamente no **Mixer de Áudio do OBS**, permitindo aplicar filtros de ganho, compressor e monitoramento de fone de ouvido.
- Precisa de uma trilha separada por pessoa (ex.: gravar só o palestrante)? Use os links individuais do rack de áudio no `/control` (`/audio?source=<canal>` → receptor WebRTC).
