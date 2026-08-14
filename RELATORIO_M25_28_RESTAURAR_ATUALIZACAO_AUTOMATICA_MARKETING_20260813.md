# M25.28 — Restaurar a atualização automática de Marketing & SEO

**Data:** 13/08/2026
**Branch:** `claude-m25-28-restaurar-update-marketing`
**Base:** `7b1bbe7` (fim da M25.27)
**HEAD final:** `c2c39a3`
**VPS:** `soprolife-painel-01` — `/opt/soprolife/soprolife-site`

---

## Resumo em uma frase

A automação nunca parou de rodar: o timer disparava a cada 10 minutos e o
Google respondia todas as vezes. O que estava quebrado eram **três falsos
positivos dos guardas de segurança**, que abortavam a gravação dos snapshots
e deixavam a tela congelada em silêncio — preservando corretamente o último
dado bom, mas sem nunca avisar que era o último.

---

## 1. Reconstrução após a queda de energia

A primeira tarefa era descobrir o que a sessão do notebook chegou a fazer
antes de desligar. Resposta: **nada que tenha sobrevivido, e nada na VPS.**

| Verificação | Resultado |
|---|---|
| Worktree local | Limpo — sem diff, sem staged, sem untracked |
| HEAD local | `7b1bbe7`, idêntico a `origin/painel-soprolife-v01` |
| Branch M25.28 no GitHub | **Não existia** |
| Git da VPS | Limpo, em `7b1bbe7`, sem modificação não commitada |
| Unit files da VPS | `soprolife-update-data.service` de 05/08 22:51; `.timer` de 30/06 23:46 — **nenhum tocado em 12–13/08** |
| Backups datados (`*.bak`, `*.orig`) posteriores a 12/08 | Nenhum |
| Lock em `/run/soprolife-update/` | Vazio, de 24/07 — não estava preso |

**Conclusão:** a sessão anterior não commitou, não publicou, não fez deploy e
não alterou service nem timer. A missão foi retomada do zero, mas com o
estado real provado antes de qualquer alteração — não presumido.

**Nota de acesso:** o SSH da VPS caiu no *check mode* do Tailscale e exigiu
reautorização humana pelo navegador. Cada tentativa emite uma URL própria,
válida só enquanto aquela conexão está viva (~10 min). Foram necessárias três
tentativas até o operador autorizar, às 20:56.

---

## 2. Estado inicial encontrado

```
× soprolife-update-data.service — failed (Result: exit-code), status=1
● soprolife-update-data.timer   — active (waiting), disparando a cada 10 min
```

O timer **estava saudável desde 09/08 14:43** e disparou pontualmente durante
toda a janela do incidente. A hipótese "timer parado" foi descartada logo no
primeiro comando.

### Congelamento real dos dados

| Arquivo | Parado em | Dias parado |
|---|---|---|
| `marketing-seo.local.json` | **05/08 22:32:02** | 8 |
| `auditoria-summary.local.json` | **09/08 14:54** | 4 |
| `resumo-dashboard.local.json` | 09/08 14:54 | 4 |
| `financeiro-summary.local.json` | 09/08 14:54 | 4 |
| `leads-summary.local.json` | 09/08 14:54 | 4 |
| `crm-clinicas.local.json` | 09/08 14:54 | 4 |
| `parcerias-pastore-summary.local.json` | 09/08 14:54 | 4 |
| `followup-pacientes-summary.local.json` | 09/08 14:54 | 4 |
| `followup-clinicas-summary.local.json` | 09/08 14:54 | 4 |
| `crm-contatos-b2b-summary.local.json` | 09/08 14:54 | 4 |

> **O escopo era maior do que o relatado.** O chamado era sobre Marketing &
> SEO, mas **todo o Command Center** estava congelado desde 09/08 — CRM,
> financeiro, leads, Pastore, follow-ups. A validação de snapshot é
> tudo-ou-nada (`raise ValueError` em `snapshots.py:805`): um único payload
> reprovado impede a gravação dos nove.

### Última execução boa e primeira ruim

| Sintoma | Última boa | Primeira ruim |
|---|---|---|
| Marketing & SEO | 05/08 22:32:02 (mtime do snapshot) | entre 05/08 22:32 e **07/08 00:55:16** |
| Snapshots do PostgreSQL | **09/08 14:54:23** (journal) | **09/08 15:05:03** (journal) |

A primeira falha de Marketing não pôde ser datada com precisão porque **o
journal é volátil**: `journalctl --list-boots` mostra um único boot, iniciado
em 07/08 00:50. Tudo anterior se perdeu no reboot. A primeira ocorrência
observável é a primeira execução após esse boot — ou seja, já estava
quebrado antes. A janela é limitada pelos dois extremos acima e isso é o
máximo que a evidência sustenta.

Volume da falha: **947** ocorrências do erro de Marketing e **586** do erro
de auditoria no journal disponível.

---

## 3. Causa raiz

Três defeitos independentes, todos **falsos positivos de guardas de PII**.
Nenhum tem relação com credencial, rede, dependência ou permissão.

### Causa 1 — uma busca no Google travou o Marketing

```
Consultando Search Console...  OK — impressões: 2308, cliques: 69
Consultando GA4...             OK — usuários: 36, sessões: 59
ERRO PII [marketing-seo]: termo clinico livre em 'searchConsole.topQueries[82].query'
ERRO: 1 violacao(oes) de PII — gravacao do summary abortada.
```

O valor exato, obtido por instrumentação somente-leitura do validador:

```
topQueries[82].query = 'precisa de pedido médico?'   impressões=1  cliques=0
```

`pii_guard.py:89` varre todo valor string com
`\blaudo\b|pedido m[eé]dico|diagn[oó]stico|resultado de exame`. Uma pessoa
pesquisou "precisa de pedido médico?" no Google, viu um resultado da
SoproLife, e essa consulta entrou no top 107 do Search Console.

O que o Search Console devolve em `query` é **termo de busca agregado e
anonimizado pelo próprio Google** — consultas raras nem são reportadas. Não
é texto sobre paciente algum; é palavra-chave de SEO, e vocabulário clínico
é inerente a uma empresa de espirometria. É exatamente o que a SoproLife
quer ranquear.

**Uma impressão. Zero cliques. Oito dias de tela congelada.**

O campo `query` já constava em `campos_institucionais`, mas essa lista isenta
apenas o detector de nome de pessoa — nunca os scans de conteúdo.

### Causa 2 — o nome da ação de laudo congelou todos os snapshots

```
ERRO PII [auditoria-summary.local.json]: chave proibida 'laudo_conteudo_entregue' em stats.por_acao
ERRO PII [auditoria-summary.local.json]: chave proibida 'laudo_original_atribuido'  em stats.por_acao
ERRO PII [auditoria-summary.local.json]: chave proibida 'laudo_nativo_previa_gerada' em stats.por_acao
ERRO PII [auditoria-summary.local.json]: chave proibida 'laudo_assinado_e_liberado' em stats.por_acao
ERRO PII [auditoria-summary.local.json]: chave proibida 'exame_reaberto_para_laudo' em stats.por_acao
ERRO PII [auditoria-summary.local.json]: chave proibida 'laudo_corretivo_aberto'    em stats.por_acao
auditoria-summary.local.json: termo proibido 'laudo' detectado em auditoria-summary.
```

Dois guardas rejeitando a mesma palavra, por motivos diferentes:

1. **`pii_guard._FORBIDDEN_KEY_TOKENS`** contém `laudo` e é aplicado a *nomes
   de chave*. Em `stats.por_acao` a chave é o **nome da operação auditada**,
   com um inteiro por valor — não um campo carregando conteúdo.
2. **`audit_summary_contract.FORBIDDEN_TERMS`** serializa o payload inteiro
   numa string e procura substring. `laudo_conteudo_entregue` contém
   `laudo`.

Data exata: **09/08 15:05** — entre 14:54:23 (último snapshot bom) e 15:05:03
(primeiro ruim) a operação médica registrou a primeira ação de laudo em
produção. A partir daí, todo o painel parou de atualizar.

O guarda estava certo em existir e errado no alvo: ele foi escrito para
impedir que o *texto* de um laudo vazasse, e passou a barrar o *nome da
operação* que gera laudos.

### Causa 3 — o CTR sem arredondamento parecia um CPF

Esta só apareceu **depois** de corrigir as duas primeiras, quando o Marketing
voltou a gravar de fato:

```
ERRO: padrão de CPF detectado em marketing-seo.local.json.
```

Localização exata:

```
searchConsole.totals.ctr         = 0.029896013864818025   -> casou: '02989601386'
searchConsole.totals.avgPosition = 6.54159445407279       -> casou: '54159445407'
```

O detector `\d{3}\.?\d{3}\.?\d{3}-?\d{2}` aceita **qualquer sequência de 11
dígitos**, e um float de dupla precisão tem 18. As linhas de detalhe
(`topQueries`, `topPages`) já arredondavam desde sempre; apenas os totais
agregados escapavam, gravados como `float()` cru.

---

## 4. O que NÃO era o problema

A missão pediu para provar cada hipótese, não descartá-la por conveniência.

| Hipótese | Veredito | Evidência |
|---|---|---|
| Timer parado/desabilitado | **Descartada** | `active (waiting)` desde 09/08, `LastTrigger` sempre atual, `NEXT` definido |
| ADC / credencial Google vencida | **Descartada** | `credencial: service_account (leitura, durável)`; SC e GA4 retornaram dados em toda execução |
| `invalid_grant` / revoked / scopes | **Descartada** | Nenhuma ocorrência no journal; a unit removeu `CLOUDSDK_CONFIG` na M23 de propósito |
| Dependências Python / venv | **Descartada** | `googleapiclient`, `google_auth_httplib2`, `google.analytics.data_v1beta` importam no venv da esteira |
| Interpretador errado (2º incidente M23) | **Descartada** | `Interpretador: /opt/soprolife/venvs/marketing/bin/python`, correto |
| Permissões / ownership root | **Descartada** | Snapshots `soprolife:soprolife`; nenhum arquivo recriado como root por deploy |
| Lock preso em `/run/soprolife-update/` | **Descartada** | Lock vazio, de 24/07; nenhum `INFO: outra atualização já está em execução` |
| Processos órfãos | **Descartada** | Nenhum `update-local-data` ou `read-marketing-seo` pendurado |

Uma observação de ownership que **não** é causa mas fica registrada:
`painel-soprolife/scripts/pii_guard.py` está como `root:root` na VPS (de
25/07), enquanto os demais scripts são `soprolife:soprolife`. É lido, nunca
escrito, então não afetou nada. Não foi alterado nesta missão.

---

## 5. Correção

Quatro commits pequenos. **Nenhum guarda foi enfraquecido** — cada dispensa é
estreita, declarada e coberta por casos negativos.

| Commit | O que faz |
|---|---|
| `dd886cc` | `pii_guard.py`: distingue rótulo de contagem de nome de campo, e termo de busca agregada de texto clínico |
| `7cee2ba` | `read-marketing-seo-adc.py`: declara `query` e `page` como busca agregada + testes |
| `38402fa` | `audit_summary_contract.py`: valida rótulo de vocabulário pela FORMA em vez de varrer como texto livre |
| `c2c39a3` | `read-marketing-seo-adc.py`: arredonda os totais do Search Console + testes |

### Como cada dispensa foi mantida estreita

**Mapa de contagem** (`mapas_de_contagem: por_acao, por_entidade,
por_operador, por_resultado`): a chave deixa de ser lida como nome de campo,
**mas passa a ser varrida como valor**. Um telefone ou CPF disfarçado de
rótulo continua abortando a gravação — antes disso nem era verificado. A
mudança é, nesse ponto, mais rigorosa que o comportamento anterior.

**Busca agregada** (`campos_busca_agregada: query, page`): dispensa
**exclusivamente** o scan de termo clínico. Telefone, CPF, e-mail, token e
detector de nome continuam valendo dentro da própria consulta.

**Vocabulário da auditoria**: o rótulo é validado pela forma (`^[a-z0-9_.]{1,80}$`)
antes de ser mascarado. O que não tem forma de slug continua sendo varrido
**e** é denunciado pela forma — a dispensa não cria esconderijo. O CPF segue
procurado no payload **original**, nunca no mascarado.

**Arredondamento**: o detector de CPF não foi tocado. O que mudou foi o dado,
que passou a ter a mesma precisão das linhas de detalhe.

### Testes

| Suíte | Resultado |
|---|---|
| `pii_guard.py --self-test` | **33 casos OK** (21 originais + 12 novos) |
| `tests/test_m23_audit_identifiers.py` | **20 passed** (15 + 5 novos) |
| `tests/test_m23_postgres_only.py` | 28 passed |
| `test-marketing-runtime.py` | todos OK (+11 casos novos) |
| `test-marketing-credencial.py` | todos OK |
| `test-search-console-reconciliation.py` | OK |
| `test-freshness-contract.py` | todos OK |
| `test-systemd-units.py` | todos OK |
| `test-m23-postgres-only.py` | todas as guardas OK |

Os testes reproduzem a falha real, não uma aproximação: as seis ações de
laudo que existiam na trilha em 13/08, e a consulta literal
`"precisa de pedido médico?"`. Cada correção tem um caso-âncora que falha
alto se a premissa deixar de valer, e casos negativos que provam que o
guarda continua barrando o que deve.

---

## 6. Deploy

Sem `reset --hard`, sem force push, sem force-with-lease.

```
local → origin/claude-m25-28-restaurar-update-marketing   (branch publicada)
local → origin/painel-soprolife-v01                       7b1bbe7..38402fa (ff)
                                                          38402fa..c2c39a3 (ff)
VPS   → git fetch + merge --ff-only                       7b1bbe7 → c2c39a3
```

**Nenhuma unit mudou** → sem `daemon-reload`, sem restart de serviço. A
correção é só de código Python lido a cada execução da esteira.

---

## 7. Validação

### Atualização manual controlada

```
Result=success
ExecMainStatus=0
ExecMainCode=1        ← CLD_EXITED (motivo da saída), não código de erro
ActiveState=inactive
SubState=dead         ← normal para oneshot bem-sucedido
```

### Timestamps antes → depois

| Arquivo | Antes | Depois |
|---|---|---|
| `marketing-seo.local.json` | 05/08 22:32 | **13/08 21:14** |
| `auditoria-summary.local.json` | 09/08 14:54 | **13/08 21:13** |
| `resumo-dashboard.local.json` | 09/08 14:54 | **13/08 21:13** |
| `financeiro-summary.local.json` | 09/08 14:54 | **13/08 21:13** |
| demais snapshots do PostgreSQL | 09/08 14:54 | **13/08 21:13** |

### Search Console e GA4

```
geradoEm      : 2026-08-14T00:13:59+00:00
SC totals     : impressions=2308, clicks=69, ctr=0.0299, avgPosition=6.5
SC topQueries : 107
GA4 totals    : users=36, sessions=59, pageviews=89
Search Console: fresh — dados até 2026-08-12
GA4           : fresh — dados até 2026-08-12
```

`ctr` gravado como `0.0299` e não mais `0.029896013864818025`.

**Sobre o atraso do Google:** os dados vão até **12/08**, não 13/08, e isso
está correto — Search Console e GA4 têm latência natural. O que precisa ser
atual é o horário da consulta e o estado de saúde da pipeline, e ambos são.
Nada foi marcado como novo artificialmente.

### Auditoria de segurança

```
OK: marketing-seo.local.json seguro — configured=True, SC=True, GA4=True.
OK: auditoria-summary seguro — 645 evento(s), 4 erro(s) de escrita, últimos=30.
status_geral: atencao
```

`status_geral` saiu de **critico** para **atencao**. O alerta remanescente —
"Trilha de auditoria: 4 erro(s) de escrita registrado(s)" — é dado
operacional real (4 eventos com `resultado=falha` na trilha), não defeito da
esteira. Fora do escopo desta missão.

### Preservação do last-known-good

Confirmada em toda a janela do incidente: durante 8 dias de falha, **nenhum
arquivo foi apagado, zerado ou substituído por dado de exemplo**. O
contrato de escrita atômica funcionou exatamente como projetado.

O problema não foi perda de dado — foi **silêncio**. A tela mostrava um
número velho sem dizer que era o último. É por isso que
`generate-saude-operacional.py` continua sendo a peça que precisa ser olhada:
foi ela que marcou `critico` durante todo o período, e ninguém viu.

### Botão "Atualizar dados"

A fila de pedido manual (`marketing-refresh-request.json`) foi exercitada em
todas as execuções desta missão — havia um pedido pendente enfileirado desde
antes, e o journal registra:

```
Pedido manual de atualização encontrado na fila.
Pedido manual de Marketing concluído após a tentativa.
```

O pedido agora é consumido **após** uma tentativa que de fato grava, em vez
de após uma que aborta.

### Execução automática real pelo timer

Ver seção 9.

---

## 8. Regressões

Nada foi tocado fora do necessário:

- **M25.23 gate** — intocado
- **M25.27 área médica** — intocada; nenhum arquivo do fluxo médico no diff
- **CRM, financeiro, Pastore, laudos** — nenhuma alteração de lógica; voltaram
  a atualizar porque a esteira voltou a gravar
- **Conta médica** — segue somente com a área de Laudos
- **Nenhum paciente ou exame criado** para esta missão
- **Timer** — cadência de 10 minutos **preservada**, não foi transformada em
  diária
- **Serviço** — segue não-root (`User=soprolife`, `Group=soprolife`)

Arquivos alterados: 5, todos em `painel-soprolife/scripts/` e
`painel-soprolife/nucleo-m15/tests/`.

---

## 9. Prova de execução automática

Atualização manual não prova nada sozinha. A prova exigida é uma execução
**disparada pelo timer**, sem intervenção.

### Ciclo automático — 21:23:57

```
--- service ---
Result=success
ExecMainStatus=0
ExecMainStartTimestamp=Thu 2026-08-13 21:23:57 -03
ExecMainExitTimestamp =Thu 2026-08-13 21:24:09 -03
ActiveState=inactive
SubState=dead
InvocationID=b442a2271d7e4853b6fa291c6dd19d1c

--- timer ---
LastTriggerUSec=Thu 2026-08-13 21:23:57 -03
Result=success
ActiveState=active
SubState=waiting
NEXT: Thu 2026-08-13 21:33:57 -03
```

**Por que isto prova que foi o timer, e não eu:**

1. `LastTriggerUSec` do timer (`21:23:57`) é **idêntico** ao
   `ExecMainStartTimestamp` do service (`21:23:57`) — o service foi ativado
   pelo próprio disparo do timer.
2. O último `systemctl start` manual desta missão foi às **21:13:57**, dez
   minutos antes, com outro `InvocationID`. Nenhum comando foi emitido na
   janela de 21:23.
3. `Triggers: ● soprolife-update-data.service` na unit do timer, e
   `TriggeredBy: ● soprolife-update-data.timer` na do service.
4. O intervalo entre os dois disparos é exatamente `OnUnitActiveSec=10min`.

Conteúdo da execução automática:

```
Snapshots atualizados a partir do banco.
Marketing & SEO atualizado.
  OK: marketing-seo.local.json seguro — configured=True, SC=True, GA4=True.
  OK: auditoria-summary seguro — 645 evento(s), 4 erro(s) de escrita, últimos=30.
status_geral: atencao
Concluído. Fonte operacional: PostgreSQL (Núcleo M15).
```

Snapshots regravados pela execução automática: `21:23` (PostgreSQL) e `21:24`
(Marketing).

### Ciclo automático — 21:34:16 (repetição)

Um sucesso isolado é fraco. O ciclo seguinte, também sem intervenção:

```
Result=success
ExecMainStatus=0
ExecMainStartTimestamp=Thu 2026-08-13 21:34:16 -03
InvocationID=e40d79e3383648aca50dce46dfb3c920     ← diferente do anterior
LastTriggerUSec=Thu 2026-08-13 21:34:16 -03
NEXT: Thu 2026-08-13 21:44:16 -03
```

Journal do disparo, do começo ao fim:

```
21:34:16  Starting soprolife-update-data.service — SoproLife — Atualização automática...
21:34:28  soprolife-update-data.service: Deactivated successfully.
21:34:28  Finished soprolife-update-data.service — SoproLife — Atualização automática...
```

`marketing-seo.local.json` regravado às **21:34**.

**Três execuções boas em sequência:** 21:13:57 (manual controlada), 21:23:57
(automática) e 21:34:16 (automática), cada uma com `InvocationID` próprio.

### Estado final exigido

| Exigência | Resultado |
|---|---|
| timer `active (waiting)` | ✅ |
| `NEXT` definido | ✅ 21:33:57 |
| service após oneshot bom: `Result=success` | ✅ |
| `ExecMainStatus=0` | ✅ |
| `inactive (dead)` após oneshot | ✅ (normal, não é falha) |

Sobre `ExecMainCode=1`: é o código de **motivo** de saída do systemd
(`CLD_EXITED`, saiu normalmente), não um código de erro. O que importa é
`ExecMainStatus=0`.

---

## 10. Processos órfãos

Nenhum. `pgrep -af "update-local-data|read-marketing-seo"` não retorna nada
além do próprio comando de verificação. Os dois scripts de diagnóstico
temporários criados em `/tmp` na VPS foram removidos ao fim da investigação.

---

## 11. Pendências

1. **Alerta de silêncio.** A esteira ficou 8 dias quebrada e ninguém soube. A
   Saúde Operacional marcou `critico` o tempo todo, mas nada empurrou esse
   estado para fora do painel. Vale uma etapa própria para notificação ativa.
2. **`pii_guard.py` como `root:root` na VPS.** Inofensivo hoje (só leitura),
   mas destoa dos demais scripts. Corrigir em janela de manutenção.
3. **Journal volátil.** `Storage` não é persistente: um reboot apagou a
   evidência anterior a 07/08 e impediu datar com precisão a primeira falha de
   Marketing. Considerar `/var/log/journal`.
4. **4 erros de escrita na trilha de auditoria** — dado operacional real,
   pendente de investigação separada.
5. **Detector de CPF genérico demais.** `\d{3}\.?\d{3}\.?\d{3}-?\d{2}` casa com
   qualquer sequência de 11 dígitos. Foi corrigido o dado, não o detector;
   outro campo numérico longo pode reproduzir a classe de falha.

---

## 12. Conclusão

Provado ponto a ponto, com evidência e não com suposição:

1. última execução boa — 05/08 22:32 (Marketing) e 09/08 14:54:23 (snapshots);
2. primeira execução ruim — 09/08 15:05:03 (snapshots); janela limitada para
   Marketing, pelo journal volátil;
3. causa raiz exata — três falsos positivos de guarda de PII, cada um com o
   valor literal que o disparou;
4. etapa que falhava — 2/6 (snapshots), 3/6 (Marketing) e 5/6 (check-access);
5. autenticação Google — conta de serviço durável, íntegra, nunca em causa;
6. dependências Python/venv — corretas, interpretador dedicado resolvido;
7. ownership/permissões — corretos, serviço segue não-root;
8. Search Console — 2308 impressões, 69 cliques, 107 consultas;
9. GA4 — 36 usuários, 59 sessões, 89 pageviews;
10. geração dos JSONs — 9 snapshots + Marketing, todos regravados;
11. last-known-good — preservado durante os 8 dias, nada apagado ou zerado;
12. botão "Atualizar dados" — fila consumida após tentativa que grava;
13. timer — `active (waiting)`, cadência de 10 min preservada, duas execuções
    automáticas consecutivas com `Result=success` e `ExecMainStatus=0`.

---

# M25.28 — ATUALIZAÇÃO AUTOMÁTICA DE MARKETING & SEO RESTAURADA E MONITORÁVEL

**Restaurada:** Marketing & SEO voltou a atualizar sozinho, e junto com ele
todo o Command Center, que estava congelado há 4 dias sem que o chamado
soubesse.

**Monitorável:** `status_geral` refletia `critico` durante toda a falha e
voltou a `atencao` com a correção — o indicador sempre funcionou. O que falta,
e fica registrado como pendência 1, é alguém ou algo **ser avisado** quando
ele vira `critico`. Foi o silêncio, não a falta de dado, que deixou o
problema durar oito dias.
