# M15.6B — adapters do snapshot real e dry-run multiaba

M15.6B acrescenta uma rota estritamente local e fail-closed para simular, sem
escrita operacional, o snapshot privado versionado das planilhas. O conteúdo
linha a linha continua fora do Git, dos logs e do painel.

## Contrato do envelope privado

O envelope `m15.raw-envelope.1` referencia arquivos `m15.raw-sheet.1` por nome
simples e SHA-256. O leitor:

- aceita somente arquivos locais no diretório `M15_IMPORT_PRIVATE_DIR`;
- rejeita path traversal, links simbólicos, checksum divergente, JSON ou
  schema desconhecido, estruturas extras, células não textuais e ordem de
  linhas não determinística;
- verifica o checksum de cada aba antes de interpretar seu JSON;
- preserva cabeçalho original, alias privado da aba e número original da
  linha;
- nunca busca rede nem executa fórmulas.

O `mapping_version` aceito é `m15-6b.1`. Mudança de formato ou mapping falha
fechada.

## Adapters e ordem

Há adapters explícitos para CRM Pacientes, CRM Espirometria, CRM Consultas,
Leads, CRM Clinicas, CRM Contatos B2B, Follow-up WhatsApp,
Financeiro_Lancamentos, as três abas Pastore e o PCMSO histórico. Cabeçalho
obrigatório ausente, desconhecido ou duplicado invalida o snapshot inteiro.

O staging em memória usa esta ordem estável:

1. exclusões históricas;
2. parceiros, clínicas e unidades;
3. contatos de parceiros;
4. pessoas, contatos e aliases;
5. consentimentos e restrições de contato;
6. leads;
7. exames;
8. consultas;
9. follow-ups;
10. relações técnicas financeiras.

IDs explícitos são autoritativos. Telefone e e-mail formam apenas candidatos;
nome isolado não vincula. Datas mantêm texto privado, data normalizada quando
válida, precisão, marcador de dia assumido, locale, aviso e referência
privada. Datas inválidas ativas vão para revisão; PCMSO inválido permanece
histórico e excluído.

Financeiro_Lancamentos é a única fonte monetária. Pastore e CRM nunca geram
valor financeiro. Relações financeiras usam exclusivamente IDs técnicos e
permanecem bloqueadas quando não resolvidas.

## Uso local

```bash
cd painel-soprolife/nucleo-m15
.venv/bin/python -m app.cli migracao dry-run-multiaba \
  --envelope snapshot-envelope.json --json
.venv/bin/python -m app.cli migracao status-multiaba --json
.venv/bin/python -m app.cli migracao revisar-multiaba \
  --batch UUID --referencia priv-TOKEN --decisao resolvido \
  --mapping-version m15-6b.1 --email REVISOR_EXISTENTE --json
```

A API protegida oferece dry-run para admin e leitura/revisão para gestor. O
painel exibe somente contagens, categorias, estado de decisão, mapping e
reconciliação prévia. Não há endpoint, comando ou botão de execução multiaba.

O lote persistido contém somente o resumo sanitizado e tokens opacos. Repetir
o mesmo envelope e mapping reutiliza o lote anterior. Entidades operacionais,
linhas brutas e lotes executáveis não são criados.

## Testes

As fixtures são totalmente sintéticas e cobrem os 12 layouts, contrato do
envelope, identidade entre abas, datas, consentimento, Pastore, fronteira
financeira, revisão protegida, reconciliação, replay e paridade CLI/API.

```bash
cd painel-soprolife/nucleo-m15
.venv/bin/python -m pytest tests/ -q
bash scripts/test-postgres-efemero.sh
cd ../..
bash painel-soprolife/scripts/quality-gate-safe.sh
```
