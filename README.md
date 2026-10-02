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

O SQLite registra os dados operacionais do sistema (equipe, tarefas, ponto, financeiro, estoque, departamentos, espaços, configurações, contas e mensagens) em `data/flux.db`. Esse caminho é resolvido a partir do projeto, independentemente do diretório de onde o servidor foi iniciado. É possível definir `DB_PATH` para apontar a outro arquivo ou volume persistente. Bancos e caches locais são ignorados pelo Git.

SQLite foi mantido em vez de NoSQL porque este sistema tem relações entre funcionários, departamentos, folha, pagamentos e ponto, além de depender de transações consistentes. Para implantação em infraestrutura efêmera, configure um volume persistente ou migre para um banco gerenciado antes de usar dados reais.

## Testes

```bash
pip install -r requirements-dev.txt
pytest -q
```
