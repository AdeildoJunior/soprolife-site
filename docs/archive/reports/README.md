# Relatórios históricos de missões

Os arquivos `RELATORIO_*.md` desta pasta são **evidências históricas** das
missões M25–M26 e de auditorias pontuais (laudos, financeiro, portal, gates).
Ficavam na raiz do repositório e foram movidos para cá em 2026-10-03, sem
alteração de conteúdo (`git log --follow` preserva o histórico de cada um).

- O estado vigente do sistema vive em
  [`docs/SOPROLIFE_MASTER_CONTEXT.md`](../../SOPROLIFE_MASTER_CONTEXT.md).
- Um relatório antigo descreve o sistema **no dia em que foi escrito**. Não use
  nenhum deles como especificação principal: confira o código e o contexto mestre.
- Não edite estes arquivos para "atualizá-los"; registre a mudança no contexto
  mestre e no commit.
- Relatórios das missões fiscais (NFS-e M27–M70) não são versionados; ficam em
  `~/SoproLife_Programacao/RELATORIOS/CRITICOS/` na máquina de desenvolvimento.

Estes `.md` nunca são servidos por HTTP: o portão do painel
(`painel-soprolife/scripts/panel_access_gate.py`) proíbe a extensão `.md`.
