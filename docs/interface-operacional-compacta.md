# Interface operacional compacta

A atualização acrescenta `density.css` e `density.js` ao frontend servido pelo FastAPI. O backend, o banco, as rotas e a configuração do Render permanecem iguais. As funções e os scripts anteriores não foram substituídos. IDs, classes, eventos e nós dos formulários existentes são preservados.

O CSS reduz espaços, alturas de controles e linhas de tabelas em computadores com ponteiro preciso. No celular e em telas sensíveis ao toque, os controles mantêm alvos de 44 px. Formulários existentes em grid passam a distribuir mais campos por linha. Campos sequenciais de rótulo/controle recebem posições de grid sem mover os elementos.

Nenhuma cor original é redefinida. As superfícies, textos, botões, destaques e temas continuam usando as definições anteriores. Os novos controles usam somente as variáveis de cor já existentes.

O painel apresenta uma faixa com tarefas a fazer, em andamento, pessoas trabalhando agora e alertas. Esses valores vêm dos dados que o próprio painel já carregou; não há consulta extra. Os indicadores financeiros mensais e o gráfico de tarefas continuam disponíveis.

As páginas longas ganham botões de seção:

- Financeiro: contas e lançamentos; DRE e fluxo; histórico.
- Funcionários: equipe e pagamentos; custos e gráficos.
- Ponto: jornada e registros; local e empresa.
- Segurança: conta e equipe; permissões e parâmetros; produtividade e auditoria.

O botão **Tudo** mostra todas as seções. Alternar uma seção muda apenas sua visibilidade, conservando valores digitados, referências dos elementos e eventos. A seção escolhida é mantida durante atualizações da página atual; a preferência em memória é apagada ao sair da sessão. Setas, Home e End também permitem alternar as seções pelo teclado. A impressão inclui as seções completas.

Tabelas de registros sem busca própria recebem busca por texto e coluna, com comparação sem distinção de acentos. A busca filtra os registros carregados na página atual, informa a quantidade encontrada e permite limpar o filtro ou usar Escape. Listas com mais de 20 registros carregados também recebem páginas de 20, 50, 100 linhas ou a opção Todas, sem remover elementos do DOM. A busca considera todas essas linhas carregadas, inclusive as outras páginas locais. Isso não altera dados, totais, exportações ou o escopo da paginação do servidor. As buscas anteriores de contas, quadros e funcionários continuam responsáveis por seus próprios filtros; as demonstrações contábeis mantêm todos os subtotais.

As melhorias são aplicadas às telas que chegam de forma assíncrona por um observador de alterações estruturais, com processamento agrupado por frame. O código não intercepta a API nem as funções globais existentes. As tabelas do console React permanecem sob seu próprio controle.

O service worker inclui os dois arquivos e renova o cache da interface. O build continua produzindo `dist/sigi`, compatível com o mesmo processo de publicação no Render.

Validação: `npm run build`, `npm run typecheck`, `node scripts/qa-density.mjs` e `npm run test:ui`. A verificação de densidade compara as cores calculadas com e sem o novo CSS nos dois temas, mede as linhas, verifica buscas e seções com dados reais de teste e confirma a edição de material pelo fluxo original. Os testes de regressão usam **Tudo** para percorrer todas as funcionalidades anteriores.
