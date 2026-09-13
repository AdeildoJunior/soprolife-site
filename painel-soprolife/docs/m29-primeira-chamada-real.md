# M29 — Addendum ao playbook da primeira chamada real

Este documento **complementa** `m28-nfse-restrito-primeira-chamada.md` (leia-o
primeiro, do início ao fim) com o que a M29 acrescentou: configuração fiscal
nacional real e versionada, descrição de serviço estruturada, wiring do
`RestrictedNfseProvider`, e o launcher humano-controlado.

## 0. O que a M29 entregou (não repetir)

- Perfil fiscal REAL da SoproLife modelado em
  `app/services/nfse_national/config.py` (Simples Nacional, RJ, código de
  tributação nacional `040201`, municipal `001`, NBS `123019900`, alíquota
  aproximada do Simples atual 6,00% via `pTotTribSN`).
- Tabela versionada e imutável `national_dps_configurations`
  (`app/services/nfse_national/fiscal_config.py`) — uma nova decisão contábil
  é sempre uma versão nova, nunca uma edição.
- Mapeamento estruturado de descrição de serviço (com/sem broncodilatador)
  em `app/services/nfse_national/service_description.py`, usado pelo
  preflight e pelo dispatcher — nunca inferido de texto livre.
- `RestrictedNfseProvider` agora É alcançável a partir de `nfse.operate()`
  (via `app/services/nfse_national/dispatch.py`), mas só quando TODOS os
  portões (ambiente, flags, certificado, política fiscal, configuração
  nacional ativa, armazenamento de artefatos) estiverem satisfeitos.
  `M15_NFSE_RESTRICTED_NETWORK_ENABLED` continua `false` por padrão — isso
  não mudou.
- Admin UI em `/fiscal` → "Configuração Fiscal Nacional": qualquer usuário
  autenticado inspeciona a versão ativa e o histórico; só `admin` cria uma
  nova versão.

## 1. Antes de qualquer chamada real: cadastrar a configuração fiscal

A M28 deixava `/fiscal/status` sempre bloqueado em
`national_dps_configuration_not_defined_for_any_real_document`. A M29
resolve isso com um cadastro real — mas **ninguém faz isso automaticamente**:

1. Confirme com contabilidade/jurídico os valores atuais (alíquota do
   Simples, códigos de tributação, regime) — os valores já modelados nesta
   sessão vêm do brief da missão M29 e devem ser CONFERIDOS, não assumidos
   como definitivos para sempre.
2. Com um usuário `admin`, use a Central de Comando (`Fiscal` →
   "Configuração Fiscal Nacional" → "Nova versão efetiva") ou
   `POST /fiscal/configuracao-nacional` diretamente, com
   `validation_state="validated"` e `effective_from` na data correta.
3. Confirme em `/fiscal/status` → `restricted_provider_foundation.readiness`
   que `national_dps_configuration_ready` passou a `true` e que o blocker
   `national_dps_configuration_not_defined_for_any_real_document` sumiu da
   lista.

## 2. Certificado real e senha

Sem mudança em relação à M28 — ver `m28-nfse-restrito-primeira-chamada.md`
§1–§2. A senha nunca é fornecida a nenhuma sessão de IA, nunca é persistida
em banco/log, e só existe como variável de ambiente do processo da API.

## 3. Rodar o preflight com o perfil real

Com a configuração nacional cadastrada e o certificado configurado no
ambiente do processo da API:

```bash
curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  https://<host>/painel-soprolife/api/m15/fiscal/documentos/<id>/preflight | jq .
```

Deve chegar a `"status": "ready_to_send"` com `blockers: []`. Se não chegar,
o campo `stage_reached`/`blockers` diz exatamente o que falta — nunca
adivinhe, corrija o item nomeado.

## 4. O launcher humano-controlado

`scripts/nfse_first_restricted_call.py` (construído na M29, **nunca
executado por nenhuma sessão de IA**) automatiza os passos de checagem e
confirmação da primeira chamada real, mas **não** liga o portão de rede
sozinho — ele imprime o comando exato e espera confirmação humana de que o
processo da API já foi reiniciado com o portão aberto.

```bash
python3 painel-soprolife/nucleo-m15/scripts/nfse_first_restricted_call.py \
  --api-base https://<host>/painel-soprolife/api/m15 \
  --document-id <fiscal_document_id> \
  --pfx-path /home/fedorasurf/.local/share/soprolife/secrets/nfse/soprolife-nfse.pfx
```

Testes determinísticos da lógica pura do launcher (sem certificado/rede
reais):

```bash
python3 painel-soprolife/nucleo-m15/scripts/test_nfse_first_restricted_call.py
```

## 5. Limitação conhecida e honesta: chave de acesso da NFS-e

O `RestrictedNfseProvider` classifica uma resposta HTTP 2xx bem-formada como
`SIMULATED`, mas **não extrai** a chave de acesso da NFS-e do corpo da
resposta (`external_id` continua `None`). `nfse._normalized()` já trata
`SIMULATED` sem `external_id` como `UNCERTAIN` — o mesmo tratamento
fail-closed que qualquer outro provedor sem identificador receberia. Isso
significa que, mesmo com tudo correto, a primeira chamada real hoje
provavelmente terminará em `uncertain`, exigindo reconciliação explícita.
Implementar a extração da chave de acesso do XML de resposta é trabalho de
uma sessão futura, com sua própria revisão — não foi inventado aqui.

## 6. Rollback

Idêntico à M28 (`m28-nfse-restrito-primeira-chamada.md` §10): fechar o
portão primeiro, nunca apagar `FiscalAttempt`/`FiscalArtifact`, nunca
reenviar um documento `uncertain` fora da reconciliação explícita, criar uma
nova versão de configuração em vez de editar uma existente se algo parecer
errado.
