# Financeiro — Fonte Única (M14.2)

## Separação de papéis

| Aba do Google Sheets | Papel | O que guarda | O que NUNCA guarda |
|---|---|---|---|
| **CRM Espirometria** | Fonte **operacional** | Paciente, atendimento, datas, status do exame, follow-up | — (é a aba privada operacional) |
| **Financeiro_Lancamentos** | Fonte **financeira única** | Valores, pagamentos, descontos, status financeiros — 1 lançamento por exame | Nome, telefone, CPF, e-mail, endereço, observação clínica (o Apps Script rejeita esses campos na escrita) |

- Os dois fluxos compartilham **`id_atendimento`** (ex.: `ESP-20260709-143055-ABC123`).
- **Nenhum valor monetário do painel pode ser derivado do CRM Espirometria.**
- A antiga aba **"Financeiro" foi removida** da planilha. Nenhum fluxo ativo a
  lê e nenhum template/setup a recria (ver notas em
  `apps-script/soprolife-sheets-template.gs`).

## Cadeia de dados

```
Nova Espirometria (painel)
  ├── registrarEspirometriaFinanceiro ──► aba Financeiro_Lancamentos   (financeiro)
  └── createEspirometria ─────────────► aba CRM Espirometria           (operacional)

aba Financeiro_Lancamentos
  └── scripts/read-financeiro-lancamentos-adc.py  (leitura via ADC)
        ├── data-private/financeiro-lancamentos.local.json   (privado, 600)
        └── data/financeiro-summary.local.json               (seguro, 644)
              ├── página Financeiro (renderFinance, app.js)
              ├── Painel Geral (card "Receita oficial de espirometrias")
              ├── Cérebro Operacional (operational-brain.js)
              ├── generate-ultimos-lancamentos.py (timeline)
              └── generate-saude-operacional.py (frescor/flags)
```

## Contrato do resumo

Schema completo: `core/contracts/schema-financeiro-summary.json`.

Regras de agregação (implementadas em `build_summary`, cobertas por
`scripts/test-financeiro-summary.py`):

- **Dedupe** por `id_atendimento` (a última linha vence — espelha o upsert);
  sem `id_atendimento`, cai para `id_lancamento`.
- **Pendente / Cortesia / Cancelado nunca contam como receita recebida**,
  mesmo que a célula `valor_recebido` tenha valor (defensivo, revalida o que
  a escrita já garante).
- `receita_pendente` = `valor_cobrado` dos Pendentes + restante dos Parciais.
- Desconto: usa o campo `desconto` gravado; ausente, deriva
  `max(0, valor_tabela − valor_cobrado)`. Cancelado não gera desconto.
- Linha sem `valor_cobrado`/status válido fica **fora de todas as somas**
  (contada em `linhas_invalidas`).
- Valores não deriváveis ficam **null** (ex.: `saldo_operacional`) — a UI
  omite o card; nunca exibir `R$ 0,00` inventado.
- O summary nunca carrega `observacao_financeira` (texto livre) — descrições
  são template `"Serviço — Local"`.

## Como regenerar os dados

Local (ou VPS, no diretório do repositório):

```bash
# diagnóstico do cabeçalho da aba
python3 painel-soprolife/scripts/read-financeiro-lancamentos-adc.py --show-structure

# leitura + agregação sem gravar
python3 painel-soprolife/scripts/read-financeiro-lancamentos-adc.py --dry-run

# gravação do privado + summary
python3 painel-soprolife/scripts/read-financeiro-lancamentos-adc.py --write

# ou o ciclo completo do painel (o financeiro é o passo 9/15)
bash painel-soprolife/scripts/update-local-data.sh
```

Pré-requisitos: ADC configurado (`gcloud auth application-default login`) e
`spreadsheet_id` em `~/.config/soprolife/painel/google-sheets.local.json`
(campo opcional `financeiro_lancamentos_sheet_name` se a aba for renomeada).

Na VPS, o timer de atualização (`soprolife-update-data`) roda o
`update-local-data.sh` — após o deploy desta etapa, o resumo é regenerado
automaticamente no próximo ciclo; para forçar, rodar o script `--write` acima.

## Validação

```bash
python3 painel-soprolife/scripts/test-financeiro-summary.py   # regras de agregação
bash painel-soprolife/scripts/check-access.sh                  # privacidade (inclui o resumo)
bash painel-soprolife/scripts/quality-gate-safe.sh             # gate completo
```

## Histórico

- **M11–M12.2**: escrita dupla Nova Espirometria → CRM Espirometria (operacional)
  + Financeiro_Lancamentos (financeiro), upsert por `id_atendimento`.
- **M14.x**: aba antiga "Financeiro" removida manualmente da planilha.
- **M14.2** (esta etapa): leitura oficial da Financeiro_Lancamentos criada
  (gerador + orquestrador + painel + guardas); resumo financeiro deixa de ser
  mantido à mão e deixa de citar o CRM Espirometria como origem de valores.
