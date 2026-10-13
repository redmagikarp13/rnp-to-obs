import base64
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
import server


def camera(cid, name):
    return {'id': cid, 'name': name, 'width': 320, 'height': 180,
            'participant_id': cid, 'frame': base64.b64encode(b'jpeg').decode()}


class LibrasIdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.path = Path(self.tmp.name) / 'config.json'
        self.file_patch = patch.object(server, 'CONFIG_FILE', str(self.path))
        self.file_patch.start()
        self.state = server.BridgeState()
        self.state.libras_auto_detect = False
        self.state_patch = patch.object(server, 'state', self.state)
        self.state_patch.start()
        self.client = TestClient(server.app)

    def tearDown(self):
        self.state_patch.stop()
        self.file_patch.stop()
        self.tmp.cleanup()

    def test_multicamera_libras_fica_na_publicacao_selecionada(self):
        cams = [camera('cam-a', 'Ana Silva') | {'participant_id': 'pessoa-a'},
                camera('cam-a-extra', 'Ana Silva') | {'participant_id': 'pessoa-a'}]
        self.state.reconcile_cameras(cams)
        self.state.set_libras_cameras(['cam-a'])
        self.state.reconcile_cameras(cams)
        self.assertEqual(self.state.libras_camera_ids, ['cam-a'])
        self.state.set_libras_cameras(['cam-a-extra'])
        self.state.reconcile_cameras(cams)
        self.assertEqual(self.state.libras_camera_ids, ['cam-a-extra'])
        self.assertEqual(self.state.libras_saved_names, ['Ana Silva'])
        self.assertEqual(self.state.libras_disabled_names, [])

    def test_multicamera_nova_publicacao_nao_herda_libras(self):
        primary = camera('cam-a', 'Ana Silva') | {'participant_id': 'pessoa-a'}
        secondary = camera('cam-a-extra', 'Ana Silva') | {'participant_id': 'pessoa-a'}
        self.state.reconcile_cameras([primary])
        self.state.set_libras_cameras(['cam-a'])
        self.state.reconcile_cameras([secondary, primary])
        self.assertEqual(self.state.libras_camera_ids, ['cam-a'])
        self.state.reconcile_cameras([secondary])
        self.assertEqual(self.state.libras_camera_ids, [])

    def test_multicamera_restauracao_por_nome_escolhe_uma_publicacao(self):
        self.state.libras_saved_names = ['Ana Silva']
        cams = [camera('cam-a', 'Ana Silva') | {'participant_id': 'pessoa-a'},
                camera('cam-a-extra', 'Ana Silva') | {'participant_id': 'pessoa-a'}]
        self.state.reconcile_cameras(cams)
        self.assertEqual(self.state.libras_camera_ids, ['cam-a'])
        self.state.set_libras_cameras(['cam-a', 'cam-a-extra'])
        self.state.reconcile_cameras(cams)
        self.assertEqual(self.state.libras_camera_ids, ['cam-a', 'cam-a-extra'])

    def test_camera_format_persiste_e_rejeita_invalido(self):
        response = self.client.post('/api/grid/config', json={'camera_format': 'retrato'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.state.camera_format, 'retrato')
        cfg = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual(cfg['camera_format'], 'retrato')
        self.client.post('/api/grid/config', json={'camera_format': 'banana'})
        self.assertEqual(self.state.camera_format, 'retrato', 'Valor fora da lista não pode ser aceito.')

    def test_ids_posicionais_legados_nao_sao_restaurados(self):
        self.path.write_text(json.dumps({'libras_camera_ids': ['1'], 'libras_saved_names': ['Ana Silva']}), encoding='utf-8')
        state = server.BridgeState()
        self.assertEqual(state.libras_camera_ids, [])
        self.assertEqual(state.libras_saved_names, ['Ana Silva'])

    def test_marcacao_salva_apenas_nome_original_nao_apelido(self):
        self.state.cameras_list = [{'id': 'cam-a', 'name': 'Intérprete 1', 'original_name': 'Ana Silva'}]
        response = self.client.post('/api/grid/config', json={'libras_camera_ids': ['cam-a']})
        self.assertEqual(response.status_code, 200)
        cfg = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual(cfg['libras_saved_names'], ['Ana Silva'])
        self.assertEqual(cfg.get('libras_camera_ids', []), [])

    def test_marcador_nao_e_herdado_apos_saida_e_reordenacao(self):
        self.state.reconcile_cameras([camera('cam-a', 'Ana Silva'), camera('cam-b', 'Bruno')])
        self.client.post('/api/grid/config', json={'libras_camera_ids': ['cam-a']})
        self.state.reconcile_cameras([camera('cam-b', 'Bruno')])
        self.assertEqual(self.state.libras_camera_ids, [])
        self.assertFalse(self.state.cameras_list[0]['is_libras'])
        self.assertNotIn('cam-a', self.state.latest_camera_frames)
        self.state.reconcile_cameras([camera('cam-c', '  ANA   SILVA '), camera('cam-b', 'Bruno')])
        self.assertEqual(self.state.libras_camera_ids, ['cam-c'])

    def test_nome_parcial_nao_marca_outra_pessoa(self):
        self.state.libras_saved_names = ['Ana Silva']
        self.state.reconcile_cameras([camera('cam-b', 'Ana'), camera('cam-c', 'Ana Silva Costa')])
        self.assertEqual(self.state.libras_camera_ids, [])

    def test_auto_detect_nao_confunde_wilson_com_ils(self):
        self.state.libras_auto_detect = True
        self.state.reconcile_cameras([camera('cam-a', 'Wilson Souza')])
        self.assertEqual(self.state.libras_camera_ids, [])

    def test_desmarcacao_persiste_apos_reiniciar_e_mudar_id(self):
        self.state.libras_auto_detect = True
        self.state.reconcile_cameras([camera('cam-a', 'Ana Silva - Libras')])
        self.client.post('/api/grid/config', json={'libras_camera_ids': []})
        restored = server.BridgeState()
        restored.reconcile_cameras([camera('cam-novo', 'Ana Silva - Libras')])
        self.assertEqual(restored.libras_camera_ids, [])

    def test_falha_de_gravacao_nao_retorna_sucesso(self):
        self.path.mkdir()
        response = TestClient(server.app, raise_server_exceptions=False).post('/api/grid/config', json={'libras_size': 'equal'})
        self.assertGreaterEqual(response.status_code, 500)

    def test_rejeita_marcacao_de_camera_que_ja_saiu(self):
        response = self.client.post('/api/grid/config', json={'libras_camera_ids': ['cam-ausente']})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.state.libras_camera_ids, [])

    def test_camera_ausente_nao_entrega_primeira_camera(self):
        self.state.latest_camera_frames = {'cam-b': b'imagem de outra pessoa'}
        response = self.client.get('/raw_frame/camera/cam-a')
        self.assertNotEqual(response.content, b'imagem de outra pessoa')


if __name__ == '__main__':
    unittest.main()
