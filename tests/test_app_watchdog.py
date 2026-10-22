"""Testes do watchdog do lançador: quando reanimar o servidor."""
import sys
import unittest

sys.path.insert(0, '.')
import app


class _Proc:
    def __init__(self, code):
        self._code = code

    def poll(self):
        return self._code


class WatchdogTests(unittest.TestCase):
    def test_reanima_quando_processo_morre(self):
        self.assertTrue(app._precisa_reanimar(_Proc(1), encerrando=False, iniciado_pelo_app=True))

    def test_nao_reanima_se_ainda_vivo(self):
        self.assertFalse(app._precisa_reanimar(_Proc(None), encerrando=False, iniciado_pelo_app=True))

    def test_nao_reanima_durante_encerramento(self):
        # Fechar a janela encerra de propósito: o watchdog não pode religar.
        self.assertFalse(app._precisa_reanimar(_Proc(1), encerrando=True, iniciado_pelo_app=True))

    def test_nao_reanima_servidor_de_terceiros(self):
        # Servidor que já rodava (não foi o app que iniciou) não é para gerir.
        self.assertFalse(app._precisa_reanimar(_Proc(1), encerrando=False, iniciado_pelo_app=False))

    def test_nao_reanima_sem_processo_rastreado(self):
        self.assertFalse(app._precisa_reanimar(None, encerrando=False, iniciado_pelo_app=True))


if __name__ == '__main__':
    unittest.main()
