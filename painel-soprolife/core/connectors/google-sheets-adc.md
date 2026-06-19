# Conector Google Sheets — ADC (Application Default Credentials)

Status: implementado com falha segura. Requer instalação de dependências e autenticação ADC.

## O que este conector faz

Lê a aba `Resumo Dashboard` da planilha privada usando as credenciais locais do `gcloud`, sem service account JSON e sem chave privada.

## Arquivos

| Arquivo | Finalidade |
|---|---|
| `scripts/read-sheets-summary-adc.py` | conector principal |
| `requirements-google.txt` | dependências Python |
| `core/connectors/google-sheets-adc.md` | este documento |

## Pré-requisitos

### 1. Instalar dependências

```bash
pip install -r painel-soprolife/requirements-google.txt
```

Ou em ambiente virtual isolado (recomendado):

```bash
python3 -m venv painel-soprolife/.venv
source painel-soprolife/.venv/bin/activate
pip install -r painel-soprolife/requirements-google.txt
```

### 2. Autenticar com ADC

```bash
gcloud auth application-default login \
    --scopes=https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/spreadsheets.readonly
```

Isso grava credenciais em `~/.config/gcloud/application_default_credentials.json`.
Esse arquivo nunca entra no repositório.

### 3. Verificar configuração

O arquivo `~/.config/soprolife/painel/google-sheets.local.json` deve conter:

```json
{
  "spreadsheet_id": "ID_REAL_DA_PLANILHA",
  "sheet_name": "Resumo Dashboard"
}
```

## Como diagnosticar a estrutura da aba

Se `valid_indicators` retornar 0, use `--show-structure` para inspecionar a aba com segurança:

```bash
python3 painel-soprolife/scripts/read-sheets-summary-adc.py --show-structure
```

Imprime apenas:
- número total de linhas;
- número de colunas na primeira linha;
- nomes das colunas da primeira linha (cabeçalho).

Nenhum valor de dado é impresso. Nenhum spreadsheet_id, URL ou token é exibido.

## Falha segura quando não há indicadores válidos

Se o script não encontrar nenhum indicador válido na aba, ele:

- exibe `ERRO: nenhum indicador válido encontrado na aba`;
- lista o formato esperado das colunas A, B, C;
- lista todas as chaves esperadas na coluna A;
- sugere usar `--show-structure` para diagnóstico;
- retorna código de saída 1;
- **não grava nada**, mesmo em modo `--write`.

Nunca preenche indicadores com 0 quando nenhum dado real foi lido.

## Como testar sem gravar

```bash
python3 painel-soprolife/scripts/read-sheets-summary-adc.py --dry-run
```

Lê a planilha, valida os indicadores e imprime o resultado. Nenhum arquivo é escrito.
Falha com código 1 se nenhum indicador válido for encontrado.

## Como testar gravando

```bash
python3 painel-soprolife/scripts/read-sheets-summary-adc.py --write
```

Grava em `~/.config/soprolife/painel/resumo-dashboard.json` (fora do repositório, permissão 600).

Em seguida, atualize o painel:

```bash
painel-soprolife/scripts/sync-dashboard-summary.sh
```

Ou use o orquestrador completo:

```bash
painel-soprolife/scripts/update-local-data.sh
```

## Formato esperado da aba "Resumo Dashboard"

| A (key) | B (label) | C (value) |
|---|---|---|
| totalLeads | Total de leads | 42 |
| leadsNovos | Leads novos | 8 |
| leadsAgendados | Leads agendados | 5 |
| leadsConcluidos | Leads concluídos | 20 |
| clinicasCadastradas | Clínicas cadastradas | 12 |
| tarefasPendentes | Tarefas pendentes | 7 |
| receitaPrevista | Receita prevista | 15000 |
| receitaRecebida | Receita recebida | 9500 |
| conteudosPlanejados | Conteúdos planejados | 6 |
| eventosAgendados | Eventos agendados | 3 |
| pacientesEmAcompanhamento | Pacientes em acompanhamento | 18 |
| examesEspirometriaRealizados | Espirometrias realizadas | 34 |
| teleconsultasRealizadas | Teleconsultas realizadas | 12 |
| followupsPendentes | Follow-ups pendentes | 5 |
| lembretesWhatsAppPendentes | Lembretes WhatsApp pendentes | 3 |
| recorrenciasAtivas | Recorrências ativas | 7 |
| consultasPrevistas | Consultas previstas | 9 |

A linha de cabeçalho (`key`, `label`, `value`) é ignorada automaticamente.
Chaves desconhecidas na coluna A são ignoradas sem erro.
Se nenhuma chave conhecida for encontrada, o script falha com código 1.

### Indicadores obrigatórios vs. opcionais

Os 10 primeiros indicadores (de `totalLeads` a `eventosAgendados`) são **obrigatórios**:
se ausentes na planilha, o JSON recebe valor `0` e o card sempre aparece no painel.

Os 7 indicadores de atendimento/CRM (de `pacientesEmAcompanhamento` a `consultasPrevistas`)
são **opcionais**: só aparecem no JSON e no painel se a aba `Resumo Dashboard` os contiver.
São indicadores **agregados** — nunca contêm nomes, telefones, CPF ou dado clínico individual.
Os valores são totalizados pelo Apps Script nas abas privadas (CRM Pacientes, CRM Espirometria,
CRM Consultas, Follow-up WhatsApp) antes de chegarem à aba Resumo Dashboard.

## Segurança

- `spreadsheet_id` nunca é impresso.
- Tokens e credenciais ADC nunca são impressos.
- Palavras proibidas (CPF, telefone, paciente, etc.) bloqueiam a execução se detectadas nas células.
- O arquivo de saída fica em `~/.config/soprolife/painel/` (fora do repositório, fora da pasta servida).
- Permissão do arquivo de saída: `600`.

## Integração futura com update-local-data.sh

Quando estável, o `update-local-data.sh` poderá chamar este script antes do `sync-dashboard-summary.sh`, substituindo a etapa de importação manual por CSV.
