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
- superfície anônima correta e acordo com o BACKEND efetivo, pré e
  pós-deploy (`check_https_workspace`) — ver "Como o acordo é provado
  hoje (M26.21)".

Qualquer condição ausente recusa a habilitação (`ReportsGateError`) antes de
qualquer mutação. Nenhuma etapa do gate cria diretório, altera unidade ou
escreve configuração — ele só decide.

## Preparação (antes da ativação — não habilita nada)

```
/opt/soprolife/private/reports   soprolife:soprolife   0700 (diretórios) / 0600 (arquivos)
```

A raiz de storage e o drop-in systemd em
`systemd/soprolife-m15-api-reports-pilot.override.conf.example` são
PRÉ-REQUISITOS DE PREPARAÇÃO seguros: eles concedem a *capacidade* da unit
gravar nesta raiz específica, mas não ativam laudos por si só — a API
continua servindo `relatorios_desabilitados` até a ativação completa (ver
abaixo). Por isso a ordem é preparar primeiro, ativar depois: o gate
`preflight-pilot` exige que o drop-in já esteja instalado e carregado
(`systemctl cat` efetivo) para poder validar a `ReadWritePaths` exata —
instalá-lo "depois" do preflight aprovar não é possível.

`nucleo-m15/scripts/prepare-reports-pilot-vps.sh [STORAGE_ROOT] [BACKUP_DEST_ROOT]`
faz exatamente essa preparação, de forma idempotente e interativa:

1. cria a raiz de storage (`soprolife:soprolife`, `0700`);
2. instala o drop-in exato e roda `systemctl daemon-reload`;
3. verifica a `ReadWritePaths` efetiva (raiz exata, sem pai mais amplo);
4. roda o backup coordenado (PostgreSQL + storage) e verifica o manifesto
   gerado (ver seção seguinte);
5. imprime `MANIFEST_PATH=...` para uso em
   `SOPROLIFE_REPORTS_BACKUP_MANIFEST` no deploy autorizado seguinte.

Este script NUNCA altera `m15.env`, nunca habilita `M15_REPORTS_ENABLED`,
nunca reinicia nem habilita `soprolife-m15-api.service`, nunca altera
`painel-soprolife/data/m15-config.json`, e nunca faz deploy de código. O
restart que torna a raiz REALMENTE gravável pelo processo em execução
acontece no deploy autorizado seguinte (que já reinicia a API de qualquer
forma).

## Ativação no deploy oficial

`nucleo-m15/scripts/deploy-producao-vps.sh` lê o `reports_mode` alvo
versionado (`data/m15-config.json`, campo `reports_mode`) e escolhe o gate:

- `disabled` → gate único de sempre (M24B/M24C);
- `pilot` → `soprolife_reports_go_live_pilot_preflight` antes de qualquer
  mutação (prompt, sudo, backup) e
  `soprolife_reports_go_live_pilot_postflight` depois do deploy;
- `production` → o MESMO gate único de sempre, que mantém o bloqueio
  incondicional.

**Primeira ativação (transição segura disabled → pilot):** o preflight do
piloto aceita um BACKEND ainda `disabled` (nunca houve laudos em produção
antes) — ele não exige que o piloto já esteja ativo antes de rodar. O
postflight, por outro lado, sempre exige que o backend em execução esteja
de fato servindo o piloto, e que o release implantado esteja com
`reports_enabled=true` e `reports_mode="pilot"`.

## Como o acordo é provado hoje (M26.21)

O gate original fazia GET ANÔNIMO de `/painel-soprolife/` procurando
`id="laudos-espirometria"` e `report-workflow.js`, e GET ANÔNIMO de
`data/m15-config.json` esperando HTTP 200. Isso funcionava quando o painel
inteiro era público; a M25.23 fechou esse vazamento e o deploy do commit
`f9c0761` passou a abortar em `reports_https_workspace_markup_missing`
mesmo com o workspace presente no release.

O contrato foi reconstruído em três provas independentes, todas anônimas e
todas fail-closed:

1. **Superfície anônima** — `/painel-soprolife/` devolve 200 com a tela de
   login e SEM nenhuma marcação administrativa (prova negativa: se o
   Command Center voltar a vazar, o gate aborta com
   `reports_https_workspace_markup_leaked`); `data/m15-config.json` devolve
   401 (`reports_https_config_not_protected` se voltar a ser público).
2. **Release implantado** — o workspace (`index.html` + `js/report-workflow.js`)
   e os flags `reports_enabled`/`reports_mode` são lidos do checkout que o
   servidor de fato serve. O código histórico
   `reports_https_workspace_markup_missing` continua existindo e agora só
   aparece quando o workspace realmente falta no release.
3. **Backend efetivo** — o probe anônimo de `/api/m15/laudos` distingue os
   três estados sem sessão nenhuma, porque `_require_reports_enabled` roda
   ANTES da autenticação:

   | Resposta anônima | Estado efetivo |
   | --- | --- |
   | `401` (autenticação recusando) | piloto servindo |
   | `503` + `relatorios_desabilitados` | desabilitado |
   | `503` + `relatorios_producao_bloqueada` | produção bloqueada |

   Qualquer outra combinação é `reports_https_api_response_invalid`.

Preflight e postflight usam as MESMAS três provas e diferem só no rigor do
item 3: o preflight aceita qualquer estado reconhecido (o checkout já é o
release alvo enquanto os serviços ainda rodam o anterior); o postflight
exige que o estado efetivo seja exatamente o do release implantado.

Nenhuma dessas provas usa cookie, token ou sessão humana: o deploy continua
sem credencial administrativa.

Quando `pilot` é o modo alvo, o `EnvironmentFile` gravado contém:

```
M15_REPORTS_MODE=pilot
M15_REPORTS_ENABLED=true
M15_REPORTS_STORAGE_DIR=<raiz autorizada exata>
```

Releases `disabled` gravam explicitamente:

```
M15_REPORTS_MODE=disabled
M15_REPORTS_ENABLED=false
```

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
