# Núcleo Operacional Nativo SoproLife — M15

Backend próprio do Centro de Comando:

```
Centro de Comando  →  API SoproLife (FastAPI)  →  PostgreSQL 16
```

O Google Sheets **deixa de ser o backend operacional definitivo**, mas as
planilhas e CSVs antigos continuam existindo como origem histórica, fonte de
migração, backup, conferência e rollback. **Nada do painel antigo foi
removido** — o M15 coexiste atrás de feature flag até validação humana.

## Stack

- Python 3.12+ (testado em 3.14), FastAPI, SQLAlchemy 2, Alembic, Pydantic 2;
- PostgreSQL 16 (produção) — SQLite para desenvolvimento/testes sem Postgres;
- pytest.

## Execução local

```bash
cd painel-soprolife/nucleo-m15
python3 -m venv .venv
.venv/bin/pip install -r requirements.lock
cp .env.example .env            # ajuste se quiser PostgreSQL

# migrações (SQLite local por padrão)
.venv/bin/alembic upgrade head

# usuário interno (senha via env, nunca em argumento)
M15_NOVA_SENHA='sua-senha-local' .venv/bin/python -m app.cli criar-usuario \
  --email voce@soprolife.local --nome "Seu Nome" --papel admin

# dados sintéticos de demonstração (idempotente)
.venv/bin/python -m app.cli seed-demo

# API oficial — host/porta validados e access log desativado
.venv/bin/python -m app.serve
```

Healthcheck: `curl http://127.0.0.1:8015/api/v1/health`

### PostgreSQL 16 via Docker (opcional)

```bash
POSTGRES_PASSWORD='troque-esta-senha' docker compose up -d
# no .env:
# M15_DATABASE_URL=postgresql+psycopg://soprolife:troque-esta-senha@127.0.0.1:5432/soprolife_m15
.venv/bin/alembic upgrade head
```

## Interface no painel

1. Rode o painel: `python3 -m http.server 8765` (raiz do repositório) e a API.
2. Ligue a flag: `data/m15-config.json` → `"enabled": true` **ou**
   `localStorage.setItem('soproM15','on')` no console do navegador.
3. Emita um token (`.venv/bin/python -m app.cli emitir-token --email ...`) e
   cole no campo "Token de acesso" da seção **Núcleo M15**.

Com a flag desligada (padrão do repositório) o painel fica exatamente como era.

## Importador seguro (dry-run padrão)

```bash
# NUNCA grava sem --execute
.venv/bin/python -m app.cli importar --tipo leads --arquivo /caminho/leads.csv
.venv/bin/python -m app.cli importar --tipo leads --arquivo /caminho/leads.csv --execute
```

Tipos: `leads`, `crm_pacientes`, `crm_espirometria`, `crm_consultas`,
`contatos_b2b`. Relatórios JSON/Markdown em `var/relatorios-importacao/`
(fora do Git). Reexecutar o mesmo arquivo não duplica nada (SHA-256 +
aliases legados). **CSVs reais ficam fora do Git** — use um caminho em
`painel-soprolife/data-private/` ou fora do repositório.

Ordem recomendada: `crm_pacientes` → `crm_espirometria` → `crm_consultas` →
`leads` → `contatos_b2b` (exames/consultas se vinculam aos pacientes por
`legacy_id`, nunca por nome).

## Parceiro institucional — comando privado

Crie `painel-soprolife/data-private/parceiros-institucionais.json` (fora do
Git) com apenas os dados confirmados:

```json
{
  "parceiros": [{
    "nome": "Parceiro Institucional Exemplo",
    "tipo": "clinica",
    "status_parceria": "ativa",
    "unidades": [{"nome": "Unidade Exemplo"}],
    "contatos": [{"nome": "Contato Exemplo", "cargo": "Responsável Técnico", "principal": true,
                   "unidade": "Unidade Exemplo"}]
  }]
}
```

```bash
.venv/bin/python -m app.cli seed-institucional \
  --arquivo ../data-private/parceiros-institucionais.json
```

Idempotente; não inventa telefone, e-mail, datas nem percentuais.

## Testes

```bash
.venv/bin/python -m pytest tests/ -q

# prova PostgreSQL 16 real: upgrade/check/downgrade/upgrade + suíte completa
bash scripts/test-postgres-efemero.sh
```

O script PostgreSQL usa o container descartável `m15-pg-teste`, confirma a
versão major 16 e o remove ao sair, inclusive em caso de erro.

## Segurança (resumo)

- Autenticação por token assinado (HMAC) para usuários internos; papéis
  admin > gestor > operacional > leitura;
- `M15_ENV=prod` exige `M15_AUTH_SECRET` (fail-closed);
- API só em `127.0.0.1`; CORS restrito ao painel local;
- trilha de auditoria append-only sem PII; timestamps UTC, exibição em
  America/Sao_Paulo;
- financeiro sem nome/telefone/CPF — somente IDs técnicos;
- WhatsApp: monta URL para revisão humana; **nunca** dispara envio automático;
- pessoas "não contatar" nunca entram na fila de follow-up.

Documentação completa: `painel-soprolife/docs/m15-1-nucleo-operacional-nativo.md`.
