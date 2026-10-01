# Continuidade — agenda, mapa e consulta médica online

Prévia local: http://127.0.0.1:8114/
Branch: `codex/site-mapa-domiciliar-20260930`.
Base pública: `origin/main`, commit `8a9566b` (SEO de Claude após redesign e inclusão de Norte Shopping).
Status: prévia local; não publicada.

## Versão atual após o retorno do usuário em 01/10

- Home: o formulário completo e o mapa voltaram a ficar juntos, no primeiro bloco após a apresentação e a faixa de serviços. No computador ficam lado a lado; no celular, mapa imediatamente após os campos, horários e confirmação, sem outra seção entre eles.
- Horários com caixas de 52 px, mantendo área de toque e indicação de seleção. Mapa com link para os endereços das unidades.
- O título do bloco foi encurtado para “Seu exame, perto de você”.
- O usuário esclareceu que o atendimento a destacar era a consulta médica **online**. Agora há botão na apresentação, link no menu principal e seção própria imediatamente depois da agenda/mapa, antes dos endereços e conteúdos longos.
- A seção de consulta online aponta para a página de telemedicina e para o WhatsApp com mensagem específica de consulta. Texto baseado na oferta já descrita pelas páginas públicas de telemedicina e consulta respiratória.
- Espirometria domiciliar continua em seção própria depois da consulta online. Nenhuma oferta de consulta médica presencial em casa foi criada.
- Endereços, horários, unidades, Norte Shopping, regras Pastore, lógica de agendamento e analytics preservados.
- Página Espirometria RJ: conteúdo recuperado por Claude preservado literalmente, com títulos e parágrafos longos organizados em linhas editoriais.
- Fontes, cores e botões seguem a identidade editorial inspirada na referência Isadora. Menu permite quebra de linha para acomodar o link extra em ampliação de 200%.
- Nenhum arquivo do painel ou informação privada alterado.

## Validação

O teste `tests/public-editorial.cjs` cobre as 11 páginas em 1440, 1024, 768, 390 e 320 px, além de agenda, datas, Norte Shopping, Pastore, links WhatsApp, menus, FAQ, mapas, diálogo, Escape, foco, ampliação e fallback sem CDN.

Rodada final: Chromium e Firefox passaram nas 110 combinações de página/largura, sem overflow horizontal ou erros JavaScript. Os cenários funcionais, ampliação de 200% e fallback sem CDN também passaram.

Foram acrescentadas verificações para evitar a regressão relatada pelo usuário: agenda logo após a apresentação, mapa dentro do mesmo bloco, alinhamento lateral no computador, proximidade no celular e acesso à consulta online desde a apresentação. Capturas adicionais da seção de telemedicina.

Titles, metadados e JSON-LD das duas páginas seguem preservados em relação à base pública. Texto da página Espirometria RJ idêntico ao da base. IDs únicos; `node --check tests/public-editorial.cjs` e `git diff --check` verificados.

Capturas desktop/mobile inspecionadas. Nenhuma mensagem enviada ou agendamento real criado.

Resultados e capturas atuais: `/tmp/soprolife-agenda-online-chromium/` e `/tmp/soprolife-agenda-online-firefox/`.

Para reabrir a prévia se o servidor encerrar:

```bash
cd /home/fedorasurf/soprolife-worktrees/site-mapa-domiciliar-20260930
python -m http.server 8114 --bind 127.0.0.1
```

Não sobrescrever a versão antiga em `site-isadora-20260928` nem a cópia com mudanças pendentes em `/home/fedorasurf/soprolife/soprolife-site`.
