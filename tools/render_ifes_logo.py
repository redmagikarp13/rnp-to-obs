"""Rasteriza o logo Ifes (marca 'iF' de quadrados verdes + círculo vermelho).

Geometria medida na imagem de referência (media-129736053): passo da grade ~221px,
quadrado verde ~202px, círculo vermelho ~234px (transborda a célula, daí os vãos
de ~2px). Cores medidas: verde #8CC43F, vermelho #EC1A25. Fundo transparente.

Saída: web/ifes-logo.png, desenhada com superamostragem 8x e reduzida com LANCZOS.
"""
import os
import sys

from PIL import Image, ImageDraw

sys.stdout.reconfigure(encoding='utf-8')

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

VERDE = (140, 196, 63, 255)      # #8CC43F
VERMELHO = (236, 26, 37, 255)    # #EC1A25

ALTURA_FINAL = 96                # px de destino (cristalino em displays 2x/3x)
SS = 8                           # fator de superamostragem

# Proporções relativas medidas (tudo em função do passo P).
P = 1.0
LADO_QUADRADO = 202 / 221        # 0.914
DIAMETRO_CIRCULO = 234 / 221     # 1.059
RAIO_CANTO = 0.18 * LADO_QUADRADO

# Ocupação da grade 4 linhas x 3 colunas: 'C' = círculo, 'G' = quadrado, None = vazio.
GRADE = [
    ['C', 'G', 'G'],
    ['G', 'G', None],
    ['G', 'G', 'G'],
    ['G', 'G', None],
]

LIN, COL = len(GRADE), len(GRADE[0])
transbordo = DIAMETRO_CIRCULO - LADO_QUADRADO
W_un = COL * P + transbordo
H_un = LIN * P + transbordo

# Escala: altura final de H_un unidades.
escala = ALTURA_FINAL / H_un
W = int(round(W_un * escala))
H = int(round(H_un * escala))

img = Image.new('RGBA', (W * SS, H * SS), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

o = transbordo / 2.0  # origem para centrar células


def px(v):
    return v * escala * SS


for li in range(LIN):
    for co in range(COL):
        celula = GRADE[li][co]
        if not celula:
            continue
        cx = o + P * (co + 0.5)
        cy = o + P * (li + 0.5)
        if celula == 'C':
            r = DIAMETRO_CIRCULO / 2.0
            d.ellipse([px(cx - r), px(cy - r), px(cx + r), px(cy + r)], fill=VERMELHO)
        else:
            s = LADO_QUADRADO / 2.0
            d.rounded_rectangle(
                [px(cx - s), px(cy - s), px(cx + s), px(cy + s)],
                radius=px(RAIO_CANTO), fill=VERDE)

img = img.resize((W, H), Image.LANCZOS)

dest = os.path.join(RAIZ, 'web', 'ifes-logo.png')
img.save(dest, 'PNG', optimize=True)
print(f'gravado {dest}: {W}x{H}px, {os.path.getsize(dest)} bytes')
