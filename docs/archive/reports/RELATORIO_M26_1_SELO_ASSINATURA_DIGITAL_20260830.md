# M26.1 — nota sem fabricante e selo de assinatura digital

**Data:** 30/08/2026
**Branch:** `painel-soprolife-v01`
**Commit:** `6e965db`
**Estado:** **EM PRODUÇÃO.** Deploy concluído em 30/08/2026 16:18 (-03).

---

## O que mudou

Duas correções editoriais no template do laudo, válidas **só para os PDFs
gerados daqui em diante**.

### 1. A nota do PDF técnico

Antes: `…do PDF técnico do equipamento (MIR) — documento SEPARADO…`
Agora:

> Traçado e medidas originais constam do PDF técnico do equipamento —
> documento SEPARADO deste laudo, inalterado, com download próprio.

A frota deixou de ser de um fabricante só: há exames feitos no KOKO. Não
troquei por "(MIR/KOKO)" — seria a mesma dívida com um item a mais,
quebrando na próxima marca que entrar. O modelo, quando importa, consta do
próprio PDF técnico, que é o documento que sabe qual é.

A constante `MIR_SEPARATE_NOTICE` virou `EQUIPMENT_SEPARATE_NOTICE`, e
`draw_mir_notice()` virou `draw_equipment_notice()`.

### 2. O selo esquerdo

Antes: `CONCLUÍDO / PELA MÉDICA` em navy.
Agora: `ASSINATURA / DIGITAL` em verde (`#0E6E66`), com a marca VIDAS abaixo,
tudo dentro do anel — composição de selo de certificação: dizeres em cima,
marca embaixo.

O texto antigo repetia o que já está escrito duas vezes logo abaixo: no
rótulo da data ("Concluído em") e na declaração do rodapé.

**A restrição da M25.21 continua sendo o que decide o texto.** Um carimbo
impresso não pode afirmar estado que expira. A médica baixa este mesmo
arquivo, assina por fora e devolve o PDF assinado; o desenho do selo continua
como saiu daqui. "ASSINATURA DIGITAL" descreve a natureza do documento e
permanece verdadeiro depois que a assinatura entra na camada PDF — ao
contrário de "AGUARDANDO ASSINATURA", que a M25.21 teve de remover.

**Nada aqui afirma ICP-Brasil.** Isso segue exclusivo do ramo qualificado,
escolhido só com prova criptográfica gravada. A negativa explícita continua
no rodapé (`RELEASE_STATEMENT`), e o PDF tem exatamente uma ocorrência de
"ICP-Brasil": a negativa.

---

## Arquivos

| Arquivo | O quê |
|---|---|
| `painel-soprolife/assets/vidas-logo.png` | **novo** — 2.519 B, 206×43, fundo transparente |
| `painel-soprolife/nucleo-m15/app/services/report_native_pdf.py` | as duas correções |
| `painel-soprolife/nucleo-m15/tests/test_m26_1_selo_assinatura_digital.py` | **novo** — 14 testes |
| `tests/test_m25_2_native_report.py` | asserções que travavam o texto antigo |
| `tests/test_m25_18_assinatura_externa.py` | idem |
| `tests/test_m25_21_selo_pdf_pre_assinatura.py` | idem |

**SHA-256 do asset:** `643a6000f4c30ca53f1b730b8a8b8e081d6eff78d9dfe806531b5390a7752a5a`
Conferido contra o blob publicado no git (`78e5a10`), não só contra o arquivo
em disco.

### Proveniência do asset

Derivado de um print que o usuário colou na sessão. O print de origem tem
artefato de compressão — uma faixa diagonal quase branca que, inscrita no
círculo, aparecia como um retângulo cinza atrás de "AS". A limpeza usa piso
de alfa 28 mais unpremultiply sobre branco.

**Se precisar regerar, a fonte é esse print — não o carimbo do VIDaaS dos
PDFs assinados.** Naquele carimbo (objeto 500×80, incremental update depois
do `%%EOF`) a marca está em cinza ATRÁS do texto preto "Assinado
digitalmente por: …", parcialmente coberta e irrecuperável.

---

## Como os testes provam o selo

A palavra "assinatura" aparece legitimamente 2× no rodapé, então presença não
prova nada. Os dois sinais usados:

- **`"ASSINATURA\nDIGITAL"`** no texto extraído — só as duas linhas de dentro
  do anel saem grudadas assim. Ausente na prévia e no ramo ICP.
- **Contagem de imagens embutidas:** laudo liberado institucional = 2 (marca
  do cabeçalho + VIDAS); prévia e ramo qualificado = 1.

**A geometria não é estimada.** A largura da marca vem da corda real do
círculo na altura em que ela está, não do diâmetro — estimativa manual foi o
que fez texto vazar do selo na M25.5.
`test_a_marca_cabe_inteira_dentro_do_anel` intercepta `circle()` e
`drawImage()` e confere os quatro cantos contra o centro real do anel. Mutei
a folga para confirmar que o teste quebra de verdade.

**O asset é opcional por construção:** ausente ou ilegível, o selo se recompõe
centralizado só com o texto e o laudo sai completo. Nenhuma parte do fluxo
clínico depende de um PNG estar no lugar. Há teste para isso.

---

## Testes

- Focados (laudo + assinatura + Pastore M26): **119 passed**.
- Suíte completa antes do merge com a M26: **1494 passed, 30 skipped, 1 failed**.

A falha é **anterior a esta entrega e não foi introduzida por ela**:
`test_m25_17_operacao_limpa.py::test_rubrica_real_nao_esta_versionada` acusa
`docs/m25-21/laudo-pre-assinatura-completo.png` e `selo-pre-assinatura.png`,
commitados em `1604ba1` (M25.21). São screenshots sintéticos do selo, não a
rubrica real da médica — falso positivo do filtro por nome de arquivo
("assinatura"), não vazamento de PII. Fora do escopo deste hotfix.

---

## Nada retroativo

O gerador só roda nos caminhos de escrita (prévia, conclusão, aditamento),
nunca no download de uma versão gravada.
`test_a_correcao_nao_reescreve_laudo_ja_emitido` trava isso estruturalmente:
varre `routers/reports.py` e falha se `_native_pdf_bytes` passar a ser chamado
fora do conjunto de funções de emissão.

- Migração desta entrega: **NENHUMA**
- Banco alterado: **NÃO**
- Laudos históricos regenerados: **ZERO**

**A ressalva que eu tinha levantado não se aplicou.** Eu havia alertado que o
`pull` poderia atravessar os commits da M26 Pastore e o intervalo não seria
vazio de migração. A VPS estava em `5408b6f`, e não em `22f5908` como estimei:
o intervalo aplicado foi **exatamente `5408b6f..6e965db`**, um commit só, com
`git diff --name-only … -- migrations/` retornando **0 arquivos**. A migração
corrente segue `a3f6b0d94c17 (head)` em PostgreSQL, inalterada.

---

## Deploy executado

| Passo | Resultado |
|---|---|
| HEAD anterior da VPS | `5408b6f` (árvore limpa) |
| Intervalo aplicado | `5408b6f..6e965db` — 1 commit, 6 arquivos |
| Migrações no intervalo | **0 arquivos** |
| `pull --ff-only` | OK, sem force, sem reset |
| HEAD VPS depois | `6e965db` = origin, divergência `0 0` |
| Árvore VPS depois | limpa |
| `vidas-logo.png` na VPS | presente, 2.519 B, `644`, legível pelo usuário `soprolife` |
| SHA-256 remoto | `643a6000…52a5a` — **idêntico** ao local/origem |
| Testes M26.1 na VPS | 14 passed |
| Testes de laudo adjacentes na VPS | 95 passed |
| Prova sintética na VPS | 15/15 OK |
| Restart | `soprolife-m15-api` apenas — PID 262251 → 277148 |
| Serviço | `active` |
| Health `GET :8015/api/v1/health` | **HTTP 200** — `status: ok`, `ambiente: prod`, `banco: ok` |
| Journal desde o restart | sem erros |
| `check-access.sh` na VPS | passou (exit 0) |

Serviços **não** tocados: `soprolife-painel`, `soprolife-painel-loopback`,
`soprolife-update-data.timer` — todos seguem no estado anterior.

### Prova de que o processo vivo carregou o código novo

- fonte gravado às **16:16:41**;
- bytecode regenerado às **16:17:10**;
- processo iniciado às **16:18:47**.

E a constante lida do módulo em produção:

```
FRASE : Traçado e medidas originais constam do PDF técnico do equipamento — documento SEPARADO deste laudo, inalterado, com download próprio.
ASSET : /opt/soprolife/soprolife-site/painel-soprolife/assets/vidas-logo.png | existe: True
```

### Prova sintética na VPS (sem paciente real)

PDF de 127.110 bytes gerado em memória com dados fictícios, pelo **mesmo
interpretador do serviço**. Frase nova presente; `MIR` e `KOKO` ausentes;
selo `ASSINATURA/DIGITAL` renderiza; 2 imagens embutidas (cabeçalho + VIDAS);
caminho do asset resolvendo para o repo de produção; `ICP-Brasil` só na
negativa; sem `PAdES`. Fallback conferido: removido o asset, o PDF continua
saindo, o selo mantém o texto e sobra 1 imagem.

### Nada foi escrito em produção

Janela do deploy (a partir de 16:15):

| Tabela | Total | Criados durante o deploy |
|---|---|---|
| `report_document_versions` | 70 | **0** |
| `report_documents` | 16 | **0** |
| `audit_logs` | 1.776 | **0** |

Última ação registrada em auditoria: `auth.token_emitido` às 16:03 — antes do
deploy começar. Nenhum laudo histórico foi regenerado.

---

## Aceite

- **OS PRÓXIMOS LAUDOS NÃO CITAM MAIS MIR OU KOKO NA NOTA DO PDF TÉCNICO.**
- **OS PRÓXIMOS LAUDOS EXIBEM O SELO ASSINATURA DIGITAL COM A MARCA VIDAS.**

Ambas valem em produção a partir de 30/08/2026 16:18 (-03).

## Rollback

```bash
ssh root@100.87.98.100
cd /opt/soprolife/soprolife-site
git reset --hard 5408b6f      # HEAD anterior registrado
systemctl restart soprolife-m15-api
```

Seguro porque a árvore estava limpa antes do deploy e não houve migração nem
escrita de dados. Laudos emitidos entre o deploy e o rollback manteriam o selo
novo — o gerador não reescreve documento já gravado.
