# M26.16 — Laudo superado por corretiva não pode parecer elegível para assinatura

16/09/2026. Worktree `claude-m26-16-superados-fila-assinatura`, base `painel-soprolife-v01` (`16f016d`,
o mesmo commit já em produção ao final da M26.15). Entregue: commit `0c23a95`, integrado por
fast-forward, deploy na VPS confirmado (`git status` limpo em `0c23a95` em oficial e produção),
`soprolife-m15-api.service` reiniciado (backend mudou nesta etapa), health `200`/`200`. Sem migration.

## Contexto real

Depois da M26.15 (painel passou a mostrar o PDF técnico vigente de LAU-000038), o usuário reportou que
o painel ainda mostrava LAU-000035 e LAU-000036 (os dois documentos SUPERADOS da cadeia de correção de
Claudia) como se ainda precisassem de assinatura — risco real de confusão, estatística e pagamento
médico incorretos.

## Causa

Corrigir um laudo (M26.12/M26.14) sempre cria um `ReportDocument` NOVO e **nunca** muda o `status` nem
a atribuição ativa do predecessor — por desenho, para preservar histórico intacto (o predecessor
continua em `liberado`, como sempre esteve). Duas consultas não sabiam disso:

1. **`_aguardando_assinatura_externa`** (risco real de segurança operacional): não excluía documentos
   já superados por uma corretiva mais nova. LAU-000036 aparecia selecionável em "Assinatura externa —
   aguardando assinatura qualificada", pronto para ser baixado, assinado com certificado real e
   devolvido — mesmo já superado pela LAU-000038. Esta é a MESMA função usada por `pendentes`,
   `baixar` e `enviar` (upload do assinado de volta), então uma única trava fecha os três pontos.
2. **`list_my_report_queue`** ("Meus laudos" da médica): nunca calculava `has_corrective_successor`/
   `is_delivered` (só o "Acompanhamento operacional" administrativo, da M26.13, calculava). Por isso
   LAU-000035 e LAU-000036 apareciam como "Concluído — aguardando assinatura qualificada", sem
   indicação nenhuma de que já haviam sido corrigidos.

Auditoria adicional encontrou um TERCEIRO problema, latente, na mesma área: `physician_production_
summary` (repasse médico, M26.13) resolvia o documento "vigente" de cada exame andando só **um salto**
a partir da raiz. Numa cadeia de 1 salto isso sempre bateu; no caso real de Claudia (raiz → corretiva de
conteúdo → corretiva do PDF técnico, 2 saltos) a produção lia assinatura/entrega/conclusão do documento
do MEIO (já superado), não do atual — o que faria a produção mostrar "assinado"/"entregue" errado (ou
zerado) mesmo depois do laudo real estar pronto.

## O que foi corrigido

- `_aguardando_assinatura_externa`: exclui qualquer documento com corretiva sucessora (mesmo padrão de
  subquery `EXISTS` já usado no Acompanhamento operacional, M26.13).
- `list_my_report_queue`: agora calcula e envia `has_corrective_successor`/`is_delivered` por linha,
  igual ao Acompanhamento operacional.
- `physician_production_summary`: a resolução do documento vigente agora caminha a cadeia de correção
  inteira (BFS até o documento terminal), não só um salto.
- Frontend (`report-workflow.js`, `renderQueue`): em "Meus laudos", um documento com
  `has_corrective_successor`/`is_delivered` agora mostra a etiqueta "Superado por corretiva" /
  "Corrigido e entregue" / "Entregue ao paciente" — mesmo vocabulário já usado no histórico
  administrativo (M26.13), em vez de deixar o chip de status antigo sugerir que falta ação.

Nenhum dado real foi apagado ou alterado — só a leitura/exibição do estado já existente.

## Testes

- Novo arquivo backend (`test_m26_16_superados_nao_reaparecem.py`, 6 testes, dirigidos pela API real):
  documento superado vem marcado em "Meus laudos"; numa cadeia de 2 saltos, só o documento terminal fica
  sem a marcação; documento superado some da fila de assinatura externa; pedido direto do id superado ao
  endpoint de download é recusado (`lote_vazio`) mesmo pedindo explicitamente; numa cadeia de 2 saltos só
  o terminal fica elegível para assinatura; produção/repasse lê assinatura do documento terminal, não do
  do meio.
- Novo teste Playwright (`test-m26-16-superados-meus-laudos.js`, 3 cenários): raiz e corretiva
  intermediária mostram a etiqueta "Superado por corretiva"; o documento terminal (vigente) não mostra.
- **Confirmação de que os testes pegam os três bugs**: cada correção foi revertida temporariamente
  (exclusão da fila de assinatura externa, resolução da cadeia de repasse, etiqueta no frontend) e os
  testes correspondentes falharam exatamente como esperado; código restaurado, testes voltam a passar.
- Suíte completa do backend: **1740 passaram** (mais 1 do que a contagem da M26.14 — este novo arquivo
  soma 6). Os 13 casos que aparecem como falha rodando localmente (`test_live_multisheet_reader.py`, 12
  casos; e uma janela de 2000 caracteres estourada em `test_m26_8_...`) são, respectivamente,
  pré-existentes/ambientais (dependem de credencial do Google Sheets ausente neste ambiente — confirmado
  reproduzindo os mesmos 12 no commit-base, antes de qualquer mudança desta etapa) e foram corrigidos
  nesta própria etapa (comentário explicativo excedia a janela fixa do teste — encurtado, sem relaxar a
  asserção).
- Suítes existentes sem regressão: M24C estrutural, M26.12 (16 cenários), M26.13 produção médica (6
  cenários), M26.15 (1 cenário) — todas limpas depois do bump de versão.
- `quality-gate-safe.sh`: as duas únicas falhas (M21, M25.26) são as mesmas já confirmadas
  pré-existentes/ambientais, fora do escopo desta missão.

Bump do carimbo de cache-busting de `report-workflow.css/js` (`2026091603 → 2026091604`), com o teste de
regressão correspondente atualizado junto.

## Verificação em produção (dados reais, somente leitura)

Depois do deploy, `GET /laudos/meus` (token real da Dra. Ana) confirma:

- `LAU-000038`: `status=atribuido`, `has_corrective_successor=False` — o vigente, sem marcação.
- `LAU-000036`: `status=liberado`, `has_corrective_successor=True` — agora marcado como superado.
- `LAU-000035`: `status=liberado`, `has_corrective_successor=True` — agora marcado como superado.

`GET /laudos/assinatura-externa/pendentes` (mesma médica): 1 item pendente (`LAU-000037`, de outro
exame) — nem `LAU-000035` nem `LAU-000036` aparecem mais na lista.
