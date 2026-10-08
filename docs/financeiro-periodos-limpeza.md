# Consulta por período e limpeza de contas

## Comportamento da página

- A consulta no topo permite escolher um mês ou um ano completo. Resumo, custeio por setor, fluxo previsto, contas e histórico usam esse período. A DRE acompanha a escolha e continua oferecendo um intervalo contábil personalizado.
- Ao trocar o período, a tela descarta os resultados e as seleções anteriores, mostra o carregamento e busca os novos dados. Respostas antigas não substituem a consulta mais recente.
- `Limpar filtros` volta ao mês atual, remove busca/status/tipo e filtros da DRE. `Atualizar consulta` recarrega os valores mantendo o período.
- A busca aceita descrição ou setor; os filtros de tipo e status afetam a lista de contas. `Exportar CSV` usa exatamente esses filtros e o período da lista. Resumo e fluxo continuam consolidando todas as contas abertas do período.
- O saldo previsto aparece junto aos indicadores principais. É uma previsão baseada em contas abertas e folha residual, não um saldo bancário conciliado.

## Critérios das datas e valores

Contas, fluxo e despesas do custeio são filtrados pelo vencimento. O mês mostra uma linha de fluxo; o ano mostra janeiro a dezembro. Uma conta vencida permanece em seu mês original na consulta por período. O histórico usa a data de pagamento no fuso da empresa; a DRE usa a competência contábil. Essas bases estão indicadas na página.

A previsão de folha soma os custos mensais antes das provisões. Salários já gerados pela folha são descontados da parcela residual, evitando duplicidade com as contas a pagar. Folhas fechadas conservam seus snapshots. Meses de anos sem regras cadastradas não recebem uma estimativa emprestada de outro ano: as contas continuam acessíveis e a indisponibilidade da previsão de pessoal aparece explicitamente.

## Limpeza de lançamentos

O administrador pode selecionar até 500 contas abertas manuais e usar `Excluir selecionados`. A confirmação informa quantidade, total e período. A exclusão individual também exige confirmação. A atualização após exclusão recarrega fluxo, resumo, custos, contas e DRE.

O backend valida toda a seleção antes de excluir. Se qualquer ID não existir, estiver quitado ou for gerado pela folha, nenhum item da seleção é removido. Exclusão e auditoria detalhada ocorrem na mesma transação, com o mesmo bloqueio usado na baixa de pagamentos. O histórico de pagamentos, os comprovantes, as folhas fechadas e os lançamentos contábeis importados não são alvos dessa limpeza. A DRE conserva essas fontes e recalcula os totais das contas restantes.

## API

| Rota | Alteração |
| --- | --- |
| `GET /finance` | `month=AAAA-MM` ou `year=AAAA`; `status=open/paid/all`, `kind=pagar/receber`, `q` |
| `GET /finance/export.csv` | Mesmos filtros da lista; sem filtros conserva a exportação geral |
| `GET /finance/summary` | Mês/ano, fluxo por período, folha do período, saldo previsto e meses sem previsão |
| `GET /finance/department-costs` | Mês/ano e totais por setor |
| `GET /finance/payments` | Mês/ano filtrando o instante do pagamento no fuso da empresa |
| `POST /finance/delete-selected` | Corpo `{ "ids": [1, 2] }`; seleção atômica de até 500 IDs, somente administrador |
| `DELETE /finance/{id}` | Usa a mesma validação, bloqueio e auditoria da exclusão em lote |

Mês e ano simultâneos são rejeitados. A chamada de resumo sem período mantém a previsão anterior de seis meses para compatibilidade; a interface envia sempre o período selecionado. As rotas da DRE e seus filtros contábeis continuam existentes.

Não há DDL novo: são utilizadas `transactions`, `payment_history`, `payment_attachments`, `payroll_runs`, `payroll_items` e `audit_log`. Cada exclusão gera `FINANCE_DELETE` com identificação, valores, datas, setor e classificação da conta.

## Verificação

`python -m pytest -q`: 73 testes, incluindo limites de períodos, totais anuais, CSV filtrado, ausência de regras históricas, exclusão atômica, auditoria, proteção de pagamentos/folha, recálculo e fronteiras de data no fuso da empresa.

`npm run build`, `npm run typecheck` e `npm run test:ui`: consulta mensal/anual, intervalo da DRE, limpeza dos filtros, cancelamento/confirmação de exclusão, recálculo visível e busca/exportação, além dos fluxos existentes. O navegador percorre desktop e celular em desenvolvimento e no pacote de produção, com SQLite temporário. O teste do parser Psycopg real permanece na suíte; esta rodada não se conectou ao banco Supabase de produção.

O cache do service worker foi atualizado. Para disponibilizar a mudança no sistema publicado, integrar o PR e atualizar backend e arquivos estáticos pelo processo de deploy existente.
