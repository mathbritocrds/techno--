# Navegação, DRE e ponto

Esta atualização continua sobre o pacote RH/financeiro/eventos. Preserva a identidade visual, os cards e os módulos existentes.

## Interface e painel

- Painel continua como acesso direto. RH, Financeiro, Gestão de tarefas e Segurança abrem um submenu abaixo da navegação. As opções obedecem à função de acesso, usam `aria-expanded` e respeitam movimento reduzido. Em celular, a barra permite rolagem horizontal dentro da própria navegação.
- RH: funcionários/folha, departamentos, ponto/jornada. Financeiro: contas/DRE, custeio, matéria-prima e análises de custo. Gestão de tarefas: quadros, aprovações, mensagens e espaços/eventos. Segurança: conta/permissões e integrações.
- O painel oculta Primeiros passos quando os seis requisitos estão completos. A configuração `settings.setup_completed=1` preserva essa conclusão, mesmo quando um cadastro inicial é excluído ou arquivado posteriormente.
- Novos gráficos mostram lucro por análise no DRE sintético e, separadamente, gastos por setor, receita mensal e resultado mensal da DRE contábil. A separação evita apresentar estimativas de custeio como lançamentos contábeis realizados.

## API da DRE

| Rota | Comportamento |
| --- | --- |
| `GET /finance/dre?start=AAAA-MM&end=AAAA-MM` | Totais, percentuais, lançamentos e agrupamentos por mês, setor e categoria |
| `GET /finance/dre/export.csv` | Exportação dos lançamentos do período, com ano, mês, setor, categoria e percentuais |
| `GET /finance/dre/template.csv` | Modelo UTF-8 de importação |
| `POST /finance/dre/import` | JSON `{content: string, preview: true/false}`; prévia padrão, confirmação transacional |

A consulta e a exportação aceitam `department_id` e `category`. O filtro de categoria restringe o detalhamento; o resumo da DRE continua cobrindo todas as categorias do período e setor. É possível selecionar um ano completo, um intervalo de competências e ordenar por valor ou percentual dos gastos na tela.

Administradores consultam todos os setores. Gestores vinculados a funcionários ativos consultam, exportam e importam somente o próprio departamento. Sem departamento, recebem uma orientação para completar o vínculo. Operadores e funcionários não acessam essas rotas. Contas a pagar, recebimentos e comprovantes continuam restritos ao administrador; o gestor usa a visão de DRE do seu setor sem solicitar essas rotas.

Percentual da receita = valor / receita do período e setor × 100. Percentual dos gastos = despesa / total das despesas do mesmo recorte × 100. Sem denominador, a API retorna `null` e a interface exibe `—`. A margem líquida usa resultado líquido / receita × 100. Valores acumulam com `Decimal` e são arredondados a centavos.

### Importação e exportação

CSV UTF-8, separador ponto e vírgula ou vírgula; valores aceitam `1000.00` ou `1.000,00`. Até 1 MB e 2.000 linhas por importação. Categorias: `revenue`, `tax`, `cost`, `operating`, `personnel`, `financial`.

Cabeçalhos do modelo: `origem;referencia;ano;mes;setor_id;setor;categoria;descricao;valor;percentual_receita;percentual_gastos`.

Obrigatórios: `ano`, `mes`, `categoria`, `descricao`, `valor`. `setor_id` identifica o setor; `setor` permite resolver um nome único. Gestores sem setor informado recebem automaticamente seu próprio setor. A importação não aceita setores inexistentes nem permite que um gestor escreva em outro setor.

- Prévia informa linhas válidas, erros, registros já existentes e uma amostra dos lançamentos. Nenhuma escrita contábil ocorre na prévia.
- Confirmação revalida todo o conteúdo. Uma linha inválida cancela o lote inteiro.
- `referencia` identifica o lançamento dentro do setor. Sem referência, o sistema calcula um hash dos campos contábeis. Linhas iguais com conteúdo idêntico precisam de referências diferentes para representar despesas distintas.
- Referências já existentes não são duplicadas. Alteração de valor, competência ou categoria em uma referência existente é recusada; a importação não reescreve histórico.
- Exportações incluem `origem=transaction`, `payroll` ou `import`. Fontes internas são ignoradas na reimportação para não duplicar contas ou folhas. Linhas novas devem usar `import` ou deixar origem vazia.
- Percentuais informados no arquivo são ignorados e recalculados. Fórmulas nunca são executadas; a exportação neutraliza células que poderiam ser interpretadas como fórmulas por planilhas.
- Importação representa lançamento contábil externo e não gera contas a pagar, baixa bancária ou histórico de pagamento. O usuário deve importar dados externos que ainda não estejam nas contas ou na folha.
- O formato é CSV compatível com Excel/LibreOffice, não arquivo nativo XLSX. Exportação ordena por competência, setor e categoria.

### Dados adicionais

`app/business_schema.py` contém o DDL SQLite; `supabase/schema.sql` contém o equivalente PostgreSQL e migrações idempotentes.

| Tabela / campo | Finalidade |
| --- | --- |
| `dre_import_batches(id,sha256,actor,created_at,row_count)` | Autor, data, hash do arquivo e quantidade confirmada por lote |
| `dre_import_entries(id,batch_id,reference UNIQUE,month,department_id,category,description,amount)` | Lançamentos contábeis externos, com origem e referência preservadas |
| `departments.lead_employee_id → employees.id` | Identidade do chefe cadastrado |
| `admins.employee_id → employees.id` | Funcionário vinculado ao acesso de gestor |

Novas tabelas usam RLS no PostgreSQL e não são expostas às funções públicas do Supabase. O backend mantém as verificações de acesso. O migrador SQLite/PostgreSQL importa os vínculos de chefia depois dos funcionários para resolver a dependência entre tabelas.

## Chefia e contas

Departamento pode ficar sem chefe. Para indicar um chefe, é obrigatório selecionar um funcionário ativo cadastrado. Funcionário de outro departamento precisa ser transferido antes. Um funcionário sem setor recebe o departamento ao ser indicado como chefe.

Cadastro e edição de acesso `manager` exigem `employee_id`; se houver departamento, o funcionário deve pertencer a ele. Novos acessos comuns passam a `operator` por padrão e podem ser promovidos em Segurança após completar o cadastro de RH. Chefia do departamento e permissões de acesso são vínculos distintos: escolher um chefe não concede acesso administrativo automaticamente.

Na inicialização, nomes antigos de chefia são vinculados somente quando correspondem a um funcionário ativo único e elegível. Nomes sem correspondência ficam disponíveis para revisão, sem representar chefia cadastrada. Gestores antigos sem vínculo válido precisam ser vinculados pelo administrador em Segurança. Não se inferem identidades por coincidência de e-mail.

Desativação ou transferência de um chefe/gestor exige substituição da chefia e remoção/ajuste do acesso primeiro. Departamento com lançamentos contábeis importados é preservado. Mudanças de função invalidam sessões existentes.

## Correções de ponto e financeiro

- `Sample.ts_ms` aceita timestamp fracionário do navegador; ambos os clientes enviam timestamp arredondado. Evita erro de validação `int_from_float`.
- Ponto público coleta uma posição recente em cada tentativa, sem aguardar três atualizações do `watchPosition` em aparelhos parados. Consentimento, precisão, raio e regras de rede permanecem validados.
- Erros de permissão, indisponibilidade e timeout de GPS ganham mensagens próprias. O GPS requer HTTPS ou contexto seguro equivalente.
- Escrita de ponto adquire bloqueio transacional antes de escolher entrada/saída, NSR e hash. Repetições aceitas no intervalo de 30 segundos são recusadas, sem gravar saída acidental ou romper a sequência.
- Automação de tarefa só é acionada em saída aceita.
- O contador do funcionário usa segundos de jornadas concluídas mais o intervalo aberto, evitando contar o mesmo período aberto duas vezes.
- Resposta de falta de permissão administrativa é `403`, preservando a sessão; `401` continua reservado para sessão inválida/expirada.
- A visão financeira do gestor carrega apenas endpoints de DRE autorizados para seu departamento.

## INSS solicitado

Tabela de 2026: limites R$ 1.621,00 / R$ 2.902,84 / R$ 4.354,27 / R$ 8.475,55; alíquotas 7,5% / 9% / 12% / 14%; parcelas a deduzir informadas 0 / 24,32 / 111,40 / 198,49. Desconto máximo R$ 988,09.

A implementação usa a alternativa progressiva autorizada: aplica cada alíquota somente à parcela correspondente e arredonda o total com `ROUND_HALF_UP`. As parcelas a deduzir são um atalho arredondado e podem diferir um centavo nos limites. A tabela está disponível em RH, e `/payroll/rules` informa limites, alíquotas, parcelas, método e teto. Exemplo: salário R$ 5.000,00 → INSS R$ 501,51; acima do teto → R$ 988,09. Folhas já fechadas mantêm os valores e a versão do snapshot; novas apurações usam `BR-2026.2`.

## Validação

`python -m pytest -q`, `npm run build`, `npm run typecheck` e `npm run test:ui`. A suíte de navegador cobre administrador, gestor e funcionário, importação/exportação, GPS autenticado e ponto público por PIN em desenvolvimento e produção, com desktop e celular. PostgreSQL foi validado com aplicação do schema em banco vazio e repetição da migração.
