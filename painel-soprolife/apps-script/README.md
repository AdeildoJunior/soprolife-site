# Apps Script — Google Sheets privado do Painel SoproLife

Esta pasta documenta os scripts usados na planilha privada do Painel SoproLife.

## Importante

Não colocar aqui:
- URL da planilha real;
- ID da planilha real;
- tokens;
- chaves de API;
- dados reais de pacientes;
- CPF;
- telefone real;
- pedido médico;
- laudo;
- resultado de exame.

## Funções disponíveis

### soprolife-sheets-template.gs
- `setupSoproLifeSheetsLite` — cria a estrutura de abas e cabeçalhos
- `setupValidacoesSoproLife` — aplica dropdowns e formatações de data/moeda
- `atualizarResumoDashboardSoproLife` — recalcula indicadores na aba Resumo Dashboard
- `onOpen` / `onEdit` — menu e gatilho automático de atualização

### sync-crm-pacientes.gs *(BLOQUEADO — M14.3A, não executar)*
- `sincronizarCRMPacientesSoproLife` — **BLOQUEADA**: lança erro sempre. O fluxo antigo
  apagava e reconstruía a aba CRM Pacientes (mestre persistente) e deduplicava por
  primeiro nome — comportamento reprovado em auditoria independente
  - Remova qualquer acionador instalável que ainda aponte para essa função
  - Auditoria substituta (read-only): `python3 painel-soprolife/scripts/reconciliar-historico.py --audit`
  - A migração incremental segura (upsert + LockService + staging + rollback) é a M14.3B

### contratos-canonicos.gs *(M14.3A — obrigatório para qualquer gravação)*
- Contratos versionados EXECUTÁVEIS: validação fail-closed para TODO campo enviado,
  patch por presença (ausência nunca limpa célula), datas valor+precisão, IDs de
  servidor (`ctNovoIdServidor`) e idempotência separada da identidade
  (`ctChaveIdempotenciaAcao` + fingerprint: mesma chave+payload = replay;
  payload diferente = conflito 409; tudo sob LockService)
- Espelho commitável: `core/contracts/registros-schemas.json` (teste: `scripts/test-contratos.js`)
- Sem este arquivo instalado, TODA gravação do Command Center falha explicitamente

### pastore-staging.gs *(M14.3A — writer isolado, STAGING)*
- `_registrarAtendimentoPastore` — grava na aba "Parceria Pastore - Atendimentos"
  (staging do acordo comercial; NÃO é base canônica de pessoas nem de valores)
- A integração ao histórico central (CRM Espirometria + Financeiro_Lancamentos) é a M14.3B

### converter-lead-em-paciente.gs *(BLOQUEADO — M14.3A 2ª rodada, não executar)*
- `converterLeadSelecionadoSoproLife` / `onEditConversaoLeadsSoproLife` — **BLOQUEADAS**:
  o fluxo antigo deduplicava paciente por telefone (prova absoluta), aceitava data
  impossível (31/02) por regex, gravava sem contrato fail-closed e não propagava
  `paciente_id` aos eventos
  - Remova o acionador instalável antigo (Extensões → Apps Script → Acionadores)
  - Até a conversão canônica (M14.3B): registre o atendimento pela tela
    "Nova Espirometria"/"Nova Consulta" do painel e atualize a etapa do lead

### organizar-leads-operacionais.gs *(BLOQUEADO — M14.3A 2ª rodada)*
- `organizarLeadsOperacionaisSoproLife` — **BLOQUEADA** (lança erro): limpava e
  reconstruía a aba Leads inteira. Migração destrutiva exige backup validado,
  manifesto, dry-run comparado e aprovação explícita (M14.3B)

### manual-das-abas.gs *(M14.3 — gerado, não editar à mão)*
- `atualizarManualDasAbasSoproLife` — recria a aba "Manual das Abas" com a matriz-resumo
  e o detalhe completo de cada aba (tipo, status, seção do painel, quem grava/lê,
  dados sensíveis, permissões, risco de exclusão e recomendação)
  - Fonte de verdade: `core/contracts/abas-manifest.json`
  - Regenerar: `python3 painel-soprolife/scripts/generate-manual-abas-gs.py` e colar o `.gs` atualizado
  - Só escreve na aba "Manual das Abas" — nunca oculta/renomeia/exclui outras abas

### limpar-leads-e-manual-abas.gs *(compatibilidade — estrutura anterior de 10 colunas)*
- `organizarLeadsEManualPlanilhasSoproLife` — padronização ADITIVA (dropdowns + notas + Manual); **não remove linha nenhuma** (a antiga limpeza de dados demo por substring foi removida — exclusão é decisão humana)
  - A parte do Manual **delega** para `atualizarManualDasAbasSoproLife`; sem
    `manual-das-abas.gs` instalado, **falha com instrução clara** (M14.3A —
    o fallback legado foi removido por estar desatualizado)
  - Backup com nome único (`_Backup_Leads_Demo_YYYYMMDD_HHMMSS_<uuid>`), nunca excluído automaticamente

## Conceito central: Leads vs. CRM Pacientes

A distinção mais importante para usar a planilha corretamente:

| Aba | Momento | O que registra |
|---|---|---|
| **Leads** | Pré-atendimento | Contatos interessados que ainda NÃO realizaram atendimento |
| **CRM Pacientes** | Pós-atendimento | Carteira-mãe — uma linha por pessoa, pós primeiro atendimento |
| **CRM Espirometria** | Histórico | Uma linha por exame realizado — vínculo via paciente_id (M14.3B) |
| **CRM Consultas** | Histórico | Uma linha por consulta realizada — vínculo via paciente_id (M14.3B) |
| **Follow-up WhatsApp** | Ações futuras | Fila de contatos planejados, não cadastro permanente |

Um lead migra para CRM Pacientes somente após o primeiro atendimento.

## Ordem de execução recomendada (planilha nova)

1. `setupSoproLifeSheetsLite` — cria abas e cabeçalhos
2. `setupValidacoesSoproLife` — aplica dropdowns iniciais
3. ~~organizarLeadsOperacionaisSoproLife~~ — **bloqueada** (migração destrutiva → M14.3B)
4. `atualizarManualDasAbasSoproLife` — cria/atualiza o Manual das Abas (M14.3, gerado do manifesto)
5. Inserir dados reais (leads, clínicas) manualmente ou via importação
6. Para leads B2C atendidos: registrar pela tela "Nova Espirometria"/"Nova Consulta" do painel
   e atualizar a etapa do lead (a conversão automática está **bloqueada** até a M14.3B)
7. Para leads B2B prontos: marcar `etapa = Convertido em clínica/parceiro` e cadastrar em CRM Clínicas
9. `atualizarResumoDashboardSoproLife` — atualiza painel (automático via onEdit)

> **Nunca** execute `sincronizarCRMPacientesSoproLife` (bloqueada na M14.3A) — a
> consolidação de CRM Pacientes agora é auditada em modo read-only por
> `reconciliar-historico.py` e a migração real depende de backup e autorização.

## Campos compartilhados entre Leads e CRM

Estes campos são copiados na conversão manual Lead → CRM Pacientes → CRM Espirometria / Consultas:

| Campo em Leads | CRM Pacientes | CRM Espirometria | CRM Consultas |
|---|---|---|---|
| `nome` | `primeiro_nome` | `primeiro_nome` | `primeiro_nome` |
| `telefone_whatsapp` | `telefone` | `telefone` | `telefone` |
| `servico_interesse` | `ultimo_servico` | `servico` | `tipo_consulta` |
| `origem` | — | `origem` | `origem` |
| `canal` | `canal` | `canal` | `canal` |
| `responsavel` | `responsavel` | `responsavel` | `responsavel` |
| `data_proxima_acao` | `proximo_contato` | `proximo_contato` | `proximo_contato` |
| `proxima_acao` | `motivo_proximo_contato` | `motivo_proximo_contato` | `motivo_proximo_contato` |
| `observacao` | `observacao_privada_minima` (+ prefixo origem) | — | — |
| (hoje) | `data_cadastro` | `data_entrada` | `data_entrada` |

Campos exclusivos do CRM (não inventados — deixados em branco para a equipe preencher):
- `exame_id` / `consulta_id` / `paciente_id` — gerado automaticamente com timestamp
- `status_exame` / `status` — valor neutro "A confirmar" quando criado via converter
- `data_exame` / `data_consulta` / `medica` / `laudo` — preenchidos pela equipe após atendimento

Campos do Leads removidos na v2 (não copiados ao CRM):
- `preferencia_atendimento`, `valor_informado`, `consentimento_whatsapp` — absorvidos em `observacao` durante migração

## Arquitetura das abas de CRM de pacientes

| Aba | Papel |
|---|---|
| CRM Pacientes | Carteira-mãe: uma linha por pessoa, relacionamento geral |
| CRM Espirometria | Histórico de exames realizados (uma linha por exame) |
| CRM Consultas | Histórico de consultas/teleconsultas (uma linha por consulta) |
| Follow-up WhatsApp | Fila de contatos futuros a enviar por WhatsApp |

**CRM Pacientes é MESTRE PERSISTENTE** (M14.3A): nenhum fluxo reescreve a aba,
elimina linhas ou recalcula IDs. A consolidação automática antiga está bloqueada.

Regras de vínculo (nunca automáticas):
1. **Nome sozinho NUNCA vincula** — homônimos existem.
2. Telefone gera apenas um **candidato**; telefone presente em mais de um
   cadastro é **ambiguous** (decisão humana).
3. Candidato não confirmado fica **pending**; sem informação suficiente,
   **unmatchable**. Nenhuma linha é eliminada; nenhuma fusão é automática.
4. Vínculo determinístico só por `paciente_id` explícito.

Formato de datas em CRM Pacientes:
- Datas completas usam `dd/MM/yyyy`; datas históricas incompletas (`MM/yyyy`)
  NUNCA viram um dia inventado — ficam como estão até a migração registrar
  `data_precisao` (ver `contratos-canonicos.gs` / `parse_data_flex`).
- Timezone: `America/Sao_Paulo`.
