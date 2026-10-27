# SIGI: contabilidade, segurança e interface

## Arquitetura entregue

O sistema existente permanece em FastAPI, com SQLite local ou PostgreSQL/Supabase. O novo módulo **Financeiro → Contabilidade e demonstrações** é implementado em React e usa a mesma sessão autenticada. A interface anterior continua funcionando e recebeu paginação, feedback de ações, skeletons e ajustes de tabelas.

`app/ui/accounting.jsx` é compilado por esbuild para `app/static/accounting.bundle.js`. O bundle é carregado somente ao abrir o módulo. Ele é versionado para que instalações Python possam servir a interface sem Node em produção; `npm run build` o reconstrói. A API e o banco continuam responsáveis pelos cálculos e autorizações.

## Modelo e fechamento matemático

| Estrutura | Função |
| --- | --- |
| `ledger_accounts` | Plano de contas: código, nome, natureza, grupo do Balanço e categoria da DRE |
| `ledger_journals` | Data contábil, histórico, departamento, referência única, autor, status e vínculo de estorno |
| `ledger_lines` | Partidas: conta, débito em centavos ou crédito em centavos |
| `audit_events` | Alterações com autor, função, data, motivo, setor e valores anteriores/novos |
| `time_entry_adjustments` | Correções administrativas de horário sem modificar a marcação original |
| `effective_time_entries` | Visão do horário original e do último horário corrigido |
| `transactions.ledger_ref` | Identificador estável da origem financeira, independente da reutilização de IDs no SQLite |

DDL SQLite: `app/accounting_schema.py`. DDL PostgreSQL, funções, triggers, grants e RLS: `supabase/schema.sql`.

Os valores escriturados são inteiros em centavos. A entrada monetária usa Decimal e rejeita frações menores que um centavo. Cada partida aceita somente débito **ou** crédito positivo. O total de débitos deve ser idêntico ao total de créditos, com pelo menos duas partidas. A verificação ocorre na API e também em triggers no banco.

Ativo e despesa têm natureza devedora; passivo, patrimônio líquido e receita têm natureza credora. Lançamentos confirmados e suas partidas não podem ser editados ou excluídos. O estorno cria outro lançamento com lados invertidos e referência ao original. Repetir a mesma referência com os mesmos valores é idempotente; reutilizá-la com outro conteúdo é recusado.

No Balanço, o resultado acumulado ainda não destinado integra o PL. Isso mantém Ativo = Passivo + PL a partir dos saldos efetivamente escriturados. Não existe uma conta automática de ajuste para esconder divergências. O Balancete inclui também receitas/despesas ainda não encerradas; sem essas contas, não seria possível conferir os débitos e créditos do período.

O JSON mantém inteiros grandes como texto quando ultrapassam a precisão exata do JavaScript. A interface usa BigInt para totalizar e formatar centavos; o CSV usa Decimal. Percentuais são apresentados com duas casas decimais e ficam indisponíveis quando a receita é zero.

## DRE CPC 51 / IFRS 18

A demonstração apresenta as 15 linhas solicitadas, com destaque para resultado operacional, LAFT e resultado líquido. Receitas/despesas de investimento, financiamento, tributos sobre lucro e operações descontinuadas possuem contas separadas. A classificação fornecida é um modelo industrial por função; atividades principais diferentes exigem revisão do plano/classificação.

A norma introduz dois novos subtotais definidos e mantém o resultado do período como total obrigatório. A CVM aprovou o CPC 51 pela Resolução 237/2025, com vigência para exercícios iniciados a partir de 2027. A disponibilidade deste modelo no software não constitui uma certificação das demonstrações da empresa; reconhecimento, mensuração, notas e classificações dependem da escrituração e do contexto da entidade.

Referências oficiais consultadas:

- [CVM — Resolução 237 e CPC 51](https://conteudo.cvm.gov.br/legislacao/resolucoes/anexos/200/resol237.htm)
- [IFRS Foundation — IFRS 18](https://www.ifrs.org/issued-standards/list-of-standards/ifrs-18-presentation-and-disclosure-in-financial-statements/)
- [Supabase — RLS, privilégios e views](https://supabase.com/docs/guides/database/postgres/row-level-security)

Rendimentos de caixa/investimentos não devem ser classificados automaticamente como financiamento. IRPJ/CSLL são valores escriturados, sem cálculo fiscal automático. Estoques, imobilizado e demais saldos patrimoniais vêm do razão contábil; a valorização operacional da lista de materiais não substitui um lançamento patrimonial.

## Integração com dados existentes

- **Conta a receber:** débito em clientes e crédito em receita, na competência informada.
- **Conta a pagar manual:** débito na categoria de despesa e crédito em fornecedores.
- **Baixa:** movimenta caixa contra clientes, fornecedores ou obrigações trabalhistas, conforme a origem.
- **Folha fechada:** reconhece custo e provisões contra obrigações; salários gerados não repetem a despesa. A baixa do salário reduz a obrigação.
- **Exclusão de conta manual aberta:** cria um estorno vinculado na competência original, mantendo a trilha contábil.

Como as origens antigas armazenam competência mensal, seu reconhecimento usa o primeiro dia dessa competência. Pagamentos usam a data local efetiva. Lançamentos manuais aceitam uma data contábil específica.

Na área de integração, o administrador pode incorporar os dados anteriores até a data escolhida. Repetir a ação não duplica fontes. Para CSVs antigos da DRE, é necessário escolher a contrapartida patrimonial de receitas/despesas; sem escolha, eles permanecem pendentes. Isso evita presumir caixa, capital ou fornecedores para dados sem essa informação. Capital, estoque e demais saldos iniciais são registrados por lançamentos balanceados.

A DRE da página Contas e DRE usa as origens operacionais e agora mostra a estrutura de 15 linhas. A DRE do módulo React usa exclusivamente a escrituração confirmada e inclui os lançamentos manuais adicionais. Os dois fundamentos são identificados nas telas. O CSV detalhado anterior continua disponível para importação; o CSV da demonstração apresenta a estrutura CPC 51.

## API

| Rota | Uso |
| --- | --- |
| `GET /accounting/accounts` | Plano de contas |
| `POST /accounting/journals` | Escrituração balanceada e idempotente |
| `GET /accounting/journals` | Diário paginado, por período/setor |
| `POST /accounting/journals/{id}/reverse` | Estorno de lançamento manual, com motivo/data |
| `GET /accounting/reports` | DRE, Balanço e Balancete derivados da mesma agregação |
| `GET /accounting/export.csv` | CSV de DRE, Balanço ou Balancete |
| `GET /accounting/audit` | Auditoria detalhada paginada e por setor |
| `POST /accounting/integrate` | Integração explícita das origens anteriores, somente administrador |
| `POST /time-entries/{id}/adjustments` | Correção administrativa de horário |
| `GET /time-entries?paginated=true` | Registros de ponto com mês, página e tamanho limitados |
| `GET /finance/dre/statement.csv` | DRE de 15 linhas das origens operacionais |

Os relatórios agregam as partidas no SQL. Diário, auditoria e ponto usam LIMIT/OFFSET; o plano inicial é pequeno e o Balancete pagina suas contas na interface, mantendo os totais de todas elas. Os tamanhos de página são limitados no servidor. Exportações representam o período inteiro, não apenas a página visível.

## Segurança e auditoria

A autenticação do SIGI é feita pela API existente; seus tokens não são JWTs do Supabase Auth. Acesso direto de `anon` e `authenticated` às tabelas financeiras e à view de ponto é revogado.

As rotas contábeis usam uma role PostgreSQL dedicada `sigi_accounting_api`, NOLOGIN e NOBYPASSRLS. A API define role e departamento como parâmetros locais da transação, usando o principal já autenticado. Administradores consultam a empresa; gestores acessam/escrituram somente seu setor; operadores não acessam a contabilidade. As políticas da matriz de permissões também são verificadas pela API. As rotinas de integração e automação permanecem no backend privilegiado, após autorização.

A view de ponto usa `security_invoker=true`, para não contornar o RLS do PostgreSQL. A criação da role e os grants exigem que a migração seja executada pelo proprietário autorizado do banco. Nenhuma credencial do banco é enviada ao React.

Toda edição de material pela aplicação gera auditoria antes/depois na mesma transação. Ajustes de ponto exigem motivo, mantêm o dia original e a ordem entrada/saída, e não alteram NSR/hash/comprovante. O horário corrigido vale para espelhos, custos de horas e folhas abertas. Folhas fechadas preservam snapshots. Auditoria e ajustes possuem triggers de proteção contra alteração/exclusão.

## Interface e implantação

- Skeletons com shimmer representam KPIs, cards e tabelas; o acesso inicial também usa campos skeleton.
- Transições discretas e stagger limitado nas tabelas; respeito à redução de movimento.
- Botões críticos ficam imediatamente ocupados/desabilitados. Formulários React mostram a diferença entre débitos e créditos antes de confirmar.
- Tabelas alternadas, alinhamento monetário, tooltips acessíveis por foco e indicadores discretos.
- Temas claro/escuro e layout móvel, com teclado em abas e dialogs nativos.

`npm run build` reconstrói o React e o pacote Python; a inicialização aplica o schema. O schema PostgreSQL agora é enviado integralmente, preservando blocos de funções/triggers. O migrador SQLite→Supabase inclui razão, auditoria e ajustes: insere cabeçalhos em rascunho, copia partidas e só então confirma cada lançamento sob validação do banco. O cache do service worker foi atualizado.

## Validação

- `python -m pytest -q`: testes existentes e novos de partidas, centavos, subtotais, saldos, estornos, integração, auditoria e escopo.
- `node scripts/test-accounting-pg.mjs`: schema aplicado duas vezes em PostgreSQL embarcado; triggers, leitura/escrita por setor, operadores e bloqueio de anon/authenticated, incluindo a view de ponto.
- `node --test scripts/location.test.mjs`: regressões de localização preservadas.
- `npm run build`, `npm run typecheck` e `npm run test:ui`: desktop/celular e desenvolvimento/pacote de produção, com criação/estorno, relatórios, CSV, paginação, temas e fluxos anteriores.

Os testes usam dados temporários e localização simulada. A migração e o deploy não foram executados no Supabase de produção; integrar o PR e atualizar o backend/arquivos estáticos é necessário para disponibilizar a alteração no sistema publicado.
