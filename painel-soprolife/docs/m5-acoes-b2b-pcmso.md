# M5 — Próximas Ações Comerciais B2B/PCMSO

## Objetivo

Painel de priorização comercial no Painel Geral: quem precisa de
follow-up, o que está parado, o que está quente, quem converteu, quem se
perdeu — com resumo por etapa e ações recomendadas, **sem nenhuma PII**.

## Fontes de dados (todas já carregadas e gateadas pelo app — zero fetch novo)

| Fonte (estado do app) | Uso |
|---|---|
| `leadsSummary.leads` (real) / `leads.json` (demo) | leads B2B por `servico_interesse`/`tipo_lead`; etapa, atraso, tem_proxima_acao — só IDs, sem nome de pessoa |
| `crm` (crm-clinicas, normalizado) | etapa canônica por clínica, prioridade, tem_proxima_acao; nome INSTITUCIONAL permitido (summary seguro validado pelo check-access) |
| `crmContatosB2B` | contagem de contatos sem próximo passo (agregada) |
| `followupClinicasSummary.clinicas` | contagens: atrasados, hoje, parceirosAtivos, perdidasArquivadas |

Núcleo puro em `js/b2b-actions.js`: `buildB2BStats(payloads)` (6 contadores +
perdidas) e `buildB2BActions(payloads, agora)` (ações ordenadas
alta→média→baixa, máx. 10, data `agora` injetável para testes).

**Dedup (regra do funil):** lead convertido conta pela clínica — nunca
duas vezes; convertidos/perdidos não geram ação de prospecção.

## Regras de segurança

- Shape fixo por ação (`id, prioridade, origem, titulo, motivo,
  proximoPasso`) — campos extras das fontes são ignorados.
- Todo texto passa pelo sanitizador do M4 (`acoesTextoSeguro`, reusado):
  CPF/telefone/e-mail/tokens/URLs secretas viram mensagem genérica.
- Nome de clínica só aparece porque JÁ vem do summary seguro
  (institucional, validado); nomes de pessoa nunca existem nas fontes.
- Render 100% via `escapeHtml`; estado vazio com os textos padrão
  ("Nenhuma ação comercial pendente." / "Os summaries atuais não
  indicam follow-up B2B em aberto."); painel oculto se nenhuma fonte
  B2B carregou.

## Como testar

```bash
node painel-soprolife/scripts/test-b2b-actions.js    # 27 casos
node --check painel-soprolife/js/b2b-actions.js
python3 -m http.server 8765   # → /painel-soprolife/ → fim do Painel Geral
```

## Limitações

- Ações são heurísticas por etapa/contagem — não leem o texto real da
  próxima ação (que ficou fora dos summaries por segurança, M2).
- Lista limitada a 10 (as de maior prioridade); sem persistência de
  "feito".
- `precisamFollowup` mostra "—" quando o summary de follow-up não
  existe (sem inventar).

## Próximos passos

- Contador B2B no hub CRM apontando para este painel.
- Marcar ação como tratada (junto com a v2 do M4).
- Quando o funil M4-planejado existir, unificar as etapas canônicas em
  módulo compartilhado.

## O que revisar antes de deploy

Diff completo + textos das ações (tom comercial) + a decisão de casa
(fim do Painel Geral, abaixo de "Próximas ações" de tarefas — títulos
distintos e tooltip para não confundir) + confirmar no navegador com os
dados reais da VPS após o primeiro ciclo.
