# Continuidade — mapa e atendimento domiciliar

Prévia local: http://127.0.0.1:8114/
Branch: `codex/site-mapa-domiciliar-20260930`.
Base: `origin/main`, commit `8a9566b` (SEO de Claude após redesign e inclusão de Norte Shopping).
Status: implementado e validado localmente; não publicado.

## Alterações

- Home: mapa original movido integralmente para uma seção própria após a apresentação e faixa de serviços. O atalho Onde atendemos leva ao mapa; endereços continuam acessíveis pelo atalho Ver endereços e horários.
- Atendimento domiciliar ganha seção logo após o mapa, link desde a abertura, página explicativa e WhatsApp com mensagem específica.
- O serviço descrito é espirometria domiciliar, já documentado no site. O usuário foi consultado para esclarecer se também oferece consulta médica presencial em casa; não houve resposta até a conclusão. Não foi inventada uma oferta de consulta domiciliar.
- Unidades, endereços, horários e configurações de agendamento preservados; formulário reorganizado após remoção do mapa lateral.
- Foto da home mais compacta no celular para reduzir a distância até o mapa.
- Página Espirometria RJ: textos recuperados por Claude preservados literalmente. Títulos longos e parágrafos foram organizados em linhas com a tipografia editorial compartilhada.
- Correção da centralização da seção verde de apresentação nas duas páginas.
- Nenhum arquivo do painel, informação privada, analytics ou regra operacional alterado.

## Validação

- `tests/public-editorial.cjs`: Chromium e Firefox, 11 páginas × 5 larguras × 2 navegadores = 110 combinações; nenhum overflow horizontal ou erro JavaScript.
- Agendamento, unidades, Norte Shopping, Pastore, datas, WhatsApp, menu, FAQ, mapas, diálogo, Escape e foco passaram.
- Ajuste do teste de captura: mapa agora é visitado antes da agenda para acionar corretamente seu carregamento por visibilidade.
- Teste adicional em 1440, 390 e 320 px: posição antecipada do mapa/domiciliar, links da apresentação, abertura do mapa, posição do título abaixo do cabeçalho e conteúdo do link WhatsApp domiciliar.
- Titles, metadados e JSON-LD das duas páginas comparados com a base e preservados. Texto da página Espirometria RJ idêntico ao da base.
- JSON-LD válido, nenhum ID duplicado nas duas páginas, destinos locais existentes.
- `node --check tests/public-editorial.cjs` e `git diff --check` passaram.
- Capturas desktop/mobile inspecionadas. Nenhuma mensagem enviada nem agendamento real criado.

Resultados e capturas: `/tmp/soprolife-mapa-20260930/` e `/tmp/soprolife-mapa-20260930-firefox/`.
Teste adicional: `/tmp/soprolife-home-priority-check.cjs`.

Para reabrir a prévia se o servidor encerrar:

```bash
cd /home/fedorasurf/soprolife-worktrees/site-mapa-domiciliar-20260930
python -m http.server 8114 --bind 127.0.0.1
```

Não sobrescrever a versão antiga em `site-isadora-20260928` nem a cópia com mudanças pendentes em `/home/fedorasurf/soprolife/soprolife-site`.
