"""Fan-out do áudio PCM: o loop de recepção da captura não pode morrer em silêncio.

Motivo deste arquivo: durante o diagnóstico (docs/AUDIO_ESTABILIDADE_2026-11.md §6)
uma linha de instrumentação usou `len(queue)` num `asyncio.Queue` (que não tem
`__len__`; o certo é `qsize()`). O `TypeError` caía no `except (WebSocketDisconnect,
Exception): pass` de `/ws/pcm_in`, que fechava o socket da captura sem deixar rastro;
a página reconectava no backoff de 2 s e o áudio inteiro descia para ~0,5 Hz — o
mesmo retrato de "trava e para" que o operador ouve, e invisível no /api/status.

Os testes abaixo travam as duas pontas: a contagem por fila e o transporte real de
um chunk do /ws/pcm_in até um ouvinte do /ws/pcm_out.
"""
import asyncio
import threading
import unittest

import server
from fastapi.testclient import TestClient


class EnqueueLatestTestes(unittest.TestCase):
    """`_enqueue_latest` é chamado no caminho quente do /ws/pcm_in: qualquer exceção
    aqui derruba a captura inteira."""

    def test_funciona_com_asyncio_queue_real(self):
        fila = asyncio.Queue(maxsize=2)
        stats = server._novo_stats_cliente()
        for i in range(2):
            server._enqueue_latest(fila, ('bin', f'x{i}'.encode()), stats)
        self.assertEqual(fila.qsize(), 2)
        self.assertEqual(stats['drops'], 0)
        self.assertEqual(stats['fila_max'], 2)

    def test_descarta_o_mais_antigo_e_conta_sem_estourar(self):
        fila = asyncio.Queue(maxsize=2)
        stats = server._novo_stats_cliente()
        for i in range(5):
            server._enqueue_latest(fila, ('bin', str(i).encode()), stats)
        # Fila continua no teto, com os itens mais recentes, e cada estouro é contado.
        self.assertEqual(fila.qsize(), 2)
        conteudo = [fila.get_nowait()[1] for _ in range(fila.qsize())]
        self.assertEqual(conteudo, [b'3', b'4'])
        self.assertEqual(stats['drops'], 3)

    def test_sem_stats_nao_quebra_chamada_antiga(self):
        fila = asyncio.Queue(maxsize=1)
        server._enqueue_latest(fila, ('txt', 'a'))
        server._enqueue_latest(fila, ('txt', 'b'))  # cheia: drop-oldest, sem exceção
        self.assertEqual(fila.get_nowait(), ('txt', 'b'))


class FanOutPcmTestes(unittest.TestCase):
    """Um chunk publicado na sala tem de sair para o ouvinte — e o socket da captura
    tem de continuar vivo depois disso.

    Asserta no que o servidor REGISTRA (enviados por ouvinte + erro capturado), nao
    num receive() bloqueante: o TestClient desta versao do Starlette nao tem timeout
    de recebimento, entao um receive() com o fan-out quebrado PENDURA o teste em vez
    de falhar — exatamente o caso que este arquivo existe para pegar.
    """

    def setUp(self):
        server.state.pcm_erro = None

    def test_chunk_chega_ao_ouvinte_sem_derrubar_a_captura(self):
        payload = b'\x00\x00\x00\x00' * 2048   # Float32 de 2048 amostras, como o real
        obtido = {}

        def troca():
            # O send do app so completa quando o cliente drena o socket: da para
            # fugir do receive? nao. entao ele roda enrolado num thread com join
            # limitado, para o teste FALHAR por tempo esgotado em vez de pendurar.
            cliente = TestClient(server.app)
            with cliente.websocket_connect('/ws/pcm_out') as ouvinte:
                with cliente.websocket_connect('/ws/pcm_in') as captura:
                    captura.send_bytes(payload)
                    captura.send_bytes(payload)
                    obtido['m1'] = ouvinte.receive_bytes()
                    obtido['m2'] = ouvinte.receive_bytes()

        execucao = threading.Thread(target=troca, daemon=True)
        execucao.start()
        execucao.join(8)
        self.assertFalse(
            execucao.is_alive(),
            'o ouvinte nao recebeu os 2 chunks em 8s: o /ws/pcm_in caiu e a captura '
            f'reconectaria a cada 2 s (audio a ~0,5 Hz). erro_pcm_in={server.state.pcm_erro}')
        self.assertEqual(
            server.state.pcm_erro, None,
            f'o fan-out lanco exceao e morreu em silencio: {server.state.pcm_erro}')
        self.assertEqual(len(obtido.get('m1', b'')), 2048 * 4)
        self.assertEqual(len(obtido.get('m2', b'')), 2048 * 4)

    def test_diag_expose_chegada_sem_esconder_erro(self):
        cliente = TestClient(server.app)
        status = cliente.get('/api/status').json()
        diag = status['pcm_diag']
        # 'erro_pcm_in' é o registro do que derrubaria o socket antes de virar
        # 'except: pass'; sem ele o pipeline degradado fica invisível.
        self.assertIn('erro_pcm_in', diag)
        self.assertEqual(diag['esperado_hz'], round(48000 / 2048, 1))
        for campo in ('chegada_pcm_in', 'event_loop_atraso_ms', 'captura', 'ouvintes'):
            self.assertIn(campo, diag)

    def test_captura_pcm_usa_web_worker_e_message_channel(self):
        with open('web/media-source.js', encoding='utf-8') as f:
            src = f.read()
        self.assertIn('new MessageChannel()', src)
        self.assertIn('new Worker(', src)
        self.assertIn('workerChan.port1', src)
        self.assertIn('workerChan.port2', src)

    def test_player_pcm_desconecta_buffer_source_ao_terminar(self):
        self.assertIn('bufferSource.onended', server.PCM_AUDIO_SCRIPT)
        self.assertIn('bufferSource.disconnect()', server.PCM_AUDIO_SCRIPT)


if __name__ == '__main__':
    unittest.main()
