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
- **Mesa Redonda / Debate entre Convidados:** Use `Split 50 / 50`, `Todas + Libras (sem slide)` ou `Só Câmeras (sem slide)`.
- **Palestrante + Intérprete (sem slides):** Use `Câmera + Libras (sem slide)`.
- **Apenas Slides em Tela Cheia:** Use `Somente Slides`.

### Foco e Fixação de Câmeras (*Focus* vs *Pin*)
- **Em Foco (Foco):** Destaca uma ou mais câmeras na tela de retorno (`/tv`) e prioriza o participante como câmera principal nos layouts de palco.
- **Fixar (Fixar):** Restringe o palco às câmeras fixadas. Se nenhuma câmera for fixada, o palco alterna automaticamente conforme quem está falando.

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

1. No cabeçalho do Dock de Controle, clique em **Desconectar**.
2. Fechar a janela do painel encerra automaticamente o servidor iniciado pelo aplicativo (sem necessidade de confirmação adicional).
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
- **Solução:** Permita a conexão em redes privadas/locais para o Python e para o executável `RNP to OBS.exe`. Por padrão, o servidor escuta apenas em loopback (`127.0.0.1:3333`); quando a opção **Acesso na Rede Local (LAN)** está habilitada no painel, ele escuta em `0.0.0.0:3333` para permitir abrir `/tv` em outras máquinas da rede local.

### 5. O som da sala some quando troco de cena
- **Causa:** O master de áudio é acoplado à fonte `/stage`. Se essa fonte não estiver visível na cena ativa e a opção "Shutdown source when not visible" estiver ligada, o OBS suspende a página e o áudio junto.
- **Solução:** Nas propriedades da fonte `/stage`, **desligue** "Shutdown source when not visible" (e mantenha "Controlar áudio via OBS" ligado). Alternativa: inclua o `/stage` em todas as cenas, ainda que fora do enquadramento, ou use uma trilha individual `/audio?source=<canal>` na cena que precisa de som sem o palco.

### 6. Previews de vídeo, medidores VU e monitor local vêm desligados ao abrir o painel
- **Comportamento esperado:** Para poupar decodificadores de vídeo e CPU na máquina da transmissão, os 3 previews do painel (`Câmeras`, `Apresentação` e `Palco`), os **medidores VU individuais** e o **Monitor Local de Áudio** iniciam desligados/mudos por padrão. A preferência fica salva no navegador quando você alternar os botões (`Mostrar preview`, `Ligar medidores` ou `Monitor Local`).

### 7. O áudio engasga quando o OBS ou o painel está em segundo plano
- **Solução:**
  1. Evite manter Browser Sources apontando diretamente para a URL da sala BBB (`conferenciaweb.rnp.br/...`) abertas junto com a ponte — cada uma roda um cliente BBB completo e satura a CPU. Use apenas os endpoints leves da ponte (`/stage`, `/cameras/grid`, `/presentation`, `/libras`, `/chat`).
  2. No bloco **Fontes para o OBS Studio** do painel (`/control`), abra o guia recolhível **⚡ Áudio trava em segundo plano? Ajustes de energia do Windows** para copiar os comandos de `powercfg` (estado mínimo da CPU em 100% na tomada, desativação de ASPM PCIe e suspensão seletiva USB) e de prioridade de áudio/rede do Windows (MMCSS e desativação de *Power Throttling*).
