# Semântica financeira auditada antes do cálculo de lucro

Atualização de 05/11/2026: o proprietário confirmou que o histórico Maclin foi
registrado já com a parcela líquida de 70%. A confirmação é append-only em
`ProfitRevenueBasis`; o lançamento original é preservado. O bruto pode permanecer
desconhecido no histórico Pix sem bloquear lucro gerencial. Cartão usa o bruto
comprovado/reconstruído em centavos, não os 70% lançados. Saída Maclin efetivamente
registrada permanece no caixa e não é deduzida novamente da receita líquida.
Novos atendimentos Maclin usam seleção explícita, valor cheio informado e partilha
30% arredondado / restante SoproLife. O bruto fica no razão e na base fiscal nova;
a receita econômica é a parcela SoproLife. Não há backfill automático nem emissão
fiscal na confirmação da base. As observações de falta de confirmação abaixo
descrevem a auditoria anterior a essa decisão.

Base: `d9d28617951a136553155fcc6799d3c5b508bd11`.

- `financial_entries` é a fonte canônica de lançamentos. `valor` é o valor digitado/registrado, não há coluna separada de preço bruto, parcela recebida ou base tributável. `status` distingue Recebido, Pendente, Parcial e Cancelado; `forma_pagamento` é única por lançamento. `payment_allocations` aloca lançamentos a alvos, não descreve parcelas por meio de pagamento. Portanto pagamento misto não pode ser inferido.
- Receita própria de espirometria é vinculada ao exame por `spirometry_exam_id`. O cadastro a cria somente quando há bloco financeiro informado. A unicidade parcial impede duplicar a receita canônica. Não há garantia de que o valor digitado é preço bruto quando existe parceria.
- Pastore não recebe diretamente do paciente: o atendimento bloqueia lançamento próprio. `partner_settlement_items` liga cada exame a um fechamento mensal, e o `FinancialEntry` do recebimento fica no fechamento, sem vínculo individual. A regra `Partnership.valor_recebido_por_exame` é receita da SoproLife, com vigência; `modelo_repasse` descreve fluxo de saída distinto. O valor bruto pago pelo paciente à clínica não está disponível para cada exame Pastore. O fechamento recebido não deve ser somado outra vez a uma projeção por exame.
- `PartnerTransfer` representa saída a parceiro, ligada a encaminhamento, fechamento ou lançamento, mas sem vínculo direto garantido a cada exame. A porcentagem da parceria também pode representar saída. Só se deve calcular comissão nova quando o bruto e a direção da receita forem comprovados.
- Auditoria somente leitura de produção em 04/11/2026: 3 exames MACLIN têm receita direta `FinancialEntry` com status Recebido e valores variáveis R$ 130, R$ 219 e R$ 100. Há **zero** `PartnerTransfer` e zero lançamento `tipo=repasse` no banco. A tabela não prova se cada receita é preço bruto ou valor líquido. Por isso, a regra de 30% só incide após confirmação administrativa append-only da base bruta, vinculada ao lançamento e ao seu valor no momento da conferência. Uma confirmação de valor líquido não gera dedução adicional. Se repasse individual explícito aparecer, ele tem precedência sobre a porcentagem.
- A única clínica de alergia no cadastro de produção chama-se `ALERGOCARE (DRA LUDMILA ABRAM)` e seu local é `ALERGOCARE`; não há cadastro chamado AlergoLife. Não se deve aplicar automaticamente a regra de AlergoLife à ALERGOCARE sem confirmação da identidade contratual.
- `PhysicianTransfer` é fechamento mensal, com `eligible_report_count`, `unit_amount`, `reference_total` e `paid_amount`. Não tem FK de exame. A elegibilidade deriva dos laudos. Atribuição individual exige juntar laudo efetivo e competência; sem isso, marcar pendência. Não criar outro lançamento de repasse.
- `FiscalPreparation.amount_snapshot` é a evidência monetária imutável de NFS-e quando há preparo; `FiscalDocumentItem.amount` permite itens agrupados. Apenas documento de produção emitido e não cancelado pode ser tratado como nota efetiva. Preparo sozinho não significa emissão.
- `SpirometryExam` guarda data, hora opcional, modalidade, local textual, parceiro/unidade e status. Não há entidade adequada de ida/sessão nem duração de sala faturável. Horários individuais não provam início/fim da locação. A sessão deve ser registrada ou confirmada, salvo agrupamento diário inequívoco de Pastore.
- A área atual de Custos & Investimentos usa resumo local separado; não há vínculo por exame. Despesas desse resumo não podem ser subtraídas novamente do cálculo por exame sem referência técnica.

Consequência: quando preço bruto, meio de pagamento, sessão/horas ou vínculo médico não forem comprováveis, o resultado individual deve ser `incompleto` e ficar fora dos KPIs de lucro fechado. Valores estimados e recebidos devem ter rótulos distintos.

Os custos padrão de 04/11/2026 têm vigência a partir desse dia, exceto combustível/estacionamento Pastore, cuja regra diária expressa foi autorizada para o histórico desde 01/07/2026. Os demais custos não foram projetados retroativamente sobre exames antigos. O administrador pode registrar uma versão anterior, com justificativa, se houver evidência administrativa para o período.

## Correção do histórico (05/11/2026)

`calculate_exam` conserva receita, recebido, a receber e cada componente de custo
conhecido, mesmo com pendências. Campos monetários desconhecidos são `null`.
`custos_comprovados` é um subtotal parcial; `custo_total` exige cálculo completo.
Regras vigentes e rateios confirmados são evidências analíticas, distintas de
novos lançamentos contábeis. Uma sessão confirmada pode ter somente alguns custos
comprovados: seus overrides prevalecem mesmo sem regra histórica; componentes
restantes continuam pendentes. Não há inferência diária fora da Pastore.

KPIs e séries incluem fatos de todos os exames filtrados. Lucro, margem, médias
e custo final calculados usam somente a população completa. A margem divide o
lucro calculado pela receita **dessa mesma população**, nunca pela receita dos
exames pendentes. `total_exames`, `exames` (completos), `incompletos`, `cobertura`
e `informacoes_faltantes` explicitam a cobertura. Sem população elegível o
resultado é `null`, inclusive em período vazio; zero numérico comprovado continua
zero. A composição agrega somente componentes conhecidos e informa quando parcial.

O navegador mantém `null` nos datasets, exibe receita e custos na evolução e
sinaliza pendências de locais também em texto acessível. A memória de exames
incompletos mostra seus fatos conhecidos e os motivos de bloqueio. Meios, status
e data de recebimento Pastore vêm do recibo canônico do fechamento quando há
vínculo e conciliação de valor, sem somar esse recibo novamente à receita.

A ausência de NFS-e válida não autoriza usar a receita como base tributável.
Esse fallback foi removido: base vem da evidência fiscal válida já existente;
um imposto explicitamente contabilizado fornece o componente conhecido sem
estimar outra despesa. Nenhuma nota é emitida, alterada ou reconciliada aqui.

Não há nova migration, backfill ou regra retroativa automática. O administrador
pode criar uma versão histórica append-only em Configurar custos, informando
vigência, valor e referência da evidência na justificativa. A autorização
histórica de combustível/estacionamento Pastore desde 01/07/2026 permanece.
