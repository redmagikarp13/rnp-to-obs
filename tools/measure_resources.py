"""Matriz de custo de CPU por recurso consumido da ponte.

Para cada endpoint que um OBS/browser-source pode consumir, mede com 2 consumidores
simultâneos (árvore Edge do perfil measure_profile) o delta NA PONTE e o custo no
CLIENTE, intercalando janelas de re-baseline (páginas em about:blank) para cancelar
a deriva de atividade da sala ao longo dos minutos.

Ponte = python server.py do workspace + árvore Edge headless da ponte.
"""
import asyncio
import sys
import time

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import psutil
from playwright.async_api import async_playwright

BASE = 'http://127.0.0.1:3333'
JANELA_REC = 15          # segundos com consumidores
JANELA_BASE = 8          # segundos de re-baseline (blank)
N_CONS = 3
REPETICOES = 2

RECURSOS = [
    ('/stage (palco composto, sem áudio local)', '/stage?audio=0', None),
    ('/presentation (slides puros)', '/presentation', None),
    ('/cameras/grid (grid de webcams)', '/cameras/grid?audio=0', None),
    ('/control (dock, previews ON)', '/control', None),
    ('/control (dock, previews OFF)', '/control',
     'localStorage.setItem("rnpDockPreviewsOff", JSON.stringify({cameras:true,presentation:true}))'),
    ('/chat (feed unificado)', '/chat', None),
]


async def videos_vivos(pg):
    """True se algum <video> está de fato decodificando (frames crescem em 1s)."""
    try:
        return await pg.evaluate("""async () => {
            const vs = [...document.querySelectorAll('video')];
            if (!vs.length) return false;
            const q0 = vs.map(v => v.getVideoPlaybackQuality ? v.getVideoPlaybackQuality().totalVideoFrames : -1);
            await new Promise(r => setTimeout(r, 1000));
            return vs.some((v, i) => {
                const q = v.getVideoPlaybackQuality ? v.getVideoPlaybackQuality().totalVideoFrames : -1;
                return q > q0[i] && q0[i] >= 0;
            });
        }""")
    except Exception:
        return False


def eh_ponte(cmd):
    c = ' '.join(cmd).lower()
    if 'server.py' in c and ('rnptoobs' in c or 'c:\\python314' in c):
        return True
    return 'msedge' in c and ('--headless' in c or 'remote-debugging-pipe' in c)


def eh_consumidor(cmd):
    return 'measure_profile' in ' '.join(cmd).lower()


def pids_de(pred):
    out = {}
    for p in psutil.process_iter(['pid', 'cmdline']):
        try:
            cmd = p.info['cmdline'] or []
            if cmd and pred(cmd):
                out[p.info['pid']] = p
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return out


def _cpu(p):
    try:
        return p.cpu_percent(None)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return 0.0


def aquecer():
    for p in list(pids_de(eh_ponte).values()) + list(pids_de(eh_consumidor).values()):
        _cpu(p)


def amostrar(segundos):
    sp = sc = n = 0.0
    for _ in range(segundos):
        time.sleep(1.0)
        sp += sum(_cpu(p) for p in pids_de(eh_ponte).values())
        sc += sum(_cpu(p) for p in pids_de(eh_consumidor).values())
        n += 1
    return sp / n, sc / n


async def main():
    if not pids_de(eh_ponte):
        print('ERRO: ponte nao detectada.'); sys.exit(1)

    # Estado da sala ANTES de medir: sem moderador não há trilhas e qualquer
    # número de vídeo seria mentira (a 2ª rodada desta matriz morreu exatamente
    # assim — sala fechada no meio da coleta).
    import json as _json
    import urllib.request
    try:
        with urllib.request.urlopen(BASE + '/api/status', timeout=5) as r:
            st = _json.load(r)
        midia = bool(st.get('cameras')) or bool(st.get('presentation')) or bool(st.get('youtube'))
        print(f'sala: connected={st.get("connected")} cameras={len(st.get("cameras") or [])} '
              f'presentation={bool(st.get("presentation"))} youtube={bool(st.get("youtube"))}')
        if not st.get('connected') or not midia:
            print('ABORTANDO: sala fechada ou sem mídia viva — abra a sala (moderador) '
                  'com câmera/apresentação ativa e rode de novo.')
            sys.exit(2)
    except Exception as e:
        print(f'AVISO: sem status da sala ({e}); medindo às cegas.')

    async with async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(
            user_data_dir='.test_runtime/measure_profile',
            channel='msedge', headless=True,
            args=['--autoplay-policy=no-user-gesture-required'])
        paginas = [ctx.pages[0] if ctx.pages else await ctx.new_page()]
        while len(paginas) < N_CONS:
            paginas.append(await ctx.new_page())
        for pg in paginas:
            await pg.goto('about:blank')
        await paginas[0].wait_for_timeout(2000)
        aquecer()

        print(f'{"recurso":42s} {"ponte d/consum":>16s} {"cliente/consum":>16s}  amostras')
        for rotulo, caminho, init in RECURSOS:
            dps, dcs = [], []
            for _ in range(REPETICOES):
                base_p, _ = amostrar(JANELA_BASE)
                for pg in paginas:
                    if init:
                        await pg.add_init_script(init)
                    await pg.goto(BASE + caminho, wait_until='domcontentloaded')
                await paginas[0].wait_for_timeout(5000)
                vivos = sum([1 for pg in paginas if await videos_vivos(pg)])
                if caminho in ('/chat',) or vivos:
                    aquecer()
                    rec_p, rec_c = amostrar(JANELA_REC)
                    dps.append((rec_p - base_p) / N_CONS)
                    dcs.append(rec_c / N_CONS)
                else:
                    print(f'{rotulo:42s} {"SEM MIDIA VIVA":>16s} {"":>16s}  0/{N_CONS} videos')
                for pg in paginas:
                    await pg.goto('about:blank')
                await paginas[0].wait_for_timeout(2000)
            if dps:
                dp = sum(dps) / len(dps)
                dc = sum(dcs) / len(dcs)
                print(f'{rotulo:42s} {dp:+14.1f}% {dc:+14.1f}%  {len(dps)}/{REPETICOES}')
        await ctx.close()


asyncio.run(main())
