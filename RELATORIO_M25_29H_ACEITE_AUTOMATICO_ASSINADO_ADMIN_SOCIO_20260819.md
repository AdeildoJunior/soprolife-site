# M25.29H — Aceite automático do PDF assinado, paridade administrativa do sócio e integridade do download

> Relatório vivo. Criado no início da missão e atualizado durante o trabalho.
> Última atualização: 2026-08-19 (em andamento).

## Ponto de partida

| item | valor |
|---|---|
| branch oficial | `painel-soprolife-v01` |
| HEAD de partida | `ec0a9d8` (M25.29G encerrada) |
| worktree | `/home/adeildo/soprolife-worktrees/claude-m25-29h-aceite-automatico-assinado` |
| branch da missão | `claude-m25-29h-aceite-automatico-assinado` |

## Objetivos

1. Eliminar a conferência administrativa manual: o PDF assinado devolvido pela
   médica passa por guardas documentais e vai direto para "pronto para entrega".
2. Provar, por hash, que "Baixar laudo assinado" entrega o PDF recebido daquele
   laudo — investigação do relato de download aparentemente sem assinatura e de
   outro paciente.
3. Dar ao sócio Luiz Antonio Faustino Lopes de Oliveira a mesma autoridade
   **administrativa** da conta principal, sem papel médico.

## Andamento

- [x] Worktree limpo criado a partir de `origin/painel-soprolife-v01`.
- [ ] Auditoria read-only do endpoint de download assinado.
- [ ] Auditoria read-only da fila em produção (012, 013, 015 e demais).
- [ ] Auditoria read-only de papéis/permissões (conta principal x Luiz).
- [ ] Implementação do aceite automático.
- [ ] Testes focados.
- [ ] Deploy, migration, manutenção de dados.

_(seções seguintes preenchidas ao longo da missão)_
