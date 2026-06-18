# Conector Google Sheets — Dry-Run

Status: esqueleto seguro implementado. API real não conectada.

## O que este modo faz

Valida a estrutura de um arquivo de configuração local sem:
- conectar à API do Google;
- imprimir valores sensíveis (spreadsheet_id, credenciais);
- ler arquivos dentro de `~/.config/soprolife/` sem autorização explícita.

## Arquivos criados

| Arquivo | Finalidade |
|---|---|
| `scripts/read-sheets-summary-dry-run.py` | script de validação |
| `core/examples/google-sheets.local.example.json` | exemplo seguro com placeholders |
| `core/connectors/google-sheets-dry-run.md` | este documento |

## Como rodar

```bash
# Teste com arquivo de exemplo seguro (recomendado)
python3 painel-soprolife/scripts/read-sheets-summary-dry-run.py \
    --config painel-soprolife/core/examples/google-sheets.local.example.json

# Sem argumento: recusa ler config privada por padrão
python3 painel-soprolife/scripts/read-sheets-summary-dry-run.py

# Com config privada real (quando disponível e autorizado)
python3 painel-soprolife/scripts/read-sheets-summary-dry-run.py \
    --allow-private-config
```

## Saída esperada

```
SoproLife OS Local Core — Google Sheets connector
mode: dry-run

config_detected: true
sheet_name_detected: true
credential_path_detected: true

Estrutura de configuração válida.

next_step: API Google ainda não conectada
```

## Estrutura esperada da configuração

Ver: `core/examples/google-sheets.local.example.json`
Ver contrato: `core/contracts/schema-resumo.json`

## Próximo passo (fase futura)

Quando autorizado, implementar `read-sheets-summary.py` com:
- autenticação via conta de serviço;
- leitura apenas da aba `Resumo Dashboard`;
- validação contra `schema-resumo.json`;
- saída em `data/resumo-dashboard.local.json`.
