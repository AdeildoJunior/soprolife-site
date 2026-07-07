---
name: soprolife-review-pack
description: Como gerar o pacote de revisão de uma etapa para o GPT arquiteto em ~/Documents/SoproLife/_REVISOES_GPT — status, log, diffs e checks, sem nunca incluir secrets ou dados privados.
---

# soprolife-review-pack

## Quando usar
Ao final de toda etapa executada (ver soprolife-etapa-segura), quando o
usuário pedir o "pacote de revisão" para levar ao GPT arquiteto.

## Pasta padrão
`~/Documents/SoproLife/_REVISOES_GPT/<data>-<etapa>/`
(ex.: `2026-07-07-m2-etapa4/`). Criar a pasta se não existir.

## Conteúdo do pacote (um arquivo `revisao.md` ou arquivos separados)
1. Cabeçalho: branch, checkpoint/tag atual, commit base, etapa executada.
2. `git status --short` (antes e depois da sessão, se disponível).
3. `git log --oneline --decorate -5`.
4. `git diff --stat` e `git diff --check`.
5. **Diff completo** dos arquivos tracked alterados.
6. Arquivos novos (untracked): listar caminho + conteúdo integral
   (são código novo; sem eles o GPT não revisa nada).
7. Saída dos checks executados (py_compile, self-test, check-access com
   saída completa, node --check, testes visuais com caminho do screenshot).
8. Riscos e decisões tomadas pela IA executora, em lista.

## Proibições absolutas no pacote
- Nenhum token, URL de Apps Script, spreadsheet ID, credencial ou `.env`.
- Nenhum conteúdo de `data-private/` ou de `*.local.json` sensível —
  se um check citar um arquivo desses, incluir só o NOME e o veredito.
- Nenhum telefone, CPF, nome de paciente, laudo ou observação privada.
- Nenhum IP real de VPS, usuário SSH ou porta — placeholders
  (`<TAILSCALE_IP>` etc.).

## Verificação final antes de entregar
```
grep -riE "AIza|ya29|script\.google|/spreadsheets/d/|apiToken" <pasta-do-pacote> && echo "VAZOU — corrigir" || echo "pacote limpo"
```
Se vazar, corrigir o pacote ANTES de o usuário abrir/enviar.
