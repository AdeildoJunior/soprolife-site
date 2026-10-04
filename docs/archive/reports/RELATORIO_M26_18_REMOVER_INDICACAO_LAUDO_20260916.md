# M26.18 — Remover indicação clínica do laudo impresso

16/09/2026. Worktree `claude-m26-18-remover-indicacao-laudo`, base `painel-soprolife-v01` (`0f1c8b9`, o
mesmo commit já em produção ao final da M26.17). Entregue: commit `50f3853`, integrado por fast-forward,
deploy na VPS confirmado (`git status` limpo em `50f3853` em oficial e produção),
`soprolife-m15-api.service` reiniciado, health `200`/`200`. Sem migration.

## Contexto real

O usuário pediu inicialmente automação: ler a indicação clínica automaticamente do PDF técnico (KoKo
ou MIR) e preencher o laudo. Na investigação, ficou provado que o PDF do aparelho KoKo desenha o texto
fora da ordem de leitura visual — a mesma posição no arquivo que parece ser "o texto ao lado do rótulo
Indicação" na verdade é o nome do solicitante, com o valor real da indicação aparecendo bem antes, no
meio de outro bloco. Duas bibliotecas de extração de PDF (`pypdf`, `pymupdf`) confirmaram
independentemente esse comportamento — não é um bug de biblioteca, é como o software do KoKo grava o
conteúdo. Extrair "o texto logo depois do rótulo" pegaria o nome do solicitante, não a indicação —
risco real de colocar informação clínica errada num documento legal.

Diante disso, o usuário retificou o pedido: **remover a indicação do laudo**, já que o PDF técnico do
equipamento (que já contém a indicação) é sempre entregue junto com o laudo, como documento separado.

## O que foi feito

`build_native_content` (`report_native_pdf.py`) parou de incluir o campo "Indicação" na tabela de
identificação do laudo. A tabela reflui sozinha — a altura já era calculada por
`max(len(left), len(right))` entre as duas colunas — sem nenhuma quebra de layout com a coluna do exame
ficando uma linha mais curta que a do paciente.

O campo `exam.clinical_indication` continua existindo na resposta de `GET /{document_id}` (consumida
pela tela interna "Exame" que a médica vê ao lado do laudo, dentro do Centro de Comando) — esse painel
de trabalho não foi tocado, só o documento impresso/PDF entregue ao paciente.

## Testes

- Suítes existentes que tocam a tabela de identificação/campos do laudo (187 casos, entre
  `test_m25_2_native_report.py`, `test_m25_15_operacao_real.py`, `test_m25_18_assinatura_externa.py`,
  `test_m25_21_selo_pdf_pre_assinatura.py`, `test_m25_21_ui_medica_premium.py`,
  `test_m26_1_selo_assinatura_digital.py`) — todas passaram sem alteração; nenhuma delas afirmava o
  texto "Indicação" no PDF renderizado, só o campo estrutural da API (que não mudou).
- PDF sintético gerado e lido visualmente antes do deploy — tabela com 3 linhas de cada lado, sem
  sobreposição nem espaço vazio malformado.
- `quality-gate-safe.sh`: as duas únicas falhas (M21, M25.26) são as mesmas já confirmadas
  pré-existentes/ambientais, fora do escopo desta missão.
- Suíte completa do backend: **1752 passaram**, as 12 falhas restantes são as mesmas pré-existentes do
  Google Sheets (confirmadas ambientais em missões anteriores desta sessão).
