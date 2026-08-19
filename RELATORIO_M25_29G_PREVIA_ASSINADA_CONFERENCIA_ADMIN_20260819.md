# M25.29G — Prévia assinada histórica e conferência administrativa simples

**Data de abertura:** 19/08/2026 · **Branch:** `claude-m25-29g-previa-assinada-conferencia`
**Base:** `298a255` (M25.29F integrada)

> ## ⏳ ESTADO: **EM ANDAMENTO — aguardando auditoria read-only de LAU-000014**
>
> **Nenhuma escrita foi feita até agora.** Nem no banco, nem em arquivo de
> laudo, nem em estado de documento. A parte de interface está pronta e
> testada; a parte de dado depende de uma auditoria que exige credencial
> `root` e ainda não foi executada.

---

## 1. O achado real

Assim que a M25.29F destravou os downloads, o PDF que a fila administrativa
entrega como **"laudo assinado"** do LAU-000014 pôde finalmente ser aberto e
inspecionado por uma pessoa.

**Caso:** `LAU-000014` / `ESP-000025`

O documento baixado contém, impresso no próprio papel:

```
PRÉVIA
PRÉVIA — DOCUMENTO NÃO CONCLUÍDO
PRÉVIA — DOCUMENTO NÃO CONCLUÍDO — conferência da médica antes da assinatura.
Código de verificação: —
versão 2
```

E o arquivo **tem assinatura digital embutida**.

### Conclusão

**O documento assinado recebido do LAU-000014 é uma PRÉVIA assinada.**

Isso confirma exatamente a hipótese que a M25.29E deixou registrada em
aberto: o LAU-000014 foi pareado por **`codigo_laudo_no_conteudo`** — o
código LAU **impresso na folha** — e não por metadado carimbado. Esse é o
caminho de pareamento que, antes da trava da M25.29D, também casava com uma
prévia, porque a prévia carrega o mesmo código impresso. O documento entrou
em 16/08, **antes** da trava existir.

O `Código de verificação: —` é a assinatura do problema: o código de
verificação só nasce na conclusão. Um documento que não o tem nunca foi
concluído.

### Consequências imediatas

* **NÃO pode ser conferido.**
* **NÃO pode ser entregue.**
* **NÃO pode ser apagado** — é evidência de um incidente real.
* A conclusão clínica **não** será alterada.

---

## 2. Por que isso não pode se repetir

A partir da M25.29D, implantada e provada em 18/08:

* a prévia não sai pelo endpoint de assinatura (`409` estruturado);
* a prévia não é aceita de volta assinada, nem com assinatura válida por
  cima — a recusa é operacional e auditada;
* o arquivo de prévia baixa como `<Nome> - PREVIA - NAO ASSINAR.pdf`;
* o PDF de prévia traz `NÃO ASSINAR` impresso.

O LAU-000014 é **histórico**: entrou pela janela que já foi fechada.

---

## 3. Fase 1 — auditoria read-only (PENDENTE)

Comando, somente leitura, sem PII:

```bash
cd /opt/soprolife/soprolife-site/painel-soprolife/nucleo-m15
/opt/soprolife/venvs/m15/bin/python scripts/auditar_caso_laudo.py LAU-000014
```

Precisa responder, antes de qualquer proposta de escrita:

| pergunta | por quê |
| --- | --- |
| todas as versões e seus `kind` | localizar a prévia e a final |
| existe `laudo_liberado`? | decide se há documento final correto para assinar |
| qual é a versão corrente | estado operacional real do laudo |
| `report_document_version_id` do assinado | a qual versão o blob está amarrado |
| `match_method` e lote | confirmar o pareamento por código impresso |
| status, conferência, entrega | saber se já saiu para alguém |
| hashes e trilha de auditoria | preservar a evidência |

> **Requer credencial `root`.** Executada por pessoa, não por mim.

---

## 4. Correção pretendida (NÃO EXECUTADA — dry-run primeiro)

Condicionada a a auditoria confirmar que **existe versão final liberada**:

1. preservar o PDF assinado da prévia como evidência histórica — blob e
   trilha intactos;
2. marcá-lo explicitamente **não entregável / rejeitado**, com motivo
   *"prévia assinada antes da conclusão"*;
3. **nenhum `DELETE`**, nenhuma reescrita de histórico;
4. removê-lo da fila de conferência administrativa pendente;
5. devolver o LAU-000014 ao estado **aguardando assinatura externa da versão
   final**;
6. preservar integralmente a conclusão clínica já liberada;
7. permitir baixar somente o PDF final correto para assinatura;
8. a médica assina o final e devolve normalmente — **sem reinterpretar o
   exame**;
9. o novo upload amarra à versão final por metadado/hash, conforme M25.29D.

**Se a auditoria mostrar que não existe versão final liberada**, o trabalho
**para aqui** e volta como decisão humana: nesse caso o caminho passa pela
médica concluir o laudo, e isso é chamada clínica.

### Pergunta operacional em aberto

Se o LAU-000014 já foi **entregue** a alguém, o documento entregue é a prévia
assinada — e há um passo fora do sistema a resolver. A auditoria informa a
data de entrega, se houver.

---

## 5. Segundo achado — a confirmação administrativa ✅ CONCLUÍDO

Ao testar a fila administrativa apareceu um `window.prompt()` exigindo
**digitar** a frase `Confirmo a conferência externa`.

### Qual clique disparava — provado

O dispatch usa `event.target.closest("button")` e três atributos `data-*`
**distintos**, verificados com `matches()`, que exige o atributo exato e não
o prefixo:

| botão | atributo | ação |
| --- | --- | --- |
| Baixar exame técnico | `data-delivery-download-mir` | download, **zero** popup |
| Baixar laudo assinado | `data-delivery-download-assinado` | download, **zero** popup |
| Registrar conferência | `data-delivery-validate` | abre a confirmação |

**Não há sobreposição de handler, nem captura de botão errado.** O popup vinha
exclusivamente do botão de conferência — o correto. O problema era a
exigência de digitação, não o disparo.

### O que passou a existir

```
Confirmar conferência do PDF assinado?

Confirme apenas se você conferiu externamente o documento assinado.
A SoproLife não realiza validação criptográfica da cadeia ICP-Brasil.

            [Cancelar]  [Confirmar conferência]
```

A intenção da M25.20 — *um clique distraído não pode virar testemunho de uma
pessoa identificada* — **continua**: são dois passos deliberados, na própria
tela, com o texto dizendo o que está sendo afirmado. O que saiu foi a
digitação, que na prática virava copiar e colar.

**O contrato do backend não foi afrouxado:** a API continua exigindo a frase,
que passou a ser constante do cliente. Nenhuma rota, nenhum estado e nenhuma
regra de autorização mudaram.

### Detalhe que apareceu e vale saber

A conferência exige **`ROLE_ADMIN`**. Um usuário `operacional` enxerga a fila
mas recebe `403` ao tentar registrar — recusa clara, não sucesso silencioso.
Está travado em teste.

### Testes — 11 novos

1. os três botões têm atributos distintos; `matches()` exige exato
2. baixar exame técnico não abre confirmação
3. baixar laudo assinado usa a mesma função, sem popup
4. a conferência abre uma confirmação, com título e dois botões
5. não existe frase digitada nem `prompt()` nessa ação
6. o contrato do backend segue exigindo a frase
7. cancelar não chama a API e não muda estado
8. confirmar grava só a conferência
9. conferir não entrega e não afirma assinatura qualificada
10. conferência exige `ROLE_ADMIN`
11. alvo de toque de 48px e empilhamento no celular

Mais o contrato da M25.20 atualizado: ele exigia `window.prompt`, e agora
exige o mecanismo de dois passos. A intenção do teste foi preservada e as
asserções aumentaram de 3 para 6.

**Resultado:** 11 verdes; 133 verdes na regressão relacionada (M25.20,
M25.29D, M25.29E, M25.29F).

---

## 6. Estado atual

| item | estado |
| --- | --- |
| Achado do LAU-000014 | ✅ documentado |
| Auditoria read-only | ⏳ **pendente — exige `root`** |
| Dry-run da correção | ⏳ depende da auditoria |
| Escrita no banco | ⬜ **nenhuma** |
| Confirmação administrativa | ✅ concluída e testada |
| Testes focados | ✅ 11 novos, 133 de regressão |
| Deploy | ⬜ não executado |

**HEAD local:** `f375eed` · **HEAD oficial:** `298a255` · **HEAD VPS:** `298a255`

Suíte completa não executada: a orientação foi hotfix focado com a operação
esperando.

Nenhuma PII e nenhum segredo constam deste relatório.

---

*Documento vivo — atualizado a cada avanço da missão.*
