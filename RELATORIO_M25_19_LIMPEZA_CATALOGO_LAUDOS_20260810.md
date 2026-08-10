# M25.19 — Limpeza da administração de laudos

**Data:** 2026-08-10
**Worktree:** `/home/adeildo/soprolife-worktrees/claude-m25-19-limpeza-admin-laudos`
**Branch:** `claude-m25-19-limpeza-admin-laudos`
**Branch oficial:** `painel-soprolife-v01`

| | |
| --- | --- |
| HEAD inicial | `f548b4a53aa463a868437bd870d8fcba1d852e67` |
| HEAD final | `e31a263dc3a38cf61cdd67ec8cb420154e994593` |
| HEAD final da VPS | `e31a263dc3a38cf61cdd67ec8cb420154e994593` |
| Migration | **nenhuma** — a mudança é HTML/CSS/JS |
| Backup pré-deploy | não aplicável (sem alteração de backend nem de banco) |
| Serviço reiniciado | **nenhum** — ver §8 |
| Produção | **publicado e verificado em 2026-08-10** |

Integração por **fast-forward**. Nenhum `reset --hard`, `force push`,
`force-with-lease` ou remoção de trabalho concorrente.

---

## 1. O problema

A tela de "Laudos de espirometria" exibia, dentro do bloco recolhido
*"Administração restrita — contas médicas e catálogo técnico"*, uma seção
**CATÁLOGO VERSIONADO / Templates clínicos** com um card por template do
servidor — inclusive os seis placeholders carimbados
`PROVISÓRIO — NÃO UTILIZAR EM PRODUÇÃO`:

```
INESPECIFICO_QUALIDADE_PROVISORIO   MISTO_PROVISORIO
NORMAL_PROVISORIO                   OBSTRUTIVO_BD_PROVISORIO
OBSTRUTIVO_PROVISORIO               SUGESTIVO_RESTRITIVO_PROVISORIO
```

Aquilo nunca foi operação. A médica **não escolhe template**: ela conclui
pelas siglas clínicas definitivas da bancada (M25.2). O catálogo de templates
só alimenta o fluxo legado de anotação sobre o PDF da MIR (M24C), que já vive
recolhido em `<details>`.

---

## 2. Arquivos alterados

| Arquivo | + | − |
| --- | --- | --- |
| `painel-soprolife/js/report-workflow.js` | 24 | 116 |
| `painel-soprolife/css/report-workflow.css` | 28 | 27 |
| `painel-soprolife/scripts/test-m24a-report-workflow.js` | 11 | 5 |
| `painel-soprolife/nucleo-m15/tests/test_m25_19_catalogo_fora_da_interface.py` | 230 | 0 |

---

## 3. O que foi removido **visualmente**

| Removido | Onde estava |
| --- | --- |
| Eyebrow "Catálogo versionado" | `renderTemplateAdmin()` |
| Título "Templates clínicos" | `renderTemplateAdmin()` |
| Todos os cards de template, com o carimbo `PROVISÓRIO — NÃO UTILIZAR EM PRODUÇÃO` | `renderTemplateAdmin()` |
| Formulário "Nova revisão de …" (rótulo, tooltip, texto, status, aprovação clínica, revisão ativa, "Criar nova revisão") | `#reportTemplateRevisionForm` |
| Estado `adminTemplates` / `selectedAdminTemplateId` e o seletor `selectedAdminTemplate()` | estado do painel |
| Carga do catálogo administrativo a cada `loadAuthenticatedData()` | uma chamada de rede por carga, para uma tela que não existe mais |
| Handler de clique no card e handler de submit da revisão | delegação de eventos |
| CSS `.report-admin-template*`, `.report-template-revision-form`, `.report-template-state` | `report-workflow.css` |

O resumo do bloco recolhido passou de

> Administração restrita — contas médicas **e catálogo técnico**

para

> **Administração restrita — contas médicas**

### Layout (item 4 da missão)

`.report-admin-shell` era um grid de **duas colunas** (contas médicas |
catálogo). Com um painel só, a segunda coluna viraria um vazio do mesmo
tamanho do bloco restante — exatamente o espaço em branco que a limpeza
deveria eliminar. A regra foi **separada** da lista compartilhada com
`.report-operational-shell` (que continua com duas colunas, intocada) e
passou a uma coluna, com `max-width: 760px` no painel: o formulário de perfil
é estreito por natureza (nome, CRM, UF, RQE) e esticado pela página inteira
parecia mais importante que a fila de laudos.

---

## 4. O que foi preservado **no backend**

Nada foi apagado do banco, do modelo ou da API.

| Preservado | Prova |
| --- | --- |
| Os 6 templates `*_PROVISORIO` no banco, em `draft` e não aprovados | `test_templates_provisorios_continuam_no_banco` |
| `GET /laudos/templates?catalog=admin`, restrito a admin | `test_catalogo_administrativo_continua_restrito_no_servidor` (403 para operacional) |
| `PATCH /laudos/templates/{id}` criando **nova revisão imutável** | `test_nova_revisao_imutavel_continua_possivel_pela_api` (201, `versao+1`, `id` novo) |
| Bloqueio de uso de template provisório (`template_nao_aprovado`) | M24C, inalterado |
| Versionamento, auditoria, renderer, migrations | nenhum arquivo tocado |

Editar o catálogo passou a ser tarefa de **API**, não de tela operacional.
Quem impede o uso de um template provisório é o **servidor** — não um aviso
vermelho na interface da médica.

---

## 5. O que foi preservado **na bancada clínica**

Nenhum arquivo do fluxo clínico foi tocado. Coberto por
`test_conclusoes_da_medica_continuam_intactas`,
`test_catalogo_clinico_de_conclusoes_permanece_completo` e
`test_m25_17_e_m25_18_nao_regridem`:

- PDF MIR à esquerda / laudo à direita (`report-clinical-split`);
- **17 conclusões clínicas** + `PERSONALIZADO` (18 no payload);
- **5 complementos pós-BD**;
- **DVO Leve** (`DVO_LEVE`) presente;
- **RBD+** (`RBD_POSITIVO`) presente;
- edição manual, prévia (`previewNativeReport`), conclusão do laudo;
- rubrica manuscrita (M25.4) — segue **dentro** de contas médicas;
- downloads com o nome do paciente (M25.17/M25.18);
- fila pelo nome do paciente.

---

## 6. Segurança

| Item | Estado |
| --- | --- |
| RBAC | inalterado — `if (can("admin")) blocks.push(renderAdminWorkspace())` |
| Administração restrita | continua recolhida e exclusiva de admin |
| Usuário comum | não vê administração médica |
| Senhas | nenhuma senha é exibida ou aceita nesta tela (asserção no teste) |
| Perfil médico | nenhuma mudança de fluxo de autorização |
| Informação clínica | nenhuma exposta; a remoção só tirou metadado técnico da tela |

A superfície de ataque **diminuiu**: a tela deixou de ter um formulário capaz
de alterar o catálogo clínico versionado.

---

## 7. Testes

### Novo — `test_m25_19_catalogo_fora_da_interface.py` (13 casos)

```
13 passed
```

Três grupos: **a vitrine sumiu** (5), **contas médicas e catálogo continuam de
pé** (5), **a bancada não foi arranhada** (3).

### Regressão da área de laudos

```
tests/test_m24c_medical_workflow.py  tests/test_m25_2_native_report.py
tests/test_m25_17_operacao_limpa.py  tests/test_m25_18_assinatura_externa.py
tests/test_m24a_frontend_contract.py tests/test_m24d_reports_pilot.py
tests/test_m25_12_localizacao_e_catalogo.py tests/test_m25_15_operacao_real.py
→ 234 passed (5m34s)
```

### Quality gate

`bash painel-soprolife/scripts/quality-gate-safe.sh`

| | Antes (HEAD `f548b4a` limpo) | Depois |
| --- | --- | --- |
| `test-m24a-report-workflow (M24A)` | OK | OK |
| `test-marketing credencial durável (M21)` | **FALHOU** | **FALHOU** |
| Total | 1 check com problema | 1 check com problema |

A falha de Marketing é **pré-existente e fora do escopo** — reproduzida no
HEAD limpo, com `git stash`, antes de qualquer alteração desta missão.

### Um check do M24A foi invertido, de propósito

`test-m24a-report-workflow.js` exigia o **contrário** do que a M25.19 pede:

```js
// antes
check("admin sinaliza provisório e cria revisão",
  workflow.includes("PROVISÓRIO — NÃO UTILIZAR EM PRODUÇÃO") && …);
// depois
check("catálogo técnico não polui mais a tela operacional",
  !workflow.includes("PROVISÓRIO — NÃO UTILIZAR EM PRODUÇÃO") && …);
```

Os dois checks vizinhos — que provam que os seis provisórios existem e que o
servidor os bloqueia — **continuam intactos**. A garantia mudou de lugar, não
desapareceu.

---

## 8. Deploy

- **Migration:** nenhuma. `git diff f548b4a..HEAD --name-only -- "*migrations*"`
  na própria VPS devolve **0 arquivos**. Nenhuma alteração de banco, portanto
  **sem backup pré-deploy**.
- **Estado anterior da VPS:** `f548b4a53aa463a868437bd870d8fcba1d852e67`,
  branch `painel-soprolife-v01`, `git status` limpo, três serviços `active` —
  ou seja, ainda no HEAD anterior, como esperado.
- **Integração:** `git fetch` + `git merge --ff-only origin/painel-soprolife-v01`.
  Fast-forward `f548b4a..e31a263`, 5 arquivos. Sem `reset --hard`, sem force.

### Reautenticação do Tailscale

O deploy ficou parado até a aprovação humana da sessão SSH
(`# Tailscale SSH requires an additional check`), o mesmo bloqueio que partiu
o deploy da M25.18 em duas etapas. Concluída a reautenticação, o deploy correu
em uma única etapa.

### Nenhum serviço foi reiniciado — e por quê

`command-center-local-server.py` trata os assets estáticos caindo no
`SimpleHTTPRequestHandler.do_GET()`, que **lê o arquivo do disco a cada
requisição**. Nenhum dos três processos guarda cópia do `report-workflow.js`
ou do `.css` em memória, e a API não teve uma linha alterada. Reiniciar
serviço aqui seria derrubar conexão de produção sem motivo.

A hipótese não ficou no papel: o smoke da §9 foi feito **antes** de qualquer
restart e o painel já respondia com o arquivo novo. Nenhum restart foi
necessário e nenhum foi feito.

### Verificações pós-deploy

| Verificação | Resultado |
| --- | --- |
| HEAD da VPS | `e31a263dc3a38cf61cdd67ec8cb420154e994593` |
| `git status` da VPS | **limpo** |
| Branch da VPS | `painel-soprolife-v01` |
| Alembic | `d1e7b9c34a25 (head)` — **idêntico ao da M25.18** |
| Health | `{"status":"ok","versao":"0.1.0","ambiente":"prod","banco":"ok"}` |
| Banco | **ok** |
| Painel | **HTTP 200** |
| Serviços | `soprolife-m15-api`, `soprolife-painel`, `soprolife-painel-loopback` → `active` |
| Serviços reiniciados | **nenhum** |

---

## 9. Smoke em produção

Feito sobre o **asset realmente servido** pelo painel em produção
(`http://127.0.0.1:8765/painel-soprolife/{js,css}/report-workflow.*`,
126.919 B de JS e 34.332 B de CSS), sem login e **sem tocar em nenhum
paciente real**. Só requisições `GET` de arquivo estático, uma leitura de
contagem no banco e uma consulta ao `git` da VPS.

### Sumiu da tela (esperado: 0 ocorrências)

| String | Ocorrências |
| --- | --- |
| `Catálogo versionado` | **0** |
| `Templates clínicos` | **0** |
| `NÃO UTILIZAR EM PRODUÇÃO` | **0** |
| `_PROVISORIO` (qualquer card provisório) | **0** |
| `reportTemplateRevisionForm` | **0** |
| `report-admin-template` | **0** |
| `catalog=admin` | **0** |
| `contas médicas e catálogo técnico` (resumo antigo) | **0** |

### Continua na tela (esperado: ≥ 1)

| String | Ocorrências |
| --- | --- |
| `Administração restrita — contas médicas` | 1 |
| `Contas médicas` | 2 |
| `reportAdminUser` (seletor de conta existente) | 3 |
| `report-clinical-split` (PDF à esquerda / laudo à direita) | 1 |
| `data-report-conclusion` (siglas clínicas) | 3 |
| `data-report-bd` (complementos pós-BD) | 3 |
| `PERSONALIZADO` | 3 |
| `Concluir laudo` | 3 |
| `report-item-name` (fila pelo nome do paciente) | 5 |

### Layout — sem coluna vazia

O CSS servido em produção:

```css
.report-admin-shell        { grid-template-columns: minmax(0, 1fr); }
.report-admin-shell > .report-panel { max-width: 760px; }
.report-operational-shell  { grid-template-columns: repeat(2, minmax(0, 1fr)); }
```

A administração ficou em **uma** coluna compacta; a área operacional
**continua** com duas, intocada.

### O catálogo continua íntegro no servidor

| Verificação em produção | Resultado |
| --- | --- |
| Templates no banco | `total=6 provisorios=6` — **nada apagado** |
| `catalog: str = Query(default="clinical", pattern="^(clinical\|admin)$")` em `reports.py` | presente |
| Arquivos de backend alterados pelo deploy | **nenhum** |

Uma tentativa de sonda sem token devolveu `401`, mas **isso não prova nada**
sobre a rota: a rota de controle inexistente também devolveu `401`, porque a
autenticação roda antes do roteamento. A prova de preservação é a contagem no
banco e o código inalterado em produção, acima.

### Screenshots

**Não foi possível capturar screenshots**: o notebook não tem Chrome
(`/usr/bin/google-chrome`, exigido por `test-m24a-browser-e2e.js`) nem
`jsdom`. A verificação entregue no lugar é mais forte para o que esta missão
mudou — ela lê o **byte servido em produção**, não uma imagem dele. O que a
tabela acima não cobre é o julgamento estético do resultado; essa parte pede
um olho humano na tela.

---

## 10. Confirmação de não regressão clínica

A bancada da médica **não sofreu regressão**. Nenhum arquivo do caminho
clínico foi alterado — `report_conclusions.py`, `native_report_builder.py`,
`report_native_pdf.py`, `reports.py` e as migrations estão byte a byte iguais
ao HEAD inicial. O diff da missão inteira toca quatro arquivos: dois de
apresentação (`js/`, `css/`) e dois de teste.

As 17 conclusões, o `PERSONALIZADO`, os 5 complementos pós-BD, o **DVO Leve**,
o **RBD+**, a edição manual, a prévia, a conclusão, a rubrica, os downloads
pelo nome do paciente e a fila continuam exatamente como estavam — e agora com
teste que quebra se alguém os remover junto com uma futura limpeza de UX.

---

## 11. Conclusão

A M25.19 está **publicada em produção**. A tela de "Laudos de espirometria"
não exibe mais catálogo técnico: quem abre a administração restrita encontra
**contas médicas** e nada além disso, em uma coluna compacta, sem faixa branca
ao lado.

O que mudou foi **onde** o catálogo mora, não **se** ele existe. Os seis
templates continuam no banco de produção, o endpoint administrativo continua
registrado com o mesmo RBAC, o versionamento imutável continua valendo e o
servidor continua sendo quem recusa um template não aprovado. A médica deixou
de ver metadado técnico que nunca foi decisão dela.

**Ressalvas honestas desta entrega:**

1. **Sem screenshots** — não há navegador neste notebook. A verificação foi
   feita sobre o byte servido em produção; o julgamento visual do resultado
   ainda depende de um olho humano abrindo a tela.
2. **Falha pré-existente no quality gate** — `test-marketing credencial
   durável (M21)` falhava antes desta missão e continua falhando. Reproduzida
   no HEAD limpo `f548b4a` com `git stash`. Fora do escopo, não corrigida.
3. **Editar catálogo virou tarefa de API** — não existe mais tela para criar
   revisão de template. Foi decisão explícita da missão; se algum dia for
   preciso editar o catálogo pela interface, isso volta como tela
   administrativa própria, longe da bancada da médica.
