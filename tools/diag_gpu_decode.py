"""Diagnóstico de decodificação de vídeo por GPU no navegador de captura.

Sobe o navegador EXATAMENTE como a produção (mesmos launch args e mesma ordem de
canais de server.py), faz um loopback WebRTC local (canvas -> sender -> receiver),
deixa decodificar alguns frames e lê o `decoderImplementation` do inbound-rtp de
vídeo via getStats(). Isso revela se o Edge headless está decodificando vídeo por
HARDWARE (GPU / D3D11VA / Media Foundation / NVDEC) ou por SOFTWARE (FFmpeg), que é
o maior componente do "piso" de CPU da ponte em notebooks.

Uso (rodar no notebook de evento, de preferência com a GPU que será usada):
    python tools/diag_gpu_decode.py
    python tools/diag_gpu_decode.py --channel msedge

Interpretação:
    HARDWARE -> o piso de decode já está na GPU; foque no modo econômico/encode.
    SOFTWARE -> o decode compete por CPU; revisar args/driver/GPU usada pelo Edge.
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from playwright.async_api import async_playwright  # noqa: E402

import server  # noqa: E402


# Loopback WebRTC local: não entra em sala real, apenas exercita o caminho de
# decode do próprio navegador para ler o decoderImplementation.
_PROBE_JS = """async () => {
    const canvas = document.createElement('canvas');
    canvas.width = 640; canvas.height = 360;
    const ctx = canvas.getContext('2d');
    let n = 0;
    const paint = setInterval(() => { ctx.fillStyle = n++ % 2 ? '#f00' : '#00f'; ctx.fillRect(0, 0, 640, 360); }, 40);
    const track = canvas.captureStream(30).getVideoTracks()[0];
    const sender = new RTCPeerConnection({iceServers: []});
    const receiver = new RTCPeerConnection({iceServers: []});
    sender.onicecandidate = e => { if (e.candidate) receiver.addIceCandidate(e.candidate); };
    receiver.onicecandidate = e => { if (e.candidate) sender.addIceCandidate(e.candidate); };
    receiver.ontrack = e => {
        const v = document.createElement('video');
        v.srcObject = e.streams[0]; v.autoplay = true; v.muted = true;
        document.body.appendChild(v);
    };
    sender.addTrack(track, new MediaStream([track]));
    await sender.setLocalDescription(await sender.createOffer());
    await receiver.setRemoteDescription(sender.localDescription);
    await receiver.setLocalDescription(await receiver.createAnswer());
    await sender.setRemoteDescription(receiver.localDescription);
    await new Promise(r => setTimeout(r, 3500));
    const stats = await receiver.getStats();
    let inbound = null;
    stats.forEach(s => {
        if (s.type === 'inbound-rtp' && s.kind === 'video') {
            inbound = {
                decoderImplementation: s.decoderImplementation || null,
                powerEfficientDecoder: (typeof s.powerEfficientDecoder === 'boolean') ? s.powerEfficientDecoder : null,
                framesDecoded: s.framesDecoded || 0,
                frameWidth: s.frameWidth || null,
                frameHeight: s.frameHeight || null,
            };
        }
    });
    let outbound = null;
    try {
        const sstats = await sender.getStats();
        sstats.forEach(s => {
            if (s.type === 'outbound-rtp' && s.kind === 'video') {
                outbound = {
                    encoderImplementation: s.encoderImplementation || null,
                    powerEfficientEncoder: (typeof s.powerEfficientEncoder === 'boolean') ? s.powerEfficientEncoder : null,
                    framesEncoded: s.framesEncoded || 0,
                };
            }
        });
    } catch (e) {}
    clearInterval(paint);
    try { sender.close(); } catch (e) {}
    try { receiver.close(); } catch (e) {}
    try { track.stop(); } catch (e) {}
    return {inbound, outbound};
}"""

_GPU_JS = """() => {
    try {
        const c = document.createElement('canvas');
        const gl = c.getContext('webgl') || c.getContext('experimental-webgl');
        if (!gl) return {renderer: null, vendor: null};
        const dbg = gl.getExtension('WEBGL_debug_renderer_info');
        return {
            renderer: dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER),
            vendor: dbg ? gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL) : gl.getParameter(gl.VENDOR),
        };
    } catch (e) { return {renderer: null, vendor: null}; }
}"""

_HW_TOKENS = ('d3d11', 'dxva', 'media foundation', 'mediafoundation', 'nvdec', 'hardware', 'accelerat')
_SW_TOKENS = ('ffmpeg',)


def _classificar(impl, power_efficient):
    """HARDWARE/SOFTWARE/DESCONHECIDO a partir do nome do decoder e do flag
    powerEfficientDecoder (presente em Chromium recente mesmo quando o nome vazio)."""
    texto = (impl or '').lower()
    if texto:
        if any(t in texto for t in _HW_TOKENS):
            return 'HARDWARE'
        if any(t in texto for t in _SW_TOKENS):
            return 'SOFTWARE'
    if power_efficient is True:
        return 'HARDWARE'
    if power_efficient is False:
        return 'SOFTWARE'
    return 'DESCONHECIDO'


async def _rodar(channel_arg):
    canais = [channel_arg] if channel_arg else server.browser_channels()
    async with async_playwright() as pw:
        navegador = None
        canal_usado = None
        ultimo_erro = None
        for ch in canais:
            try:
                if ch:
                    navegador = await pw.chromium.launch(headless=True, channel=ch,
                                                         args=server.BROWSER_LAUNCH_ARGS)
                else:
                    navegador = await pw.chromium.launch(headless=True,
                                                         args=server.BROWSER_LAUNCH_ARGS)
                canal_usado = ch or 'chrome-headless-shell'
                break
            except Exception as erro:  # tenta o próximo canal da ordem
                ultimo_erro = erro
                continue
        if not navegador:
            print(f'[ERRO] Nenhum navegador subiu. Último erro: {ultimo_erro}')
            return 2
        try:
            page = await navegador.new_page()
            await page.set_content('<body></body>')
            gpu = await page.evaluate(_GPU_JS)
            resultado = await page.evaluate(_PROBE_JS)
        finally:
            await navegador.close()

    print('=== Diagnóstico de decodificação de vídeo (GPU) ===')
    print(f'Canal do navegador : {canal_usado}')
    print(f'GPU (WebGL)        : {gpu.get("renderer") or "?"}  [{gpu.get("vendor") or "?"}]')
    inbound = (resultado or {}).get('inbound')
    outbound = (resultado or {}).get('outbound')
    if not inbound:
        print('inbound-video      : sem estatísticas (nenhum frame decodificado no loopback)')
        return 1
    impl = inbound.get('decoderImplementation')
    classe = _classificar(impl, inbound.get('powerEfficientDecoder'))
    print(f'decoderImplementation : {impl or "(vazio)"}')
    print(f'powerEfficientDecoder : {inbound.get("powerEfficientDecoder")}')
    print(f'framesDecoded         : {inbound.get("framesDecoded")}')
    print(f'resolução             : {inbound.get("frameWidth")}x{inbound.get("frameHeight")}')
    if outbound:
        print(f'encoderImplementation : {outbound.get("encoderImplementation") or "(vazio)"}')
        print(f'powerEfficientEncoder : {outbound.get("powerEfficientEncoder")}')
    print(f'Classificação (decode): {classe}')
    print()
    if classe == 'SOFTWARE':
        print('>> Decode em SOFTWARE: o piso de CPU da ponte inclui decodificar vídeo na CPU.')
        print('   Em notebook gamer, isso normalmente indica GPU não usada pelo Edge headless.')
        print('   Ações: atualizar driver da GPU; testar RNP_BROWSER_CHANNEL=msedge; confirmar')
        print('   que a GPU dedicada (não a integrada) é usada; revisar --use-angle=d3d11.')
    elif classe == 'HARDWARE':
        print('>> Decode em HARDWARE: a GPU já assume o decode. O piso restante é inerente à')
        print('   presença na sala; use o Modo Econômico (720p/15fps + sync 1Hz) para aliviar.')
    else:
        # Alguns builds de Edge headless não expõem decoderImplementation/powerEfficient.
        # O renderer WebGL ainda diz se o navegador enxerga uma GPU real ou um
        # rasterizador de software (SwiftShader/llvmpipe), que é o sinal dominante.
        renderer = (gpu.get('renderer') or '').lower()
        software_render = any(t in renderer for t in ('swiftshader', 'software', 'llvmpipe', 'basic render', 'microsoft basic'))
        if software_render:
            print('>> GPU NÃO usada: o navegador caiu em rasterizador de SOFTWARE (SwiftShader).')
            print('   Isso joga o decode/composição para a CPU e explode o piso em notebook.')
            print('   Ações: atualizar driver da GPU; testar RNP_BROWSER_CHANNEL=msedge; garantir que')
            print('   a GPU dedicada é a usada; revisar --use-angle=d3d11 / --ignore-gpu-blocklist.')
        elif renderer:
            print('>> GPU real detectada pelo navegador, mas este build não expôs o decoder.')
            print('   Bom sinal: com GPU D3D11 ativa o decode tende a ser por hardware. Confirme o')
            print('   ganho real medindo a CPU com tools/measure_resources.py (full vs eco).')
        else:
            print('>> Não foi possível classificar. Rode novamente e confira também com')
            print('   tools/measure_resources.py.')
    return 0


def main():
    parser = argparse.ArgumentParser(description='Diagnóstico de decode de vídeo por GPU.')
    parser.add_argument('--channel', default=None,
                        help="Canal fixo (msedge/chrome/chromium). Padrão: ordem de server.browser_channels().")
    args = parser.parse_args()
    sys.exit(asyncio.run(_rodar(args.channel)))


if __name__ == '__main__':
    main()
