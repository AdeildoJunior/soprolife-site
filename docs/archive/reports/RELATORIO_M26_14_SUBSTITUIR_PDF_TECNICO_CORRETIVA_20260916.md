# M26.14 — Substituir PDF técnico de uma corretiva ainda não iniciada

16/09/2026. Worktree `claude-m26-14-substituir-pdf-tecnico`, base `painel-soprolife-v01` (`4dd6527`).
Entregue: commit `9d7890b`, integrado por fast-forward, deploy na VPS confirmado (`git status` limpo em
`9d7890b` em oficial e produção), `soprolife-m15-api.service` reiniciado (único que carrega o código
alterado), health `200`/`200`. Sem migration.

## Contexto real

Um laudo da Dra. Ana foi devolvido para correção (M26.12, "Retornar para laudadora") por conteúdo
clínico incorreto. Ao reabrir, foi descoberto um SEGUNDO problema, independente: o PDF técnico do
aparelho de espirometria estava incompleto (faltava a anotação do motivo do exame). O usuário já
gerou o PDF corrigido no aplicativo do espirômetro.

## Causa

O mecanismo de corretiva (M25.2/M26.12) sempre **copia** o PDF técnico do documento predecessor —
não existia nenhum caminho, em nenhum endpoint, para trocar esse arquivo. As duas alternativas com o
que já existia eram ruins: reabrir outra corretiva herdaria o mesmo PDF incompleto; subir um PDF novo
pelo fluxo de upload inicial criaria um documento solto, sem vínculo de correção e sem revogar
automaticamente o acesso do paciente ao documento anterior.

## O que foi construído

Novo endpoint `POST /{document_id}/pdf-tecnico-original` (multipart) — troca a versão `kind=original`
de um documento, com três travas deliberadas:

- só funciona em documento que **é** uma corretiva (`corrects_document_id` preenchido) — nunca no
  documento raiz;
- só enquanto o documento está `atribuido` (a médica ainda não começou a elaborar — nem uma prévia foi
  gerada);
- a versão antiga do PDF **nunca é apagada** — vira histórico, só deixa de ser a corrente
  (`current_version_id` aponta para a nova).

RBAC: `ROLE_OPERACIONAL` (mesmo papel do upload inicial). Auditoria: `laudo_pdf_tecnico_substituido`,
com o id da versão anterior e da nova.

Frontend: botão "Substituir PDF técnico desta corretiva" no Acompanhamento operacional, só aparece
quando o documento selecionado satisfaz as duas condições acima.

## Testes

- Backend (`test_m26_14_substituir_pdf_tecnico.py`, 9 testes, dirigidos pela API real): substituição
  bem-sucedida preserva a versão antiga e troca a corrente; médica conclui normalmente depois; recusa
  em documento original (não corretiva); recusa depois que a médica já começou; recusa depois que a
  corretiva já foi liberada; PDF inválido recusado; documento inexistente 404; médica sozinha e
  conta de leitura não conseguem (RBAC).
- Suíte completa do backend: **1780 passaram, 0 falhas relacionadas**.
- Frontend (`test-m26-14-substituir-pdf-tecnico.js`, Playwright, 4 cenários): botão só aparece nas
  condições certas; envio manda `multipart/form-data` para a rota certa; erro do servidor aparece
  perto da ação (foco automático) sem travar a tela nem esconder o formulário; larguras
  1440/768/390 sem overflow.
- Suítes existentes (M24C estrutural, M26.12 Playwright de 16 cenários) sem regressão.
- `quality-gate-safe.sh`: as duas únicas falhas (M21, M25.26) são as mesmas já confirmadas
  pré-existentes/ambientais, fora do escopo desta missão.

Bump do carimbo de cache-busting de `report-workflow.css/js` (`2026091601 → 2026091602`), com o teste
de regressão correspondente atualizado junto.
