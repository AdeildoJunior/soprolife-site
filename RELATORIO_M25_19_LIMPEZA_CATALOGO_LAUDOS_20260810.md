# M25.19 — Limpeza da administração de laudos

**Data:** 2026-08-10
**Worktree:** `/home/adeildo/soprolife-worktrees/claude-m25-19-limpeza-admin-laudos`
**Branch:** `claude-m25-19-limpeza-admin-laudos`
**Branch oficial:** `painel-soprolife-v01`

| | |
| --- | --- |
| HEAD inicial | `f548b4a53aa463a868437bd870d8fcba1d852e67` |
| HEAD final | `2e11c52` (código) + este relatório |
| Migration | **nenhuma** — a mudança é HTML/CSS/JS |
| Backup pré-deploy | não aplicável (sem alteração de backend nem de banco) |

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

- **Migration:** nenhuma. O diff toca apenas `js/`, `css/`, testes e um script
  de teste — **registrado que não há migration e não há alteração de banco**,
  portanto sem backup pré-deploy.
- **Restart:** somente `soprolife-painel` seria necessário se os assets forem
  servidos por ele; a API não muda de comportamento.

| Etapa | Estado |
| --- | --- |
| Commit `2e11c52` | ✅ |
| Fast-forward `f548b4a..2e11c52 → origin/painel-soprolife-v01` | ✅ |
| Push da branch de trabalho | ✅ |
| Atualização da VPS | ⏳ **bloqueado** |

### Bloqueio: Tailscale SSH pediu reautenticação humana

```
# Tailscale SSH requires an additional check.
# To authenticate, visit: https://login.tailscale.com/a/l131a2af2371f5b
```

Mesmo bloqueio que partiu o deploy da M25.18 em duas etapas. A rede está boa
(`tailscale status` → `soprolife-painel-01 … idle`, ping 70 ms); o que falta é
a aprovação humana da sessão SSH. **Nenhum comando foi executado na VPS.**

<!-- ETAPA 2 — preencher após a reautenticação:
| HEAD da VPS | |
| git status da VPS | |
| Alembic | |
| Health | |
| Banco | |
| Painel | |
| Serviços | |
-->

---

## 9. Smoke em produção

Pendente do deploy. O roteiro, sem tocar em nenhum paciente real:

1. abrir "Laudos de espirometria";
2. rolar até **Administração restrita — contas médicas**;
3. confirmar que **não existe** "Catálogo versionado";
4. confirmar que **não existe** nenhum card `*_PROVISORIO`;
5. confirmar que **"Contas médicas"** continua disponível;
6. confirmar que não sobrou coluna vazia ao lado do painel;
7. confirmar que a bancada (PDF à esquerda, siglas à direita) está intacta.

Verificação objetiva equivalente, que pode ser feita sem login e sem tocar em
dado nenhum: baixar o asset servido e conferir que as strings sumiram.

```
curl -s https://<painel>/painel-soprolife/js/report-workflow.js \
  | grep -c "Catálogo versionado"     # esperado: 0
```

### Screenshots

**Não foi possível capturar screenshots neste notebook**: não há Chrome
(`/usr/bin/google-chrome` ausente, exigido por `test-m24a-browser-e2e.js`) nem
`jsdom` instalado. A verificação equivalente feita aqui foi **estática e
determinística** — as 13 asserções do arquivo novo mais o check do M24A, todas
lendo o `report-workflow.js` e o `report-workflow.css` reais que vão para
produção. A confirmação visual fica para o smoke.

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
