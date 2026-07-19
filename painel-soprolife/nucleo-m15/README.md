# Núcleo Operacional Nativo SoproLife — M15

Backend próprio do Centro de Comando:

```
Navegador → proxy de mesma origem (:8765) → API loopback (:8015) → PostgreSQL 16
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

# usuário interno (senha solicitada com eco oculto, nunca em argumento)
.venv/bin/python -m app.cli criar-usuario \
  --email voce@soprolife.local --nome "Seu Nome" --papel admin

# dados sintéticos de demonstração (idempotente)
.venv/bin/python -m app.cli seed-demo

# API oficial — host/porta validados e access log desativado
.venv/bin/python -m app.serve
```

Healthcheck direto: `curl http://127.0.0.1:8015/api/v1/health`

### PostgreSQL 16 via Docker (somente desenvolvimento opcional)

```bash
POSTGRES_PASSWORD='troque-esta-senha' docker compose up -d
# no .env:
# M15_DATABASE_URL=postgresql+psycopg://soprolife:troque-esta-senha@127.0.0.1:5432/soprolife_m15
.venv/bin/alembic upgrade head
```

## Interface no painel

1. Rode a API e, na raiz do repositório, o servidor com proxy:
   `python3 painel-soprolife/scripts/command-center-local-server.py`.
2. Ligue a flag: `data/m15-config.json` → `"enabled": true` **ou**
   `localStorage.setItem('soproM15','on')` no console do navegador.
3. Entre na seção **Núcleo M15** com e-mail e senha do usuário interno
   (criado pela CLI `criar-usuario` ou pela aba Administração, papel admin).
   Alternativa: cole um token da CLI (`.venv/bin/python -m app.cli
   emitir-token --email ...`). A sessão vive só em memória.

Desde a M15.3A a interface cobre a operação diária (pessoas, leads,
espirometrias, consultas, parceiros, encaminhamentos, follow-up, financeiro)
e a administração de usuários (aba exclusiva do papel admin). Redefinir a
senha de um usuário revoga imediatamente os tokens antigos dele.

Com a flag desligada (padrão do repositório) o painel fica exatamente como era.
O frontend usa `/painel-soprolife/api/m15`, nunca uma URL absoluta da API. Em
produção a API continua em `127.0.0.1:8015`; Docker/Podman não são usados.

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

## Migração governada por snapshot (M15.6A)

Para snapshots privados do Google Sheets, o fluxo governado substitui o
`importar` direto: manifesto imutável → registro → dry-run → aprovação
humana → execução explícita (frase exata) → reconciliação → evidência de
rollback. Comandos em `python -m app.cli migracao --help`; runbook completo
em `painel-soprolife/docs/m15-6a-migracao-sheets-readiness.md`. Manifestos,
snapshots e evidências de backup vivem SOMENTE no diretório privado
aprovado (`M15_IMPORT_PRIVATE_DIR`, padrão `data-private/import-snapshots`),
fora do Git.

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

## Snapshot multiaba M15.6B

O envelope bruto privado versionado pode ser validado e simulado com:

```bash
.venv/bin/python -m app.cli migracao dry-run-multiaba \
  --envelope snapshot-envelope.json --json
.venv/bin/python -m app.cli migracao status-multiaba --json
```

O fluxo compartilha aliases simulados entre abas, gera fila de revisão e
reconciliação prévia sanitizadas e não grava entidades operacionais. Execução
multiaba real permanece indisponível; a API e o browser não oferecem execute.
Veja `../docs/m15-6b-real-snapshot-adapters-dry-run.md`.

O script PostgreSQL usa o container descartável `m15-pg-teste`, confirma a
versão major 16 e o remove ao sair, inclusive em caso de erro.

## Segurança (resumo)

- Autenticação por token assinado (HMAC) para usuários internos; papéis
  admin > gestor > operacional > leitura;
- `M15_ENV=prod` exige `M15_AUTH_SECRET` (fail-closed);
- API só em `127.0.0.1`; CORS local e acesso remoto somente pelo proxy de
  mesma origem do painel;
- trilha de auditoria append-only sem PII; timestamps UTC, exibição em
  America/Sao_Paulo;
- financeiro sem nome/telefone/CPF — somente IDs técnicos;
- WhatsApp: monta URL para revisão humana; **nunca** dispara envio automático;
- pessoas "não contatar" nunca entram na fila de follow-up.

Documentação: `painel-soprolife/docs/m15-1-nucleo-operacional-nativo.md` e
`painel-soprolife/docs/m15-2-proxy-seguro-deploy-vps.md`. O segundo documento
inclui instalação/update/logs/backup/rollback; o primeiro usuário fica no
runbook separado `painel-soprolife/docs/m15-2-primeiro-usuario.md`.
