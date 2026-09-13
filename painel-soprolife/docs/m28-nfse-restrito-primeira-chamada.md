# M28 — Playbook da primeira chamada real à Produção Restrita do NFS-e

Este documento é para a PRÓXIMA sessão controlada, humana, que vai instalar
um certificado real e fazer a primeira chamada de verdade. Esta sessão
(M28 — fundação de automação offline) **não fez nada disso** — ver o
relatório final da missão para as declarações explícitas.

Pré-requisito de leitura: `~/soprolife-relatorios/RELATORIO_NFSE_M27_RESTRICTED_20260913_180823.md`
(fundação do provedor) e o relatório desta missão M28 (arquitetura da automação
offline: prontidão, preflight, fila, lote, artefatos).

## 0. O que já está pronto (não repetir)

- Builder DPS, validação XSD, assinatura XMLDSig, classificação de resposta,
  transporte restrito com portão fail-closed: `app/services/nfse_national/`.
- Fila fiscal real (`FiscalDocument`/`FiscalPreparation`/`FiscalAttempt`),
  lote "Emitir pendentes" (mock), painel de prontidão (`/fiscal/status`),
  resumo de fila (`/fiscal/fila-resumo`), preflight offline
  (`/fiscal/documentos/{id}/preflight`), estrutura de gates de produção —
  tudo isso já existe e já foi testado com dados sintéticos.
- **O que NÃO existe ainda e este playbook não resolve sozinho**: uma tela
  de administração para cadastrar `NationalDpsConfiguration` (código de
  serviço, alíquota, regime) por política fiscal real, e a decisão de negócio
  sobre esses valores tributários concretos. Sem isso, `/fiscal/status` e
  `/fiscal/documentos/{id}/preflight` sempre vão reportar
  `national_dps_configuration_not_defined_for_any_real_document` — isso é
  esperado e não é um bug desta fundação.

## 1. Onde o certificado real deve ficar

- Caminho recomendado: fora do repositório e fora de qualquer diretório
  versionado — ex.: `/home/fedorasurf/soprolife-private/nfse/soprolife-restrito.p12`
  (crie a árvore `soprolife-private/` se não existir; não é `~/soprolife-worktrees/*`
  nem `~/soprolife-site`).
- **Nunca** dentro de `soprolife-worktrees/`, `soprolife-site/`, ou qualquer
  caminho que `git status` enxergue.
- Permissões recomendadas: diretório pai `0700`, arquivo `.p12` `0600`,
  dono = usuário que roda o processo da API (nunca `0644`/`0640`).
  ```bash
  mkdir -p ~/soprolife-private/nfse
  chmod 700 ~/soprolife-private/nfse
  cp <certificado-real>.p12 ~/soprolife-private/nfse/soprolife-restrito.p12
  chmod 600 ~/soprolife-private/nfse/soprolife-restrito.p12
  ```

## 2. Como a senha deve ser suprida

- **Nunca** em um arquivo versionado, nunca em um commit, nunca em um log.
- Use `M15_NFSE_RESTRICTED_CERTIFICATE_PASSWORD` como variável de ambiente do
  processo (systemd `Environment=`/`EnvironmentFile=` com permissão `0600`,
  ou um gerenciador de segredos já em uso na VPS) — nunca no `.env` versionado
  do worktree de desenvolvimento.
- A senha nunca aparece em `/fiscal/status`: o campo é só
  `secret_configured: true/false`.

## 3. Variáveis/configurações necessárias

| Variável | Valor esperado |
| --- | --- |
| `M15_NFSE_ENABLED` | `true` |
| `M15_NFSE_ENVIRONMENT` | `restricted` (nunca `production` — não existe transporte funcional) |
| `M15_NFSE_REAL_ENABLED` | `true` |
| `M15_NFSE_RESTRICTED_BASE_URL` | URL HTTPS oficial de Produção Restrita (confirmar no dia — ver §5) |
| `M15_NFSE_RESTRICTED_CERTIFICATE_PATH` | caminho absoluto do §1 |
| `M15_NFSE_RESTRICTED_CERTIFICATE_PASSWORD` | senha real, via variável de ambiente segura |
| `M15_NFSE_FISCAL_ARTIFACTS_DIR` | diretório privado absoluto, fora do Git (ex.: `~/soprolife-private/nfse/artifacts`) |
| `M15_NFSE_RESTRICTED_NETWORK_ENABLED` | **continua `false`** até o checklist da §7 estar 100% ✓ |

## 4. Comando de validação do certificado

Sem certificado real ainda, valide a leitura fail-closed com o comando de
prontidão (§6) — ele já reporta `certificate_configured`,
`certificate_syntactically_valid`, validade e resumo seguro do titular, sem
nunca imprimir a senha ou a chave privada.

## 5. Configuração tributária obrigatória antes de emitir qualquer documento real

Antes de preparar `NationalDpsConfiguration` para um documento real:

1. Confirmar com contabilidade/jurídico: `codigo_tributacao_nacional`,
   `codigo_tributacao_municipal`, `trib_issqn`, `tp_ret_issqn`,
   `aliquota_percentual`, `regEspTrib`, `opSimpNac`, inscrição municipal.
2. Confirmar no dia da integração o Swagger/OpenAPI restrito vigente
   (a fundação M27 cita o manual/XSD como evidência, mas não baixou o
   Swagger interativo — ver relatório M27 §10.3).
3. Só depois disso construir o `NationalDpsConfiguration` real (hoje não há
   tela de admin para isso — é um passo de código/dados desta futura sessão,
   fora do escopo do M28).

## 6. Como inspecionar prontidão/preflight SEM enviar nada

```bash
# Prontidão (somente leitura, RBAC leitura):
curl -s -H "Authorization: Bearer $TOKEN" \
  https://<host>/painel-soprolife/api/m15/fiscal/status | jq .restricted_provider_foundation

# Resumo de fila:
curl -s -H "Authorization: Bearer $TOKEN" \
  https://<host>/painel-soprolife/api/m15/fiscal/fila-resumo | jq .

# Preflight de um documento específico (RBAC gestor; grava artefatos SÓ se
# chegar a READY_TO_SEND — nunca envia nada pela rede):
curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  https://<host>/painel-soprolife/api/m15/fiscal/documentos/<id>/preflight | jq .
```

Leia `status`, `stage_reached` e `blockers` — cada blocker é um código
estável (ex.: `certificate_not_supplied`, `dps_schema_invalid`,
`national_dps_configuration_not_defined_for_any_real_document`).

## 7. O portão de rede restrito — o que ele é exatamente

`M15_NFSE_RESTRICTED_NETWORK_ENABLED=false` é o **padrão permanente**.
`HttpxRestrictedTransport._assert_gate_open()` reavalia isto (e
`environment == 'restricted'` e HTTPS) em **toda** chamada — não é uma
checagem única no boot.

### Checklist de segurança ANTES de ligar o portão

- [ ] Certificado real instalado com as permissões do §1, validado por §6
      (`certificate_syntactically_valid: true`, não expirado).
- [ ] `NationalDpsConfiguration` real definida e revisada por
      contabilidade/jurídico (§5).
- [ ] Política fiscal (`FiscalPolicy`) `validated` para o ambiente
      `restricted`, cobrindo DIRECT e HOME (`readiness.fiscal_policy_ready == true`).
- [ ] `M15_NFSE_FISCAL_ARTIFACTS_DIR` configurado e confirmado 0700
      (`readiness.artifact_storage_ready == true`).
- [ ] URL restrita confirmada como a vigente no dia (§5.2), HTTPS.
- [ ] Documento de teste (não real, se possível) escolhido para a PRIMEIRA
      tentativa — não o maior lote pendente.
- [ ] Uma pessoa disponível para observar o resultado imediatamente após a
      chamada (não deixar rodando sem supervisão).

### Ação exata para ligar o portão temporariamente

```bash
# Systemd: editar o EnvironmentFile privado (0600), não o .env do worktree.
M15_NFSE_RESTRICTED_NETWORK_ENABLED=true
# reiniciar só o processo da API, confirmar com o comando do §6 que
# network_gate_enabled agora aparece true.
```

### Ação exata para desligar de novo

```bash
M15_NFSE_RESTRICTED_NETWORK_ENABLED=false
# reiniciar o processo da API de novo. Confirmar com §6.
```

Nunca deixe o portão ligado entre sessões de teste — ele é para a duração de
UMA chamada supervisionada.

## 8. Como inspecionar resposta/artefatos sanitizados depois de uma chamada real

- O provedor nunca persiste corpo bruto de resposta, cabeçalhos ou erro de
  exceção — só o `Outcome` classificado (`simulated`/`uncertain`/`rejected`/
  `not_found`/`cancelled`) e, se chegou a `READY_TO_SEND`, os artefatos DPS
  (não-assinado/assinado) sob `M15_NFSE_FISCAL_ARTIFACTS_DIR`, com
  permissão 0600, indexados em `fiscal_artifacts` (metadados — nunca os
  bytes na tabela).
- Para inspecionar um artefato: localizar a linha em `fiscal_artifacts`
  (via `/fiscal/documentos/{id}/tentativas` ou consulta direta ao banco),
  ler o arquivo pelo `storage_relative_path` sob o diretório configurado.
  Nunca copiar o artefato para dentro do Git ou de um diretório público.

## 9. O que fazer em timeout / UNCERTAIN

- **Nunca reenviar automaticamente.** `nfse.operate()` recusa uma segunda
  tentativa de `issue` sobre um documento `uncertain`/`issuing`/
  `reconciling` com o código `reconciliation_required` — isso já é testado
  (`tests/test_nfse_foundation.py::test_uncertain_no_blind_retry_and_reconciliation`,
  `tests/test_nfse_offline_safety_gates.py`).
- Ação correta: usar a operação de **reconciliação** explícita
  (`POST /fiscal/documentos/{id}/reconciliar`), que consulta o estado real
  antes de decidir — nunca assume ausência a partir de um timeout/5xx.
- Se a reconciliação também ficar incerta, aguardar e tentar reconciliar de
  novo mais tarde — não é um estado de erro do sistema, é o estado correto
  quando a evidência ainda não existe.

## 10. Procedimento de rollback / parada

1. Desligar `M15_NFSE_RESTRICTED_NETWORK_ENABLED` (§7) imediatamente — isso
   por si só impede qualquer nova chamada, mesmo que outro passo falhe.
2. Não apagar nenhum `FiscalAttempt`/`FiscalArtifact` — são evidência
   append-only por desenho; um gatilho de banco recusa `UPDATE`/`DELETE`.
3. Se um documento ficou em `uncertain`/`issuing` e não pode ser
   reconciliado no momento, deixe assim — reconciliar mais tarde é seguro;
   reenviar não é.
4. Se algo parecer errado com a configuração tributária, **não corrija a
   `FiscalPolicy` existente** (ela é imutável por versão) — crie uma nova
   versão e repita o preflight antes de qualquer nova tentativa de emissão.
5. Comunicar ao restante da equipe antes de tentar de novo.

---

Nada neste documento autoriza, por si só, ligar o portão ou usar um
certificado real — ele existe para que a PRÓXIMA sessão explicitamente
autorizada saiba exatamente onde estão os botões e o que checar antes de
apertá-los.
