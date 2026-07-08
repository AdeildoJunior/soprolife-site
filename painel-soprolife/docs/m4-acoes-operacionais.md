# M4 — Central de Ações Operacionais

## Objetivo

Transformar os alertas da Saúde Operacional (M3) em uma área prática de
ação: para cada alerta, o painel mostra **o que fazer agora** (ação
recomendada curta e não técnica), nível, origem, status e o próximo
passo detalhado atrás de um botão "Ver próximo passo".

## Fonte dos dados

Nenhum fetch novo: consome `state.saudeOperacional`, o MESMO estado da
M3 (real `saude-operacional-summary.local.json` quando existe, senão o
demo `saude-operacional.json`). A transformação é feita pela função PURA
`buildOperationalActions(payload)` em `js/operational-actions.js`
(carregada antes do `app.js`; exporta via `module.exports` para os
testes em Node).

A ação recomendada vem de um mapa por família de alerta (prefixo do id
gerado pelo M3): pipeline → "Rodar a atualização local ou verificar o
timer da VPS."; check-access → "Interromper qualquer deploy e revisar a
segurança."; JSON/PII/auditoria/fontes → ações próprias; desconhecido →
"Revisar o alerta na Saúde Operacional."

## Regras de segurança

- Só os campos conhecidos do alerta são lidos (`id`, `nivel`, `titulo`,
  `proximo_passo`) — extras são ignorados e o shape da ação é fixo.
- Textos são normalizados e truncados (220 chars); nível fora da lista
  vira `desconhecido`.
- Texto com cara de PII/segredo (CPF, telefone, e-mail, `AIza`, `ya29`,
  `AKfycb`, `Bearer`, `/spreadsheets/d/`, `script.google`,
  `private_key`/`client_secret`/`refresh_token`) é substituído por
  mensagem genérica — nunca aparece bruto.
- O render usa o `escapeHtml` existente em TODO texto interpolado.
- Estado vazio: "Nenhuma ação operacional pendente." + subtítulo.
- Payload nulo/inválido → painel oculto/lista vazia, sem quebrar.

## Como testar

```bash
node painel-soprolife/scripts/test-operational-actions.js   # 22 casos
node --check painel-soprolife/js/operational-actions.js
node --check painel-soprolife/js/app.js
python3 -m http.server 8765   # → /painel-soprolife/ → Automações
```

Cobertura dos testes: sem alertas → `[]`; níveis atenção/crítico; ação
recomendada por família; `proximo_passo` ausente → fallback; campos
extras ignorados (shape fixo + valor não vaza); 6 variantes de texto
suspeito → mensagem genérica; payload null/string/alertas-não-array/
alerta-não-objeto → seguro; nível inválido → desconhecido; truncamento.

## Limitações

- "Status: pendente" é fixo — não há persistência de "feito" (candidato
  a v2, exigiria estado local ou escrita auditada).
- A ação recomendada é estática por família de alerta; alertas de
  famílias novas do M3 caem no texto genérico até o mapa ser atualizado.
- O botão não executa nada — é orientação (por desenho: o painel não
  dispara comandos).

## Próximos passos

- Marcar ação como "feita" com persistência leve (localStorage) ou via
  trilha auditada.
- Contador de ações pendentes no banner crítico do overview.
- Quando o M3 ganhar novas famílias de alerta, estender
  `ACOES_RECOMENDADAS` junto (mesmo PR).
