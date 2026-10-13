# 📖 Guia de Operação e Boas Práticas — RNP to OBS Studio

Este documento serve como manual operacional para técnicos de transmissão, operadores de streaming e equipes audiovisuais que utilizam o **RNP to OBS Studio** em produções ao vivo, bancas, seminários e eventos institucionais.

---

## 🧭 Fluxo de Trabalho Recomendado para uma Transmissão

### 1. Pré-Live (15 a 30 minutos antes do evento)
1. **Iniciar o Servidor / Painel:**
   - Dê um duplo clique no atalho **`RNP to OBS - Painel de Controle`** ou execute `Abrir Painel.bat`.
   - Certifique-se de que a luz indicadora no cabeçalho fique **Verde (Pronto para conectar)**.
2. **Entrar na Sala da RNP:**
   - Cole o link da sala de conferência da RNP no campo correspondente (ex: `https://conferenciaweb.rnp.br/ifes/...`).
   - Defina o nome do bot que aparecerá na conferência (padrão: `OBS Studio`).
   - Clique no botão **Conectar na Sala RNP**.
   - O sistema entrará automaticamente como ouvinte/espectador silencioso, contornando diálogos de áudio e mantendo o bypass anti-inatividade ativo.
3. **Vincular a Live do YouTube (se aplicável):**
   - Cole o link ou ID da live no campo **Link da Live no YouTube**.
   - Clique em **Vincular Live**. O status exibirá `Live do YouTube conectada`.
4. **Verificar os Sinais no OBS Studio:**
   - Abra o OBS Studio e verifique se as cenas (`Palco`, `Libras`, `Grid`, `Apresentação`, `Chat`) estão recebendo os feeds.
   - Verifique se a fonte do **palco** (`/stage`) está movimentando o mixer de som do OBS ao haver falas na sala — é ela que carrega o master de áudio (com "Controlar áudio via OBS" marcado).

---

## 🎚️ Operação durante a Live

### Seleção e Gestão de Intérprete de Libras
- O sistema busca automaticamente por palavras como `Libras`, `Intérprete` ou `ILS` no nome do participante.
- Para marcar ou trocar o intérprete manualmente em tempo real:
  - Localize o card da câmera correspondente na seção **Câmeras Detectadas**.
  - Clique no botão **🤟 Marcar como Libras**.
  - O card receberá o selo ciano `[LIBRAS]`.
  - O endpoint `/libras` e o layout paritário no `/stage` atualizarão instantaneamente a imagem do intérprete.
- Para desmarcar: basta clicar novamente em **Desmarcar Libras**.

### Troca de Layouts do Palco (`/stage`)
O operador pode alternar os layouts a qualquer instante conforme o momento da transmissão:
- **Abertura / Apresentação de Slides com Palestrante:** Use o preset `Lado a Lado (70/30)`.
- **Destaque Máximo aos Slides com Palestrante Discreto:** Use `PIP Canto Inferior` ou `PIP Canto Superior`.
- **Mesa Redonda / Debate entre Convidados:** Use `Split 50 / 50` ou alterne para a cena de `Grid de Câmeras`.
- **Apenas Slides em Tela Cheia:** Use `Somente Slides`.

### Fixar Palestrante Principal (*Pin Speaker*)
- Ao fixar um participante específico (clicando em **📌 Fixar** no card da câmera), ele ganha prioridade absoluta de exibição ao lado dos slides nos modos de palco.
- Se nenhum palestrante for fixado, o sistema alterna automaticamente para a câmera de quem estiver com a voz ativa no momento.

### Destaque de Mensagens do Chat (*Lower-Thirds*)
- Todas as mensagens do chat da RNP e do YouTube aparecem em tempo real no feed do Dock de Controle.
- Para colocar uma pergunta ou comentário do público na tela:
  - Passe o cursor sobre a mensagem no feed do Dock e clique em **⭐ Destacar**.
  - A mensagem surgirá com animação suave na cena do OBS que contém a fonte `/chat/featured`.
  - Para remover o destaque, clique no botão **Ocultar Destaque** no painel de controle.

### Monitoramento de Enquetes ao Vivo
- Quando o mediador da sala BBB disparar uma enquete ou publicar os resultados, o card `/poll` surgirá automaticamente na tela do OBS.
- O temporizador padrão manterá o resultado visível por 15 segundos (com barra regressiva animada) e fará o fade-out automático, sem necessidade de intervenção do operador.
- No Dock, o operador pode forçar a exibição contínua, alterar o estilo visual (`Neon`, `Glass`, `Discord`, `Cyberpunk`, `Minimal`) ou fechar o card manualmente.

---

## 🛑 Pós-Live e Encerramento

1. No cabeçalho do Dock de Controle, clique em **Desconectar da Sala**.
2. Ao fechar a janela do painel, escolha **Sim** na caixa de diálogo de confirmação para finalizar o servidor e liberar as portas e memória.
3. Se preferir encerrar via linha de comando ou atalho, basta executar **`Parar Servidor.bat`**.

---

## 🔍 Solução de Problemas Frequentes (FAQ)

### 1. O áudio está com eco ou repetindo na transmissão
- **Causa:** Mais de uma fonte foi marcada no OBS com "Controlar áudio via OBS". O master de áudio sai **apenas** pelo `/stage`.
- **Solução:** Deixe "Controlar áudio via OBS" marcado **somente** na fonte `http://localhost:3333/stage` e **desmarcado** em `/libras`, `/cameras/grid`, `/presentation`, `/chat` etc. O endpoint `/audio` não serve mais master (devolve 404); ele existe apenas como `/audio?source=<canal>` para trilhas individuais.

### 2. A imagem da câmera de Libras não atualizou na cena do OBS
- **Causa:** O cache do Browser Source do OBS pode reter um quadro antigo se a cena não for atualizada.
- **Solução:** Clique com o botão direito na fonte no OBS e escolha **Atualizar cache da página atual**. A versão mais recente do servidor já inclui sistema de *cache-busting* contínuo para evitar esse comportamento.

### 3. As fotos de perfil dos participantes do chat não aparecem
- **Causa:** Políticas de CORS do YouTube ou participantes da RNP que entraram como convidados sem foto cadastrada.
- **Solução:** O servidor gera automaticamente avatares SVG estilizados com as iniciais do participante e cor exclusiva para cada nome, garantindo visual profissional e sem contaminação entre usuários.

### 4. O firewall do Windows solicitou permissão
- **Solução:** Permita a conexão em redes privadas/locais para o Python e para o executável `RNP to OBS.exe`. O servidor opera exclusivamente no endereço de loopback local (`127.0.0.1:3333`).

### 5. O som da sala some quando troco de cena
- **Causa:** O master de áudio é acoplado à fonte `/stage`. Se essa fonte não estiver visível na cena ativa e a opção "Shutdown source when not visible" estiver ligada, o OBS suspende a página e o áudio junto.
- **Solução:** Nas propriedades da fonte `/stage`, **desligue** "Shutdown source when not visible" (e mantenha "Controlar áudio via OBS" ligado). Alternativa: inclua o `/stage` em todas as cenas, ainda que fora do enquadramento, ou use uma trilha individual `/audio?source=<canal>` na cena que precisa de som sem o palco.
