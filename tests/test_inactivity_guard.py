"""Regressão da Blindagem Anti-Inatividade (web/media-source.js).

O modal "Verificação de atividade do usuário" da RNP com contagem regressiva
apareceu sobre a apresentação no /stage porque o keep_alive (ciclo de 25 s)
não conhece o botão "Verificar". O guard em página deve confirmá-lo sozinho,
em subsegundo, via MutationObserver instalado por ensure_media_adapter.
"""
import asyncio
import unittest

from playwright.async_api import async_playwright

from media_bridge import ensure_media_adapter

PAGE_HTML = """<!DOCTYPE html>
<html lang="pt-BR"><body>
 <div id="layout">
  <div id="presentationContainer"><p>slide ao fundo</p></div>
  <div class="modal fade in" role="dialog">
    <div class="modal-dialog">
      <div class="modal-content">
        <div class="modal-header"><h4>Verificação de atividade do usuário</h4></div>
        <div class="modal-body"><p>Verifica se o usuário ainda está na sessão (275)</p></div>
        <div class="modal-footer">
          <button class="btn btn-default" onclick="window._sairClicado = true">Sair da sessão</button>
          <button class="btn btn-primary" onclick="window._verificarClicado = true; this.closest('.modal').remove()">Verificar</button>
        </div>
      </div>
    </div>
  </div>
 </div>
</body></html>"""

MODAL_ATRASADO_HTML = """<!DOCTYPE html>
<html lang="pt-BR"><body>
  <div id="layout">sala carregada</div>
  <script>
    setTimeout(() => {
      const modal = document.createElement('div');
      modal.setAttribute('data-test', 'inactivityWarningModal');
      modal.innerHTML = '<button data-test="inactivityWarningConfirmBtn" ' +
        'onclick="window._confirmado = true">Confirmar</button>';
      document.body.appendChild(modal);
    }, 300);
  </script>
</body></html>"""


class GuardInatividadeTests(unittest.TestCase):
    def _rodar(self, html, script):
        async def main():
            async with async_playwright() as p:
                browser = None
                for ch in ('msedge', 'chrome', None):
                    try:
                        browser = await p.chromium.launch(headless=True, **({'channel': ch} if ch else {}))
                        break
                    except Exception:
                        continue
                if not browser:
                    raise RuntimeError('Nenhum navegador Chromium disponível')
                page = await browser.new_page()
                await page.set_content(html)
                await ensure_media_adapter(page.main_frame)
                try:
                    return await script(page)
                finally:
                    await browser.close()
        return asyncio.run(main())

    def test_modal_rnp_verificar_e_confirmado_automaticamente(self):
        async def script(page):
            await page.wait_for_function('() => window._verificarClicado === true', timeout=4000)
            return {
                'verificar': await page.evaluate('() => window._verificarClicado === true'),
                'sair': await page.evaluate('() => window._sairClicado === true'),
                'contador': await page.evaluate('() => window._rnpInactivityDismissed || 0'),
            }
        resultado = self._rodar(PAGE_HTML, script)
        self.assertTrue(resultado['verificar'])
        self.assertFalse(resultado['sair'], 'o guard nunca pode clicar em sair da sessão')
        self.assertGreaterEqual(resultado['contador'], 1)

    def test_modal_que_monta_depois_da_injecao_tambem_e_confirmado(self):
        async def script(page):
            await page.wait_for_function('() => window._confirmado === true', timeout=4000)
            return await page.evaluate('() => window._confirmado === true')
        self.assertTrue(self._rodar(MODAL_ATRASADO_HTML, script))

    def test_popup_microfone_e_ocultado_sem_afetar_presentation_container(self):
        html = """<!DOCTYPE html>
<html lang="pt-BR"><body>
  <div id="layout">
    <div id="presentationContainer">
      <div id="micPopupBox" class="mute-toast">
        <span>Clique em <svg></svg> para ativar seu microfone.</span>
      </div>
    </div>
  </div>
</body></html>"""
        async def script(page):
            await page.wait_for_function('() => (window._rnpHiddenMicPopups || 0) >= 1', timeout=4000)
            return await page.evaluate('''() => ({
                popupDisplay: getComputedStyle(document.getElementById('micPopupBox')).display,
                presDisplay: getComputedStyle(document.getElementById('presentationContainer')).display,
            })''')
        res = self._rodar(html, script)
        self.assertEqual(res['popupDisplay'], 'none')
        self.assertNotEqual(res['presDisplay'], 'none')

    def test_enquetes_e_opcoes_de_voto_nunca_sao_clicadas(self):
        html = """<!DOCTYPE html>
<html lang="pt-BR"><body>
  <div id="layout">
    <div id="presentationContainer"><p>slide de fundo</p></div>
    <!-- Modal de Enquete ativo no BigBlueButton -->
    <div id="polling-container" data-test="pollingContainer" role="dialog">
      <div data-test="pollQuestion">Você concorda com a proposta desta atividade na sessão?</div>
      <div class="poll-options">
        <button data-test="pollAnswerOption" onclick="window._votoSim = true">Sim</button>
        <button data-test="pollAnswerOption" onclick="window._votoNao = true">Não</button>
        <button data-test="pollAnswerOption" onclick="window._votoContinuar = true">Continuar</button>
        <button class="btn btn-default" onclick="window._votoVerificar = true">Verificar</button>
      </div>
    </div>
  </div>
</body></html>"""
        async def script(page):
            # Dá tempo para qualquer MutationObserver ou guard rodar
            await asyncio.sleep(1.0)
            return await page.evaluate('''() => ({
                votoSim: !!window._votoSim,
                votoNao: !!window._votoNao,
                votoContinuar: !!window._votoContinuar,
                votoVerificar: !!window._votoVerificar,
                inactivityCount: window._rnpInactivityDismissed || 0
            })''')
        res = self._rodar(html, script)
        self.assertFalse(res['votoSim'], 'O guard NUNCA pode votar "Sim" na enquete!')
        self.assertFalse(res['votoNao'], 'O guard NUNCA pode votar "Não" na enquete!')
        self.assertFalse(res['votoContinuar'], 'O guard NUNCA pode clicar em "Continuar" dentro da enquete!')
        self.assertFalse(res['votoVerificar'], 'O guard NUNCA pode clicar em "Verificar" dentro de um contêiner de enquete!')
        self.assertEqual(res['inactivityCount'], 0)


if __name__ == '__main__':
    unittest.main()
