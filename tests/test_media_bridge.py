import asyncio
import os
import re
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch, AsyncMock

from fastapi.testclient import TestClient
import server


class _FakeFrame:
    def __init__(self, url):
        self.url = url


class _FakePage:
    def __init__(self, urls):
        self.frames = [_FakeFrame(u) for u in urls]
        self.main_frame = self.frames[0]


class FindBbbFrameTests(unittest.TestCase):
    """O frame da reunião não pode ser escolhido por substring que o slug da sala
    também contém (bug: sala 'mconf-cefor03' -> o portal /sala/mconf-... casava em
    'mconf' e virava o bbb_frame, sem #app/webcamVideoItem -> sources=[])."""

    def test_escolhe_cliente_html5_nao_portal_mconf(self):
        page = _FakePage([
            'https://conferenciaweb.rnp.br/sala/mconf-cefor03',
            'https://live-aws028.mconf.rnp.br/html5client/?sessionToken=abc',
        ])
        f = server.find_active_bbb_frame(page)
        self.assertIn('html5client', f.url,
                      'Deve escolher o frame do cliente BBB, não o portal Mconf.')

    def test_escolhe_cliente_bigbluebutton(self):
        page = _FakePage([
            'https://conferenciaweb.rnp.br/sala/xyz',
            'https://bbb.example.org/bigbluebutton/api?join',
        ])
        f = server.find_active_bbb_frame(page)
        self.assertIn('bigbluebutton', f.url)

    def test_fallback_main_frame_sem_cliente(self):
        page = _FakePage(['https://conferenciaweb.rnp.br/sala/mconf-cefor03'])
        f = server.find_active_bbb_frame(page)
        self.assertIs(f, page.main_frame)


class YouTubeChatTests(unittest.TestCase):
    def test_pytchat_criado_sem_handler_de_signal(self):
        # pytchat.create default interruptable=True chama signal.signal(SIGINT), que
        # estoura dentro de asyncio.to_thread (worker thread) com "signal only works
        # in main thread" — travando o chat do YouTube. A ponte cria com interruptable=False.
        chamado = {}

        class _FakeChat:
            def is_alive(self):
                return False

        def _fake_create(video_id, **kw):
            chamado.update(kw)
            return _FakeChat()

        with patch.object(server.pytchat, 'create', _fake_create):
            asyncio.run(server.youtube_chat_loop('abc123DEF'))
        self.assertEqual(chamado.get('interruptable'), False,
                         'pytchat.create deve rodar com interruptable=False fora da main thread.')

    def test_youtube_chat_loop_usa_items_sem_chamar_sync_items_bloqueante(self):
        # Chatdata.sync_items() no pytchat é um generator que chama time.sleep(1.0)
        # na thread onde é iterado. Iterá-lo no event loop congelava o servidor por
        # 1 s a cada 2 s e fazia o áudio do /stage picotar no OBS.
        class _FakeItem:
            datetime = '2026-11-05 19:00:00'
            message = 'Olá live!'
            author = type('A', (), {'name': 'Maria', 'imageUrl': ''})()

        class _FakeData:
            def __init__(self):
                self.items = [_FakeItem()]

            def sync_items(self):
                raise AssertionError('Não deve chamar sync_items() quando data.items existe!')

        class _FakeChat:
            def __init__(self):
                self._calls = 0

            def is_alive(self):
                self._calls += 1
                return self._calls == 1

            def get(self):
                return _FakeData()

        server.state.youtube_messages_list.clear()
        with patch.object(server.pytchat, 'create', lambda video_id, **kw: _FakeChat()):
            with patch.object(server.asyncio, 'sleep', new=AsyncMock()):
                asyncio.run(server.youtube_chat_loop('abc123DEF'))
        self.assertEqual(len(server.state.youtube_messages_list), 1)
        self.assertEqual(server.state.youtube_messages_list[0]['user'], 'Maria')


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

    def test_medidor_conexao_estados_e_limites(self):
        self.state.connected = True
        casos = [
            ({'rtt_ms': 80, 'jitter_ms': 5, 'loss_pct': 0}, 'good'),
            ({'rtt_ms': 300, 'jitter_ms': 5, 'loss_pct': 0}, 'fair'),
            ({'rtt_ms': 600, 'jitter_ms': 5, 'loss_pct': 0}, 'poor'),
            ({'rtt_ms': 20, 'jitter_ms': 30, 'loss_pct': 0}, 'fair'),
            ({'rtt_ms': 20, 'jitter_ms': 60, 'loss_pct': 0}, 'poor'),
            ({'rtt_ms': 20, 'jitter_ms': 5, 'loss_pct': 2}, 'fair'),
            ({'rtt_ms': 20, 'jitter_ms': 5, 'loss_pct': 5}, 'poor'),
            ({'rtt_ms': None, 'jitter_ms': 0, 'loss_pct': None}, 'good'),
            ({'rtt_ms': None, 'jitter_ms': None, 'loss_pct': None}, 'unknown'),
            ({'rtt_ms': float('nan'), 'jitter_ms': -1, 'loss_pct': '0'}, 'unknown'),
            ({'degraded': True}, 'poor'),
        ]
        for metrics, expected in casos:
            with self.subTest(metrics=metrics):
                self.state.connection_stats = dict(metrics, age_ms=0)
                self.state.connection_stats_received_at = time.monotonic()
                result = self.client.get('/api/status').json().get('connection_quality')
                self.assertIsNotNone(result, 'A API deve fornecer a qualidade da conexão RNP.')
                self.assertEqual(result['level'], expected)
        self.state.connecting = True
        self.assertEqual(self.client.get('/api/status').json()['connection_quality']['level'], 'connecting')
        self.state.connecting = False
        self.state.connected = False
        result = self.client.get('/api/status').json()['connection_quality']
        self.assertEqual(result['level'], 'disconnected')
        self.assertIsNone(result['rtt_ms'])

    def test_medidor_conexao_nao_reutiliza_dados_vencidos(self):
        self.state.connected = True
        for browser_age, server_age in ((6001, 0), (0, 7), (4000, 3)):
            self.state.connection_stats = {'rtt_ms': 10, 'age_ms': browser_age}
            self.state.connection_stats_received_at = time.monotonic() - server_age
            result = self.client.get('/api/status').json().get('connection_quality')
            self.assertIsNotNone(result)
            self.assertEqual(result['level'], 'unknown')
            self.assertIsNone(result['rtt_ms'], 'Não exibir valores antigos como atuais.')

    def test_medidor_conexao_limpa_amostras_ao_desconectar(self):
        self.state.connection_stats = {'rtt_ms': 50, 'age_ms': 0}
        asyncio.run(server.stop_connection())
        self.assertIsNone(self.state.connection_stats)

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


    def test_grid_config_rejeita_enums_invalidos_sem_mutar_parcial(self):
        # Os campos de enumeração são refletidos no HTML dos Browser Sources; um valor
        # inventado não pode ser gravado. E a validação é ANTES de mutar: nada parcial.
        self.state.libras_size = 'equal'
        self.state.stage_preset = 'bottom-bar'
        for payload in (
            {'libras_size': 'gigante'},
            {'camera_name_style': 'holograma'},
            {'grid_layout_mode': 'diagonal'},
            {'stage_preset': 'apocalipse-zumbi'},
            {'stage_preset': 'side-by-side', 'libras_size': 'nope'},  # válido+inválido
        ):
            r = self.client.post('/api/grid/config', json=payload)
            self.assertEqual(r.status_code, 400, f'{payload} deveria ser rejeitado')
        self.assertEqual(self.state.libras_size, 'equal', 'inválido não pode mutar')
        self.assertEqual(self.state.stage_preset, 'bottom-bar',
                         'nada pode ser aplicado quando outro campo do payload é inválido')

    def test_grid_config_aceita_enums_validos(self):
        r = self.client.post('/api/grid/config', json={
            'libras_size': 'huge', 'camera_name_style': 'pill',
            'grid_layout_mode': 'row', 'stage_preset': 'cameras-only'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.state.libras_size, 'huge')
        self.assertEqual(self.state.camera_name_style, 'pill')
        self.assertEqual(self.state.grid_layout_mode, 'row')
        self.assertEqual(self.state.stage_preset, 'cameras-only')

    def test_config_save_rejeita_enum_invalido(self):
        self.state.grid_layout_mode = 'auto'
        r = self.client.post('/api/config/save', json={'grid_layout_mode': 'tetraédrico'})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.state.grid_layout_mode, 'auto', 'não pode gravar valor inválido')

    def test_grid_config_limita_custom_name(self):
        # texto livre mas precisa ser str e ter limite (é refletido no HTML da câmera).
        self.assertEqual(self.client.post('/api/grid/config',
            json={'custom_name': 12345, 'cam_id': 'cam-w_a'}).status_code, 400)
        self.assertEqual(self.client.post('/api/grid/config',
            json={'custom_name': 'x' * 200, 'cam_id': 'cam-w_a'}).status_code, 400)
        self.assertEqual(self.client.post('/api/grid/config',
            json={'custom_name': 'Prof Ana', 'cam_id': 'cam-w_a'}).status_code, 200)

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

    def test_logo_ifes_servido_como_asset(self):
        response = self.client.get('/assets/ifes-logo.png')
        self.assertEqual(response.status_code, 200)
        self.assertIn('image/png', response.headers['content-type'])

    def test_logo_aparece_so_no_tema_ifes(self):
        self.state.control_theme = 'ifes'
        self.assertIn('ifes-logo.png', self.client.get('/control').text,
                      'O logo institucional deve aparecer no tema Ifes.')
        self.state.control_theme = 'broadcast'
        self.assertNotIn('ifes-logo.png', self.client.get('/control').text,
                         'O logo Ifes não deve aparecer no tema broadcast.')

    def test_salvar_recarregar_padroes_na_barra_e_painel_conexao_marcado(self):
        # Pedido do usuário: Salvar/Recarregar/Padrões sobem para junto do seletor de
        # tema; o painel de conexão fica marcado para o dock.js reposicionar por largura.
        html = self.client.get('/control').text
        barra = html[html.index('dock-statusbar'):html.index('dock-monitor-fixed')]
        for idb in ('btnBarSave', 'btnBarReload', 'btnBarDefaults', 'btnBarRestart'):
            self.assertIn(idb, barra, f'{idb} deve estar na barra de status.')
        self.assertNotIn('Memória & Persistência', html,
                         'O card de memória some: virou os botões da barra.')
        self.assertIn('id="connectPanelCard"', html)
        self.assertIn('id="connectPopover"', html)

    def test_ordem_dos_blocos_e_configuravel_e_validada(self):
        # A ordem dos blocos é preferência persistida no config do servidor (todos os
        # clientes iguais), então /api/grid/config precisa validar como permutação.
        ordem = ['fontes', 'mixer', 'chat', 'cameras', 'palco-preview',
                 'palco-presets', 'palco-libras', 'enquete', 'slides']
        r = self.client.post('/api/grid/config', json={'dock_block_order': ordem})
        self.assertEqual(r.status_code, 200)
        self.assertIn('data-block-order="' + ','.join(ordem) + '"',
                      self.client.get('/control').text,
                      'O dock deve renderizar a ordem salva no contêiner do mosaico.')
        for invalida in (['mixer', 'mixer'] + ordem[2:],
                         ordem[:-1],
                         ordem + ['extra'],
                         'mixer,fontes'):
            r = self.client.post('/api/grid/config', json={'dock_block_order': invalida})
            self.assertEqual(r.status_code, 400, f'Ordem inválida {invalida} deve ser rejeitada.')
        self.assertEqual(self.state.dock_block_order, ordem,
                         'Uma ordem rejeitada não pode mutar o estado.')

    def test_monitor_de_fontes_fica_na_zona_fixa(self):
        # Decisão C híbrida: o monitor é informação de live, então sobe para a zona
        # fixa (comprimido no estreito, completo no largo) e sai do mosaico.
        html = self.client.get('/control').text
        self.assertIn('dock-monitor-fixed', html)
        self.assertLess(html.index('dock-monitor-fixed'), html.index('dock-tabs'),
                        'O monitor deve vir antes das abas, na zona fixa.')
        mon = html[html.index('dock-monitor-fixed'):html.index('dock-tabs')]
        self.assertIn('healthPillRnp', mon, 'O monitor completo mora na zona fixa.')
        mosaico = html[html.index('id="dockMosaico"'):html.index('<script', html.index('id="dockMosaico"'))]
        self.assertNotIn('healthPillRnp', mosaico, 'O monitor sai do mosaico.')

    def test_youtube_mora_no_popover_de_conexoes(self):
        # Integrar o chat do YouTube é uma conexão de fonte, como a RNP: mora no
        # popover de Conexões da barra, não como bloco do mosaico.
        html = self.client.get('/control').text
        self.assertLess(html.index('id="connectPopover"'), html.index('btnYtConnect'),
                        'O controle do YouTube deve estar dentro do popover.')
        self.assertLess(html.index('btnYtConnect'), html.index('dock-monitor-fixed'),
                        'O popover termina antes do monitor.')
        mosaico = html[html.index('id="dockMosaico"'):html.index('<script', html.index('id="dockMosaico"'))]
        self.assertNotIn('btnYtConnect', mosaico, 'O YouTube sai do mosaico.')
        barra = html[html.index('dock-statusbar'):html.index('dock-monitor-fixed')]
        self.assertIn('Conexões', barra, 'O botão do popover passa a se chamar Conexões.')

    def test_assets_do_dock_tem_cache_buster_no_html(self):
        # Clientes (OBS/CEF sobretudo) seguram o CSS/JS velho mesmo com no-store,
        # porque o href não muda entre deploys. O HTML precisa carregar um ?v= que
        # muda quando os arquivos mudam, para forçar refetch no próximo load.
        html = self.client.get('/control').text
        for asset in ('dock.css', 'dock.js', 'audio-mixer.js'):
            self.assertRegex(html, rf'/assets/{re.escape(asset)}\?v=\d+',
                             f'O asset {asset} precisa de cache-buster no HTML.')

    def test_camada_de_componentes_cobre_geometria_basica(self):
        # Guarda contra a poda: o bloco migrado já não traz .card/body/.badge, então
        # a camada de componentes precisa prover a geometria (não só a cor). Sem o
        # padding do card/body o conteúdo encosta nas bordas; sem o pill do badge ele
        # vira barra retangular.
        css = self.client.get('/assets/dock.css').text
        comp = css[css.index('CAMADA DE COMPONENTES'):]
        card = comp[comp.index('.card {'):comp.index('}', comp.index('.card {'))]
        self.assertIn('padding:', card, '.card precisa de padding na camada de componentes.')
        body = comp[comp.index('body {'):comp.index('}', comp.index('body {'))]
        self.assertIn('padding:', body, 'body precisa de padding na camada de componentes.')
        badge = comp[comp.index('.badge {'):comp.index('}', comp.index('.badge {'))]
        self.assertIn('border-radius:', badge, '.badge precisa do pill na camada de componentes.')

    def test_captura_pcm_usa_audioworklet_e_paginas_pausam_ocultas(self):
        # ScriptProcessorNode fazia callback de captura na main thread a ~23 Hz (CPU
        # no piso da ponte); AudioWorklet move isso para a thread de áudio. E página
        # oculta (dock minimizado/aba de fundo) não precisa pollar nem repintar.
        js = (Path(__file__).resolve().parent.parent / 'web' / 'media-source.js').read_text(encoding='utf-8')
        self.assertIn('audioWorklet.addModule', js, 'Captura PCM deve usar AudioWorklet.')
        self.assertIn('if (!ctx.audioWorklet)', js,
                      'Deve haver guard de existência: audioWorklet é undefined em about:blank.')
        self.assertIn('createScriptProcessor', js, 'ScriptProcessor resta só como fallback.')
        html = self.client.get('/control').text
        self.assertIn('document.hidden', html, 'Polls do dock devem pausar com a janela oculta.')
        mixer = self.client.get('/assets/audio-mixer.js').text
        self.assertIn('document.hidden', mixer, 'A mesa deve pausar repaint com janela oculta.')
        dock = self.client.get('/assets/dock.js').text
        self.assertIn('document.hidden', dock, 'O fps da barra deve pausar com janela oculta.')

    def test_sampler_de_picos_nao_pode_ser_gateado_por_ouvintes(self):
        # Gatear o sampler por assinantes de /ws/peaks_out quebrou a detecção de
        # fala: o servidor lê audioInfo().peak no sync_loop mesmo com a mesa do
        # dock fechada. O pico precisa continuar sendo amostrado sempre.
        js = (Path(__file__).resolve().parent.parent / 'web' / 'media-source.js').read_text(encoding='utf-8')
        self.assertNotIn('peaksWanted', js,
                         'O sampler de picos não pode pausar por falta de ouvintes da mesa.')

    def test_picos_sao_repassados_a_mesa_sem_interpretar(self):
        # A ponte é só um fan-out do pacote de picos: o que entra em /ws/peaks_in
        # sai igual em /ws/peaks_out, sem leitura nem transformação.
        with self.client.websocket_connect('/ws/peaks_out') as mesa:
            with self.client.websocket_connect('/ws/peaks_in') as prod:
                prod.send_text('{"cam1": 0.42}')
                self.assertEqual(mesa.receive_json(), {'cam1': 0.42})

    def test_youtube_nao_fica_aninhado_no_form_row(self):
        # O card do YouTube já foi inserido dentro do .input-group do Nome (dentro do
        # .form-row de 2 colunas): herdou meia largura e espremeu o e-mail. Entre o
        # input do Nome e o link do YouTube precisam fechar input, input-group e
        # form-row — senão o YouTube está aninhado na coluna errada.
        html = self.client.get('/control').text
        entre = html[html.index('id="userName"'):html.index('id="ytUrl"')]
        self.assertGreaterEqual(entre.count('</div>'), 3,
                                'O card do YouTube não pode ficar aninhado no input-group do Nome.')

    def test_palco_composto_desmembrado_em_blocos(self):
        # O card "Palco Composto" agrupava preview + presets + Libras; no mosaico
        # cada um deve ser um bloco 1x1 independente.
        html = self.client.get('/control').text
        for bloco in ('palco-preview', 'palco-presets', 'palco-libras'):
            self.assertIn(f'data-block="{bloco}"', html, f'Bloco {bloco} ausente.')
        i_prev = html.index('data-block="palco-preview"')
        i_pres = html.index('data-block="palco-presets"')
        i_lib = html.index('data-block="palco-libras"')
        self.assertLess(i_prev, html.index('stagePreviewIframe'),
                        'O preview deve estar dentro do bloco preview.')
        self.assertLess(html.index('stagePreviewIframe'), i_pres,
                        'O preview deve terminar antes do bloco de presets.')
        self.assertLess(i_pres, html.index('preset-buttons-grid'))
        self.assertLess(html.index('preset-buttons-grid'), i_lib,
                        'Os presets devem terminar antes do bloco de Libras.')
        self.assertLess(i_lib, html.index('selLibrasSize'))

    def test_presets_sem_slide_existem_no_dock_e_no_stage(self):
        # Pedidos: presets que escondem o slide e deixam as câmeras preencherem tudo.
        # Cada preset precisa do botão no dock, do nome na lista de UI, do CSS
        # '.preset-<nome>' e do ramo de layout no /stage.
        nomes = ('cameras-only', 'camera-libras', 'grid-libras')
        html = self.client.get('/control').text
        for p in nomes:
            self.assertIn(f'btnPreset_{p}', html, f'botão do preset {p} ausente no dock.')
            self.assertIn(f"'{p}'", html, f'nome {p} ausente da lista de presets do dock.')
        stage = self.client.get('/stage').text
        for p in nomes:
            self.assertIn('.preset-' + p, stage, f'CSS do preset {p} ausente no /stage.')
        for p in nomes:
            self.assertIn(p, stage, f'ramo de layout do preset {p} ausente no /stage.')

    def test_aviso_webrtc_mora_dentro_do_bloco_de_fontes(self):
        # O aviso de modo WebRTC era um bloco 1x1 próprio que sobrava como tile vazio
        # no mosaico. É informação de setup das fontes, então mora DENTRO do card de
        # Fontes para o OBS Studio (que lista as URLs), não como bloco separado.
        html = self.client.get('/control').text
        self.assertNotIn('data-bloco="aviso"', html,
                         'O aviso não pode mais ser um bloco próprio do mosaico.')
        fontes = html[html.index('data-bloco="fontes"'):html.index('data-bloco="palco-preview"')]
        self.assertIn('dock-notice', fontes, 'O aviso deve estar dentro do bloco de Fontes.')
        self.assertIn('Modo WebRTC', fontes)

    def test_conexao_vira_menu_dropdown_na_barra(self):
        # Decisão do usuário: o painel de conexão deixa de ser bloco e vira popover
        # ancorado na barra de status (não ocupa fluxo, então não empurra a dobra).
        html = self.client.get('/control').text
        barra = html[html.index('dock-statusbar'):html.index('dock-monitor-fixed')]
        self.assertIn('btnConnectMenu', barra, 'A barra precisa do botão do menu de conexão.')
        self.assertIn('id="connectPopover"', barra, 'O popover mora na barra.')
        pop = html[html.index('id="connectPopover"'):]
        self.assertIn('connectPanelCard', pop[:6000],
                      'O painel de conexão deve morar dentro do popover.')
        self.assertNotIn('dockConnectFixed', html,
                         'O contêiner de reparenting por largura foi abolido.')
        mosaico = html[html.index('id="dockMosaico"'):html.index('<script', html.index('id="dockMosaico"'))]
        self.assertNotIn('connectPanelCard', mosaico,
                         'O painel sai do mosaico (virou popover).')

    def test_cada_fonte_tem_um_unico_botao_de_link(self):
        # Os links de fonte existiam 2-3x (tile na Conexão + botão solto em outra
        # seção; Libras 3x). Dedupe: um único lugar canônico por fonte.
        html = self.client.get('/control').text
        for fonte in ('/poll', '/stage', '/libras', '/chat', '/chat/featured',
                      '/cameras/grid', '/presentation'):
            self.assertEqual(html.count(f"copyUrl('{fonte}'"), 1,
                             f'A fonte {fonte} deve ter um único botão de link.')

    def test_sensibilidade_de_fala_fica_recolhida(self):
        # É ajuste raro, não operação: no card fixo de áudio ele não pode empurrar
        # as abas para baixo da dobra no dock estreito.
        html = self.client.get('/control').text
        self.assertIn('<details class="dock-advanced">', html,
                      'O avançado de áudio deve virar um <details> recolhível.')
        d = html.index('<details class="dock-advanced">')
        f = html.index('</details>', d)
        self.assertIn('speakThreshold', html[d:f],
                      'O limiar de fala deve morar dentro do bloco recolhível.')

    def test_botoes_para_desativar_previews(self):
        # Previews ao vivo (grid de câmeras, apresentação) pesam CPU; o operador
        # precisa poder desligá-los sem perder os links de fonte.
        html = self.client.get('/control').text
        for idb in ('btnToggleCamPreview', 'btnToggleScreenPreview', 'btnToggleStagePreview'):
            self.assertIn(idb, html, f'Botão {idb} de controle de preview ausente.')

    def test_api_reiniciar_reseta_e_reconecta(self):
        # Botão 'Reiniciar': força reset (flags caem, navegador limpo) e agenda um
        # run_connection novo com a config salva — recupera uma ponte travada onde o
        # 'Conectar' comum não consegue subir de novo.
        with patch.object(server, 'run_connection', new=AsyncMock()) as recon:
            self.state.connected = True
            self.state.connecting = True
            r = self.client.post('/api/restart')
        self.assertEqual(r.status_code, 200, 'Deve responder ao reinício.')
        self.assertFalse(self.state.connected, 'O reinício derruba connected.')
        self.assertFalse(self.state.connecting, 'O reinício derruba connecting.')
        recon.assert_called_once()

    def test_escudo_anti_inatividade_fica_na_barra_de_status(self):
        # Pedido do usuário: o aviso de anti-inatividade é estado global da sessão,
        # então mora ao lado do indicador de conexão, não enterrado no mosaico.
        html = self.client.get('/control').text
        barra = html[html.index('dock-statusbar'):html.index('dock-monitor-fixed')]
        self.assertIn('shieldBadge', barra,
                      'O escudo anti-inatividade deve ficar na barra de status.')
        self.assertEqual(html.count('shieldBadge'), 1,
                         'O escudo não pode ficar duplicado em outro lugar do dock.')

    def test_shell_tem_zona_fixa_e_mosaico_de_blocos(self):
        # Modelo de blocos: a unidade do mosaico é o card (categoria + span + ordem),
        # não o grupo. A zona fixa fica só com barra de status + monitor de fontes.
        html = self.client.get('/control').text
        for marcador in ('dock-statusbar', 'dock-monitor-fixed', 'dock-tabs', 'dock-mosaico'):
            self.assertIn(marcador, html, f'Shell do dock precisa de {marcador}.')
        for bloco in ('mixer', 'fontes', 'palco-preview', 'cameras', 'chat',
                      'palco-presets', 'palco-libras', 'enquete', 'slides'):
            self.assertIn(f'data-bloco="{bloco}"', html, f'Bloco {bloco} ausente.')
        self.assertNotIn('dock-group', html, 'Os grupos foram abolidos em favor dos blocos.')
        self.assertNotIn('dock-audio-fixed', html,
                         'O mixer não é mais zona fixa: virou bloco reordenável.')
        mixer = html[html.index('data-bloco="mixer"'):html.index('data-bloco="fontes"')]
        self.assertIn('audioDockCard', mixer, 'O mixer mora no bloco mixer.')
        self.assertLess(html.index('dock-monitor-fixed'), html.index('dock-tabs'),
                        'O monitor continua na zona fixa, antes das abas.')
        self.assertIn('data-bloco="slides" data-cat="palco"', html,
                      'Apresentação é bloco da categoria Palco.')
        self.assertIn('data-bloco="enquete" data-cat="palco"', html,
                      'Enquete é bloco da categoria Palco.')

    def test_barra_de_status_nao_duplica_medidor_master(self):
        html = self.client.get('/control').text
        barra = html[html.index('dock-statusbar'):html.index('dock-monitor-fixed')]
        self.assertNotIn('vuMasterBar', barra,
                         'Barra de status não pode repetir o medidor master do mixer.')
        audio = html[html.index('data-bloco="mixer"'):html.index('data-bloco="fontes"')]
        self.assertIn('vuMasterBar', audio, 'O medidor master vive no bloco mixer.')

    def test_dock_sem_link_de_teste_webrtc(self):
        # WebRTC hoje é o transporte padrão, não um experimento: o link "Abrir teste
        # WebRTC" no topo da mesa só adicionava ruído. O /webrtc em si permanece
        # (os links individuais do OBS apontam para ele), só o atalho de "teste" sai.
        js = self.client.get('/assets/audio-mixer.js').text
        self.assertNotIn('Abrir teste WebRTC', js,
                         'O atalho de teste WebRTC não deve mais ser injetado na mesa.')
        self.assertIn('/webrtc', js,
                      'Os links individuais do OBS continuam usando /webrtc.')

    def test_dock_sem_cores_inline_no_markup(self):
        # Style inline vence qualquer folha de estilo: enquanto o markup pintar cor
        # por atributo style, os tokens não têm como valer e o tema não aparece.
        html = self.client.get('/control').text
        estilos = re.findall(r'style="([^"]*)"', html)
        com_cor = [s for s in estilos
                   if re.search(r'(?<![-\w])(?:background|color|border-color)\s*:', s)
                   or re.search(r'border(?:-(?:top|bottom|left|right))?\s*:[^;]*(?:#[0-9a-fA-F]{3,8}|rgba?\()', s)]
        self.assertEqual(com_cor, [],
                         f'{len(com_cor)} inline styles ainda pintam cor por cima dos tokens.')

    def test_control_sem_style_inline_e_css_base_no_asset(self):
        # O CSS do dock vive só em web/dock.css: nenhum <style> inline no /control,
        # e o base migrado (geometria do rack/chat/enquete) está no asset.
        html = self.client.get('/control').text
        self.assertNotIn('<style>', html, 'O /control não pode mais injetar CSS inline.')
        css = self.client.get('/assets/dock.css').text
        self.assertIn('.audio-mixer-rack', css, 'O base migrado deve estar no dock.css.')
        sem_comentarios = re.sub(r'/\*.*?\*/', '', css, flags=re.S)
        self.assertIsNone(re.search(r'body\.theme-\w+\s+\.[\w-]+', sem_comentarios),
                          'Nem no asset pode haver override de componente por tema.')

    def test_dock_css_estiliza_componentes_com_tokens(self):
        css = self.client.get('/assets/dock.css').text
        for seletor, token in (('.card', 'var(--surface)'),
                               ('.section-title', 'var(--text-muted)'),
                               ('.btn-ctrl', 'var(--primary)'),
                               ('input[type="text"]', 'var(--surface-inset)')):
            self.assertIn(seletor, css, f'Camada de componentes deve cobrir {seletor}.')
            self.assertIn(token, css, f'{seletor} deve consumir o token {token}.')
        # Componente nunca é escopado por tema: tema é só override de token.
        # (Remove comentários antes: prosa que CITA o padrão proibido não é violação.)
        sem_comentarios = re.sub(r'/\*.*?\*/', '', css, flags=re.S)
        self.assertIsNone(re.search(r'body\.theme-\w+\s+\.[\w-]+', sem_comentarios),
                          'dock.css não pode escopar componentes por tema.')

    def test_logo_substitui_o_emoji_do_titulo(self):
        # O pedido era SUBSTITUIR o 🎛️ do título pela marca do If, não somar os dois.
        for tema in ('ifes', 'broadcast'):
            self.state.control_theme = tema
            html = self.client.get('/control').text
            h1 = re.search(r'<h1.*?</h1>', html, re.S)
            self.assertIsNotNone(h1, f'Título do dock não encontrado (tema {tema}).')
            self.assertNotIn('\U0001F39B', h1.group(0),
                             f'O emoji do mixer não pode sobrar no título (tema {tema}).')

    def test_dock_tem_controle_de_sensibilidade_de_fala(self):
        # O ganho do microfone varia por sala/operador; o limiar precisa ser ajustável
        # sem editar config.json na mão.
        html = self.client.get('/control').text
        campo = re.search(r'<input[^>]*id="speakThreshold"[^>]*>', html)
        self.assertIsNotNone(campo, 'O dock precisa expor o limiar de detecção de fala.')
        attrs = campo.group(0)
        self.assertIn('value="0.02"', attrs, 'Deve refletir o valor vigente do estado.')
        self.assertIn('min="0.005"', attrs)
        self.assertIn('max="0.5"', attrs)
        self.assertIn('saveSpeakThreshold', html,
                      'Handler dedicado: autoSaveField posta em /api/config/save, que não valida o limiar.')

    def test_limiar_de_fala_tem_default_e_persiste(self):
        self.assertEqual(server.DEFAULT_CONFIG['speak_peak_threshold'], 0.02,
                         '0,05 ficava na mediana de fala de um microfone real (medido ao vivo).')
        self.assertEqual(self.state.speak_peak_threshold, 0.02)
        response = self.client.post('/api/grid/config', json={'speak_peak_threshold': 0.01})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.state.speak_peak_threshold, 0.01)
        self.assertEqual(self.client.get('/api/status').json()['speak_peak_threshold'], 0.01)
        self.assertEqual(self.state.export_config()['speak_peak_threshold'], 0.01,
                         'O limiar precisa persistir em config.json.')

    def test_limiar_de_fala_rejeita_valor_invalido(self):
        self.state.speak_peak_threshold = 0.02
        for invalido in (0, -0.1, 0.6, 5, 'abc', None):
            response = self.client.post('/api/grid/config', json={'speak_peak_threshold': invalido})
            self.assertEqual(response.status_code, 400, f'{invalido!r} deveria ser rejeitado.')
            self.assertEqual(self.state.speak_peak_threshold, 0.02,
                             f'{invalido!r} não pode alterar o limiar vigente.')
        # Limites válidos inclusive.
        for valido in (0.005, 0.5):
            self.assertEqual(
                self.client.post('/api/grid/config', json={'speak_peak_threshold': valido}).status_code,
                200, f'{valido} está dentro da faixa e deveria ser aceito.')

    def test_seletor_de_tema_do_dock_oferece_so_temas_validos(self):
        # /api/theme/set só aceita CONTROL_THEMES. Um seletor que ainda oferece temas
        # aposentados leva o operador a escolher algo que falha com 400 — e o JS legado
        # mostrava "Salvo!" mesmo assim. Olha só o bloco do #selControlTheme: `glass` e
        # `broadcast` também são valores legítimos de chat_style/poll_style.
        html = self.client.get('/control').text
        bloco = re.search(r'<select id="selControlTheme".*?</select>', html, re.S)
        self.assertIsNotNone(bloco, 'Seletor de tema do painel não encontrado.')
        valores = re.findall(r'<option value="([^"]+)"', bloco.group(0))
        self.assertEqual(sorted(valores), sorted(server.CONTROL_THEMES),
                         'O seletor de tema deve oferecer exatamente os temas válidos.')

    def _cams_fixture(self):
        self.state.reconcile_cameras([
            {'id': 'cam-a', 'participant_id': 'p_a', 'name': 'Ana'},
            {'id': 'cam-b', 'participant_id': 'p_b', 'name': 'Beto'},
        ])

    def test_foco_aceita_multiplas_cameras_e_mantem_legado(self):
        # Tela de TV pede destaque para MAIS de uma pessoa: foco vira conjunto.
        # O campo legado focused_camera_id continua funcionando (=> primeiro focado).
        self._cams_fixture()
        r = self.client.post('/api/grid/config', json={'focused_camera_ids': ['cam-a', 'cam-b']})
        self.assertEqual(r.status_code, 200)
        st = self.client.get('/api/status').json()
        self.assertEqual(st['focused_camera_ids'], ['cam-a', 'cam-b'])
        self.assertEqual(st['focused_camera_id'], 'cam-a', 'Legado deve expor o primeiro focado.')
        self.assertEqual([c['is_focused'] for c in st['cameras']], [True, True])
        self.client.post('/api/grid/config', json={'focused_camera_id': 'cam-b'})
        self.assertEqual(self.client.get('/api/status').json()['focused_camera_ids'], ['cam-b'])
        self.client.post('/api/grid/config', json={'focused_camera_id': None})
        self.assertEqual(self.client.get('/api/status').json()['focused_camera_ids'], [])
        # Inválido: 400 e o conjunto vigente não muta (valida antes de aplicar).
        self.client.post('/api/grid/config', json={'focused_camera_ids': ['cam-a']})
        r = self.client.post('/api/grid/config', json={'focused_camera_ids': 'cam-a'})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.client.get('/api/status').json()['focused_camera_ids'], ['cam-a'])

    def test_foco_perde_cameras_que_saem_da_sala(self):
        self._cams_fixture()
        self.client.post('/api/grid/config', json={'focused_camera_ids': ['cam-a', 'cam-b']})
        self.state.reconcile_cameras([{'id': 'cam-b', 'participant_id': 'p_b', 'name': 'Beto'}])
        self.assertEqual(self.client.get('/api/status').json()['focused_camera_ids'], ['cam-b'])

    def test_tv_existe_com_opcao_de_chat_destacado(self):
        html = self.client.get('/tv').text
        for marcador in ('tvMain', 'tvStrip', 'tvFeatured', 'stage-media.js'):
            self.assertIn(marcador, html, f'/tv precisa de {marcador}.')
        self.assertIn('data-chat="1"', html, 'Chat destacado ligado por padrão.')
        self.assertIn('data-chat="0"', self.client.get('/tv?chat=0').text)
        fontes = self.client.get('/control').text
        self.assertEqual(fontes.count("copyUrl('/tv'"), 1,
                         'Link da TV deve existir uma única vez no bloco de fontes.')

    def test_handlers_do_dock_moram_em_dock_js_e_nao_inline(self):
        # Task 6b: o corpo dos handlers saiu do f-string do /control e vive em
        # web/dock.js. Se alguém re-inlineizar JS, o cache-buster ?v= deixa de valer
        # e o contrato de seletor globais (onclick -> window.*) pode quebrar silenciosamente.
        html = self.client.get('/control').text
        js = self.client.get('/assets/dock.js').text
        for fn in ('function fetchStatus', 'function setStagePreset', 'function autoSaveField'):
            self.assertNotIn(fn, html, f'{fn} não deve mais estar no HTML inline do /control.')
            self.assertIn(fn, js, f'{fn} deve estar servido por /assets/dock.js.')
        # O PCM master continua inline (é script injetado, não handler do dock).
        self.assertIn('PCM_AUDIO_SCRIPT', open('server.py', encoding='utf-8').read())

    def test_vitrine_de_temas_removida(self):
        for rota in ('/catalog', '/themes'):
            self.assertEqual(self.client.get(rota).status_code, 404,
                             f'{rota} deveria ter sido removida; o seletor de tema vive na barra de status.')

    def test_dock_nao_tem_link_morto_para_a_vitrine(self):
        # Remover a rota sem remover os <a href="/catalog"> deixaria link morto no dock.
        html = self.client.get('/control').text
        self.assertNotIn('href="/catalog"', html)
        self.assertNotIn('href="/themes"', html)

    def test_api_theme_set_exige_campo_theme(self):
        # Sem isto, um POST {} gravava o tema padrão por cima da preferência do usuário.
        self.state.control_theme = 'ifes'
        response = self.client.post('/api/theme/set', json={})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.state.control_theme, 'ifes',
                         'POST sem campo theme não pode sobrescrever o tema vigente.')

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

    def test_api_connect_e_config_save_rejeitam_room_url_nao_http(self):
        for bad_url in ('file:///C:/Windows/win.ini', 'javascript:alert(1)', 'ftp://example.com/room'):
            res_conn = self.client.post('/api/connect', json={'room_url': bad_url, 'user_name': 'Teste'})
            self.assertEqual(res_conn.status_code, 400, f'/api/connect aceitou URL insegura: {bad_url}')
            res_save = self.client.post('/api/config/save', json={'room_url': bad_url})
            self.assertEqual(res_save.status_code, 400, f'/api/config/save aceitou URL insegura: {bad_url}')

    def test_rotas_mutaveis_rejeitam_origin_externo(self):
        headers = {'Origin': 'https://evil.example', 'Host': 'localhost:3333'}
        for rota, payload in (
            ('/api/connect', {'room_url': 'https://conferenciaweb.rnp.br/sala', 'user_name': 'Bot'}),
            ('/api/disconnect', {}),
            ('/api/config/save', {'user_name': 'X'}),
            ('/api/config/reset', {}),
            ('/api/poll/show', {}),
            ('/api/server/stop', {}),
        ):
            res = self.client.post(rota, json=payload, headers=headers)
            self.assertEqual(res.status_code, 403, f'{rota} deveria bloquear Origin externo.')

    def test_novos_padroes_do_dock_e_tutorial_de_energia(self):
        html = self.client.get('/control').text
        self.assertIn('id="energiaCard"', html, 'Card de ajustes de energia ausente no /control.')
        self.assertIn('id="btnToggleMeters"', html, 'Botão de toggle dos medidores VU ausente no /control.')
        self.assertIn('RnpMeters.toggle()', html)
        self.assertIn('class="tab-icon"', html, 'Abas do dock devem usar ícones compactos.')
        self.assertNotIn('class="tab-long"', html, 'Rótulos longos nas abas não devem alargar o dock.')
        self.assertNotIn(r'HKLM\\', html, 'Caminhos de registro renderizados no HTML devem ter barra simples.')
        self.assertIn(r'HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile', html)

        dj = self.client.get('/assets/dock.js').text
        self.assertIn('let previewOff = { cameras: true, presentation: true, stage: true };', dj)
        self.assertIn("if (savedMuted !== '0') {", dj)
        self.assertIn('function copiarComando(', dj)
        self.assertNotIn('thumbCam_', dj, 'Card de câmera sincronizada não deve renderizar miniatura redundante.')

        am = self.client.get('/assets/audio-mixer.js').text
        self.assertIn("let metersOff = localStorage.getItem(METERS_KEY) !== '0';", am)
        self.assertIn('window.RnpMeters = {', am)
        self.assertIn('const POLL_LENTO_MS = 3000;', am)

        dc = self.client.get('/assets/dock.css').text
        self.assertIn('.medidores-desligados', dc)
        self.assertIn('.energia-cmd', dc)

    def test_extract_rnp_chat_e_poll_nao_bloqueiam_nem_reprocessam_mensagens_inalteradas(self):
        class FakeFrame:
            def __init__(self, payload):
                self.payload = payload
                self.scripts = []

            async def evaluate(self, script, *args):
                self.scripts.append(script)
                return self.payload

        batch = [
            {'id': 'rnp_1', 'source': 'rnp', 'user': 'Participante', 'text': 'Olá :smile:', 'time': '19:00', 'avatar': ''},
            {'id': 'rnp_2', 'source': 'rnp', 'user': 'Maria', 'text': 'Boa noite', 'time': '19:01', 'avatar': 'https://img/a.png'},
        ]
        frame = FakeFrame(batch)
        asyncio.run(server.extract_rnp_chat(frame))
        self.assertEqual(len(self.state.chat_messages), 2)
        self.assertIn('window._rnpLastChatOpenAttempt', frame.scripts[0])

        # Segunda rodada idêntica não deve reordenar nem recriar entradas existentes
        first_ref = self.state.rnp_messages_map['rnp_1']
        ts_before = first_ref['timestamp']
        asyncio.run(server.extract_rnp_chat(frame))
        self.assertIs(self.state.rnp_messages_map['rnp_1'], first_ref)
        self.assertEqual(self.state.rnp_messages_map['rnp_1']['timestamp'], ts_before)

        # Quando o remetente real de rnp_1 passa a ser conhecido, atualiza in-place
        batch[0] = {'id': 'rnp_1', 'source': 'rnp', 'user': 'João', 'text': 'Olá :smile:', 'time': '19:00', 'avatar': ''}
        asyncio.run(server.extract_rnp_chat(frame))
        self.assertEqual(self.state.rnp_messages_map['rnp_1']['user'], 'João')

        # Enquete: não deve varrer todos os divs com innerText e deve publicar ao fechar votação
        poll_frame = FakeFrame({
            'is_active': True, 'status': 'voting', 'question': 'Enquete 1',
            'options': [{'id': 0, 'text': 'Sim', 'votes': 0, 'percentage': 0}],
            'total_votes': 0, 'published': False,
        })
        asyncio.run(server.extract_rnp_poll(poll_frame))
        self.assertNotIn("querySelectorAll('aside, div, section')", poll_frame.scripts[0])
        self.assertEqual(self.state.poll_data['status'], 'voting')
        poll_frame.payload = None
        asyncio.run(server.extract_rnp_poll(poll_frame))
        self.assertEqual(self.state.poll_data['status'], 'published')


if __name__ == '__main__':
    unittest.main()
