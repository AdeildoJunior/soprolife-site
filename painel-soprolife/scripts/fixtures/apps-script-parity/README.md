# Fixtures sintéticas — paridade Apps Script (M14.3A.2)

Casos de teste 100% sintéticos para `compare-apps-script-export.py`.
**Nenhum arquivo aqui contém PII, segredo real, token real, URL real ou
ID real** — tudo é inventado e marcado como sintético.

## Como uma fixture vira um "diretório remoto"

Cada subdiretório tem um `fixture.json`:

```json
{
  "nome": "curto",
  "descricao": "o que o caso simula",
  "base": "canonico",            // copia os 9 .gs reais do Git p/ um tmp
  "remover": ["arquivo.gs"],     // remove do tmp após copiar a base
  "renomear": {"a.gs": "a.js"},  // renomeia no tmp (simula export .js)
  "esperado": "READY|BLOCKED",
  "achados_esperados": ["CODIGO", "..."],   // códigos que DEVEM aparecer
  "allow_extra": [],                        // passado como --allow-extra
  "allow_extra_torna_ready": false          // 2ª rodada com --allow-extra
}
```

O harness (`test-apps-script-parity.py`) monta o diretório remoto em
`/tmp`: copia a base canônica, aplica `remover`/`renomear` e por fim
copia todos os arquivos da fixture (exceto `fixture.json`) por cima.
Assim as fixtures não duplicam os arquivos canônicos grandes e não
desatualizam quando os `.gs` reais mudam.

## Casos

| Caso | Simula | Esperado |
|------|--------|----------|
| 01-remoto-identico | export igual ao Git | READY |
| 02-canonico-ausente | falta sync-crm-pacientes.gs | BLOCKED |
| 03-hash-divergente | stub organizar com bytes diferentes | BLOCKED |
| 04-extra-remoto | arquivo extra inofensivo | BLOCKED (READY c/ --allow-extra) |
| 05-pastore-planilha-antigo | pastore-planilha.gs.gs legado | BLOCKED |
| 06-pastore-formulas-antigo | pastore-formulas.gs legado | BLOCKED |
| 07-dopost-duplicado | segundo doPost | BLOCKED |
| 08-onopen-duplicado | segundo onOpen | BLOCKED |
| 09-onedit-duplicado | segundo onEdit | BLOCKED |
| 10-funcao-bloqueada-reativada | sync antigo ativo (sem stub) | BLOCKED |
| 11-clearcontents-perigoso | extra com clearContents/deleteRows | BLOCKED |
| 12-backup-antigo | cópia "backup" de canônico | BLOCKED |
| 13-arquivo-js | export externo com extensão .js | READY |
| 14-appsscript-json | manifesto do projeto presente | READY (info) |
| 15-extensao-gs-gs | nome com .gs.gs | BLOCKED |
| 16-segredo-sintetico | credencial FALSA hardcoded | BLOCKED |
| 17-regex-com-texto-de-segredo | literais regex com texto tipo segredo (não é atribuição real) | BLOCKED (READY c/ --allow-extra) |
