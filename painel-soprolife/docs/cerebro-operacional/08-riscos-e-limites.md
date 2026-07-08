# Riscos e Limites do Cérebro Operacional

## Riscos e mitigações

1. **Falsa autoridade**: números de projeção v0 usam premissas demo —
   risco de decisão baseada em placeholder. Mitigação: premissa SEMPRE
   visível + status "esqueleto/parcial/ativa" + rótulo "Demo seguro"
   até o M11/M12.
2. **Vazamento por agregação**: mesmo agregados podem identificar
   alguém em amostras pequenas (ex.: "1 paciente de bairro X").
   Mitigação: o cérebro só usa contagens já publicadas nos summaries
   (que passaram pelo M2) e nunca cruza para granularidade menor.
3. **Deriva de contrato**: camadas futuras mudando shapes quebrariam a
   UI/testes. Mitigação: contratos no doc 02 + testes de shape estável.
4. **Automação precoce**: tentação de fazer a Camada 7 enviar coisas.
   Regra dura: gerar texto ≠ enviar; envio é humano; registro é
   auditado. Nenhuma exceção sem revisão de arquitetura.
5. **Fadiga de prioridade**: tudo "alta" = nada alta. Mitigação: pesos
   somam 100, faixas fixas, fila limitada a 7, top 3 destacado.

## Limites declarados (v0)

- Não lê dados privados nem clínicos individuais — nunca lerá.
- Não usa rede/IA externa; é determinístico e roda no navegador.
- Não substitui julgamento: recomenda, não decide.
- Projeções v0 não servem para compromisso financeiro.
- Briefing v0 não compara com ontem (chega no M9).

## Contrato de segurança herdado

Camada 0 = M2 (pii_guard nos geradores) + sanitizador M4 no cliente +
check-access como 2ª linha + shape fixo em toda saída.
