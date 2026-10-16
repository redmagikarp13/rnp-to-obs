"""Janela nativa do painel: pywebview com fallback para navegador em modo app."""
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, '.')
import dock_window


class _Event:
    def __init__(self):
        self.subs = []

    def __iadd__(self, fn):
        self.subs.append(fn)
        return self


class _Window:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.events = types.SimpleNamespace(closed=_Event())


class _Webview:
    def __init__(self, falha=None):
        self.janelas = []
        self.start_kwargs = None
        self.falha = falha

    def create_window(self, *args, **kwargs):
        if self.falha:
            raise self.falha
        j = _Window(*args, **kwargs)
        self.janelas.append(j)
        return j

    def start(self, **kwargs):
        self.start_kwargs = kwargs


class DockWindowTests(unittest.TestCase):
    def test_janela_nativa_usa_pywebview_com_storage_e_dimensoes(self):
        # O dock guarda prefs no localStorage (aba ativa, previews off); sem
        # storage_path + private_mode=False o operador resetaria a cada abertura.
        stub = _Webview()
        fechou = []
        cb = lambda *a: fechou.append(True)
        with mock.patch.dict(sys.modules, {'webview': stub}):
            dock_window.abrir_janela_pywebview(dock_window.SERVER_URL, cb)
        self.assertEqual(len(stub.janelas), 1, 'Deve criar exatamente uma janela.')
        kw = stub.janelas[0].kwargs
        self.assertEqual(kw['width'], 1360)
        self.assertEqual(kw['height'], 860)
        self.assertEqual(kw['min_size'], (900, 600))
        self.assertIn('RNP to OBS', kw['title'])
        self.assertEqual(stub.start_kwargs.get('private_mode'), False,
                         'localStorage precisa persistir entre aberturas.')
        self.assertTrue(str(stub.start_kwargs.get('storage_path')).endswith('RNP_to_OBS_Profile'),
                        'O storage deve ir para o perfil existente do painel.')
        self.assertEqual(len(stub.janelas[0].events.closed.subs), 1,
                         'O fechamento da janela precisa avisar o lançador.')

    def test_fallback_para_navegador_quando_pywebview_falha(self):
        # Máquina sem WebView2/backend não pode ficar sem painel: cai no caminho
        # Edge/Chrome --app= de sempre, com o mesmo perfil e o prompt ao fechar.
        stub = _Webview(falha=RuntimeError('sem backend'))
        proc = mock.Mock()
        fechou = []
        cb = lambda *a: fechou.append(True)
        with mock.patch.dict(sys.modules, {'webview': stub}), \
             mock.patch.object(dock_window.subprocess, 'Popen', return_value=proc) as pop:
            dock_window.launch_app_window(cb)
        cmd = pop.call_args[0][0]
        self.assertTrue(any(a.startswith('--app=') for a in cmd),
                        'O fallback deve abrir o navegador em modo app.')
        self.assertTrue(any('RNP_to_OBS_Profile' in a for a in cmd),
                        'O fallback deve reusar o perfil do painel.')
        proc.wait.assert_called_once()
        self.assertEqual(fechou, [True], 'O prompt de encerrar roda após a janela fechar.')

    def test_fallback_quando_pywebview_nao_esta_instalado(self):
        with mock.patch.dict(sys.modules, {'webview': None}), \
             mock.patch.object(dock_window, 'abrir_janela_navegador') as nav:
            dock_window.launch_app_window()
        nav.assert_called_once()


class FecharJanelaEncerraServicoTests(unittest.TestCase):
    def test_fechar_janela_encerra_servico_iniciado_pelo_app(self):
        # Pedido do usuário: encerrar a janela encerra o serviço — sem prompt.
        import app
        app.SERVIDOR_INICIADO_PELO_APP = True
        with mock.patch.object(app, 'stop_server_api') as stop:
            app.parar_servidor_ao_fechar()
        stop.assert_called_once()

    def test_fechar_janela_nao_mata_servico_de_terceiros(self):
        # Um servidor que já rodava antes do app (dev, ou operação só pelo OBS)
        # não é do app para matar: fechar a janela não pode derrubá-lo.
        import app
        app.SERVIDOR_INICIADO_PELO_APP = False
        with mock.patch.object(app, 'stop_server_api') as stop:
            app.parar_servidor_ao_fechar()
        stop.assert_not_called()


if __name__ == '__main__':
    unittest.main()
