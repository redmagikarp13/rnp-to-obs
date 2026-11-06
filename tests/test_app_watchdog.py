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

    def test_nao_reanima_quando_saida_intencional_codigo_zero(self):
        # Encerramento pelo /control (POST /api/server/stop) sai com os._exit(0):
        # o watchdog não deve reanimar um servidor que foi mandado parar.
        self.assertFalse(app._precisa_reanimar(_Proc(0), encerrando=False, iniciado_pelo_app=True))

    def test_backoff_cresce_exponencialmente_e_satura_no_teto(self):
        # Sem backoff, um servidor que falha no boot causa respawn infinito a cada 3s.
        # A espera deve dobrar por queda rapida consecutiva e parar no teto.
        self.assertEqual(app._proximo_backoff(0), 3)
        self.assertEqual(app._proximo_backoff(1), 3)
        self.assertEqual(app._proximo_backoff(2), 6)
        self.assertEqual(app._proximo_backoff(3), 12)
        self.assertEqual(app._proximo_backoff(4), 24)
        self.assertEqual(app._proximo_backoff(5), 48)
        self.assertEqual(app._proximo_backoff(6), 60)   # 96 -> teto 60
        self.assertEqual(app._proximo_backoff(20), 60)  # satura

    def test_decidir_espera_reseta_ao_ficar_de_pe(self):
        # Servidor reanimado que fica de pe zera a contagem (crash unico apos horas
        # de uptime deve religar logo, nao com backoff acumulado).
        self.assertEqual(app._decidir_espera(ficou_de_pe=True, falhas_consecutivas=4), (0, 3))

    def test_decidir_espera_acumula_ao_cair_de_novo(self):
        self.assertEqual(app._decidir_espera(ficou_de_pe=False, falhas_consecutivas=0), (1, 3))
        self.assertEqual(app._decidir_espera(ficou_de_pe=False, falhas_consecutivas=2), (3, 12))
        self.assertEqual(app._decidir_espera(ficou_de_pe=False, falhas_consecutivas=6), (7, 60))


if __name__ == '__main__':
    unittest.main()
