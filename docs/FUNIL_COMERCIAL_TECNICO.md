# Funil Comercial — contrato técnico

A implementação evolui `leads`, que já referencia `people.id`. Não existe
segundo cadastro de pacientes nem segundo banco. `interactions` não tem
vínculo confiável com cada oportunidade, então não define primeira resposta.
`audit_logs` continua registrando ações saneadas; `funnel_events` preserva
fatos e datas anteriores com proteção append-only no ORM e PostgreSQL.

Migration `f131126funil`, pai `a04f1c202611`:

- `leads`: `primeiro_contato_em`, `primeira_resposta_em`, `agendado_em`,
  `realizado_em` (timestamptz); `exame_previsto_em` (date);
  `origem_aquisicao`, `unidade_pretendida`, `status_funil`,
  `motivo_encerramento`; `partner_id`, `partner_unit_id` (FKs);
  `idempotency_key` (unique), `idempotency_fingerprint`.
- `spirometry_exams.lead_id` nullable, FK/index; relação muitos exames para
  um contato permite reteste sem outra conversão.
- `funnel_events`: UUID, lead, tipo, horário do fato, ator, exame relacionado,
  datas anterior/nova, motivo categórico, snapshot de dimensões controladas,
  created_at. Sem texto de conversa, documentos ou conteúdo clínico.
- `funnel_deployment`: timestamp efetivo do upgrade para o aviso de cobertura.

Todas as colunas novas de entidades históricas são nullable. A migration
não atualiza nem classifica registros existentes. Os três registros de
`partner_units` continuam sendo a entidade institucional. Barra, domiciliar,
Zona Norte etc. são categorias prospectivas porque não há entidade de
unidades próprias que as represente. O catálogo vem do servidor, não da UI.

API sob `/api/v1/funil`:

| Método | Rota | Contrato |
| --- | --- | --- |
| GET | `/config` | Catálogos controlados e unidades institucionais |
| POST | `/contatos` | Pessoa existente OU PersonCreate; criação atômica e idempotente |
| GET | `/contatos` | Lista operacional paginada, pessoa opcional, abertos opcional |
| PATCH | `/contatos/{id}` | Correção explícita de dimensões com evento |
| GET | `/contatos/{id}/eventos` | Cronologia sem conteúdo de interação |
| POST | `/contatos/{id}/eventos` | Resposta, agenda, remarcação, cancelamento, falta, espera, encerramento |
| POST | `/contatos/{id}/vincular-exame` | Mesmo person_id obrigatório, sem associação textual |
| GET | `/metricas?inicio=...&fim=...` | Agregados; unidade/origem/canal/serviço opcionais |

Leitura exige `leitura`; escrita exige `operacional`; horário inicial
retroativo informado exige gestor. Os schemas recusam campos extras e
categorias fora do catálogo. A API de agendamento inicialmente integra
espirometria; consulta permanece no seu fluxo de atendimento existente.
Nenhum dado de contato pessoal é aceito em eventos ou métricas.

`POST /espirometrias` e os dois fluxos `POST /atendimentos` aceitam `lead_id`
e `continuar_sem_rastreamento` no bloco de exame. Um único contato aberto
pode ser escolhido automaticamente por person_id. Mais de um gera escolha
explícita (409), ou o operador pode continuar sem rastreamento. A reserva
pendente desse contato é reutilizada ao completar o atendimento. Os endpoints
existentes permanecem válidos sem campos novos. PATCH do exame sincroniza
etapa e remarcação; realização direta não inventa agendamento anterior.

Eventos: NOVO_CONTATO, PRIMEIRA_RESPOSTA, EXAME_VINCULADO, AGENDADO,
REMARCADO, CANCELADO, NAO_COMPARECEU, EXAME_REALIZADO, AGUARDANDO_RETORNO,
PERDIDO, DIMENSOES_CORRIGIDAS. A primeira resposta e realização são gravadas
uma vez. Transições travam o lead no PostgreSQL para evitar sobrescrita em
concorrência. Não há tarefa que infira perda, falta ou cancelamento.

Conversões são contagens de contatos únicos da coorte por primeiro contato:
respondidos/contatos, agendados/contatos, agendados realizados/agendados,
realizados/contatos. Remarcações são eventos. Tempos são medianas de diferenças
não negativas entre timestamps operacionais, com disponibilidade e denominador.
Sem denominador, percentual e mediana são NULL. A semana começa segunda em
America/Sao_Paulo; a atual e recortes incompletos são parciais. Coortes recentes
não têm janela de maturação equivalente; isso é informado na interface.

Cobertura usa uma coorte separada de produção por data do exame: realizados
com lead / realizados, origem conhecida / contatos e reservas atuais vinculadas
/ reservas atuais. Não presume que exames históricos realizados tenham sido
agendados. Filtros de dimensões dos leads excluem exames não rastreados da
cobertura filtrada, indicado na interface. Sem filtro, eles são incluídos.

Rollback: seguir `SOPROLIFE_MASTER_CONTEXT.md` §11, conservar backup privado
verificado antes do upgrade e o HEAD anterior. A versão anterior do código
funciona com o schema aditivo. Preferir rollback apenas do código após haver
fatos novos. `alembic downgrade a04f1c202611` só é permitido se não houver
contatos rastreados, eventos ou vínculos; caso contrário recusa perda de
história. Não restaurar dump por cima da operação em andamento.

Site público: change separado na branch do site, apenas `assets/analytics.js`
e testes, com cache-buster de analytics nas páginas. Eventos canônicos
`click_whatsapp`, `click_agendamento`, `click_telefone` nos CTAs sem evento
equivalente. `click_whatsapp_ipanema` e `click_agendar_pastore` permanecem
com os nomes consumidos pelo Marketing, com parâmetros seguros e deduplicação
por tipo de clique. `whatsapp_click` genérico passa a `click_whatsapp`.
Parâmetros vêm de vocabulário fechado; não há link_url, texto digitado ou ID
interno. Page views removem query/hash e limitam paths. Só há eventos após
consentimento concedido; UTM institucional conhecido pode ficar na memória
anônima de sessão após consentimento. Isso não atribui pessoa ao Google.
Os controles externos de configuração do GA4 não são modificados nesta missão.

Limitações: registro operacional manual; não há integração de mensagens.
Primeira resposta não tem edição posterior na UI; evita sobrescrita acidental.
Horário clínico exato não é inferido. Serviços de consulta não recebem agenda
nova. Eventos passados e contatos externos nunca registrados continuam sem
cobertura. Cada novo contato explícito representa uma oportunidade, não uma
pessoa inédita na empresa. Nenhuma alteração em GBP ou SEO da Barra.
