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
- **Inteligência operacional** no painel: composição financeira, presença dos últimos 14 dias, horas do mês com marcações abertas atualizadas ao vivo e alertas automáticos de estoque, tarefas críticas/atrasadas e horas extras. O painel atualiza a cada 30 segundos; horas extras geram alerta a partir de 8 h por funcionário no mês.
- **Quadros em tabela**: item, status (crítico/atenção/ok), prioridade, prazo, progresso, aprovação e responsável — além do kanban.
- **Matéria-prima** com estoque, valor e limite mínimo configurável por item para alertas de reposição.
- **Painéis de custo** que consolidam o DRE.
- **Departamentos** e **espaços** (vínculo de e-mail de administrador).
- **Integrações** (webhook, CSV, WhatsApp, ERP).
- **Custeio mensal por setor**: consolida despesas pagas, em aberto e folha por competência.
- **Exportação financeira em CSV** para contas a pagar e receber, com neutralização de fórmulas em campos de texto.
- **Funcionários**: cadastro na tela de Folha e exportação autenticada da lista em CSV.
- **Contas por e-mail**: primeiro usuário configura a empresa; somente um administrador autenticado pode criar acessos adicionais na mesma instalação.
- **Acesso por função**: Administrador tem controle total; Gestor consulta quadros e decide aprovações; Operador tem acesso de leitura aos quadros. Administradores podem alterar as funções em Segurança.
- **Gestão de tarefas**: arraste cards entre etapas no Kanban ou altere-as na tabela; cada card tem checklist com progresso, tags, anexos (PDF/JPEG/PNG/WebP, até 6 MB) e histórico de alterações. Gestores veem tarefas do próprio departamento e tarefas compartilhadas sem departamento.
- **Custeio por tarefa**: associe a tarefa em andamento à entrada e à saída do ponto para apurar horas e custo de mão de obra. A taxa horária é congelada no registro, usando salário mensal dividido por 220 horas. Insumos podem ser vinculados ao card e são baixados uma única vez quando ele sai de “A fazer”; falta de saldo impede a movimentação.
- **Automações de quadro**: regras configuráveis para pedir aprovação quando o progresso chega a 100% ou avisar quando o custo real ultrapassa o teto do card. Cada regra dispara uma vez por tarefa; os avisos usam os canais ativos configurados em Segurança.
- **Filtros e exportação do quadro**: combine etapa, prioridade, severidade, aprovação, departamento e busca; salve combinações neste navegador. Exporte CSV compatível com Excel ou use a impressão do navegador para salvar em PDF.
- **Busca e comandos rápidos**: `Ctrl+K`/`Cmd+K` abre a busca e navegação; `/` abre os comandos e `N` leva à criação de tarefa.
- **Agenda de espaços**: calendário mensal compartilhado entre departamentos para reservar salas, bancadas e recursos, com bloqueio de horários conflitantes. Gestores reservam em nome de seu departamento e podem cancelar as próprias reservas.
- **Restrição opcional por IP no ponto**: Segurança aceita faixas CIDR. Quando configurada, o servidor exige que o IP da conexão esteja autorizado além da validação GPS habitual.
- **Auditoria**: ações de escrita da API registram conta, horário, método, recurso, resposta HTTP e IP de conexão; a tela Segurança exibe as ações recentes.
- **Autenticação em duas etapas**: configure TOTP em Segurança usando Google Authenticator, Authy ou outro aplicativo compatível. A chave é guardada no banco e o login exige o código após a ativação.
- **Busca global**: use `Ctrl+K` ou `Cmd+K` para localizar quadros e, como administrador, colaboradores e produtos.
- **Offline/PWA**: o aplicativo instala como PWA e mantém a interface disponível offline. Depois de abrir os quadros ou materiais online, alterações em registros existentes podem entrar em uma fila local e sincronizam ao recuperar a conexão; cadastros novos e outros módulos continuam exigindo conexão.
- **Eventos em tempo real**: alterações aceitas são transmitidas via WebSocket às sessões abertas na mesma instância do servidor; o painel também atualiza periodicamente.
- **Avisos por e-mail e WhatsApp**: em Segurança, cadastre destinatários e teste o envio. Aprovações, novas tarefas críticas/atrasadas e materiais sem saldo/abaixo do mínimo disparam avisos aos canais ativos.
- **Contas de funcionário**: a chefia pode criar e-mail e senha no cadastro de Folha. O funcionário entra pela opção Funcionário na tela inicial; sua conta não dá acesso aos dados administrativos.
- **Comunicação da equipe**: mensagens gerais para todos e conversas privadas entre a chefia e cada funcionário. O servidor valida o acesso e mantém cada conversa privada isolada.
- **Tema claro e escuro** com transição suave, preferência persistida no navegador e suporte ao tema do sistema.

## Persistência

Por padrão, o SQLite registra os dados operacionais do sistema em `data/flux.db`. Para conectar um projeto Supabase:

1. Copie a connection string PostgreSQL do painel do Supabase. O aplicativo aceita os formatos `postgresql://` e o legado `postgres://`.
2. Configure-a como segredo `SUPABASE_DATABASE_URL` no ambiente do servidor. Não compartilhe nem salve essa senha no código, no Git ou no navegador.
3. Antes de iniciar o aplicativo conectado ao Supabase, execute `python scripts/migrate_to_supabase.py` nesse mesmo ambiente para importar os dados de `data/flux.db`.
4. Reinicie o aplicativo. A partir daí, ele usa o Supabase; sem `SUPABASE_DATABASE_URL`, usa SQLite local.

O esquema está em [`supabase/schema.sql`](supabase/schema.sql), com RLS ativado e acesso direto pela API pública do Supabase revogado para as tabelas da aplicação. A migração é transacional, preserva os IDs e para sem alterar os dados se encontrar tabelas de destino preenchidas. Faça um backup de `data/flux.db` antes de migrar.

## Segurança e notificações

Os administradores configuram 2FA, funções, auditoria e destinos em **Segurança**. A conta principal de administrador não pode ser rebaixada. O código TOTP é válido por janelas curtas; guarde a chave de configuração em local seguro.

Para habilitar envios reais, configure os segredos no ambiente do servidor (nunca no navegador ou no Git):

- E-mail: `SMTP_HOST`, `SMTP_PORT` (padrão `587`), `SMTP_USER`, `SMTP_PASSWORD` e `SMTP_FROM`.
- WhatsApp Cloud API: `WHATSAPP_TOKEN` e `WHATSAPP_PHONE_ID`; cadastre o destinatário com código do país.

O botão **Enviar teste** valida os canais de forma explícita. A fila offline guarda alterações pendentes no IndexedDB do navegador até sincronizar; use esse recurso somente em dispositivos confiáveis. Eventos WebSocket são locais à instância/processo e não substituem um broker compartilhado em uma implantação com várias instâncias.

Anexos ficam no banco de dados e são disponibilizados apenas como download autenticado. A filtragem CIDR usa o endereço de conexão visto pelo servidor; em instalações atrás de proxy, configure o encaminhamento confiável de IP no servidor antes de ativá-la. O custo de mão de obra usa horas entre marcações de entrada/saída vinculadas ao card; períodos que atravessem tarefas devem ser registrados em pares separados para evitar atribuição incorreta.

## Testes

```bash
pip install -r requirements-dev.txt
pytest -q
```
