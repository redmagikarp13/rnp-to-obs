"""Confinar a ponte RNP->OBS aos nucleos de eficiencia (E-cores) para simular um
notebook fraco e validar que ela roda bem com poucos recursos.

Ideia: num CPU hibrido (Intel 12a-14a geracao), os E-cores sao bem mais fracos que
os P-cores. Se a ponte (python server.py + a arvore Edge headless que decodifica as
cameras da sala) sustentar video + audio presa a POUCOS E-cores, ela tera folga num
notebook de evento. A afinidade e herdada pelos processos-filho no Windows, e este
script a reaplica a arvore inteira a cada instante para pegar os processos que o
Playwright/Edge vao nascendo.

Uso:
    python tools/rodar_em_ecores.py --topology          # so mostra P-cores/E-cores
    python tools/rodar_em_ecores.py                     # sobe a ponte em 4 E-cores
    python tools/rodar_em_ecores.py --cores 2 --measure # 2 E-cores + saude ao vivo
    python tools/rodar_em_ecores.py --port 3399         # porta dedicada p/ o teste

Depois conecte a sala em http://127.0.0.1:<porta>/control e observe: no modo
Economico (botao raio) vs Cheio, o FPS das cameras e a saude do audio. Se o FPS cair
ou o audio picotar com a ponte presa aos E-cores, ative o modo Economico.

Observacao: isto confina SO a ponte. O navegador do operador (/control) e o OBS nao
sao filhos dela e continuam nos P-cores -- num notebook real tudo divide a mesma CPU,
entao trate o resultado como o piso da ponte, nao como o cenario inteiro.
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request

import psutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def topology():
    """Deduz P-cores e E-cores. Em Intel hibrido so os P-cores tem HyperThreading,
    entao: P_fisicos = logicos - fisicos; E_fisicos = fisicos - P_fisicos; e os
    E-cores ocupam os indices logicos finais. Em CPU sem E-cores (ex.: AMD), E=0."""
    fisicos = psutil.cpu_count(logical=False) or 1
    logicos = psutil.cpu_count(logical=True) or 1
    p_fisicos = max(0, logicos - fisicos)
    e_fisicos = max(0, fisicos - p_fisicos)
    p_logicos = logicos - e_fisicos
    e_indices = list(range(p_logicos, logicos))
    return {
        'fisicos': fisicos, 'logicos': logicos,
        'p_fisicos': p_fisicos, 'e_fisicos': e_fisicos,
        'p_indices': list(range(p_logicos)), 'e_indices': e_indices,
    }


def escolher_indices(topo, cores):
    """Indices logicos a usar: os E-cores; se nao houver E-cores, cai nos primeiros
    `cores` logicos (simula maquina fraca mesmo sem nucleo de eficiencia)."""
    base = topo['e_indices'] or list(range(topo['logicos']))
    if cores and cores > 0:
        return base[:cores]
    return base


def aplicar_arvore(proc, indices):
    """Aplica a afinidade ao processo e a todos os descendentes atuais."""
    alvos = [proc]
    try:
        alvos += proc.children(recursive=True)
    except Exception:
        pass
    n = 0
    for p in alvos:
        try:
            p.cpu_affinity(indices)
            n += 1
        except Exception:
            pass
    return n


def ler_status(porta):
    url = f'http://127.0.0.1:{porta}/api/status'
    try:
        with urllib.request.urlopen(url, timeout=2) as resp:
            return json.loads(resp.read().decode('utf-8', 'replace'))
    except Exception:
        return None


def resumir_status(st):
    if not st:
        return 'status indisponivel (ponte ainda subindo?)'
    if not st.get('connected'):
        return 'ponte no ar, sala NAO conectada'
    cams = st.get('cameras', [])
    fps = [c.get('fps') for c in cams if isinstance(c.get('fps'), (int, float))]
    fps_med = round(sum(fps) / len(fps), 1) if fps else None
    diag = st.get('pcm_diag', {}) or {}
    chegada = diag.get('chegada_pcm_in', {}) or {}
    gaps = chegada.get('gaps_gt_60ms')
    lag = diag.get('event_loop_atraso_ms')
    modo = st.get('performance_mode', '?')
    partes = [f"modo={modo}", f"cams={len(cams)}", f"fps_med={fps_med}"]
    if gaps is not None:
        partes.append(f"gaps_audio>{gaps}")
    if lag is not None:
        partes.append(f"loop_lag_ms={lag}")
    return ' | '.join(str(p) for p in partes)


def main():
    ap = argparse.ArgumentParser(description='Confina a ponte aos E-cores.')
    ap.add_argument('--topology', action='store_true', help='so imprime P/E-cores e sai')
    ap.add_argument('--cores', type=int, default=4,
                    help='quantos E-cores usar (0 = todos). Padrao 4 (proxy de notebook fraco).')
    ap.add_argument('--port', type=int, default=int(os.environ.get('RNP_PORT', '3333')),
                    help='porta da ponte (RNP_PORT). Use uma dedicada para o teste.')
    ap.add_argument('--measure', action='store_true',
                    help='consulta /api/status a cada 3s e imprime a saude (fps/audio)')
    ap.add_argument('--cmd', default=None,
                    help='comando alternativo para subir a ponte (padrao: python server.py)')
    args = ap.parse_args()

    topo = topology()
    print(f"CPU: {topo['fisicos']} fisicos / {topo['logicos']} logicos")
    print(f"  P-cores: {topo['p_fisicos']} (logicos {topo['p_indices'][0] if topo['p_indices'] else '-'}..{topo['p_indices'][-1] if topo['p_indices'] else '-'})")
    print(f"  E-cores: {topo['e_fisicos']} (logicos {topo['e_indices'][0] if topo['e_indices'] else '-'}..{topo['e_indices'][-1] if topo['e_indices'] else '-'})")
    if args.topology:
        return 0
    if not topo['e_indices']:
        print('  (sem E-cores detectados: vou restringir aos primeiros logicos para simular maquina fraca)')

    indices = escolher_indices(topo, args.cores)
    if not indices:
        print('[ERRO] Nenhum nucleo para confinar.')
        return 2
    print(f'Confinando a ponte aos nucleos logicos {indices} '
          f'({len(indices)} {"E-core(s)" if topo["e_indices"] else "logico(s)"}).')

    if args.cmd:
        cmd = args.cmd
    else:
        cmd = [sys.executable, os.path.join(ROOT, 'server.py')]
    env = dict(os.environ)
    env['RNP_PORT'] = str(args.port)
    print('Subindo:', ' '.join(cmd) if isinstance(cmd, list) else cmd)
    proc = subprocess.Popen(cmd, cwd=ROOT, env=env)
    ps = psutil.Process(proc.pid)

    # Aplica imediatamente (filhos herdam a mascara) e reaplica para pegar novos filhos.
    aplicar_arvore(ps, indices)
    print(f'Ponte em http://127.0.0.1:{args.port}/control  (Ctrl+C para encerrar)')
    ultima_medida = 0.0
    try:
        while proc.poll() is None:
            time.sleep(1.0)
            n = aplicar_arvore(ps, indices)
            if args.measure and (time.time() - ultima_medida) >= 3.0:
                ultima_medida = time.time()
                print(f'  [arvore={n} proc] {resumir_status(ler_status(args.port))}')
    except KeyboardInterrupt:
        print('\nEncerrando a ponte...')
    finally:
        try:
            for p in [ps] + ps.children(recursive=True):
                try:
                    p.terminate()
                except Exception:
                    pass
            proc.wait(timeout=8)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    return 0


if __name__ == '__main__':
    sys.exit(main())
