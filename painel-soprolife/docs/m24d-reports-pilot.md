# M24D — piloto interno controlado de laudos

Substitui o bloqueio incondicional único do M24C por um contrato explícito
de três estados. `disabled` (padrão) e `production` nunca servem a API de
laudos; só `pilot`, e mesmo assim apenas quando toda a autorização dedicada
abaixo estiver presente.

## Estados

| `M15_REPORTS_MODE` | Comportamento |
|---|---|
| `disabled` (padrão) | Igual ao M24A/B/C: `503 relatorios_desabilitados` para tudo. |
| `pilot` | Habilita o fluxo clínico M24C completo (upload → atribuição → prévia → `assinatura_pendente`), com o aviso PILOTO INTERNO congelado em todo PDF. Nunca alcança `assinado`/`finalizado`/`liberado` — não existe caminho de sucesso de assinatura nesta versão, igual ao M24C. |
| `production` | Sempre `503 relatorios_producao_bloqueada`, independente de qualquer outra variável. Não há assinatura qualificada nem aprovação jurídica/clínica nesta versão. |

`M15_REPORTS_ENABLED=true` sozinho, ou `M15_REPORTS_MODE=pilot` sozinho,
nunca bastam — a API só serve o piloto com os dois presentes ao mesmo tempo
(`nucleo-m15/app/routers/reports.py::_require_reports_enabled`).

## Autorização de deploy do piloto

`nucleo-m15/scripts/reports_go_live_gate.py preflight-pilot` (chamado via
`nucleo-m15/scripts/lib-reports-go-live-gate.sh::soprolife_reports_go_live_pilot_preflight`)
exige, todas ao mesmo tempo, antes de qualquer mutação:

- `M15_REPORTS_MODE=pilot`;
- `M15_REPORTS_ENABLED=true` (backend) e `reports_enabled=true` (frontend
  versionado, `data/m15-config.json`);
- `SOPROLIFE_REPORTS_PILOT_AUTHORIZATION=HABILITAR PILOTO DE LAUDOS` — frase
  exata, independente da autorização geral de go-live do M15;
- raiz de storage absoluta, fora do Git, sem ancestral symlink, modo 0700,
  dono `soprolife:soprolife` (`_validate_storage_root`);
- a mesma raiz, e só ela (sem pai mais amplo), em `ReadWritePaths` da unit
  systemd efetiva (`_validate_exact_readwritepath`);
- `SOPROLIFE_REPORTS_BACKUP_MANIFEST` apontando para um manifesto de backup
  verificado (ver abaixo) — recente (≤24h), com hashes reais dos artefatos
  e contagens técnicas não-negativas;
- acordo HTTPS pré e pós-deploy entre API e frontend
  (`check_https_workspace`).

Qualquer condição ausente recusa a habilitação (`ReportsGateError`) antes de
qualquer mutação. Nenhuma etapa do gate cria diretório, altera unidade ou
escreve configuração — ele só decide.

## Storage recomendado

```
/opt/soprolife/private/reports   soprolife:soprolife   0700 (diretórios) / 0600 (arquivos)
```

Ver `systemd/soprolife-m15-api-reports-pilot.override.conf.example` para o
drop-in de `ReadWritePaths` correspondente — não aplicado por este milestone.

## Backup coordenado

`nucleo-m15/scripts/backup-reports-pilot.sh STORAGE_ROOT [DEST_ROOT]`:

1. gera o dump PostgreSQL (`pg_dump --format=custom`) e verifica com
   `pg_restore --list` antes de aceitar;
2. arquiva a raiz de storage (`tar`) e verifica com `tar tvf` antes de
   aceitar;
3. só então grava o manifesto (`nucleo-m15/scripts/reports_pilot_backup.py`), com
   hashes SHA-256 reais dos dois artefatos e as contagens técnicas
   (`report_documents`, `report_document_versions`,
   `physician_profiles`).

Se qualquer verificação falhar, o script aborta ANTES de gravar o
manifesto — a habilitação do piloto nunca acontece com um backup não
comprovado.

## Aviso do piloto

Todo PDF composto em modo piloto usa o rodapé dedicado
`PILOTO_INTERNO_NAO_ASSINADO` (`nucleo-m15/app/services/report_catalog.py`), que
sempre contém, ao vivo e congelado no snapshot da versão:

```
PILOTO INTERNO — DOCUMENTO NÃO ASSINADO — NÃO LIBERAR AO PACIENTE
```

O mesmo aviso aparece na área de trabalho do laudo (`js/report-workflow.js`)
sempre que o modo ativo for `pilot`.

## Achados de auditoria fechados nesta versão

- **F2 (autoverificação)** — `PATCH /laudos/admin/medicos/{id}` recusa
  `verification_status=verified` quando `admin.id == account.id`
  (`409 autoverificacao_medica_proibida`) e exige
  `verification_reference` (referência técnica segura, nunca texto livre).
  Reforçado por `CHECK ck_physician_profiles_verification_not_self` no
  banco.
- **F3 (médico suspenso sem saída)** — nova rota admin-only
  `POST /laudos/{id}/recuperar-medico-suspenso`: só aplica quando o médico
  atribuído deixou de ser elegível, preserva a prévia clínica anterior como
  evidência histórica (imutável, sem reescrita), encerra a atribuição
  antiga e cria uma nova, voltando o documento para `atribuido`.
- **F4 (oráculo de existência)** — `GET /laudos/{id}`,
  `GET /laudos/{id}/assinatura` e o download de versão agora checam o
  papel ANTES de buscar o documento; um papel sem qualquer acesso recebe a
  mesma resposta para id existente e inexistente.

Nenhum outro bloqueio de habilitação clínica da auditoria M24C (conteúdo
aprovado, provedor de assinatura, redação jurídica, etc.) é afetado — o
piloto continua sem qualquer caminho de sucesso de assinatura.
