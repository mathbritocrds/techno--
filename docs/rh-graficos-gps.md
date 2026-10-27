# Funcionários: gráficos e localização do ponto

## Leitura dos valores

A página Funcionários e folha mantém os indicadores e os cadastros existentes e acrescenta dois gráficos:

- **Como o custo se divide:** salários/adicionais, benefícios líquidos pagos pela empresa, FGTS/encargos e reservas de 13º/férias. Exibe valores e percentuais do custo total.
- **Líquido e custo por pessoa:** compara o que cada funcionário recebe com o custo para a empresa, usando a mesma escala. Mostra as quatro pessoas com maior custo e permite expandir as demais.

O seletor alterna entre a competência mensal e o ano, sem novas consultas. Os dados vêm de `/payroll` e `/payroll/annual-cost`, incluindo bonificações e horas extras. Na visão anual, folhas fechadas conservam seus valores e as abertas são projetadas. Fechado não significa pago. A pesquisa dos cadastros continua independente dos gráficos consolidados da equipe.

As reservas usam as compensações já existentes no cálculo da folha, sem duplicar o salário de férias. As bonificações já estão em salários e não são somadas novamente. Benefícios consideram VT/VA concedidos menos a participação do funcionário. A composição deve corresponder ao custo total apresentado nos indicadores.

## GPS menos exigente

A tolerância padrão de precisão passa de **50 para 150 metros**, aplicada igualmente à prévia, ao ponto por PIN e ao ponto autenticado. A tela de parâmetros permite configurar entre 5 e 500 metros e explica que um valor maior aceita menor precisão.

Isso altera a precisão aceita da leitura; a distância entre a coordenada e a sede continua sendo comparada com o raio configurado. Consentimento, PIN/sessão, localização recente, estabilidade das amostras, restrição de IP quando configurada e proteção contra registros duplicados continuam validados pelo backend.

O navegador tenta primeiro uma localização comum, com cache de até 15 segundos e espera de até 10 segundos. Se ela não atender à tolerância configurada, tenta maior precisão por até 15 segundos. Mantém a melhor leitura recente, sem alterar a precisão informada pelo aparelho. Uma negativa de permissão encerra a coleta imediatamente. Se nenhuma leitura atender à política, o backend recusa o ponto e informa a precisão recebida e o limite permitido.

Referência das opções da API do navegador: [MDN — getCurrentPosition](https://developer.mozilla.org/en-US/docs/Web/API/Geolocation/getCurrentPosition).

## Backend e atualização de dados

| Rota | Comportamento |
| --- | --- |
| `GET /clock/policy` | Retorna a tolerância de precisão e a idade máxima da leitura; respeita a restrição de IP do ponto e não expõe a localização da sede |
| `GET /employee/summary` | Inclui `clock_gps_accuracy_m` para o navegador usar a política da empresa |
| `POST /clock/live` | Inclui `accuracy_limit_m` e utiliza a mesma tolerância do registro |
| `PUT /admin/operational-settings` | Aceita tolerância de precisão de 5 a 500 metros |

Não há tabelas novas. Na inicialização, uma atualização transacional em `settings` troca o antigo valor padrão persistido `50`/`50.0` por `150`. Valores personalizados diferentes são preservados. O marcador `clock_gps_default_version=2` impede que essa atualização se repita e sobrescreva uma configuração posterior do administrador.

O cache do service worker foi atualizado para entregar os novos arquivos estáticos. Para refletir no sistema publicado, integrar o PR e atualizar backend e frontend pelo deploy existente.

## Validação

- `python -m pytest -q`: **84 testes aprovados**, incluindo precisão 120/150/151 m por PIN e sessão, limite personalizado, idade/distância/consentimento e atualização única dos padrões antigos.
- `node --test scripts/location.test.mjs`: **7 testes aprovados** para leitura comum, refinamento, permissão negada, leitura antiga, falha de refinamento e conexão segura.
- `npm run build` e `npm run typecheck`: aprovados.
- `npm run test:ui`: gráficos mensais/anuais reconciliados com os totais da API, ponto com precisão simulada de 120 m por PIN e sessão, e fluxos existentes em desktop/celular, tanto no desenvolvimento quanto no pacote construído. Playwright foi usado porque agent-browser não estava disponível. As fontes originais foram carregadas localmente para a revisão visual.

Os testes usam bancos temporários e localização simulada. Não medem a qualidade real do sinal em aparelhos físicos nem se conectam ao Supabase de produção.
