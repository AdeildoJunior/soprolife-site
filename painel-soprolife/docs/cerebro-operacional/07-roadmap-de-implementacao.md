# Roadmap de Implementação — M9 a M15

> Uma etapa por sessão (soprolife-etapa-segura); quality gate verde
> antes de cada commit; deploy só com o fluxo M7.

| Etapa | Entrega | Troca placeholder → real |
|---|---|---|
| **M9 ✅** | Briefing Diário real (ENTREGUE) | contrato completo com resumo executivo, status previsível, riscos e por área — ao vivo dos summaries; snapshot de "ontem" ficou para o M10 |
| **M10** | "Hoje eu tenho que fazer o quê?" | fila de decisão com feito/pendente (persistência leve), snapshot diário para "o que mudou" real, eixos de prioridade emitidos pelos geradores M4/M5 |
| **M11** | Projeções comerciais B2B | taxa de conversão real por etapa (histórico), funil consolidado |
| **M12** | Projeções financeiras | ponto de equilíbrio, cenários, custos vs receita com premissas revisadas com os sócios |
| **M13** | Motor de campanhas/follow-ups | GERA textos para copiar (WhatsApp manual) — nenhum envio automático; usa Command Center auditado para registrar |
| **M14** | Assistente de atualização de CRM | sugestões de correção (etapa fora do padrão, sem próximo passo) aplicáveis com 1 clique VIA Apps Script auditado |
| **M15** | Painel executivo semanal | resumo da semana exportável (texto/HTML) para os sócios |

## Critérios transversais

- Cada M troca UMA camada do esqueleto sem mudar os contratos (doc 02).
- Automação (Camada 7) nunca envia nada sozinha: gera texto/sugestão,
  humano aprova, Command Center registra (auditoria M1).
- Toda nova saída visível passa por sanitizador + check-access.
