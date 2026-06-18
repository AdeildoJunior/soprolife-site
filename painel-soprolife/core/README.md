# core/ — Contratos e Conectores do SoproLife OS Local Core

Esta pasta contém:

- **contracts/** — esquemas JSON que definem quais campos o painel aceita de cada fonte.
- **connectors/** — documentação de como integrar cada sistema externo sem vazar dados.

## O que esta pasta NÃO contém

- Backend real;
- credenciais;
- tokens;
- URLs privadas;
- dados reais de pacientes;
- código de produção executável.

## Como usar

Antes de implementar qualquer conector, leia o contrato da área correspondente em `contracts/`.
O contrato define o conjunto máximo de campos que o painel aceita — o conector deve produzir exatamente esse formato e nada além.

Ver arquitetura completa: `../SOPROLIFE_OS_LOCAL_CORE.md`
