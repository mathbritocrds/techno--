# Flux Gestão

Sistema de gestão de empresas com visual inspirado no Flux: quadros de tarefas (estilo monday.com), custeio de produtos, folha de pagamento e **ponto eletrônico com validação por GPS no servidor**. Back end em Python (FastAPI + SQLite), front end em HTML/JS puro servido pela própria API.

## Estrutura

```
flux-gestao/
├── app/
│   ├── main.py          # API, banco de dados, login e regras de negócio
│   └── static/index.html  # front end (login, painel, quadros, custeio, folha, ponto)
├── tests/test_api.py    # testes automatizados (pytest)
├── .github/workflows/ci.yml  # roda os testes a cada push
├── Dockerfile · docker-compose.yml
├── requirements.txt · requirements-dev.txt
└── .env.example
```

## Rodar localmente

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export ADMIN_USER=admin ADMIN_PASSWORD='uma-senha-forte'
uvicorn app.main:app --reload
```
Abra http://localhost:8000. Documentação da API: http://localhost:8000/docs.

## Rodar com Docker

```bash
cp .env.example .env      # edite a senha
docker compose up -d --build
```
O banco fica no volume `flux-data`.

## Testes

```bash
pip install -r requirements-dev.txt
pytest -q
```

## Recursos

- **Quadros** de tarefas no estilo monday.com, com três etapas.
- **Custeio** de produtos com custo, lucro e margem.
- **Folha** de pagamento integrada ao ponto: horas extras do mês entram no cálculo.
- **Financeiro**: contas a pagar e a receber, vencidos e fluxo de caixa de 6 meses já descontando a folha.
- **Interface com movimento**: transições entre telas com a aba deslizando, entrada escalonada dos cartões, números que contam até o valor, gráficos que se desenham, tarefas que viajam entre colunas, esqueleto de carregamento e avisos animados. Quem usa `prefers-reduced-motion` no sistema recebe tudo sem animação. O tema claro/escuro é lembrado no navegador.
- **Ponto com localização em tempo real**, espelho de horas, exportação CSV e verificação de integridade.

## Como funciona o ponto

1. O gestor vai ao local de trabalho e, em **Ponto**, define a sede e o raio. Também informa razão social e CNPJ para o comprovante.
2. O funcionário abre a tela inicial, aba **Bater ponto**, escolhe o nome, digita o PIN e autoriza a localização.
3. Enquanto a tela está aberta, o aparelho lê o GPS continuamente e mostra na hora se está dentro ou fora da área (`POST /clock/live`).
4. Ao registrar, o aparelho envia até 5 leituras recentes. O servidor usa a **hora do servidor**, a distância até a sede e estas verificações:
   - recusa GPS impreciso (erro acima de 50 m);
   - recusa leituras muito dispersas (mais de 100 m entre si);
   - recusa leituras antigas ou com relógio do aparelho errado;
   - recusa deslocamento impossível desde o último registro (acima de ~200 km/h);
   - bloqueia o PIN após 5 erros.
5. Cada marcação aceita recebe **NSR** sequencial e um **hash SHA-256 encadeado** ao anterior. O comprovante mostra empresa, trabalhador, NSR, data/hora e hash. Se alguém alterar o banco depois, **Verificar integridade** aponta o NSR adulterado.

## Conformidade (Portaria MTP 671/2021)

O sistema já faz: comprovante a cada marcação, NSR sequencial, hash SHA-256, geolocalização, espelho de ponto e exportação das marcações.

**Ainda não faz** (necessário para operar como REP-P certificado): assinatura digital ICP-Brasil/CAdES, registro do programa no INPI, e o layout oficial dos arquivos AFD e AEJ. O CSV exportado é um formato próprio e **não substitui** o AFD. Consulte a contabilidade ou um especialista trabalhista antes de usar como controle de jornada oficial.

## Privacidade (LGPD)

A localização só é lida enquanto a tela de ponto está aberta, e só depois de o funcionário marcar a autorização. Não há rastreamento durante o expediente. Cada registro guarda a coordenada usada na validação; defina um prazo de retenção e informe os funcionários.

## Variáveis de ambiente

| Variável | Para que serve |
|---|---|
| `ADMIN_USER` / `ADMIN_PASSWORD` | Login do gestor (criado na primeira execução) |
| `ADMIN_TOKEN` | Opcional: acesso direto à API pelo header `X-Admin-Token` |
| `TZ_OFFSET_HOURS` | Fuso da empresa em horas (padrão `-3`, Brasília) |
| `DB_PATH` | Caminho do banco SQLite (padrão `data/flux.db`) |

## Antes de publicar na internet

- Use **HTTPS** (o navegador só libera o GPS em HTTPS), por exemplo com Caddy ou Nginx na frente.
- Defina uma `ADMIN_PASSWORD` forte; a senha padrão é só para testes.
- Os cálculos de INSS (11% até o teto de R$ 7.800) e encargos (28%) são **estimativas**. Ajuste à tabela vigente e confirme com a contabilidade.
- GPS de celular pode ser falsificado por apps de localização falsa; para mais segurança, combine com QR code dinâmico ou rede Wi-Fi da empresa.

## Licença

Escolha uma licença antes de tornar o repositório público (por exemplo, MIT) e adicione o arquivo `LICENSE`.
