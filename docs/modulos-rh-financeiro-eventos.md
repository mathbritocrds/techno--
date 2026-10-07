# RH, financeiro e gestão de eventos

## Arquitetura

A implementação amplia o SIGI existente: FastAPI, HTML/JavaScript no mesmo domínio e SQLite ou PostgreSQL/Supabase. Mantém navegação, calendário, cores, cards, tabelas e diálogo existentes. `app/static/business.js` acrescenta controles às três telas; o salário base recebe apenas redução de espaçamento. Os arquivos preexistentes de infraestrutura TanStack permanecem no repositório, mas a aplicação executada é FastAPI. O build agora valida Python e JavaScript e empacota essa aplicação em `dist/sigi`; o preview de produção executa o pacote construído.

| Arquivo | Responsabilidade |
| --- | --- |
| `app/payroll.py` | Regras fiscais por competência, cálculos em Decimal e saldos/provisões |
| `app/business.py` | Serviços de folha, fechamento, DRE, pagamentos, anexos e escalas |
| `app/business_schema.py` | DDL SQLite e alterações aditivas para instalações existentes |
| `app/main.py` | Integração com autenticação, ponto, financeiro e reservas existentes |
| `supabase/schema.sql` | DDL PostgreSQL, upgrade idempotente, RLS e revogação de acesso público |
| `app/static/business.js` | Formulários e consultas adicionais com a identidade visual atual |
| `tests/test_business.py` | Casos financeiros, fiscais e de concorrência lógica da agenda |

Todas as rotas de RH e financeiro exigem administrador. Gestores podem consultar a agenda e editar reservas próprias do seu departamento; a matriz de permissões continua sendo aplicada. Funcionários consultam somente o próprio holerite e a própria escala. Escritas continuam no mecanismo de auditoria e notificações de atualização em tempo real.

## APIs

| Método e rota | Entrada / efeito |
| --- | --- |
| `GET /payroll?month=2026-10` | Prévia de competência aberta ou snapshot de competência fechada |
| `GET /payroll/rules?month=2026-10` | Tabelas fiscais, deduções e versão |
| `GET, PUT /employees/{id}/payroll-profile` | Admissão, desligamento, dependentes, pensão, deduções, VT, VA, FGTS e encargos |
| `POST /payroll/close` | `{ "month": "2026-10" }`; fecha e gera contas a pagar pelo líquido |
| `GET /payroll/annual-cost?year=2026` | Realizado nas folhas fechadas e projeção nas abertas, separados |
| `POST /employees/{id}/vacations/quote` | Período aquisitivo, início e dias; calcula remuneração bruta, terço, dobra e prazo |
| `POST /employees/{id}/vacations` | Registra gozo, controla sobreposição e consome saldo do período aquisitivo |
| `GET /employee/payroll?month=2026-10` | Holerite individual com IRRF, FGTS e descontos de benefícios |
| `POST /finance` | Acrescenta `category` e `competence`; receita recebe categoria `revenue` |
| `PATCH /finance/{id}/paid` | Baixa idempotente, congela valor, autor e data no histórico |
| `DELETE /finance/{id}` | Exclui apenas avulsos em aberto; preserva salários gerados e pagamentos |
| `GET /finance/payments?month=2026-10` | Histórico filtrado pelo mês da baixa, com metadados de anexos |
| `POST /finance/payments/{id}/attachments` | JSON: `name`, `mime`, `content_base64`; PDF/PNG/JPEG até 6 MB |
| `GET /finance/payments/{id}/attachments/{file_id}` | Download autenticado e sem cache, com nomes UTF-8 |
| `GET /finance/dre?start=2026-01&end=2026-10` | Relatório gerencial por competência, independente da baixa |
| `POST, PUT /spaces[/{id}]` | Acrescenta capacidade e opção de compartilhar |
| `POST /space-bookings` | Participantes, exclusividade, grupo de compatibilidade, detalhes e status |
| `PATCH /space-bookings/{id}` | Edita evento e recalcula escala atomicamente; valida todos os conflitos |
| `DELETE /space-bookings/{id}` | Cancela sem eliminar o evento e libera a escala |
| `GET, POST /spaces/{id}/staff-rules` | Equipe automática, função e minutos antes/depois do evento |
| `DELETE /spaces/{id}/staff-rules/{rule_id}` | Remove vínculo e atualiza escalas daquele espaço |
| `GET /space-bookings/{id}/staff` | Escala por evento, com horários e nomes |
| `GET /employee/event-shifts` | Escalas restritas ao funcionário autenticado |

As falhas de entrada retornam 400/422; ausência de recursos retorna 404; conflito de agenda, capacidade, equipe ou saldo retorna 409. O fechamento e a baixa podem ser repetidos sem duplicar registros. A transação usa `BEGIN IMMEDIATE` no SQLite e lock transacional no PostgreSQL, aproveitando o adaptador existente. Uma falha de escala desfaz a criação/edição do evento e preserva a escala anterior.

## Modelo de dados e migração

O DDL executável completo está em `app/business_schema.py` para SQLite e `supabase/schema.sql` para PostgreSQL. A inicialização aplica o esquema aditivo; não recria nem remove tabelas existentes. Pagamentos antigos marcados como pagos são importados uma vez para o histórico com autor `legacy-import`; quando não existe timestamp, o vencimento serve de referência e não constitui evidência de uma baixa real naquela data.

| Tabela | Dados e relações principais |
| --- | --- |
| `employee_payroll_profiles` | PK/FK funcionário; contrato, deduções, benefícios e parâmetros patronais |
| `payroll_runs` | Competência única; fechamento, autor e versão fiscal |
| `payroll_items` | FK folha + funcionário; snapshot JSON; único por folha/funcionário |
| `vacation_records` | FK funcionário; início aquisitivo, início de gozo, dias e autor |
| `payment_history` | FK única transação; descrição, valor, categoria, competência, data, autor e origem |
| `payment_attachments` | FK pagamento; conteúdo base64, MIME, tamanho, SHA-256, autor e timestamp |
| `space_staff_rules` | FK espaço/funcionário; função, buffers; vínculo único por espaço/funcionário/função |
| `event_staff_assignments` | FK evento/funcionário; função e intervalo; único por evento/funcionário/função |
| `transactions` | Novas colunas categoria, competência e chave de origem única quando preenchida |
| `spaces` | Novas colunas capacidade e compartilhamento |
| `space_bookings` | Novas colunas status, participantes, exclusividade, compatibilidade e detalhes |

A DRE é uma consulta derivada de lançamentos e snapshots. Lançamentos externos importados são armazenados nas tabelas próprias descritas em [Navegação, DRE e ponto](navegacao-dre-ponto.md), sem copiar novamente contas ou folhas. Anexos ficam no banco, seguindo o padrão existente; tipos são validados pelo conteúdo binário e o SHA-256 permite identificar o arquivo. A API não oferece remoção de pagamentos, snapshots ou comprovantes. Isso preserva o histórico operacional, mas não substitui armazenamento externo imutável contra administradores do banco.

Para migrar de SQLite ao Supabase, `scripts/migrate_to_supabase.py` inclui as oito tabelas novas e preserva IDs e relações. Faça backup antes de aplicar em produção. O schema PostgreSQL foi executado duas vezes em PostgreSQL embarcado (PGlite), verificando sintaxe e idempotência; a conexão com o Supabase de produção não foi executada.

## Regras trabalhistas implementadas

### Folha mensal CLT

- Valores monetários são calculados com Decimal e arredondamento de meio centavo para cima. Horas extras continuam no modelo existente: salário / 220 × 1,5 × horas acima da jornada diária configurada.
- INSS é progressivo em 2026: 7,5% até R$ 1.621,00; 9% até R$ 2.902,84; 12% até R$ 4.354,27; 14% até R$ 8.475,55. Incide por faixa, com teto.
- IRRF escolhe o maior entre deduções legais cadastradas (INSS + R$ 189,59 por dependente + pensão judicial + outras deduções autorizadas) e desconto simplificado mensal de R$ 607,20. Aplica a tabela progressiva de 2026 e a redução: até R$ 5.000,00, limitado ao imposto e a R$ 312,89; entre R$ 5.000,01 e R$ 7.350,00, `978,62 - 0,133145 × rendimento tributável bruto`. A redução usa o rendimento, não a base após deduções.
- FGTS: remuneração bruta × 8%, ou 2% para aprendiz. É encargo do empregador. Acumulado significa valores calculados nas folhas fechadas deste sistema; não é saldo da Caixa nem comprovação de recolhimento.
- VT: `min(concessão, salário básico proporcional × percentual)`, percentual máximo 6%. VA: coparticipação contratada, limitada à concessão. Ambos são concedidos fora do líquido em dinheiro. Benefícios legados mantêm o tratamento existente de concessão em dinheiro não salarial; valores com natureza salarial precisam ser classificados/revistos pelo RH.
- Admissão/desligamento no meio do mês geram salário e benefícios proporcionais, divisor 30 e limite de um salário; mês integral recebe um salário. Cadastro sem admissão recebe apenas prévia e não pode ser fechado.
- Encargos patronais além do FGTS são configuráveis por funcionário, padrão 20%, permitindo informar o total aplicável ao regime da empresa. O RH deve ajustar INSS patronal, RAT/FAP e terceiros; não há inferência automática do regime tributário.

### 13º, férias e provisões

- 13º proporcional: remuneração de referência × avos / 12; cada mês com pelo menos 15 dias de contrato conta um avo. A remuneração de referência é salário + média de adicionais informada pelo RH.
- Férias usam ciclos de aniversário da admissão; gozo registrado reduz o saldo do período correspondente. Direito anual de 30, 24, 18 ou 12 dias é configurado pelo RH conforme faltas. Proporcionais usam avos de pelo menos 15 dias no ciclo atual, acrescidos de 1/3.
- Férias completas: remuneração × dias / 30 × 4/3. Após o fim do prazo concessivo, aplica dobra, inclusive ao terço. A calculadora indica a data-limite de pagamento: dois dias antes do início.
- Provisão mensal de 13º = remuneração / 12; férias = remuneração × dias de direito / 30 / 12 × 4/3; encargos sobre provisões usam FGTS + percentual patronal. Meses abaixo de 15 dias não constituem essas provisões.
- O custo anual mostra salários + benefícios pagos pela empresa + FGTS + encargos + provisões. Para não contar o salário das férias novamente sobre os 12 salários, desconta da composição a parcela salarial já alocada à provisão de férias e seus encargos. Para salário fixo, 30 dias e ano inteiro, a remuneração anual equivale a 12 salários + 13º + terço de férias, antes de benefícios e encargos.
- Fechamento congela resultados e gera salários líquidos no financeiro. Mudanças de salário, perfil e ponto afetam somente competências abertas. O custo anual distingue realizado em competências fechadas de projeções abertas.

**Escopo fiscal:** tabela cadastrada para 2026; competências de outros anos são recusadas para evitar aplicar alíquotas vencidas. O 13º e as férias são calculados como provisões/saldos e remuneração bruta; o módulo não liquida automaticamente férias/13º, não apura suas retenções exclusivas e não compensa adiantamentos de férias no salário mensal. O registro de gozo controla saldo, não efetua pagamento. Esses fluxos exigem extensão própria antes de utilizar o sistema como folha oficial completa. Não inclui rescisão, múltiplos vínculos de INSS, afastamentos, médias automáticas, abono pecuniário, variáveis da convenção coletiva ou envio ao eSocial/FGTS Digital. Não realiza transferências bancárias.

## Financeiro e DRE

Receitas e despesas são reconhecidas pela competência cadastrada; na migração, usa-se o mês do vencimento. A DRE soma receitas, impostos/deduções, custos, despesas operacionais, pessoal e despesas financeiras. Pessoal inclui `accrual_cost` de folhas fechadas mais lançamentos avulsos da categoria `personnel`. Contas de salário geradas pela folha são ignoradas nesta soma: já fazem parte do snapshot. Competências sem fechamento são explicitamente informadas na tela; não se misturam projeções com resultados fechados.

O custo por setor também exclui salários gerados da soma de despesas para evitar duplicidade. O fluxo de caixa inclui salários em aberto nas contas a pagar e reduz a previsão adicional da folha pelo valor de salários já gerados. Encargos/retenções remanescentes permanecem na previsão, sem gerar automaticamente guias de recolhimento. A previsão de seis meses após 2026 usa o custo atual como projeção, sem apresentar cálculo fiscal para outro ano.

## Agenda e escalas

Eventos só compartilham espaço se o recurso permitir, todos forem compartilháveis e todos tiverem o mesmo grupo de compatibilidade não vazio. Uma varredura dos inícios/fins calcula pico de participantes simultâneos; eventos adjacentes não conflitam. Status cancelado/concluído não bloqueia novas reservas. Intervalos incluem fuso e são normalizados para UTC, com precisão de minuto.

Vínculos de preparação, atendimento e manutenção geram escalas por evento. Buffers configuráveis ampliam o intervalo reservado ao funcionário; funcionários inativos ou escalados em outro evento provocam conflito e rollback. Alterar/cancelar evento ou equipe recalcula as escalas. Duas funções do mesmo funcionário no mesmo evento podem coincidir; eventos distintos nunca podem sobrepor a escala desse funcionário. Férias não são integradas à disponibilidade da escala nesta versão.

## Verificação

- `python -m pytest -q`: testes existentes e novos, incluindo IRRF, INSS, saldos, fechamento idempotente, snapshots, DRE sem duplicidade, anexos, conflitos de capacidade e rollback de escala.
- `npm run build`: compilação Python, sintaxe dos scripts inline/externo e pacote de produção.
- `npm run typecheck`: validação da infraestrutura TypeScript existente.
- DDL PostgreSQL: execução dupla em PGlite com papéis `anon`/`authenticated`.
- `npm run test:ui`: fluxos de login, folha, RH, financeiro, comprovantes, equipe e eventos em desktop e celular, em desenvolvimento e no pacote de produção. Usa bancos temporários isolados; executado com Playwright, pois agent-browser não estava disponível. As fontes originais foram carregadas por interceptação local na verificação para contornar a restrição de rede do navegador, sem alterar a interface do produto.

## Fontes oficiais consultadas em 07/10/2026

- [Receita Federal — tributação de 2026](https://www.gov.br/receitafederal/pt-br/assuntos/meu-imposto-de-renda/tabelas/2026)
- [INSS — contribuição mensal de 2026](https://www.gov.br/inss/pt-br/direitos-e-deveres/inscricao-e-contribuicao/tabela-de-contribuicao-mensal)
- [FGTS — recolhimento do empregado](https://www.fgts.gov.br/Paginas/subpaginas/recolhimento-empregado.aspx)
- [Decreto 10.854/2021 — regras de vale-transporte](https://www2.camara.leg.br/legin/fed/decret/2021/decreto-10854-10-novembro-2021-791950-normaatualizada-pe.html)
- [Ministério do Trabalho — 13º e férias](https://www.gov.br/trabalho-e-emprego/pt-br/acesso-a-informacao/acoes-e-programas/programas-projetos-acoes-obras-e-atividades/proteja/duvidas-frequentes)

## Atualização complementar

Veja [Navegação, DRE e ponto](navegacao-dre-ponto.md) para submenus, importação/exportação CSV, chefia vinculada a funcionário, correções de GPS, gráficos e a tabela INSS com teto explícito de R$ 988,09.
