# Flux Gestão

Sistema de gestão com visual Flux: quadros (kanban e tabela), custeio, matéria-prima, painéis de custo com DRE sintético, departamentos, espaços, folha, financeiro, integrações e ponto eletrônico com GPS no servidor.

## Rodar

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export ADMIN_USER=admin ADMIN_PASSWORD='uma-senha-forte'
uvicorn app.main:app --reload --host 0.0.0.0 --port 8080
```

Login padrão de testes: `admin` / `admin123`.

## O que foi acrescentado (People Core)

- **DRE sintético** no painel: receita bruta, consumo de matéria-prima, custos operacionais + impostos e lucro líquido grosso com margem.
- **Quadros em tabela**: item, status (crítico/atenção/ok), prioridade, prazo, progresso, aprovação e responsável — além do kanban.
- **Matéria-prima** com estoque e valor.
- **Painéis de custo** que consolidam o DRE.
- **Departamentos** e **espaços** (vínculo de e-mail de administrador).
- **Integrações** (webhook, CSV, WhatsApp, ERP).

Design, animações e estrutura do front (orbs, pílulas, transições, ponto ao vivo) permanecem os mesmos.

## Testes

```bash
pip install -r requirements-dev.txt
pytest -q
```
