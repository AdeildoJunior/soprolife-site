# M14.3A.2 — Kit offline de paridade do Apps Script

Kit para provar, **sem acessar o Google**, que o projeto Apps Script
remoto contém exatamente o código aprovado no Git — e para preparar uma
publicação manual controlada com evidência de rollback. Responde aos
três bloqueadores do relatório Codex 20260712-221016: inventário/hash do
remoto, comparação dos arquivos antigos da Pastore e backup restaurável +
inventário de triggers/propriedades/implantação.

Nada aqui publica, implanta ou toca rede. A publicação em si continua
sendo um ato humano no editor do Apps Script.

## 1. Três coisas diferentes que podem divergir

| Camada | O que é | Quando muda |
|--------|---------|-------------|
| **Git** | os 9 `.gs` em `painel-soprolife/apps-script/` | commit |
| **Código salvo no editor** | o que está gravado no projeto Apps Script | ao salvar no editor |
| **Versão implantada (Web App /exec)** | snapshot congelado numa versão | só ao criar Nova versão na implantação |

Consequências práticas:

- salvar um arquivo no editor **já muda** onOpen/onEdit, menus e funções
  manuais — sem nova implantação;
- o `doPost` da URL `/exec` **só muda** quando se cria uma nova versão da
  implantação;
- **rollback da implantação NÃO restaura** o código salvo nem os
  acionadores instaláveis — são três restaurações independentes (ver
  `ROLLBACK.md` do pacote).

## 2. Manifesto canônico (fonte única)

`painel-soprolife/core/contracts/apps-script-publication-manifest.json`
descreve os 9 arquivos canônicos: hash SHA-256, bytes, linhas, símbolos
globais (145 no total), entrypoints, dependências entre arquivos,
operações de escrita e regras de publicação (ordem, símbolos proibidos,
funções bloqueadas). É **determinístico** (sem timestamp) e gerado por:

```bash
python3 painel-soprolife/scripts/generate-apps-script-publication-manifest.py --write   # regenerar
python3 painel-soprolife/scripts/generate-apps-script-publication-manifest.py --check   # validar (usado no quality gate)
python3 painel-soprolife/scripts/generate-apps-script-publication-manifest.py --print-summary
```

Sempre que um `.gs` canônico mudar, rode `--write`, revise o diff do
manifesto e commite os dois juntos — o `--check` do quality gate trava
qualquer esquecimento.

## 3. Como obter a exportação remota manual

No editor do Apps Script (extensões do Sheets → Apps Script):

1. Para **cada arquivo** listado à esquerda: abrir, selecionar tudo,
   copiar e colar num arquivo local com o **mesmo nome** (`nome.gs`) em
   um diretório novo, ex.: `~/SoproLife-privado/export-remoto-AAAAMMDD/`.
   Ferramentas externas que exportam `.js` também servem — o comparador
   normaliza a extensão.
2. Configurações do projeto → marcar "Mostrar o arquivo de manifesto" →
   copiar `appsscript.json` para o mesmo diretório (opcional, ajuda).
3. Preencher `remote-inventory.json` (formato em
   `core/contracts/apps-script-remote-export.schema.json`): lista de
   arquivos como aparecem no editor, acionadores instaláveis, **somente
   os NOMES** das Script Properties (nunca valores), versão ativa e
   descrição da implantação. **Sem** Script ID, deployment ID, URL ou
   token.

## 4. Onde guardar (backup restaurável)

- O diretório exportado **é o backup de rollback**: guarde-o completo,
  datado, **fora do Git** (ex.: `~/SoproLife-privado/`), e não o apague
  após a publicação.
- Nunca commitar a exportação, nem `.clasp.json`, nem qualquer ID/URL.
- O `.gitignore` já protege `data-private/`; a exportação nem deve
  entrar na árvore do repositório.

## 5. Comparar remoto × Git

```bash
python3 painel-soprolife/scripts/compare-apps-script-export.py \
  --remote-dir ~/SoproLife-privado/export-remoto-AAAAMMDD \
  --report /tmp/comparacao.txt --json-report /tmp/comparacao.json
```

Opções: `--strict` (qualquer divergência, até aviso, falha),
`--allow-extra NOME` (tolera extra específico temporariamente),
`--no-write` (só stdout).

O comparador informa: idênticos, ausentes, hashes divergentes (inclusive
quando a diferença é só CRLF de copiar/colar), extras, globals e
entrypoints remotos, globals duplicados, símbolos antigos proibidos
(`_nextId`, `_reescreverAbaPacientes`, `_cvConjuntoTelefones`,
`_conteudoManualAbas`, `onOpen_syncCRM`), stubs reativados, operações
destrutivas, arquivos `pastore-planilha.gs.gs`/`pastore-formulas.gs`/
backups/`.gs.gs` e conteúdo com cara de segredo.

### READY × BLOCKED

- **READY** (exit 0): os 9 canônicos idênticos byte a byte e nenhum
  achado bloqueante. Avisos podem existir (exit 3 com `--strict`).
- **BLOCKED** (exit 2): existe pelo menos um achado bloqueante. **Não
  montar pacote, não publicar.** Cada extra remoto só pode ser removido
  depois de exportado (backup), comparado e aprovado — nunca "mesclar
  por intuição".
- **Erro de uso/entrada/validação** (exit 1): argumentos inválidos,
  `--remote-dir`/`--manifest` inexistentes, ou `remote-inventory.json`
  que não passa no contrato (`apps-script-remote-export.schema.json`).
  Este exit code nunca colide com BLOCKED (2) — um inventário inválido
  jamais vira READY nem BLOCKED, só erro de uso.

## 6. Preparar o pacote de publicação

```bash
# dry-run (padrão — não escreve nada):
bash painel-soprolife/scripts/prepare-apps-script-publication-pack.sh \
  --output ~/SoproLife-privado/pack-AAAAMMDD \
  --remote-dir ~/SoproLife-privado/export-remoto-AAAAMMDD

# montagem real (exige comparador READY):
bash ... --execute
```

O script valida o manifesto (`--check`), roda o comparador, **recusa o
pacote em BLOCKED**, exige `--output` fora do repositório e monta:
`arquivos/` (só os 9 canônicos), manifesto, `SHA256SUMS`,
`INVENTARIO-SIMBOLOS.txt`, `ORDEM-DE-COPIA.txt`,
`CHECKLIST-PUBLICACAO.md`, `ROLLBACK.md` e a evidência da comparação.
Termina com varredura de segredos no próprio pacote.

## 7. Ordem de publicação (resumo; detalhes no pacote)

1. Backup completo do remoto + `remote-inventory.json` preenchido.
2. Criar versão/backup remoto anterior; remover acionadores antigos.
3. Substituir os 9 arquivos na ordem de `ORDEM-DE-COPIA.txt`
   (contratos → command-center → pastore → stubs → template → manual →
   limpar-leads), salvando um a um.
4. Remover somente extras já comparados e aprovados.
5. Recontar: exatamente 9 arquivos e 145 globals
   (`INVENTARIO-SIMBOLOS.txt`).
6. Script Properties: `API_TOKEN` preservado, `BUILD_VERSION` = commit.
7. Implantar → Gerenciar implantações → **editar a existente** → Nova
   versão (mantém deployment ID/URL). Descrição = commit.
8. Prova não mutante: POST com corpo `{` malformado, sem token →
   esperado `ok=false`, `code=400` (falha antes de autenticação e de
   qualquer escrita). Nunca "testar" com writers ou `_test*`.

## 8. Inventário de triggers, propriedades e implantação

Sempre coletar ANTES de mudar qualquer coisa (o rollback depende disso):
acionadores instaláveis (função/evento), nomes das Script Properties e
versão ativa da implantação. O formato do `remote-inventory.json` está
no schema; ele nunca contém valores de propriedades.

O comparador valida `remote-inventory.json` contra o schema **antes** de
qualquer comparação — campo fora da lista permitida (ex.: `propertyValues`,
`scriptId`, `deploymentId`, `deploymentUrl`, `token`), tipo errado ou
conteúdo com cara de segredo fazem o comparador sair com exit 1 e não
gerar relatório nenhum (nunca READY, nunca BLOCKED). A mensagem de erro
cita só o caminho/nome do campo — nunca o valor.

## 9. Rollback

Três camadas independentes (detalhado no `ROLLBACK.md` do pacote):
implantação (voltar a versão anterior na implantação existente),
código salvo (restaurar do diretório exportado) e triggers (restaurar
conforme inventário). Depois, repetir apenas a prova não mutante.

## 10. O que NUNCA entra no Git

Exportação remota, backups do remoto, `.clasp.json`, Script ID,
deployment ID, URL `/exec`, `API_TOKEN` ou qualquer valor de Script
Property, credencial, e qualquer dado real de paciente. As fixtures de
teste (`scripts/fixtures/apps-script-parity/`) são 100% sintéticas.

## 11. Testes e quality gate

```bash
python3 painel-soprolife/scripts/test-apps-script-parity.py   # 22 testes offline
bash painel-soprolife/scripts/quality-gate-safe.sh            # seção 8c/10 M14.3A.2
```

Os testes escrevem apenas em `/tmp` e cobrem determinismo, hashes,
símbolos, as 16 fixtures, exit codes, strict, e o pacote (recusado em
BLOCKED, produzido em READY, allowlist e ausência de segredos).
