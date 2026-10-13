import asyncio
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
import server


class MediaRoutesTests(unittest.TestCase):
    def setUp(self):
        # Isola a persistência: várias rotas chamam save_disk_config, e sem isto
        # elas gravariam no config.json real (produção) a cada teste.
        self._cfg_dir = tempfile.TemporaryDirectory()
        self.cfg_patch = patch.object(server, 'CONFIG_FILE',
                                      os.path.join(self._cfg_dir.name, 'config.json'))
        self.cfg_patch.start()
        self.state = server.BridgeState()
        self.state.connected = False
        self.patch = patch.object(server, 'state', self.state)
        self.patch.start()
        self.client = TestClient(server.app)

    def tearDown(self):
        self.patch.stop()
        self.cfg_patch.stop()
        self._cfg_dir.cleanup()

    def test_receptor_experimental_disponivel_sem_sala(self):
        response = self.client.get('/webrtc')
        self.assertEqual(response.status_code, 200)
        self.assertIn('text/html', response.headers['content-type'])

    def test_sem_sala_nao_inventa_fontes(self):
        response = self.client.get('/api/media')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['sources'], [])

    def test_sinalizacao_rejeita_sdp_sem_oferta(self):
        response = self.client.post('/api/webrtc/offer', json={'source_id': 'cam-a'})
        self.assertEqual(response.status_code, 422)

    def test_sinalizacao_nao_aceita_origem_externa(self):
        response = self.client.post('/api/webrtc/offer',
            headers={'Origin': 'https://site-externo.example'},
            json={'source_id': 'cam-a', 'kind': 'video', 'sdp': 'v=0\r\n', 'type': 'offer'})
        self.assertEqual(response.status_code, 403)

    def test_audio_individual_nunca_cai_no_master(self):
        response = self.client.get('/audio?source=pessoa-a', follow_redirects=False)
        self.assertEqual(response.status_code, 307)
        self.assertIn('/webrtc?', response.headers['location'])
        self.assertIn('kind=audio', response.headers['location'])

    def test_segundo_connect_cancela_o_anterior(self):
        started, cancelled = [], []

        async def fake_run(room_url, user_name, user_email):
            started.append(room_url)
            try:
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                cancelled.append(room_url)
                raise

        async def scenario():
            with patch.object(server, 'run_connection', fake_run):
                await server.start_connection('https://a', 'u', 'e')
                await asyncio.sleep(.05)
                await server.start_connection('https://b', 'u', 'e')
                await asyncio.sleep(.05)
                # Snapshot antes de o loop fechar (o teardown cancelaria 'b' e poluiria a leitura).
                return list(started), list(cancelled), server.state.connection_task.done()

        started, cancelled, second_done = asyncio.run(scenario())
        self.assertEqual(started, ['https://a', 'https://b'])
        self.assertEqual(cancelled, ['https://a'], 'Um connect novo deve cancelar a tentativa anterior, não sobrepor.')
        self.assertFalse(second_done, 'A segunda tentativa deve seguir ativa.')


    def test_salvar_config_nao_escreve_no_arquivo_de_producao(self):
        # Várias rotas persistem config. Se CONFIG_FILE não for isolado nos testes,
        # cada rodada da suíte sobrescreve o config.json real da produção
        # (tema, Libras, formato de câmera...), corrompendo o ambiente do operador.
        real = Path(server.__file__).resolve().parent / 'config.json'
        antes = real.read_bytes() if real.exists() else None
        response = self.client.post('/api/grid/config', json={'camera_format': 'quadrada'})
        self.assertEqual(response.status_code, 200)
        depois = real.read_bytes() if real.exists() else None
        self.assertEqual(depois, antes,
                         'A suíte não pode sobrescrever o config.json de produção.')

    def test_audio_sem_source_nao_serve_mais_o_master(self):
        # O master passou a vir do /stage (PCM_AUDIO_SCRIPT); servir um segundo
        # player PCM em /audio duplicava consumo no OBS sem necessidade.
        response = self.client.get('/audio', follow_redirects=False)
        self.assertEqual(response.status_code, 404,
                         '/audio sem source não deve mais servir o player master.')

    def test_dock_nao_oferece_link_do_master_de_audio(self):
        response = self.client.get('/control')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("copyUrl('/audio'", response.text,
                         'O dock não deve mais oferecer o master de /audio; o master vem do /stage.')

    def test_tema_padrao_e_broadcast(self):
        self.assertEqual(server.DEFAULT_CONFIG['control_theme'], 'broadcast')

    def test_temas_aposentados_migram_para_broadcast(self):
        for antigo in ('hardware', 'nordic', 'glass', 'obsidian', '', None, 'inexistente', 123):
            self.assertEqual(server.normalize_theme(antigo), 'broadcast',
                             f'{antigo!r} deveria migrar para broadcast')

    def test_temas_validos_permanecem(self):
        self.assertEqual(server.normalize_theme('ifes'), 'ifes')
        self.assertEqual(server.normalize_theme('broadcast'), 'broadcast')

    def test_estado_normaliza_tema_vindo_do_disco(self):
        with patch.object(server, 'load_disk_config', lambda: {**server.DEFAULT_CONFIG, 'control_theme': 'glass'}):
            st = server.BridgeState()
        self.assertEqual(st.control_theme, 'broadcast',
                         'Config com tema aposentado não pode subir com valor inválido.')

    def test_api_theme_set_aceita_os_dois_temas(self):
        for tema in ('broadcast', 'ifes'):
            response = self.client.post('/api/theme/set', json={'theme': tema})
            self.assertEqual(response.status_code, 200, f'/api/theme/set deveria aceitar {tema}.')
            self.assertEqual(response.json()['control_theme'], tema)
            self.assertEqual(self.state.control_theme, tema)

    def test_api_theme_set_rejeita_tema_aposentado(self):
        # A rota validava contra a lista antiga; sem corrigir, aceitaria `glass`
        # e rejeitaria `broadcast` — o oposto do pretendido.
        response = self.client.post('/api/theme/set', json={'theme': 'glass'})
        self.assertEqual(response.status_code, 400,
                         'Tema aposentado precisa ser rejeitado por /api/theme/set.')

    def test_assets_do_dock_servidos_com_no_store(self):
        css = self.client.get('/assets/dock.css')
        self.assertEqual(css.status_code, 200, 'web/dock.css precisa ser servido como asset.')
        self.assertIn('text/css', css.headers['content-type'])
        self.assertEqual(css.headers['cache-control'], 'no-store',
                         'O dock é recarregado dentro do OBS; não pode cachear.')
        js = self.client.get('/assets/dock.js')
        self.assertEqual(js.status_code, 200, 'web/dock.js precisa ser servido como asset.')
        self.assertIn('javascript', js.headers['content-type'])
        self.assertEqual(js.headers['cache-control'], 'no-store')

    def test_dock_referencia_os_assets(self):
        html = self.client.get('/control').text
        self.assertIn('/assets/dock.css', html)
        self.assertIn('/assets/dock.js', html)

    def test_webrtc_e_modo_padrao_com_opt_out_explicito(self):
        # A produção sobe por start.bat / app.pyw / .exe sem variável de ambiente,
        # então o modo validado (WebRTC) precisa ser o padrão — com opt-out explícito.
        with patch.dict(os.environ):
            os.environ.pop('RNP_WEBRTC_ONLY', None)
            self.assertTrue(server.BridgeState().webrtc_only,
                            'Sem RNP_WEBRTC_ONLY a ponte deve subir em modo WebRTC (padrão de produção).')
        with patch.dict(os.environ, {'RNP_WEBRTC_ONLY': '0'}):
            self.assertFalse(server.BridgeState().webrtc_only,
                             'RNP_WEBRTC_ONLY=0 deve manter disponível o caminho legado (JPEG).')
        with patch.dict(os.environ, {'RNP_WEBRTC_ONLY': '1'}):
            self.assertTrue(server.BridgeState().webrtc_only,
                            'RNP_WEBRTC_ONLY=1 continua ligando o modo WebRTC.')


if __name__ == '__main__':
    unittest.main()
