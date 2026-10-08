# Login, contas, funcionários e reservas

## Comportamento entregue

O login apresenta um único formulário. `POST /auth/sign-in` identifica a conta pelo e-mail ou pelo nome de usuário cadastrado e reutiliza os verificadores existentes de senha, limite de tentativas e TOTP. O sistema não tenta outro perfil depois de uma senha incorreta. As rotas de login antigas continuam disponíveis. A sessão administrativa passa a armazenar o e-mail canônico: antes, entrar pelo nome de usuário criava um token que falhava nas consultas de contas, DRE e outras telas autenticadas.

`GET /auth/branding` publica apenas o nome da empresa. O login tem mostrar/ocultar senha, erros junto ao campo, estado de carregamento, atalho independente de ponto e criação de conta discreta. Na instalação inicial, criar a primeira conta é a ação principal.

O painel mostra quatro indicadores, dois gráficos contábeis e detalhes adicionais em **Ver mais gráficos**. Os gráficos sintéticos das análises de custo permanecem separados dos contábeis. Primeiros passos usa o indicador persistente já existente e desaparece depois da conclusão. Todos os setores cabem na navegação móvel; o submenu preserva filtros de permissão, estado ativo e animação curta, respeitando redução de movimento.

Funcionários e folha usa cartões com nome, cargo, departamento, salário e bonificação. Os cartões apresentam custo e líquido mensais e anuais, enquanto a composição de descontos, benefícios, encargos e provisões fica expansível. Cadastro, benefícios/férias, criação de acesso e bonificação abrem diálogos. A busca filtra cartões localmente, sem substituir o campo a cada tecla. O formulário duplicado de cadastro foi removido.

A criação do funcionário pode receber admissão e conta opcionais em uma única transação. Se o e-mail estiver ocupado, a senha for inválida ou o departamento não existir, nenhum cadastro parcial é criado.

Contas e DRE mantém lançamentos, baixa, histórico, comprovantes e DRE por competência com filtros, importação e exportação CSV. A importação contábil continua sem gerar pagamentos. A DRE reconhece a despesa da folha **após o fechamento**, sem duplicar as contas a pagar geradas pela folha.

Reservas possuem **Editar reserva** visível. É possível alterar recurso, título, detalhes, horários, participantes, exclusividade, compatibilidade e status. O servidor revalida capacidade, conflitos e escala na mesma transação. O funcionário edita apenas suas próprias reservas; gestor apenas suas reservas do setor; administrador edita todas. Trocar o recurso atualiza a equipe automática vinculada ao novo recurso.

## API e dados

| API | Regra |
| --- | --- |
| `POST /auth/sign-in` | Login único; sem token antes da confirmação TOTP |
| `GET /auth/branding` | Nome da empresa para a tela pública |
| `POST /employees` | Campos opcionais `hired_on`, `account_email`, `account_password`; criação atômica de cadastro, perfil e acesso |
| `GET /employees/{id}/bonuses/{AAAA-MM}` | Valor, observação e identificação do último responsável; somente administrador |
| `PUT /employees/{id}/bonuses/{AAAA-MM}` | Valor não negativo, finito, até R$ 10 milhões e observação até 500 caracteres; edição bloqueada em folha fechada |
| `GET /payroll` | `bonus_amount` por pessoa; totais mensais existentes recalculados |
| `GET /payroll/annual-cost` | Acrescenta bonificações, líquido fechado/projetado/total por pessoa e `total_cost`, `total_net` da equipe |
| `GET /employee/payroll` | Inclui a bonificação no holerite restrito à própria pessoa |
| `GET /employee/spaces` | Inclui campos de edição e `can_edit` para reservas próprias |
| `PATCH /space-bookings/{id}` | Reutiliza a validação e o recálculo transacional de escalas |

DDL aditivo em `app/business_schema.py` e equivalente PostgreSQL em `supabase/schema.sql`:

```sql
CREATE TABLE IF NOT EXISTS employee_bonuses (
 employee_id INTEGER NOT NULL REFERENCES employees(id),
 month TEXT NOT NULL,
 amount REAL NOT NULL DEFAULT 0 CHECK(amount >= 0),
 note TEXT NOT NULL DEFAULT '',
 updated_at TEXT NOT NULL,
 updated_by TEXT NOT NULL,
 PRIMARY KEY(employee_id, month)
);
```

A chave composta permite uma bonificação consolidada por pessoa e competência, com atualização idempotente. A auditoria registra chamadas de alteração; o snapshot da folha preserva o valor no fechamento. O esquema Supabase ativa RLS e revoga acesso direto de `anon` e `authenticated`. O migrador SQLite→Supabase inclui a nova tabela depois de funcionários. A migração é aditiva e pode ser executada novamente.

## Cálculos e interpretação dos totais

A funcionalidade implementa **bonificação salarial**: valor do mês somado ao bruto, com recálculo de INSS, IRRF, FGTS e encargos patronais. Não presume classificação como prêmio isento. A CLT distingue verbas salariais e prêmios nos [artigos 457, §§ 1º, 2º e 4º](https://www.planalto.gov.br/ccivil_03/decreto-lei/del5452.htm). A média de adicionais de 13º/férias permanece no parâmetro `variable_average`, informado pelo RH conforme os períodos e regras aplicáveis.

- Custo mensal total = custo de caixa + provisões de 13º/férias/encargos, compensando a parcela salarial de férias para evitar duplicidade.
- Custo anual total = soma das 12 competências; folhas fechadas usam snapshots, abertas usam condições atuais e bonificações conhecidas de cada mês.
- Líquido mensal a pagar = soma dos líquidos individuais do mês.
- Líquido anual total = soma das folhas mensais fechadas e projetadas. Não representa caixa já pago nem acrescenta pagamentos de 13º/férias ainda não processados. Essas provisões integram o custo anual e têm detalhamento próprio.

As tabelas fiscais disponíveis permanecem restritas a 2026. A nova versão de cálculo é `BR-2026.3`; folhas anteriores permanecem congeladas. O INSS segue a tabela e teto de R$ 988,09 já implementados no pacote anterior.

## Verificação

`python -m pytest -q`, `npm run build` e `npm run typecheck` verificam backend, cálculos e empacotamento. `npm run test:ui` percorre desenvolvimento e pacote construído em desktop e celular: primeira conta, erros inline, senha visível, login por usuário, funcionário/gestor, navegação completa, gráficos, busca, cadastro/edição, bonificação, fechamento, criação/baixa de contas, anexo, importação/exportação DRE, reservas/escalas e ponto com GPS. Desktop usa movimento normal; celular usa redução de movimento. Os testes usam bancos temporários separados dos dados reais.
