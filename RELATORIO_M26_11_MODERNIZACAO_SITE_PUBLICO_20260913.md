# M26.11 — Modernização do site público SoproLife

Data: 13/09/2026. Entrega para revisão visual local, sem publicação.

## Isolamento

- Branch: `codex/m26-11-site-moderno`.
- Worktree: `/home/fedorasurf/soprolife-worktrees/codex-m26-11-site-moderno`.
- Base: `origin/main`, commit `f866c75`, após `git fetch origin`.
- Nenhum merge, push, deploy, acesso à VPS ou alteração de GitHub Pages.
- Não foram alterados painel, NFS-e, banco, Financeiro, laudos, portal de resultados, API clínica ou dados reais.

## Auditoria e implementação

Foram identificadas 14 páginas públicas de conteúdo: home, Conheça, Serviços, Espirometria, Espirometria RJ, Domiciliar RJ, Ipanema, Consulta com pneumologista, Telemedicina, Links e quatro entradas de funil. Os redirects `.html`, a página offline e os arquivos técnicos foram preservados. O portal de resultados ficou fora das alterações.

O site é estático, com CSS e JavaScript inline repetidos e assets compartilhados: `sopro-visual-system-v3.css`, `sl-v3.css`, `sl-landing-v1.css`, `sl-booking.css`, `sl-booking.js`, `sl-units-map.js`; os funis possuem estilo e motor próprios. Foram encontradas muitas sobreposições históricas de CSS, inclusive largura fixa de 264 px e deslocamento negativo no mapa, header mobile com dimensões fixas e navegação escondida.

A nova camada `assets/sl-modern.css` concentra tokens de cores navy/teal, fonte de sistema, espaçamento, largura máxima, raios, sombras, foco, botões, cards e campos. Mantém compatibilidade com a marcação existente, sem framework, build ou alteração de conteúdo institucional. É carregada após as camadas legadas para prevalecer sobre seus ajustes inline.

`assets/sl-modern.js` adiciona menu mobile com estado acessível, link para pular ao conteúdo, identificação da página atual, Instagram no menu mobile, contenção de foco nos diálogos de mapa e navegação manual dos depoimentos. Os depoimentos deixam de depender de rotação automática; `prefers-reduced-motion` é respeitado.

Header, heroes, cards de serviços, seções, médicos, CTAs, formulários, agenda e rodapés receberam a camada visual comum. Foram mantidos logo oficial e destinos de WhatsApp, Instagram, serviços e resultados.

## Mapas

O provedor encontrado foi Leaflet com tiles CARTO Voyager. Os mapas realmente presentes estão na home, Espirometria RJ e Ipanema, cada um com versão pequena e modal. Domiciliar tem um card de área de atendimento, sem mapa físico; isso foi preservado.

- Todos os tiles passaram a usar `https://tile.openstreetmap.org/{z}/{x}/{y}.png`, sem chave.
- Attribution com link de copyright visível nas versões pequenas e ampliadas.
- Removidos largura fixa e deslocamento negativo do mapa da agenda; grid responsivo e altura definida no celular.
- Tiles mantêm dimensões originais de 256 px, sem redimensionamentos de imagens gerais. A base recebe tratamento cromático próprio, descrito abaixo.
- `ResizeObserver` atualiza o tamanho do Leaflet; o mapa compartilhado reenquadra os marcadores ao mudar de tamanho ou unidade.
- Mantidas as coordenadas existentes de Barra, Zona Norte e Pastore Ipanema. Domiciliar não recebe endereço inventado.
- Falhas de tiles/CDN mostram acesso alternativo ao OpenStreetMap. Links de rota existentes continuam disponíveis.

Referências técnicas: [política de tiles OpenStreetMap](https://operations.osmfoundation.org/policies/tiles/) e [Leaflet 1.9.4](https://leafletjs.com/reference.html). O navegador faz o carregamento normal dos tiles visíveis, com cache HTTP e Referer; não foi criado download offline ou armazenamento em service worker. Mapas continuam dependendo de conexão e disponibilidade do provedor externo.

### Ajuste final solicitado: mapa claro e modal amplo

A modernização e a agenda do commit `ea5d4f7` foram preservadas. A continuação finaliza o ajuste visual dos mapas no mesmo worktree. O último teste interrompido havia identificado uma regra antiga de Ipanema (`position:absolute; inset:24px`) que deslocava o modal em 768 px. O posicionamento foi unificado com o das demais páginas.

Antes de escolher a base clara, foram verificadas as condições oficiais atuais:

- [CARTO Basemaps Terms, atualizados em 26/08/2026](https://carto.com/legal/basemap-terms/): o acesso gratuito exige chave emitida pela CARTO; requisições sem chave podem receber marca d’água. Positron não foi adotado sem autenticação.
- [Stadia Maps — autenticação](https://docs.stadiamaps.com/authentication/): dispensa autenticação no desenvolvimento local, mas exige cadastro/autenticação de domínio em produção. Não foi introduzida dependência que funcionaria somente no localhost.

Foi aplicado o fallback expressamente autorizado: OpenStreetMap com cores suavizadas e contraste legível apenas na camada dos tiles. Após a revisão visual do usuário, a saturação foi ajustada para 65%, contraste para 88%, brilho para 106% e opacidade para 100%, devolvendo cor à água, vegetação e vias. Pins, popups, controles e attribution permanecem sem filtro. Como a base é raster, POIs e símbolos não podem ser removidos individualmente: ficam visualmente secundários, sem prometer uma cartografia vetorial nova.

- `assets/sl-maps-calm.css`: acabamento comum nas três páginas com mapa; formulário e mapa com áreas equivalentes.
- `assets/sl-units-map.js`: implementação única também para Ipanema, com coordenadas centralizadas em `SL_BOOKING`. Removidos os handlers antigos que abriam WhatsApp ao clicar no card da unidade.
- Pins próprios numerados, teal/navy, halo branco e seleção com anel adicional. Clique mostra nome, resumo, Como chegar e WhatsApp; não envia mensagem nem abre WhatsApp automaticamente.
- Lista e pins sincronizados com a unidade do formulário. A lista apresenta uma linha compacta por unidade e ações secundárias apenas na selecionada. Informações e links da parceira permanecem em detalhes expansíveis.
- Modal centralizado com 90% da largura e 86% da altura no desktop; faixa lateral de 280 px e mapa ocupando a maior parte da área útil. Em 1440 px, a área do mapa tem aproximadamente 1016 px de largura.
- No celular, mapa acima da lista, rolagem interna da lista e modal dentro da viewport. Escape e Fechar devolvem o foco ao botão de abertura.
- Zoom pela roda do mouse habilitado no mapa ampliado, assim como arraste e gesto de pinça. O mapa pequeno preserva a rolagem normal da página.
- `invalidateSize` e reenquadramento ao abrir/redimensionar; todos os pins físicos enquadrados na visão geral, com margem para não ficarem cobertos pelo botão de ampliação ou attribution.
- Nenhuma chave, segredo, provedor CARTO ativo ou tentativa de esconder marca d’água foi introduzida.

## Agenda e data

Não existe consulta a uma fonte real de reservas nessa agenda pública. Nenhuma ocupação foi inventada.

- Domiciliar, Barra e Zona Norte: segunda a sábado; oito horários abertos para solicitação: 08:00, 09:00, 10:00, 11:00, 13:00, 14:00, 15:00 e 16:00.
- Pastore Ipanema: regra existente preservada, terças e sábados, 08:00–12:00 a cada 30 minutos, com orientação e link para o sistema oficial da parceira.
- A data inicial é a primeira data aberta **estritamente futura**, a partir de amanhã no fuso `America/Sao_Paulo`, dentro da janela existente de 30 dias a partir de hoje.
- Domingo fechado; sem feriados inventados. Datas inválidas e passadas são rejeitadas; dia fechado avança para a próxima data permitida com explicação.
- Troca de tipo/unidade revalida a data; quando necessário, escolhe a primeira data aberta. Uma data já válida é mantida. A seleção anterior de horário é limpa para revisão.
- O código usa ISO `YYYY-MM-DD`; a interface e o resumo usam `dd/mm/aaaa`. `Intl.DateTimeFormat().formatToParts()` fixa o dia da operação independentemente do fuso do visitante.
- Selecionar horário marca o botão com texto e `aria-pressed`; mostra resumo e ação “Continuar no WhatsApp”. O link leva tipo, unidade, data e horário, sem enviar mensagens automaticamente. A equipe confirma a solicitação; não há promessa de reserva em tempo real.
- A agenda revalida datas também quando a aba atravessa a meia-noite. Sem Flatpickr/CDN, funciona com calendário nativo e resumo em português.

## Validação reproduzível

- `tests/m26-11-browser.cjs`: Chromium headless; 14 páginas × 1440, 1024, 768, 430 e 390 px, total de 70 combinações, incluindo menu mobile e overflow.
- Agenda na home, Espirometria RJ e Domiciliar: oito horários habilitados, seleção única, troca de tipo/unidade, resumo, telefone e quatro campos da mensagem WhatsApp; domingo corrigido; agenda Pastore preservada.
- Relógio simulado e visitante no fuso de Tóquio: domingo, sexta/sábado, virada setembro/outubro, dezembro/janeiro e instante UTC que ainda é o dia anterior em São Paulo. Validação de data inexistente, como 31/02.
- Mapas pequenos/ampliados: tiles OpenStreetMap reais, attribution e marcadores; abertura/fechamento e retorno de foco no desktop e celular.
- Depoimentos com redução de movimento; fallback do calendário com CDNs bloqueados.
- `tests/m26-11-preservation.py`: comparação com a base de 21 HTMLs; metadados, títulos, JSON-LD e links originais preservados. Sitemap, robots, resultados, redirects, service worker e CNAME idênticos à base.
- `node --check` nos três scripts compartilhados alterados e `git diff --check`.
- Resultado final: 70 combinações sem overflow horizontal, 10 registros funcionais, 6 registros de mapas e zero erros JavaScript. Evidência estruturada: `artifacts/m26-11/test-results.json`.
- `tests/m26-11-maps-calm.cjs`: teste específico das três páginas com mapa nas cinco larguras; seleção lista → pin e pin → lista/formulário, popup, destinos das ações, attribution, tiles reais, enquadramento dos pins, zoom por roda do mouse no desktop, ausência de overflow e resize com modal aberto de 1440 para 390 px. Evidência: `artifacts/m26-11/maps-calm-results.json`.

Os testes não enviam WhatsApp, não submetem dados pessoais e não chamam a API clínica. Links externos são inspecionados, sem confirmar agendamentos reais. Validação executada em Chromium; não representa certificação formal de acessibilidade nem teste em aparelhos físicos/Safari.

## Screenshots e revisão

Diretório: `artifacts/m26-11/screenshots/`.

Principais: `home-desktop.png`, `home-mobile.png`, `espirometria-desktop.png`, `espirometria-mobile.png`. Há também versões `-completa.png` de página inteira e `-agenda.png` com o card selecionado. As capturas de agenda ocultam somente header/barra fixa durante a captura para que não cubram o conteúdo; as capturas principais mostram a interface normal. Datas e seleções são sintéticas, produzidas localmente.

Capturas do ajuste final: `mapa-normal-desktop.png`, `mapa-ampliado-desktop.png`, `mapa-normal-390px.png`, `mapa-ampliado-390px.png` e `mapa-pin-selecionado-desktop.png`. As imagens de mapa normal mostram o card completo para avaliar sua integração com o formulário; os modais mostram a interface normal com o fundo da página desfocado.

Servidor local utilizado: `http://127.0.0.1:8111/`.

```bash
python3 -m http.server 8111 --bind 127.0.0.1 --directory /home/fedorasurf/soprolife-worktrees/codex-m26-11-site-moderno
```

Agenda: `http://127.0.0.1:8111/espirometria-rio-de-janeiro/#agendamento`.

Para repetir os testes, com Playwright/Chromium disponíveis:

```bash
NODE_PATH=/tmp/m26-11-browser/node_modules node tests/m26-11-browser.cjs
NODE_PATH=/tmp/m26-11-browser/node_modules node tests/m26-11-maps-calm.cjs
python3 tests/m26-11-preservation.py
```

A dependência de teste foi instalada fora do repositório, em `/tmp/m26-11-browser`; o site não depende dela. Entrega registrada em commit nesta branch de trabalho; identificação disponível em `git log -1 --oneline`. Integração e publicação ficam aguardando aprovação visual.
