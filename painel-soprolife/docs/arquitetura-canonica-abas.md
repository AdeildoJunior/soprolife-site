# Arquitetura canônica das abas — M14.3

Este documento define a arquitetura de dados canônica da planilha
"SoproLife - Painel Interno - Dados Privados" e sua relação com o
Painel SoproLife / Centro de Comando. Ele consolida decisões sobre
entidades, IDs, datas, enums, Pastore, Manual das Abas e o plano de
reconciliação histórica.

**Nada nesta etapa altera dados reais.** Toda migração descrita aqui é
futura, em dry-run primeiro, e exige autorização explícita.

## Fontes de verdade no repositório

| Arquivo | O que define |
|---|---|
| `core/contracts/abas-manifest.json` | Cada aba: tipo, status, quem grava, quem lê, PII, permissões, matriz de simplificação |
| `core/contracts/enums-canonicos.json` | Vocabulário canônico + aliases legados (só sugestão de migração) |
| `core/contracts/ids-canonicos.json` | Padrões de ID por entidade + estratégia de mapeamento legado→canônico |
| `scripts/soprolife_normalizacao.py` | Implementação única de datas com precisão, IDs, enums e chave de paciente |
| `scripts/reconciliar-historico.py` | Auditoria e plano de reconciliação (somente leitura) |
| `scripts/generate-manual-abas-gs.py` | Gera `apps-script/manual-das-abas.gs` a partir do manifesto |

## O modelo de entidades

```
PESSOA (CRM Pacientes — 1 linha por paciente, paciente_id)
  │
  ├── EXAME     (CRM Espirometria — 1 linha por exame,   exame_id = id_atendimento)
  ├── CONSULTA  (CRM Consultas    — 1 linha por consulta, consulta_id)
  │
  └── cada EXAME tem N MOVIMENTOS financeiros (ledger append-only):
        no máximo 1 receita principal ativa + ajustes/complementos/estornos
        (Financeiro_Lancamentos — id_lancamento por movimento,
         vinculados ao exame por id_atendimento)
```

Regras invioláveis:

- **Uma pessoa aparece uma vez** em CRM Pacientes. Vários exames da mesma
  pessoa são várias linhas em CRM Espirometria, nunca vários pacientes.
- **CRM Espirometria contém todos os exames reais** — de qualquer origem:
  SoproLife direta, domiciliar, coworking/clínica, empresa/PCMSO, Pastore.
  ID antigo, data incompleta ou falta de lançamento financeiro **não**
  tornam um registro teste.
- **Financeiro_Lancamentos é a única fonte monetária.** O CRM Espirometria
  é operacional e nunca origina valores.
- **CRM Consultas** hoje tem 1 consulta real — correto; preservar.

## Por que faltam pacientes em CRM Pacientes — e o que mudou (M14.3A)

A antiga consolidação (`sincronizarCRMPacientesSoproLife`) deduplicava por
**telefone → primeiro nome** e REESCREVIA a aba inteira. Ela está
**BLOQUEADA** (lança erro; menu removido): apagar/reconstruir o mestre
persistente e fundir por nome foram reprovados em auditoria independente.

Regras vigentes de vínculo (nunca automáticas):
- **nome sozinho NUNCA vincula** (homônimos existem);
- **telefone gera apenas candidato**; telefone presente em mais de um
  cadastro = **ambiguous**;
- candidato não confirmado = **pending**; sem informação suficiente =
  **unmatchable**; vínculo determinístico só por `paciente_id` explícito
  (**linked**);
- nenhuma linha é eliminada; nenhuma fusão é automática.

`reconciliar-historico.py` audita a cobertura nesses quatro estados; a
migração incremental real (upsert + LockService + staging + rollback) é a
M14.3B, com backup e autorização.

## IDs (M14.3A: autoridade do servidor)

- Formato canônico novo: `PREFIXO-<UUID>` emitido pelo **servidor**
  (`ctNovoIdServidor`, contratos-canonicos.gs) — nunca lastRow, contador
  sem lock, `Math.random`/relógio do navegador ou data do atendimento.
- O id gerado no navegador (`ESP-20260709-143055-ABC123`) é apenas a
  **chave de idempotência**, validada POR AÇÃO (prefixo+formato fechado —
  "x"/"PAC-0001"/prefixo cruzado são recusados) e armazenada em coluna
  técnica própria (`idempotency_key`, criada sob demanda). O ID do registro
  (`exame_id`/`consulta_id`) é SEMPRE UUID do servidor. Semântica (2ª
  rodada): mesma chave + mesmo payload (fingerprint) = **replay** sem
  regravar; payload diferente = **conflito 409**; tudo sob LockService.
  O painel preserva a chave em sessionStorage até o formulário concluir
  (refresh não duplica). O timestamp embutido é criação técnica, nunca a
  data do exame. Corrigir um evento já gravado será ação explícita (M14.3B).
- No financeiro, `id_atendimento` é chave de NEGÓCIO **obrigatória**
  (nenhum lançamento novo nasce órfão pela API); a atualização de pagamento
  é **patch por presença**: só colunas realmente presentes no payload são
  escritas — ausência nunca limpa célula, defaults só no INSERT, imutáveis
  (`id_lancamento`, `id_atendimento`, `criado_em`, `tipo_movimento`,
  `fonte`) nunca recalculados, fórmulas/colunas extras preservadas.
- IDs legados (`ESP-0001`, `ESM-*`, `PAC-0001`, `CON-0001`…) são válidos e
  **preservados para sempre**: na migração viram `id_legado` na própria
  linha + entrada no futuro mapa `_Mapa_IDs`
  (`id_canonico | id_legado | entidade | fonte_original | data_migracao |
  estado_reconciliacao`), append-only.
- Nenhum fluxo pode criar IDs divergentes para o mesmo atendimento.

## Datas

- **Armazenamento canônico**: ISO `AAAA-MM-DD` (novas colunas/migração);
  exibição no painel continua brasileira (`DD/MM/AAAA`).
- Campos separados por papel: `data_cadastro`, `data_entrada`,
  `data_exame`, `data_consulta`, `data_recebimento` (proposta no
  financeiro), `criado_em`, `atualizado_em`.
- **Precisão explícita** (`data_precisao`: `dia | mes | ano | desconhecida`):
  "06/2026" nunca vira um dia inventado — a âncora `2026-06-01` existe só
  para ordenação e **sempre** acompanhada de `data_precisao = mes`.
  Implementação: `parse_data_flex` em `soprolife_normalizacao.py`.
- Correção já feita pelo usuário: o exame de 03/07/2026 é 02/07/2026.
- **Enforcement (M14.3A)**: os writers do Apps Script validam valor +
  precisão juntos (`ctValidarDataComPrecisao`) — data inválida é erro
  explícito; mês/ano nunca vira dia factual; vazio nunca vira hoje;
  precisão incoerente com o valor é recusada.

## Enums

Vocabulário canônico em `enums-canonicos.json`, dividido em duas classes
(M14.3A):

- **`aliases`** — apenas equivalência lexical real (mesma palavra/flexão:
  "Exame realizado" → "Realizado", "cancelada" → "Cancelado"). É o único
  conjunto que uma migração autorizada pode aplicar em lote.
- **`decisao_manual`** — mudanças de significado, estágio, local,
  consentimento ou resultado ("Confirmado"→Realizado, "agendado"→Aguardando,
  "Não confirmado"→Não informado, "pastore"/"coworking" em local,
  "Convertido em paciente"): o valor apontado é só o candidato provável e
  **nunca** é aplicado sem decisão humana caso a caso.

Nenhuma ferramenta corrige dado real sozinha.

Divergências atuais documentadas:
- consentimento: "Não informado" (API) × "Não confirmado" (legado) —
  canônico: **Não informado**; a conversão do legado é decisão manual;
- status_relacionamento: "Ativo" (API) × "Em acompanhamento" (legado) —
  ambos canônicos, com semântica distinta;
- nome da aba de marketing: "Marketing Conteúdo" × "Marketing Conteudo"
  em pontos diferentes do código — conferir o nome real na planilha antes
  de padronizar.

## Pastore

Decisão: **não existe segunda base de pacientes.**

- A aba **Parceria Pastore - Atendimentos** é **staging/fila operacional**
  do acordo comercial (repasse, custos por exame). Alvo: cada atendimento
  gera `id_atendimento` + linha canônica em **CRM Espirometria**
  (`local_atendimento=Parceiro`, `parceiro=Pastore`, `unidade=Pastore
  Ipanema`) + lançamento em **Financeiro_Lancamentos**
  (`origem_preco=Parceria`).
- **Pastore - Custos** e **Pastore - Config** continuam abas próprias
  (custos e parâmetros são específicos da parceria).
- Pacientes Pastore entram no CRM Pacientes pelo mesmo fluxo de
  consolidação de qualquer exame.
- Os registros já existentes na aba de atendimentos **não são migrados
  nesta etapa** — `reconciliar-historico.py` os detecta como
  "fora do histórico central" e o plano dry-run propõe a integração.

## Valores financeiros: ausência permanece ausente (2ª rodada)

Nenhum default monetário é inventado pelo writer nem pelo cliente:
`valor_tabela` ausente NÃO vira 250; `origem_preco` ausente NÃO vira
"Tabela"; `valor_recebido` é obrigatório em Recebido/Parcial; o único zero
não digitado é a **regra formal** de Pendente/Cortesia/Cancelado
(registrada no contrato e coberta por teste comportamental); `desconto` só
é derivado quando `valor_tabela` foi enviado.

## Financeiro: cardinalidade, backfill e órfãos

Cardinalidade (contrato `registros-schemas.json`, M14.3A): um atendimento
pode ter **N movimentos** financeiros; no máximo **uma receita principal
ativa**; ajustes, complementos e estornos são **append-only** e referenciam
o movimento original (`papel_movimento` + `movimento_referencia`, colunas
v2). Duplicata é **conflito visível** para a reconciliação — nunca
resolvida em silêncio por "última linha vence". O ledger completo é
M14.3B; a M14.3A garante que o contrato não impõe 1:1.

- ~13 exames no CRM × 5 lançamentos: os exames históricos sem lançamento
  entram no plano como `backfill_financeiro` — o lançamento futuro usa
  **valores reais informados pelo usuário** (os exames antigos foram
  cobrados por valores variados); **nunca inventar valor**.
- Os 3 lançamentos de 09/07/2026 sem vínculo por ID são
  **órfãos a reconciliar** (`estado_reconciliacao=orfao_a_reconciliar`):
  vínculo assistido por data/valor com exame candidato, confirmação
  humana, **nunca exclusão automática**.
- A aba antiga **"Financeiro" foi removida e não pode ser recriada** por
  nenhum template/fluxo (marcada `nunca_recriar` no manifesto).

## Manual das Abas

Gerado a partir do manifesto:

```
core/contracts/abas-manifest.json  →  scripts/generate-manual-abas-gs.py
  →  apps-script/manual-das-abas.gs  →  (colar no Apps Script e executar
     atualizarManualDasAbasSoproLife)  →  aba "Manual das Abas"
```

Conteúdo: cadeia de dados em linguagem simples, matriz-resumo
(tipo/status/fonte/seção/PII/permissões/recomendação) e bloco detalhado
por aba com todos os campos exigidos pela M14.3. O gerador legado
(`limpar-leads-e-manual-abas.gs`) delega para o novo; sem o arquivo gerado
instalado, **falha com instrução clara** — o fallback legado (que citava a
aba Financeiro removida) foi excluído na M14.3A. Testes:
`scripts/test-manual-abas.py` e `scripts/test-guardas-estaticas.py`.

## Matriz de simplificação (resumo)

| Aba | Decisão |
|---|---|
| CRM Pacientes / CRM Espirometria / CRM Consultas / Financeiro_Lancamentos | **Nunca excluir** — núcleo do sistema |
| Leads, CRM Clinicas, CRM Contatos B2B, Follow-up WhatsApp, Tarefas, Marketing Conteúdo, Agenda Operacional | Manter visível |
| Base Prospecção B2B PCMSO | Manter como fonte (staging B2B) |
| Pastore - Atendimentos | Transformar em visão/staging (após integração ao histórico central) |
| Pastore - Custos | Manter como fonte |
| Pastore - Config | Manter como configuração (pode ocultar) |
| Resumo Dashboard | Ocultar para usuários comuns (derivada, regenerável) |
| Log Centro Comando, Log Auditoria, Log Conversões Leads | Manter como log (podem ficar ocultas) |
| Leads Convertidos | Ocultar para usuários comuns (arquivo) |
| Manual das Abas | Manter visível (regenerável) |
| `_Backup_Leads_Demo_*`, `_Backup_Leads_Operacional_*` | Ocultar; manter as 2 mais recentes; excluir as demais **somente por decisão humana** após conferir a aba Leads |
| Financeiro (antiga) | Removida — **nunca recriar** |

Nenhuma aba real foi ocultada, renomeada ou excluída nesta etapa.

## Conversão de leads e fluxos de manutenção — BLOQUEADOS (2ª rodada)

- `converter-lead-em-paciente.gs`: BLOQUEADO — deduplicava pessoa por
  telefone, aceitava data impossível por regex, gravava sem contrato e não
  propagava `paciente_id`. Até a conversão canônica (M14.3B), o atendimento
  é registrado pela tela do painel e a etapa do lead é atualizada.
- `organizar-leads-operacionais.gs`: BLOQUEADO — limpava e reconstruía a
  aba Leads (migração destrutiva → backup validado + manifesto + aprovação,
  M14.3B).
- `limpar-leads-e-manual-abas.gs`: agora ADITIVO — sem remoção de linhas.
- `dedupLeadsConvertidos`: só relatório; exclusão bloqueada.
- Guarda global: `.clear()/.clearContents()/deleteRow(` proibidos em todo
  fluxo operacional, com allowlist explícita apenas para saídas derivadas
  regeneráveis (aba Manual das Abas, Resumo Dashboard).

## Contratos executáveis e writers fail-closed (M14.3A)

`apps-script/contratos-canonicos.gs` (espelho commitável:
`core/contracts/registros-schemas.json`; teste: `scripts/test-contratos.js`)
é executado pelos writers reais:

- cabeçalho sem coluna obrigatória → **erro** de schema incompatível;
- **TODO campo presente no payload** (v1 ou v2 — inclusive telefone) sem
  coluna correspondente → **erro** com a lista de colunas ausentes (nada é
  descartado em silêncio; presença ≠ ausência: campo enviado vazio conta
  como presença explícita);
- validação COMPLETA antes de qualquer mutação (inclusive antes da criação
  das colunas técnicas);
- coluna desconhecida na aba → **preservada**, nunca escrita;
- atualização = **patch seletivo**; ausência de campo nunca apaga célula;
- resposta informa `campos_persistidos`, `campos_ignorados` e
  `contrato_versao`.

Por isso o painel **ainda não envia** os campos canônicos novos
(local_atendimento etc.) no registro operacional: as colunas v2 serão
criadas na M14.3B e só então o formulário passa a enviá-los.

## Privacidade das saídas da ferramenta (2ª rodada)

Nenhum ID sai em claro em relatório/plano — nem os reconhecidos
(ESP/ESM/PAC/CON/FIN podem carregar nome embutido, ex.: `ESM-<nome>`):
toda saída detalhada usa **categoria do formato + hash efêmero**. O scanner
final também bloqueia padrões de ID com texto embutido, além de
CPF/telefone/e-mail. `paciente_id` explícito que não existe no cadastro é
`pending`/`orphan_link` (nunca `unmatchable`); compatibilidade Pastore por
nome/telefone gera **candidato** anotado, nunca "já integrado".

## Ferramenta de reconciliação

```bash
# auditoria com dados sintéticos (testes)
python3 painel-soprolife/scripts/reconciliar-historico.py --fixtures DIR --audit

# auditoria real (ADC somente leitura — rodar quando autorizado)
python3 painel-soprolife/scripts/reconciliar-historico.py --from-adc --audit
python3 painel-soprolife/scripts/reconciliar-historico.py --from-adc --dry-run

# plano detalhado (PRIVADO — gravar só em data-private/, sai com chmod 600)
python3 painel-soprolife/scripts/reconciliar-historico.py --from-adc \
    --plan painel-soprolife/data-private/reconciliacao-plano.local.json

# relatório commitável (sem PII: só contagens, IDs técnicos e hashes)
python3 painel-soprolife/scripts/reconciliar-historico.py --from-adc \
    --export-safe-report relatorio-reconciliacao.txt
```

Detecta: cobertura de pacientes em quatro estados (linked / pending /
ambiguous / unmatchable — nome nunca vincula, telefone é só candidato,
telefone compartilhado é ambíguo), duplicidades, exames sem
lançamento, lançamentos órfãos/duplicados, IDs irregulares/ausentes,
datas incompletas/inválidas, enums despadronizados, Pastore fora do
histórico, campos obrigatórios ausentes, valores financeiros ausentes e
divergências CRM×financeiro. **Não existe modo de aplicação** — toda ação
do plano nasce `aplicar: false`.

## Plano futuro (fora desta etapa, cada passo com autorização)

1. Rodar `--from-adc --audit` e revisar com o usuário os achados reais.
2. Decidir caso a caso: vínculos prováveis, duplicidades e órfãos.
3. Criar as colunas propostas (`paciente_id`, `id_legado`,
   `data_*_precisao`, `local_atendimento`, `parceiro`, `unidade`,
   `modalidade`, `data_recebimento`) nas abas — sem tocar nas existentes.
4. Migração controlada: preencher colunas novas + `_Mapa_IDs`, com backup
   das abas antes e dry-run comparado.
5. Backfill financeiro com valores confirmados pelo usuário.
6. Integrar atendimentos Pastore ao histórico central.
7. Só então avaliar ocultar/arquivar abas conforme a matriz.
