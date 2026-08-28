---
name: frontend-design-system
description: >-
  Guia avançado de engenharia de Front-End, Design Systems modernos, estética visual premium,
  Glassmorphism, Neon Glow, micro-animações CSS, tipografia profissional e interfaces ultra-responsivas.
---

# 🎨 Front-End & Modern Design System - Guia e Padrões de Excelência

Este guia estabelece os padrões visuais, tokens de design, técnicas de estilização CSS e micro-interações para construir interfaces modernas, atraentes e de alta performance.

---

## 🌟 1. Princípios de Design Visual Premium

1. **Paleta de Cores Curada (Evitar cores genéricas):**
   - **Backgrounds Profundos:** `#0b1120` (Dark Slate), `#030712` (Onyx Black), `#0f172a` (Deep Navy).
   - **Acentos Vibrantes:** 
     - Cyan / Neon Sky: `#38bdf8`, `#06b6d4`, `#22d3ee`
     - Purple / Violet: `#a855f7`, `#c084fc`, `#8b5cf6`
     - Emerald / Success: `#10b981`, `#34d399`
     - Amber / Highlight: `#eab308`, `#f59e0b`
     - Coral / Alert: `#ef4444`, `#f87171`
2. **Tipografia Moderna (Google Fonts):**
   - **Primária:** `Outfit`, `Inter` ou `Plus Jakarta Sans` (`font-family: 'Outfit', sans-serif;`).
   - **Mono / Código / Métricas:** `Fira Code`, `JetBrains Mono`.
3. **Efeito Glassmorphism (Vidro Fosco):**
   ```css
   background: rgba(15, 23, 42, 0.85);
   backdrop-filter: blur(14px);
   -webkit-backdrop-filter: blur(14px);
   border: 1px solid rgba(255, 255, 255, 0.12);
   box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4);
   border-radius: 12px;
   ```
4. **Neon Glow & Iluminação Perimetral:**
   ```css
   border: 1.5px solid #06b6d4;
   box-shadow: 0 0 16px rgba(6, 182, 212, 0.35), inset 0 0 8px rgba(6, 182, 212, 0.15);
   ```

---

## ✨ 2. Micro-Animações e Curvas de Transição

Use sempre curvas com inércia moderna (`cubic-bezier`):

```css
/* Entrada suave estilo Broadcast */
@keyframes slideInUp {
    from {
        opacity: 0;
        transform: translateY(20px) scale(0.96);
    }
    to {
        opacity: 1;
        transform: translateY(0) scale(1);
    }
}

.card-animated {
    animation: slideInUp 0.35s cubic-bezier(0.16, 1, 0.3, 1) forwards;
}

/* Transições fluidas em botões e inputs */
.btn-interactive {
    transition: all 0.2s cubic-bezier(0.16, 1, 0.3, 1);
}
.btn-interactive:hover {
    transform: translateY(-1px);
    box-shadow: 0 4px 12px rgba(56, 189, 248, 0.35);
}
```

---

## 📱 3. Responsividade Ultra-Compacta (Docks & Painéis Laterais)

Regras essenciais para interfaces que precisam funcionar em larguras de `280px` a `380px` (como Docks do OBS ou sidebars):

1. **Scrollbar Ultra-Fina:**
   ```css
   ::-webkit-scrollbar { width: 5px; height: 5px; }
   ::-webkit-scrollbar-track { background: #030712; }
   ::-webkit-scrollbar-thumb { background: #1e293b; border-radius: 3px; }
   ::-webkit-scrollbar-thumb:hover { background: #38bdf8; }
   ```
2. **Layouts Fluidos com Grid Auto-Fit:**
   ```css
   display: grid;
   grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
   gap: 8px;
   ```
3. **Evitar Quebra de Layout em Textos:**
   ```css
   word-break: break-word;
   overflow-wrap: anywhere;
   ```
