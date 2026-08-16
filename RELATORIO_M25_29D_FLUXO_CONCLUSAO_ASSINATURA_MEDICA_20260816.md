# M25.29D — Concluir o laudo uma vez, e só o documento final pode ser assinado

**Data:** 2026-08-16
**Branch:** `claude-m25-29d-fluxo-laudo-assinatura`
**Base:** `e1baeed954d758f42a878ce0aee086b1f61c1ea9` (HEAD oficial de partida — confirmado)

> **Estado desta etapa: implementada, testada e provada localmente.
> NÃO implantada.**
> O SSH da VPS está no modo *check* do Tailscale e exige uma autenticação no
> navegador que só o operador pode fazer. Sem ela não foi possível executar a
> Fase 0 na VPS (HEAD, health, Alembic, timer) nem investigar LAU-000013 /
> ESP-000028 na base real. Tudo o que não depende disso está pronto, com as
> provas abaixo; o que depende está isolado e descrito nas seções 3 e 11.

---

## 1. O incidente

A Dra. Ana Cristina do Nascimento Cunha assinou externamente, com o próprio
certificado, um PDF que ainda era **prévia**. O arquivo dizia, por escrito:

```
PRÉVIA
PRÉVIA — DOCUMENTO NÃO CONCLUÍDO
PRÉVIA — DOCUMENTO NÃO CONCLUÍDO — conferência da médica antes da assinatura.
Código de verificação: —
```

Caso: **LAU-000013 / ESP-000028**.

Esse PDF **não é documento final entregável** e nada nesta etapa passa a
tratá-lo como tal.

## 2. Causa — três defeitos somados, nenhum deles "erro da médica"

### 2.1 O fluxo pedia dois botões deliberados antes de uma confirmação

A bancada exigia, em sequência:

| passo | rótulo | o que a médica lê |
| --- | --- | --- |
| 1 | **Gerar prévia do laudo** | "terminei de escrever" |
| 2 | **Concluir laudo** | abre o diálogo |
| 3 | **Sim, concluir laudo** | confirma |

Depois do passo 1 já existe um PDF na tela, com o nome do paciente, com o
logotipo, com a conclusão clínica inteira. Quem não conhece a máquina por
dentro lê o passo 1 como o fim do trabalho — e o arquivo que sobra ali é uma
prévia.

O diálogo do passo 2 trazia quatro marcadores explicando adendo, versão
corretiva, registro de auditoria e onde a assinatura qualificada acontece.
Tudo verdade; nada acionável no instante em que se decide entre continuar e
voltar.

### 2.2 O nome do arquivo mentia

`painel-soprolife/nucleo-m15/app/routers/reports.py` → `download_report_version`
nomeava **qualquer** versão nativa com o sufixo `- Para assinatura.pdf`:

```
Ana … - Para assinatura.pdf     ← prévia
Ana … - Para assinatura.pdf     ← documento concluído
```

Na pasta de downloads de um iPhone os dois são o mesmo item. O painel
"Documentos do exame" oferecia a prévia sob o título "Laudo médico SoproLife",
com um botão "Baixar" indistinguível do da versão final.

### 2.3 O retorno assinado aceitaria a prévia

`upload_external_signature_batch` pareia o PDF que volta pelo metadado
carimbado, pelo código LAU impresso ou pelo código de verificação impresso.
**A prévia carrega o mesmo código LAU impresso do documento final.** Enquanto
o laudo está em elaboração o upload é barrado por outro motivo ("este laudo
não está aguardando assinatura"); depois de concluído, o pareamento por código
acertaria o documento e o sistema gravaria a **prévia assinada** como o PDF
assinado daquele laudo — com `source_version_id` apontando para a versão
liberada que nunca foi assinada.

Era esse o buraco de verdade.

## 3. O que aconteceu com o PDF assinado — PENDENTE (A ou B)

A pergunta obrigatória da missão — *a prévia assinada ficou fora do sistema (A)
ou voltou ao Centro de Comando (B)?* — **não pôde ser respondida nesta sessão**:
ela exige leitura da base de produção.

O que está pronto para respondê-la em um comando, sem escrever nada e sem
imprimir PII:

```bash
# na VPS, depois do SSH autenticado
cd /opt/soprolife/soprolife-site/painel-soprolife/nucleo-m15
.venv/bin/python scripts/auditar_caso_laudo.py LAU-000013
```

Ele imprime, entre outras coisas, a seção decisiva:

```
=== PDF ASSINADO EXTERNAMENTE — CHEGOU AO SISTEMA? ===
  NÃO. Nenhum arquivo assinado foi recebido para este laudo.
```

ou, se tiver chegado, uma linha por arquivo com `status`, `match_method`,
`sha256`, o lote e — o campo que decide o incidente — **`origem era PRÉVIA?`**.

Conduta já definida para cada desfecho, conforme a missão:

* **A (não voltou):** nenhum registro é criado. Nada a corrigir na base.
* **B (voltou):** o registro é **preservado** como evidência e marcado como
  não entregável/rejeitado. Nenhum `DELETE`, nenhuma reescrita de histórico. O
  ajuste seria apresentado antes como *dry-run*, com backup datado, e só depois
  aplicado.

Nenhuma escrita foi feita em produção nesta sessão.

## 4. Fluxo antigo × fluxo novo

```
ANTES                                  DEPOIS
─────────────────────────────────      ─────────────────────────────────
[Gerar prévia do laudo]                [Concluir e preparar para assinatura]
        ↓                                        ↓
   (PDF de prévia na tela,             "Concluir este laudo?"
    baixável como                       Confira as conclusões antes
    "- Para assinatura.pdf")            de continuar.
        ↓                               Depois de concluir, o laudo será
[Concluir laudo]                        registrado como versão final e
        ↓                               ficará pronto para baixar e assinar.
"Confirmar conclusão do laudo"
 (4 marcadores)                        [Voltar e revisar] [Concluir laudo]
        ↓                                        ↓
[Sim, concluir laudo]                  ✓ Laudo concluído
        ↓                              "Agora baixe o PDF final para
   laudo concluído                      assinatura."
   (sem próximo passo na tela)
                                       [ Baixar PDF para assinar ]
```

**Confirmações antes:** 1 diálogo, alcançado após 2 cliques deliberados.
**Confirmações depois:** 1 diálogo, alcançado no primeiro clique. Provado por
teste (`confirmacoes_na_tela == 1` em todas as larguras) e por contrato de
tela (`data-report-release-confirm` aparece exatamente duas vezes no arquivo:
uma que desenha, uma que trata).

"Só conferir a prévia" continua existindo como botão secundário, contornado,
sem competir com o principal — e o bloco resultante diz, em texto, que aquele
arquivo não deve ser assinado.

## 5. Trava no frontend

`painel-soprolife/js/report-workflow.js`

* a ação principal (`submit` do formulário) chama
  `previewNativeReport({ concluir: true })`: gera a prévia e abre a
  confirmação única, sem passo intermediário;
* reabrir a confirmação **regenera** a prévia antes de perguntar. Se a médica
  editou o texto depois de conferir, a prévia antiga já não corresponde ao que
  ela está confirmando — e o servidor responderia "conteúdo divergente da
  prévia", um erro que ela não tem como interpretar nem corrigir;
* estado `liberado` renderiza `✓ Laudo concluído` + **Baixar PDF para assinar**
  (`data-report-download-final`), que é o único passo que resta;
* o painel "Documentos do exame" rotula a prévia como prévia — título
  "(prévia)", descrição "não assine este arquivo", botão "Baixar prévia",
  faixa lateral âmbar — usando o campo `previa` que o servidor passou a
  devolver. A tela não precisa conhecer `kind` para saber o que é assinável.

Vocabulário na tela da médica: "Em elaboração", "Concluir e preparar para
assinatura", "Laudo concluído", "Baixar PDF para assinar", "Aguardando
assinatura", "Enviar PDF assinado". Sem `release`, `version`, `batch`, `kind`,
`qualified_signature` — verificado por teste.

## 6. Trava no backend (download)

`POST /laudos/assinatura-externa/baixar`

```
409  laudo_ainda_em_previa
     "Este laudo ainda é uma prévia. Conclua o laudo antes de baixar para
      assinatura."
```

* a verificação roda **antes** de qualquer byte ser lido, sobre cada
  `document_id` PEDIDO, e para o pedido inteiro. Antes, um id não elegível era
  descartado em silêncio: se sobrasse um concluído na seleção, o download
  acontecia como se nada faltasse;
* `_e_previa()` olha as duas coisas — `status != liberado` **ou** versão
  corrente de tipo prévia. Basta uma;
* laudo de outra médica continua invisível: ids alheios são ignorados sem
  resposta, para o endpoint não virar oráculo de existência;
* a resposta é `application/json`. **Nenhum byte de PDF sai.**

O download de conferência continua permitido — com o nome
`<Nome> - PREVIA - NAO ASSINAR.pdf`.

## 7. Trava no retorno do PDF assinado

`POST /laudos/assinatura-externa/enviar`

```
recusado
"O arquivo assinado corresponde a uma PRÉVIA não concluída.
 Conclua o laudo e assine o PDF final."
```

A checagem roda **antes de gravar qualquer coisa** e vale mesmo quando o
pareamento deu certo. Duas leituras independentes, porque cada uma falha de um
jeito diferente:

1. **metadado** `/SoproLifeDocumentState` — o PDF de prévia passa a sair
   carimbado como prévia, e o concluído como concluído;
2. **tarja impressa** — texto normalizado (sem acento, espaços colapsados)
   contendo `DOCUMENTO NAO CONCLUIDO` ou `NAO ASSINAR`.

O carimbo de **conclusão tem precedência**: se o metadado diz "concluído", a
busca no texto nem roda. Sem isso, uma médica que escrevesse "não assinar"
dentro da conclusão clínica veria o próprio laudo final recusado.

Uma assinatura criptográfica válida não altera nada disso: o que está errado
não é a assinatura, é a folha que foi assinada.

**ICP-Brasil:** nada mudou. Receber continua não sendo validar,
`qualified_signature` continua `false`, nenhum comportamento da M25.8 foi
ressuscitado.

## 8. O PDF de prévia

`app/services/report_native_pdf.py`

* a tarja do topo deixou de descrever apenas o estado e passou a instruir:

  > PRÉVIA — DOCUMENTO NÃO CONCLUÍDO. **NÃO ASSINAR** — conclua o laudo no
  > Centro de Comando antes de baixar o documento para assinatura.

* o aviso de prévia deixou de ser `elif` do aviso de piloto. Eram
  alternativos: religar uma faixa de piloto apagaria a única marca de topo que
  diz para não assinar o documento.

O laudo **concluído** não mudou: mesma tarja "DOCUMENTO LIBERADO", mesmo selo,
mesma rubrica, mesmo código de verificação, mesma declaração impressa sobre
ICP-Brasil.

## 9. MIR

Intocado, como exige a missão: não é fundido, não é assinado por cima, não é
regenerado, continua com download próprio e nome próprio ("Exame técnico").
Provado por teste que compara **byte a byte** o PDF devolvido com o que foi
enviado, depois da conclusão do laudo.

## 10. Testes

`painel-soprolife/nucleo-m15/tests/test_m25_29d_fluxo_conclusao_assinatura.py`
— **25 testes, todos verdes**, fixtures 100% sintéticas.

| # | item da missão | teste |
| --- | --- | --- |
| 1 | prévia não baixa pelo endpoint de assinatura | `test_previa_nao_pode_ser_baixada_para_assinatura` |
| 1 | e não passa escondida num lote | `test_previa_nao_contamina_um_lote_com_laudo_concluido` |
| 2 | filename `PREVIA - NAO ASSINAR` | `test_previa_baixa_com_nome_inequivoco` |
| 2 | tarja + carimbo de prévia | `test_previa_imprime_e_carimba_que_nao_deve_ser_assinada` |
| — | e o concluído nunca é lido como prévia | `test_laudo_concluido_nunca_parece_previa` |
| 3 | uma única confirmação | `test_uma_unica_confirmacao_de_conclusao` |
| 3 | ação principal leva direto a ela | `test_acao_principal_leva_direto_a_confirmacao` |
| 4 | cancelar mantém o rascunho | `test_cancelar_a_confirmacao_preserva_o_rascunho` |
| 5,6 | confirmar gera versão final aguardando assinatura | `test_confirmar_gera_versao_final_aguardando_assinatura` |
| 7,8 | versão final baixa, com o nome certo | `test_versao_final_baixa_com_o_nome_de_assinatura` |
| 9 | assinado da versão final entra no fluxo | `test_assinado_da_versao_final_entra_no_fluxo` |
| 10 | assinado de prévia é rejeitado | `test_assinado_de_previa_e_rejeitado` |
| 10 | e a recusa é contável na auditoria | `test_recusa_por_previa_e_contada_na_auditoria` |
| 10 | segunda camada: tarja sem metadado | `test_upload_de_previa_nao_vira_documento_final_mesmo_sem_metadado` |
| 11 | MIR separado e intacto | `test_mir_permanece_separado_e_intacto` |
| 12 | conclusão clínica não muda | `test_conclusao_clinica_nao_muda_no_caminho` |
| 13 | histórico imutável | `test_historico_permanece_imutavel` |
| 14 | médica sem financeiro/admin | `test_medica_continua_sem_financeiro_e_sem_admin` |
| 15 | administrador funcional | `test_administrador_continua_funcional` |
| 16 | mobile/iPhone | `test_mobile_iphone_empilha_os_botoes_da_conclusao` |
| 16 | área de toque | `test_botoes_tem_area_de_toque_adequada` |
| 17 | desktop | medições reais, seção 11 |
| 18 | sem PII na auditoria automatizada | `test_auditoria_do_caso_descreve_sem_vazar_pii` |
| — | tela concluída mostra o próximo passo | `test_tela_de_conclusao_mostra_o_proximo_passo` |
| — | sem vocabulário interno na tela | `test_tela_medica_nao_usa_vocabulario_interno` |
| — | documentos dizem o que é prévia | `test_documentos_dizem_o_que_e_previa` |

### Suíte completa

```
1319 passed, 13 failed, 30 skipped   (suíte inteira, 29min)
```

**Nenhuma das 13 falhas foi causada por esta etapa** — todas reproduzem no
HEAD de partida:

Falhas pré-existentes, **não** causadas por esta etapa:

* `test_live_multisheet_reader.py` (12) — `ModuleNotFoundError: googleapiclient`.
  Dependência ausente do `requirements.lock` (já registrada como limitação
  conhecida do ambiente local).
* `test_m25_17_operacao_limpa.py::test_rubrica_real_nao_esta_versionada` —
  falso positivo de nome: o teste procura imagem com "assinatura" no nome e
  encontra `docs/m25-21/selo-pre-assinatura.png` e
  `laudo-pre-assinatura-completo.png`. **Ambas conferidas visualmente nesta
  sessão**: são o selo institucional "CONCLUÍDO PELA MÉDICA" e um laudo com
  dados sintéticos ("TESTE APAGAR Paciente M25.21"). Nenhuma rubrica manuscrita
  real está versionada. É defeito do teste da M25.17, fora do escopo desta
  missão.

Um teste de contrato de tela da M25.18 (`test_ui_fala_em_concluir_e_nao_em_assinar`)
foi **atualizado**: ele fixava os textos exatos do diálogo antigo, que esta
etapa substituiu por decisão da missão. O que ele trava — a tela fala em
CONCLUIR, e a linguagem de assinatura não volta como rótulo de botão —
continua sendo verificado.

## 11. Mobile / iPhone / desktop — medido, não estimado

Chrome headless via CDP sobre o **CSS e o JS reais** do painel, com dados
fictícios (`nucleo-m15/tests/visual/`). Larguras: **430, 768, 1024, 1366, 1440,
1920**. Cenários novos: `d-confirmacao-unica` (a confirmação aberta) e
`e-laudo-concluido` (o botão de baixar para assinar).

| medida | resultado em TODAS as larguras |
| --- | --- |
| `overflow_horizontal` | `false` |
| `botoes_sobrepostos` | `false` |
| `botoes_dentro_da_viewport` | `true` |
| `confirmacoes_na_tela` | `1` no cenário de confirmação, `0` nos demais |
| `tem_undefined` / `tem_nan` | `false` |

Alturas em 430px: CTA principal 52px, "Só conferir a prévia" 52px, botões da
confirmação 48px cada, "Baixar PDF para assinar" 56px — todos com largura
total (324/320px) e empilhados.

**Dois defeitos reais só apareceram nesta medição** e foram corrigidos:

1. o CTA principal saía com **260px de altura** no iPhone — `flex: 1 1 260px`
   é largura enquanto a linha é horizontal, mas o mesmo 260px vira **altura**
   quando ela empilha;
2. os dois botões da confirmação tinham **33px** de alvo de toque, herdados do
   `.m15-btn` genérico: a decisão mais consequente da tela em duas faixas finas
   coladas uma na outra.

Também foi corrigida a hierarquia visual: `.m15-btn` nasce navy sólido, então
"principal" e "secundário" saíam com peso idêntico — dois botões escuros lado a
lado, que é a forma visual da própria dúvida que esta etapa remove. Os
secundários ("Só conferir a prévia", "Voltar e revisar") viraram contornados.

Evidências versionadas em `painel-soprolife/docs/m25-29d/` (todas com dados
fictícios do harness): `confirmacao-unica-430.png`,
`confirmacao-unica-1366.png`, `laudo-concluido-430.png`, `bancada-430.png`,
`medidas.json`.

O harness é copiado para o painel na hora de rodar e **removido depois** —
verificado ao fim da sessão: `harness.html` e `_harness_exemplo.pdf` não
existem na árvore, e nenhum processo de servidor/Chrome ficou órfão.

## 12. Estado de LAU-000013 — PENDENTE

Nada foi alterado. Nenhum backup foi feito porque nenhuma escrita foi
proposta ainda: a decisão sobre o que ajustar depende do resultado da
auditoria da seção 3.

Expectativa, a confirmar com a auditoria: o laudo está **em elaboração** com a
conclusão clínica já digitada, preservada no snapshot da última prévia. Nesse
caso **nada precisa ser escrito na base** — o fluxo novo, depois do deploy,
leva a Dra. Ana de onde ela está até o PDF final sem que ela reinterprete o
exame.

Se a auditoria mostrar outra coisa (por exemplo, uma prévia assinada já
recebida e classificada como final), o ajuste virá com *dry-run* primeiro,
backup datado antes, mudança mínima, versões antigas preservadas e registro de
manutenção — como manda a missão.

## 13. Deploy — PENDENTE

Nada foi implantado. Nenhuma migration é necessária: **nenhuma mudança de
schema** nesta etapa (Alembic head local `a2f6c81d4b73`, inalterado).

Sequência preparada, a executar quando o SSH estiver autenticado:

```bash
# 1. local
git push origin claude-m25-29d-fluxo-laudo-assinatura
git checkout painel-soprolife-v01 && git pull --ff-only
git merge --ff-only claude-m25-29d-fluxo-laudo-assinatura
git push origin painel-soprolife-v01

# 2. VPS — Fase 0 primeiro (read-only)
git -C /opt/soprolife/soprolife-site status --short
git -C /opt/soprolife/soprolife-site rev-parse HEAD
git -C /opt/soprolife/soprolife-site fetch
git -C /opt/soprolife/soprolife-site log HEAD..origin/painel-soprolife-v01 --oneline

# 3. auditoria do caso real, ANTES de qualquer escrita
cd /opt/soprolife/soprolife-site/painel-soprolife/nucleo-m15
.venv/bin/python scripts/auditar_caso_laudo.py LAU-000013

# 4. deploy
git -C /opt/soprolife/soprolife-site pull --ff-only
systemctl restart soprolife-m15-api.service
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:<PAINEL_PORT>/api/v1/health
```

Sem `reset --hard`, sem force push, sem `force-with-lease`.

## 14. Não-regressão

| garantia | como fica |
| --- | --- |
| M25.23 — gate de autenticação | intacto; teste próprio nesta etapa (`test_medica_continua_sem_financeiro_e_sem_admin`) |
| Dra. Ana vê só laudos de espirometria | inalterado — nenhuma rota de navegação foi tocada |
| M25.27 — área médica | inalterada; `boot()` e o manifesto não foram tocados |
| M25.28 / M25.29C — timer e snapshots | **não tocados** (a verificar na VPS na Fase 0) |
| Financeiro | não alterado |
| Pastore | nenhum fechamento tocado |
| CRM | não alterado |
| Migrations | nenhuma |

A única mudança fora do domínio de laudos é uma chave nova na allowlist de
auditoria (`recusadas_por_previa`), um inteiro — o guarda de PII da M25.28
continua descartando tudo que não está na lista.

## 15. Instrução para a Dra. Ana (depois do deploy)

> **Abra LAU-000013 em "Meus laudos".**
> A conclusão que a senhora já escreveu estará lá.
>
> 1. Confira o texto na tela.
> 2. Clique no botão azul **"Concluir e preparar para assinatura"**.
> 3. Vai aparecer **uma única pergunta**: *"Concluir este laudo?"*.
>    Clique em **"Concluir laudo"**. (Se quiser rever antes, clique em
>    "Voltar e revisar" — nada se perde.)
> 4. A tela mostra **✓ Laudo concluído**.
> 5. Clique em **"Baixar PDF para assinar"**.
>    O arquivo se chama **`Ana … - Para assinatura.pdf`**.
> 6. Assine esse arquivo com o seu certificado e envie em
>    **"Assinatura externa"**.
>
> **Se o nome do arquivo tiver "PREVIA - NAO ASSINAR", não assine.** É a
> prévia de conferência — volte e clique em "Concluir e preparar para
> assinatura".
>
> Status depois de concluir: **"Concluído — aguardando assinatura qualificada"**.

---

## Resumo operacional

| item | estado |
| --- | --- |
| Fase 0 local (git, HEAD) | ✅ |
| Fase 0 VPS (HEAD, health, Alembic, timer) | ⛔ bloqueado no SSH |
| Investigação LAU-000013 / ESP-000028 | ⛔ bloqueado no SSH — script pronto |
| Pergunta A ou B (a prévia assinada voltou?) | ⛔ **em aberto** |
| Fluxo de uma confirmação | ✅ |
| Trava frontend | ✅ |
| Trava backend (download) | ✅ |
| Trava no upload do assinado | ✅ |
| PDF de prévia inequívoco | ✅ |
| Filename inequívoco | ✅ |
| MIR separado | ✅ |
| Testes (25 novos) | ✅ |
| Mobile 430 / 768 / 1366 / 1920 | ✅ medido |
| Escrita na base real | ⬜ nenhuma |
| Deploy | ⬜ não executado |

**HEAD final desta branch:** `cb879772552e50ed09ec958f27e6ffcae9f2eb02`
