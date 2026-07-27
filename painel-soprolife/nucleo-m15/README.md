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

## Laudos PDF seguros (M24A)

M24A possui feature flag independente e permanece desabilitado por padrão:
`M15_REPORTS_ENABLED=false` no backend e `reports_enabled=false` na
configuração pública. Ativar o restante do Núcleo não expõe menu, workspace ou
API operacional de laudos. Não habilite essas flags até as decisões clínicas e
de produto listadas no runbook serem aprovadas.

Um futuro uso também exige `M15_REPORTS_STORAGE_DIR` absoluto, privado e fora
do Git. A implementação valida estrutura/conteúdo ativo e integridade
SHA-256/tamanho/páginas em toda releitura, cria diretórios 0700 e arquivos
0600, congela o template por versão e audita cada entrega bem-sucedida sem PII.
Configuração, backup/restore coordenado, retenção, LGPD, implantação e rollback
estão em `../docs/m24a-laudos-pdf-operacao.md`. Nenhum template clínico,
médico/CRM, rodapé jurídico ou provedor de assinatura é presumido.

## Snapshot multiaba M15.6B

O envelope bruto privado versionado pode ser validado e simulado com:

```bash
.venv/bin/python -m app.cli migracao dry-run-multiaba \
  --envelope snapshot-envelope.json --json
.venv/bin/python -m app.cli migracao status-multiaba --json
```

O fluxo compartilha aliases simulados entre abas, gera fila de revisão e
reconciliação prévia sanitizadas e não grava entidades operacionais. A API e
o browser não oferecem execute (somente status).
Veja `../docs/m15-6b-real-snapshot-adapters-dry-run.md`.

## Execução multiaba final (M15.6C)

Caminho único de escrita do formato multiaba — CLI local, admin exato, todos
os portões verdes e frase digitada interativamente (nunca por argumento nem
variável de ambiente). O deploy do código NÃO executa nada: dados reais só
mudam quando o comando abaixo for rodado por decisão humana.

### Revisão multiaba em lote

O arquivo JSON deve estar em `M15_IMPORT_PRIVATE_DIR`, ser regular, pertencer
ao usuário do processo, ter modo `0600` ou `0400` e não ser symlink/hardlink.
O schema fechado `m15.review-batch.1` exige `coverage_mode=all_actionable`,
`batch_id`, `snapshot_sha256`, `mapping_version`, `queue_fingerprint` e uma
entrada para cada token acionável com `token`, `category`, `decision` e
`expected_current_decision`. Use `null` em `decision` apenas para preservar
um item que ainda não possua decisão; o comando nunca remove histórico.

```bash
# somente validação e preview: sempre zero escrita
.venv/bin/python -m app.cli migracao revisar-multiaba-em-lote \
  --arquivo decisoes-m15.json --email admin@ex --somente-preview

# aplicação futura: mostra o mesmo preview e pede uma única frase em TTY
.venv/bin/python -m app.cli migracao revisar-multiaba-em-lote \
  --arquivo decisoes-m15.json --email admin@ex
```

A frase é `GRAVAR REVISOES MULTIABA <batch-id> <fingerprint-curto>`. Ela não
tem argumento, flag `--yes` nem alternativa por variável de ambiente ou pipe.
Depois da digitação, arquivo, fila e estados são relidos sob lock do
`ImportBatch`; decisões e auditorias entram em um único commit. Replay idêntico
é `no_op` sem novas linhas. O recibo contém apenas hashes, contagens e IDs
técnicos, e este comando nunca chama a execução operacional.

```bash
# decisões humanas executáveis por token privado (vincular_candidato,
# criar_pessoa, excluido) — telefone só vincula com decisão explícita
.venv/bin/python -m app.cli migracao revisar-multiaba --batch <id> \
  --referencia <token> --decisao vincular_candidato \
  --mapping-version m15-6b.1 --email gestor@exemplo

# plano de rollback ANTES de escrever (obrigatório) + portões completos
.venv/bin/python -m app.cli migracao plano-rollback-multiaba \
  --envelope snapshot-envelope.json --batch <dry_run_id> --email admin@ex
.venv/bin/python -m app.cli migracao preflight-execucao-multiaba \
  --envelope snapshot-envelope.json --batch <dry_run_id> \
  --backup-evidencia ev.json --email admin@ex

# execução real (frase EXECUTAR MIGRACAO MULTIABA <batch> digitada na hora),
# reconciliação com fechamento exato e rollback seletivo provadamente seguro
.venv/bin/python -m app.cli migracao executar-multiaba --envelope ... \
  --batch <dry_run_id> --backup-evidencia ev.json --email admin@ex
.venv/bin/python -m app.cli migracao reconciliar-multiaba \
  --batch-execucao <id> --email admin@ex
.venv/bin/python -m app.cli migracao rollback-multiaba \
  --batch-execucao <id> --email admin@ex
```

Toda linha criada guarda proveniência (lote, domínio, fingerprint
irreversível, mapping, chave de idempotência única): repetir o mesmo lote
cria zero linhas; o rollback usa só IDs criados pelo lote, em ordem reversa,
e falha fechado diante de dependência externa ou proveniência incompleta.
PCMSO permanece excluído; Financeiro_Lancamentos segue fonte monetária única.

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
