# M26.17 — Liberdade de texto no laudo e "Meus laudos" sem superados

16/09/2026. Worktree `claude-m26-17-liberdade-texto-laudo`, base `painel-soprolife-v01` (`b15011b`, o
mesmo commit já em produção ao final da M26.16). Entregue: commit `9a290b4`, integrado por fast-forward,
deploy na VPS confirmado (`git status` limpo em `9a290b4` em oficial e produção),
`soprolife-m15-api.service` reiniciado (backend mudou nesta etapa), health `200`/`200`. Sem migration.

## Contexto real

A Dra. Ana mandou prints: escreveu a conclusão completa do laudo na caixa "Texto final do laudo" e, ao
tentar concluir, recebeu "A conclusão personalizada exige texto escrito pela médica.", mesmo com o texto
visivelmente preenchido — um bloqueio que impedia salvar o laudo. Separadamente, o usuário reportou que
os exames antigos da Claudia (LAU-000035/036, superados pela corretiva LAU-000038 na M26.14/15) ainda
apareciam no painel como "laudado e aguardando assinatura".

## Problema 1 — bloqueio no texto do laudo

**Causa:** a tela tem DUAS caixas de texto livre quando a conclusão é "Personalizado": uma pequena
("Conclusão personalizada", campo `conclusion_custom_text`) e a grande, sempre visível, que é o texto
que de fato vai ser assinado ("Texto final do laudo", campo `final_text`). O servidor validava
`conclusion_custom_text` incondicionalmente quando a conclusão é "Personalizado" — mesmo quando a médica
escreveu tudo na caixa grande e nunca tocou na pequena. `final_text` já tem sua própria validação
(3–6000 caracteres) e é o texto realmente impresso no PDF; a caixa pequena só alimenta um campo de
auditoria estrutural (`conclusion_text_snapshot`).

**Correção:** quando a caixa pequena está vazia, `resolve_conclusion_text` agora usa o `final_text` como
origem do resumo de auditoria (truncado ao limite do campo), em vez de bloquear. A médica pode escrever
em qualquer uma das duas caixas. Se as duas estiverem vazias, continua bloqueado — não há texto nenhum
para assinar, o que é o mínimo clínico razoável, não o bloqueio aleatório reportado.

## Problema 2 — exames superados continuavam na fila ativa

**Causa:** a M26.16 tinha adicionado uma ETIQUETA ("Superado por corretiva") aos documentos superados em
"Meus laudos", mas não os removeu da lista ativa — só o Acompanhamento operacional (admin) e a fila de
assinatura externa tinham a exclusão de fato. LAU-000035/036 continuavam com o chip antigo "Concluído —
aguardando assinatura qualificada", como se ainda precisassem de ação.

**Correção:** `GET /laudos/meus` agora exclui por padrão documentos com corretiva sucessora ou já
entregues — mesmo par `incluir_superados`/`somente_superados` já usado no Acompanhamento operacional
(M26.13). O frontend passou a buscar a fila ativa e o histórico em duas chamadas separadas, e "Meus
laudos" ganhou uma seção recolhida "Históricos", espelhando a mesma seção já existente no painel
administrativo — nada foi apagado, só saiu da lista de trabalho.

## Testes

- Novo arquivo backend (`test_m26_17_liberdade_texto_laudo.py`, 5 testes): texto só na caixa grande não
  bloqueia; as duas caixas vazias continuam recusadas (comportamento clínico correto); caixa pequena
  sozinha continua funcionando como antes (regressão); limite de tamanho da caixa pequena, quando
  realmente usada, continua valendo; laudo superado some de "Meus laudos" por padrão e aparece com
  `incluir_superados`/`somente_superados`.
- Novo teste Playwright (`test-m26-17-historico-meus-laudos.js`, 3 cenários): a tela busca fila ativa e
  histórico em chamadas separadas; a fila ativa mostra só o documento vigente; a seção "Históricos"
  existe, nasce recolhida, e lista os 2 superados com a etiqueta certa.
- **Confirmação de que os testes pegam os bugs**: cada correção (reordenação do endpoint de prévia,
  exclusão na fila de "Meus laudos", seção de histórico no frontend) foi revertida temporariamente e o
  teste correspondente falhou exatamente como esperado; código restaurado, testes voltam a passar.
- `test_m26_16_superados_nao_reaparecem.py` ajustado: 2 dos seus testes assumiam que `/laudos/meus`
  sempre devolvia TODOS os documentos (comportamento pré-M26.17); passaram a pedir
  `incluir_superados=true`, já que o objetivo deles é conferir o VALOR da marcação, não o filtro (que
  ganhou teste próprio nesta etapa).
- Suítes existentes sem regressão: M24C estrutural, M26.12 (16 cenários), M26.13 produção médica (6
  cenários), M26.15 (1 cenário), M26.16 (3 cenários) — todas limpas depois do bump de versão.
- `quality-gate-safe.sh`: as duas únicas falhas (M21, M25.26) são as mesmas já confirmadas
  pré-existentes/ambientais, fora do escopo desta missão.

Bump do carimbo de cache-busting de `report-workflow.css/js` (`2026091604 → 2026091605`), com o teste de
regressão correspondente atualizado junto.
