# Contas e DRE: falha ao abrir em PostgreSQL

A tela do administrador carrega `/finance/department-costs` ao abrir Contas e DRE. A consulta exclui salários já gerados pela folha com `source_key NOT LIKE 'payroll:%'` e recebe o intervalo mensal por parâmetros.

O adaptador de PostgreSQL convertia `?` para `%s`, mas deixava o percentual literal do SQL sem escape. O Psycopg interpretava esse percentual como um parâmetro adicional e lançava `IndexError: tuple index out of range`. A falha de um dos carregamentos interrompia a renderização da página inteira. SQLite não usa esse parser, por isso os testes anteriores não reproduziam o defeito.

`PostgresConnection.execute()` agora escapa percentuais literais para `%%` antes de converter os placeholders. Os valores continuam vinculados separadamente; percentuais e apóstrofos dos dados não são alterados. A correção segue a [documentação oficial do Psycopg](https://www.psycopg.org/docs/usage.html#passing-parameters-to-sql-queries).

## Painel

O gráfico semicircular de tarefas foi restaurado na seção operacional, sempre visível. Exibe o percentual concluído, a legenda com as três etapas e o atalho para abrir tarefas. Mantém os componentes e a aparência existentes, com descrição acessível para leitores de tela. O cache do service worker foi atualizado para distribuir o novo JavaScript.

## Validação

- `python -m pytest -q`: 68 testes aprovados. Os três novos casos usam `mogrify()` do Psycopg real para validar a consulta com `LIKE`, um percentual sem parâmetros e dados com percentual/apóstrofo. Um socket local fornece somente metadados de conexão; esses testes não executam SQL.
- A consulta de custos foi executada separadamente no PostgreSQL embarcado PGlite, verificando pagamentos abertos/pagos, intervalo mensal e exclusão dos salários gerados pela folha.
- `npm run build` e `npm run typecheck`: aprovados.
- `npm run test:ui`: abertura do financeiro, criação/baixa de contas, comprovantes, importação/exportação de DRE e gráfico de tarefas em desktop e celular, no código de desenvolvimento e no pacote construído. A suíte usa SQLite temporário; a regressão do parser PostgreSQL tem cobertura separada.

Não há alterações de rotas, contratos de API ou tabelas. É necessário atualizar tanto o backend quanto os arquivos estáticos. A publicação da aplicação em uso depende da integração desta correção e do processo de deploy do repositório.
