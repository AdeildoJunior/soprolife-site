# SoproLife AI Orchestrator

Use esta skill para decidir qual IA/modelo usar.

## Papéis

GPT:
- coordenação;
- arquitetura;
- revisão;
- priorização;
- integração entre IAs;
- transformar estratégia em tarefas executáveis.

Claude Fable:
- estratégia;
- arquitetura de produto;
- roadmap;
- análise de risco;
- planos de 7/30/90 dias;
- revisão crítica de decisões.

Não usar Fable para publicar, deployar ou mexer em produção sem revisão. Fable pensa grande, mas não deve ser solto como executor operacional.

Claude Code Sonnet:
- implementar código;
- editar Apps Script;
- editar JS/CSS/HTML;
- scripts Python;
- rodar checks;
- mostrar diffs;
- executar tarefas pequenas e controladas.

## Regra de execução

Toda tarefa técnica deve terminar com:
- git status;
- diff stat;
- checks;
- riscos;
- parada para aprovação.

## Ordem ideal

Fable pensa.
GPT organiza.
Sonnet executa.
Usuário aprova.
