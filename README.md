# SIGI Gestão

Sistema integrado de gestão: quadros (kanban e tabela), custeio, matéria-prima, painéis de custo com DRE sintético, departamentos, espaços, folha, financeiro, integrações e ponto eletrônico com GPS no servidor.

## Rodar

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8080
```

Na primeira execução, use **Criar conta da empresa** na tela de login para registrar o e-mail, a senha e os dados da empresa. Para instalações já configuradas, o atalho de login solicita a autenticação de um administrador para autorizar novos acessos da mesma empresa. Senhas são armazenadas com hash; não há credenciais padrão para instalações novas.

## O que foi acrescentado (SIGI)

- **DRE sintético** no painel: receita bruta, consumo de matéria-prima, custos operacionais + impostos e lucro líquido grosso com margem.
- **Quadros em tabela**: item, status (crítico/atenção/ok), prioridade, prazo, progresso, aprovação e responsável — além do kanban.
- **Matéria-prima** com estoque e valor.
- **Painéis de custo** que consolidam o DRE.
- **Departamentos** e **espaços** (vínculo de e-mail de administrador).
- **Integrações** (webhook, CSV, WhatsApp, ERP).
- **Custeio mensal por setor**: consolida despesas pagas, em aberto e folha por competência.
- **Exportação financeira em CSV** para contas a pagar e receber, com neutralização de fórmulas em campos de texto.
- **Funcionários**: cadastro na tela de Folha e exportação autenticada da lista em CSV.
- **Contas por e-mail**: primeiro usuário configura a empresa; somente um administrador autenticado pode criar acessos adicionais na mesma instalação.
- **Contas de funcionário**: a chefia pode criar e-mail e senha no cadastro de Folha. O funcionário entra pela opção Funcionário na tela inicial; sua conta não dá acesso aos dados administrativos.
- **Comunicação da equipe**: mensagens gerais para todos e conversas privadas entre a chefia e cada funcionário. O servidor valida o acesso e mantém cada conversa privada isolada.
- **Tema claro e escuro** com transição suave, preferência persistida no navegador e suporte ao tema do sistema.

## Persistência

Por padrão, o SQLite registra os dados operacionais do sistema em `data/flux.db`. Para conectar um projeto Supabase:

1. Copie a connection string PostgreSQL do painel do Supabase.
2. Configure-a como segredo `SUPABASE_DATABASE_URL` no ambiente do servidor. Não compartilhe nem salve essa senha no código, no Git ou no navegador.
3. Antes de iniciar o aplicativo conectado ao Supabase, execute `python scripts/migrate_to_supabase.py` nesse mesmo ambiente para importar os dados de `data/flux.db`.
4. Reinicie o aplicativo. A partir daí, ele usa o Supabase; sem `SUPABASE_DATABASE_URL`, usa SQLite local.

O esquema está em [`supabase/schema.sql`](supabase/schema.sql), com RLS ativado e acesso direto pela API pública do Supabase revogado para as tabelas da aplicação. A migração é transacional, preserva os IDs e para sem alterar os dados se encontrar tabelas de destino preenchidas. Faça um backup de `data/flux.db` antes de migrar.

## Testes

```bash
pip install -r requirements-dev.txt
pytest -q
```
