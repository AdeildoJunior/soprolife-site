# Central de Parcerias — 20/11/2026

A entrada lateral `parcerias` abre o consolidado e os cards Pastore/Maclin/Alergocare.
A seção `parcerias-pastore` conserva integralmente a implementação anterior e é
aberta pelo card Pastore ou por links de fechamento já existentes. O gate resolve
essa seção pela entrada lateral Parcerias, sem duplicar itens no menu.

Identidade: Pastore usa `canonical_pastore`; Maclin/Alergocare usam
`partner_shares.resolve_rule` vigente. Filtra exames por partner_id canônico,
nunca por origem/local/nome livre. Sem criação de parceiro/unidade/regra e sem
incluir Alergo Life. Ausência/ambiguidade da configuração deixa card indisponível.

APIs GET `/parcerias/central` e `/parcerias/central/{codigo}`: projeções somente
leitura, `private, no-store`, ROLE_LEITURA; valores só para ROLE_GESTOR efetivo.
Período padrão mês corrente até hoje, intervalo máximo 366 dias, detalhe paginado
20/até 50. Exames concluídos, datas exatas ou mês integralmente abrangido; datas
imprecisas excluídas são indicadas. Laudador não recebe payload nem módulo JS.

Novas partilhas: valores congelados e saldo atual de `partner_shares.position`.
Recebimentos/regularizações/acertos não são outra receita. Partilhas canceladas
não fornecem valores válidos. Histórico sem partilha só consome a projeção
canônica de lucro quando ela tem evidência monetária explícita; não cria 70/30,
recebedor, obrigação nem acerto retroativos. Projeção de lucro só é consultada
para históricos sem partilha; não é recalculada inutilmente para novas partilhas.

Pastore: parcelas da SoproLife somente de fechamentos completos abrangidos pelo
filtro, com valor conferido ou lançamento vigente; nunca soma recibo e fechamento.
Não rateia fechamento conferido por um subconjunto do período. Valor previsto da
regra não é apresentado como dinheiro confirmado. Bruto do paciente, parcela
parceira e valor a pagar ficam desconhecidos quando não há fonte própria. Agenda,
fechamento mensal, fiscal e suas ações permanecem no módulo Pastore existente.

Somatórios são completos ou nulos; ausência aparece como “—”, sem completar com
zero e sem apresentar subtotal conhecido como total. Zero resulta de conjunto
vazio confirmado ou saldo calculado pela fonte. Saldos atuais referem-se aos
exames/fechamentos do período, sem pretender reconstruir saldo em data passada.

Maclin/Alergocare usam o mesmo componente de detalhe, com IDs próprios, regra
vigente, produção, valores, recebedor e link aos acertos/histórico existentes no
Financeiro. Enquadramento fiscal só é exibido da configuração vigente: DIRECT
sobre bruto, confirmação humana. Nenhuma emissão/preparação fiscal nesta central.

Busca mantém o destino original do parceiro e acrescenta “Ver parceria” para
Maclin/Alergocare canônicos. Destino `parceria` em `/comando/registro` revalida
marca, papel e ID; bookmark `#cc/soprolife/parceria/UUID` reabre o parceiro correto.
Alergo Life não recebe esse link. Voltar ao consolidado remove bookmark individual.

Sem migração/tabela/fato novo. Testes Python/PG16 e navegadores usam somente
fixtures sintéticas. Deploy autorizado segue runbook existente em modo full,
com backup/fingerprints, schema check, restart API, health API/proxy/portal e
validação autenticada somente leitura. Evidências e SHAs efetivamente publicados:
`~/SoproLife_Programacao/RELATORIOS/CENTRAL_PARCERIAS_20261120.md`.


## Acabamento visual do Centro de Comando

Pedido complementar do usuário: interface mais gráfica, com menos emojis.
Vocabulário vetorial local em `ui-icons.js`, sempre decorativo/aria-hidden, com
rótulos e avisos semânticos conservados. UI do painel, CRM, avisos de prontidão,
status de assinatura e calendário usam traço consistente, sem alterar dados ou
condições. O calendário continua montando DOM sem innerHTML; ícone via máscara
SVG local. Nenhuma mudança de texto/modelo emitido em PDF médico.

`premium-ui.css` mantém navy/teal e a distinção EEG, ajusta densidade, cartões,
tipografia e foco. Gráfico compacto da coorte usa somente contagens da API;
sem série, meta ou tendência inventada. Navegação mobile recolhe após seleção;
o scroll reserva espaço para a barra do contexto. Novos filtros usam o calendário
brasileiro existente, com datas ISO preservadas na API. Testes de vocabulário
verificam ausência de emojis, SVG seguro e fallback de nomes desconhecidos.
