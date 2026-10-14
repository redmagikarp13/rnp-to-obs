import asyncio
import os
import re
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
        for idb in ('btnToggleCamPreview', 'btnToggleScreenPreview'):
            self.assertIn(idb, html, f'Botão {idb} de controle de preview ausente.')

    def test_escudo_anti_inatividade_fica_na_barra_de_status(self):
        # Pedido do usuário: o aviso de anti-inatividade é estado global da sessão,
        # então mora ao lado do indicador de conexão, não enterrado no grupo Conexão.
        html = self.client.get('/control').text
        barra = html[html.index('dock-statusbar'):html.index('dock-audio-fixed')]
        self.assertIn('shieldBadge', barra,
                      'O escudo anti-inatividade deve ficar na barra de status.')
        conexao = html[html.index('id="group-conexao"'):html.index('id="group-palco"')]
        self.assertNotIn('shieldBadge', conexao,
                         'O escudo não pode ficar duplicado no grupo Conexão.')

    def test_shell_do_dock_tem_zona_fixa_e_quatro_grupos(self):
        html = self.client.get('/control').text
        for marcador in ('dock-statusbar', 'dock-audio-fixed', 'dock-tabs', 'dock-groups'):
            self.assertIn(marcador, html, f'Shell do dock precisa de {marcador}.')
        for grupo in ('conexao', 'palco', 'cameras', 'chat'):
            self.assertIn(f'data-group="{grupo}"', html, f'Grupo {grupo} ausente.')
        # O mixer fixo vem ANTES das abas (zona fixa no topo).
        self.assertLess(html.index('dock-audio-fixed'), html.index('dock-tabs'),
                        'O mixer fixo deve vir antes da barra de abas.')
        # Apresentação/Slides mora no grupo Palco, não solto depois de Chat.
        palco = html[html.index('id="group-palco"'):html.index('id="group-cameras"')]
        self.assertIn('Apresentação', palco, 'Slides/Apresentação deve estar no grupo Palco.')

    def test_barra_de_status_nao_duplica_medidor_master(self):
        html = self.client.get('/control').text
        barra = html[html.index('dock-statusbar'):html.index('dock-audio-fixed')]
        self.assertNotIn('vuMasterBar', barra,
                         'Barra de status não pode repetir o medidor master do mixer.')
        fixa = html[html.index('dock-audio-fixed'):html.index('dock-tabs')]
        self.assertIn('vuMasterBar', fixa, 'O medidor master vive no mixer fixo.')

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

    def test_css_legado_sem_blocos_de_tema(self):
        # Os 5 blocos `body.theme-X .component` eram o mecanismo legado de tema; com
        # tokens, tema é só override de variável — bloco de tema em CSS é defeito.
        self.assertNotIn('body.theme-', server.CONTROL_PANEL_CSS,
                         'CONTROL_PANEL_CSS não pode mais conter overrides por tema.')

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


if __name__ == '__main__':
    unittest.main()
