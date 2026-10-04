# Relatório M25.10 — Interface de laudos: diagnóstico e correção

Data: 2026-08-08

> **A interface aprovada nunca foi substituída nem perdida.** Não houve nada
> a restaurar. O que existia era um problema de *visibilidade por perfil* e
> de excesso de ruído administrativo na tela. Os dois foram corrigidos e
> publicados.

---

## 1. Causa exata

O JavaScript servido pela produção era **byte a byte idêntico** ao commit
publicado, e continha o fluxo inteiro. Medido, não presumido:

```
GET .../js/report-workflow.js   →  100.413 bytes
renderExamAndLocation .......... presente
"Gerar prévia" ................. presente
"Assinar e liberar laudo" ...... presente
queueUnits (fila por unidade) .. presente
renderBatchBar (lote M25.8) .... presente
renderQualifiedAction (VIDaaS).. presente
cmp com o HEAD local ........... IDÊNTICO
```

A causa está em `report-workflow.js`, na composição da tela:

```js
if (explicit("medico")) blocks.push(renderPhysicianWorkspace());
if (can("operacional"))  blocks.push(renderOperationalWorkspace());
if (can("admin"))        blocks.push(renderAdminWorkspace());
```

`explicit("medico")` exige o papel **literal** na conta. E `security.py`
declara, por escrito:

```python
# ``admin`` deliberadamente NÃO implica ``medico``. Uma conta
# administrativa só ganha autoria clínica se também possuir a linha
# explícita de papel médico e passar pelas demais guardas clínicas.
```

A conta usada (`contato@soprolife.com.br`) tem **apenas `admin`**. Logo:

- **não** renderiza a fila médica nem a assinatura — correto por desenho;
- renderiza a área operacional ("Novo PDF original", "Acompanhamento") —
  porque `admin` implica `operacional`;
- renderiza a área administrativa ("Contas médicas", "Catálogo técnico").

**O que apareceu na tela era exatamente a visão de administrador.**

## 2. Onde estava a interface correta

No mesmo lugar de sempre: `painel-soprolife/js/report-workflow.js`, no
commit implantado. Nada foi movido, sobrescrito ou revertido.

## 3. Diferenças entre `9ba5a58` e a versão publicada

Comparação feita por inspeção (`git show`), sem alterar histórico.

| | `9ba5a58` (M25.3) | Publicado (`7c890cc`) |
| --- | --- | --- |
| Tamanho do JS | 71.111 bytes | 100.413 bytes |
| Fluxo M25.3 completo | sim | **sim, preservado** |
| Painel de assinatura manuscrita | não | sim (M25.4) |
| Fila por unidade | não | sim (M25.6) |
| Assinatura qualificada VIDaaS | não | sim (M25.7) |
| Lote externo | não | sim (M25.8) |

**A versão publicada é um superconjunto da M25.3.** Voltar para `9ba5a58`
teria apagado quatro etapas de trabalho — por isso não foi feito.

## 4. Os outros dois pontos do relato

**Templates vermelhos "PROVISÓRIO — NÃO UTILIZAR EM PRODUÇÃO".** São reais:
seis templates em `draft`, **zero aprovados**. Mas **não bloqueiam o laudo**:
o fluxo nativo usa o *catálogo de conclusões*
(`conclusion_code` + `bronchodilator_code`), não os templates, que são
legado da M24. O rótulo vermelho está correto — está avisando de um estado
verdadeiro.

**LAU-TF0001 na lista operacional.** É resíduo do meu teste de fumaça de
ontem. O banco tem gatilhos *append-only* que impedem apagar evidência
clínica e atribuições, então o registro **não pode ser removido**. Ele está
neutralizado: sem versões, com o usuário de teste inativo e sem papel médico.

## 5. Arquivos corrigidos

| Arquivo | Mudança |
| --- | --- |
| `js/report-workflow.js` | administração recolhida; nota explicando a fila ausente |
| `css/report-workflow.css` | estilo do bloco recolhível e da nota |
| `index.html` | cache-bust `2026080603` → `2026080801` |

**Nenhuma mudança na regra de papéis.** Dar `medico` a uma conta
administrativa "para conseguir ver a tela" destruiria uma proteção
deliberada: a de que autoria clínica nunca é herdada.

### O que mudou na prática

1. **"Administração restrita" e "Catálogo técnico" agora vêm recolhidos.**
   Competiam visualmente com o fluxo operacional e faziam a tela parecer um
   painel de configuração. Continuam inteiros, a um clique.
2. **A tela agora DIZ por que a fila não aparece**, quando a conta é
   administrativa. Antes simplesmente não mostrava nada — e a conclusão
   natural era "a interface sumiu".

## 6. Commit novo e commit implantado

| | |
| --- | --- |
| Commit criado | `7c890cc` |
| Commit implantado na VPS | `7c890cce75b1bdb5f6975b7b3d34c6a39ec8621f` |
| Branches | `codex-m25a-…` e `painel-soprolife-v01`, ambas em `7c890cc` |

Publicado por fast-forward. Sem force push, sem reset, sem rebase.

## 7. Backup

```
/opt/soprolife/backups/m25-10/20260808T142812Z/m15.dump
```

Verificado com `pg_restore -l`: **45 tabelas com dados**. Banco de produção
preservado; nenhuma migration nova (segue em `d4a71c88b2e6`).

## 8. Testes

| Suíte | Resultado |
| --- | --- |
| Módulo do laudo nativo (M25.2/25.3) | 43 passaram |
| Módulo do lote externo (M25.8) | 30 passaram |
| Contrato de frontend (M24A) | 6 passaram |
| Proxy do Command Center | 46 passaram |
| Suíte JS do painel | todos os casos |
| `node --check` | limpo |

## 9. Evidência da produção

Conferido pela URL pública, com cache-bust, **depois** do deploy:

```
health ............ status ok, ambiente prod, banco ok
index.html ........ report-workflow.js?v=2026080801
                    report-workflow.css?v=2026080801
JS servido ........ IDÊNTICO ao commit publicado
```

Marcadores presentes no arquivo que o navegador recebe:

| Marcador | Presente |
| --- | --- |
| `Fila médica não aparece nesta conta` (novo) | sim |
| `Administração restrita — contas médicas` (recolhido) | sim |
| `renderPhysicianWorkspace` (fila + laudar) | sim |
| `Gerar prévia` | sim |
| `Assinar e liberar laudo` | sim |
| `renderBatchBar` (lote) | sim |

**Sobre captura de tela:** não consigo abrir navegador autenticado contra a
produção — não tenho a senha de nenhuma conta, e não vou pedi-la nem
redefini-la. A evidência acima é do arquivo efetivamente entregue pelo
servidor, que é o que determina o que a tela monta.

## 10. URL

```
https://soprolife-painel-01.tailcaf0e4.ts.net/painel-soprolife/
```

## 11. Fluxo do técnico/admin (o que você vê hoje)

1. Entra com a conta administrativa.
2. **Novo PDF original** — localiza o exame pelo código institucional e
   anexa o PDF técnico da MIR.
3. Escolhe a unidade de realização.
4. Confirma o recebimento; o laudo entra na fila da médica atribuída.
5. **Acompanhamento operacional** — vê o estado de cada laudo.
6. A administração de contas e o catálogo ficam recolhidos, fora do caminho.

## 12. Fluxo da Dra. Ana (aparece na conta dela)

1. Entra com `annapec3@hotmail.com`.
2. Escolhe a unidade.
3. Vê a fila e abre o exame.
4. Visualiza o PDF técnico da MIR.
5. Confere paciente, exame e local.
6. Escolhe a conclusão e o complemento pós-broncodilatador; edita o texto.
7. **Gerar prévia do laudo** e confere.
8. **Assinar e liberar laudo** (liberação institucional) **ou**
   **Finalizar revisão** → lote → assina no VIDaaS → devolve.
9. Baixa o laudo e, separadamente, o PDF da MIR.

## 13. Pendências humanas

1. **Verificar o perfil médico da Dra. Ana** — está `pending`. Sem isso ela
   entra mas não lauda. Comando na seção 16.1 do relatório M25.9.
2. **Entregar a senha de primeiro acesso** — está em
   `/opt/soprolife/secrets/ana-primeiro-acesso.txt` na VPS. Canal seguro.
3. **Cadastrar a assinatura manuscrita** dela.
4. **Configurar `M15_REPORTS_VALIDATION_BASE_URL`** — hoje ausente, o laudo
   sai sem QR.
5. **Aprovar ou arquivar os 6 templates provisórios**, se incomodarem na
   área administrativa. Não bloqueiam nada.
6. **LAU-TF0001** permanece por *append-only*. Se atrapalhar a leitura da
   lista, dá para reatribuí-lo/encerrá-lo pelo fluxo da aplicação — nunca
   por SQL.

## 14. Rollback

```bash
ssh root@100.87.98.100
cd /opt/soprolife/soprolife-site
git checkout painel-soprolife-v01
git reset --hard 56517ce          # volta só a interface; banco não muda
systemctl restart soprolife-m15-api.service
```

Esta etapa **não** alterou schema nem dados: o rollback é só de arquivos
estáticos. O backup do banco existe de qualquer forma, na seção 7.

## 15. Caminho completo do relatório

```
/home/adeildo/soprolife-site/RELATORIO_M25_10_RESTAURACAO_INTERFACE_LAUDOS_20260808.md
```
