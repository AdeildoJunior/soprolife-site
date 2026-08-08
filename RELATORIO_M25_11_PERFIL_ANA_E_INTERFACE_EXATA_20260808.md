# Relatório M25.11 — Perfil da Dra. Ana e interface de laudos

Data: 2026-08-08

---

# PARTE 1 — PERFIL DA DRA. ANA

## 1. Causa da falha

**Não era falta de persistência. Era erro 422 a cada tentativa.**

A função `savePhysician`, em `report-workflow.js`, montava o payload assim:

```js
verification_status: form.elements.verification_status.value,
active: form.elements.active.checked,
```

E **nunca enviava `verification_reference`**. Mas o router sempre exigiu esse
campo para aceitar `verified`:

```python
if requested_verification == "verified":
    reference = (values.get("verification_reference") or "").strip()
    if len(reference) < 4:
        raise ReportDomainError(422, "referencia_verificacao_obrigatoria", ...)
```

O formulário **não tinha esse campo em lugar nenhum**. Ou seja: era
impossível verificar qualquer médica pela interface.

O erro aparecia num *toast* que some em segundos. A leitura natural de quem
salva e vê "Pendente" de volta é "não persistiu" — mas o servidor tinha
recusado.

### Reproduzido antes de corrigir

Enviando o payload **exato** que a interface produzia:

```
{"erro":{"codigo":"referencia_verificacao_obrigatoria",
         "mensagem":"Verificação exige uma referência técnica segura."}}
```

Com a referência incluída: `active: True | status: verified` — e persistindo
após reler.

## 2. Correção aplicada

| Arquivo | Correção |
| --- | --- |
| `js/report-workflow.js` | campo **Referência da verificação** no formulário, com ajuda explicando que é obrigatório |
| `js/report-workflow.js` | `verification_reference` passou a ir no payload |
| `js/report-workflow.js` | erro agora fica **fixo na tela**, além do toast |
| `app/serializers.py` | `ser_physician_profile` devolve `verification_reference` |

O serializer importava: sem devolver o campo, ele voltava vazio a cada
recarga e o operador reenviava sem ele — reproduzindo o mesmo erro.

**Nenhuma regra de segurança foi afrouxada.** A exigência da referência
continua; o que faltava era a interface conseguir cumpri-la.

## 3. Estado final real no banco (produção)

```
status=verified  ativo=True  crm=5262307-5  rqe=58224
especialidade=Médica Pneumologista
papel medico: 1
referência: CREMERJ-BUSCA-PUBLICA-20260808-CRM5262307-5
```

Ativação registrada na trilha de auditoria
(`perfil_medico_verificado_e_ativado`, 1 registro), com a referência e o
admin responsável.

**A senha não foi tocada, não foi redefinida e não consta deste relatório.**

## 4. Confirmação do login médico

A mensagem *"O perfil médico está inativo ou não verificado"* vinha de
`_require_active_physician`, que exige `active=true` **e**
`verification_status='verified'`. As duas condições agora estão satisfeitas
em produção, então a fila e as ações clínicas passam a aparecer para ela.

**Não pude fazer o login como ela** — não tenho a senha, e não vou pedir nem
redefinir. Dois testes automatizados cobrem o comportamento: o perfil
verificado persiste, e a médica com perfil ativo acessa a própria fila.

## 5. Correção visual da assinatura manuscrita

`.report-check` usava `align-items: center`. Com o texto da confirmação
quebrando em três linhas, a caixa descia para o meio do parágrafo e encostava
nas palavras. Passou a `flex-start`, com a caixa fixada na primeira linha.

Corrigido também o texto de ajuda, que anunciava proporção mínima **0,8:1** —
valor que virou **0,25:1** na M25.5, quando a assinatura real da Dra. Ana
(0,42) foi recusada pelo limite antigo.

---

# PARTE 2 — A INTERFACE

## 6. Onde a interface aprovada foi encontrada

**No próprio HEAD. Ela nunca foi perdida, substituída ou revertida.**

Fiz a busca pedida por todas as expressões, na árvore atual:

| Expressão | Onde está |
| --- | --- |
| `DVO Leve` | `app/services/report_conclusions.py` |
| `RBD+` | `app/services/report_conclusions.py` |
| `Distúrbio ventilatório obstrutivo leve` | `app/services/report_conclusions.py` |
| `Com resposta significativa ao broncodilatador` | `app/services/report_conclusions.py` |
| `PERSONALIZADO` | catálogo **e** `js/report-workflow.js` |
| chips de sigla | `report-conclusion-chip`, `report-bd-chip` no JS |

Não foi preciso vasculhar reflog, stash, worktree, blobs órfãos nem
históricos de sessão: **os arquivos estão versionados e publicados**. Uma
busca em `git log --all` só encontraria as mesmas linhas que já estão no
HEAD.

## 7. Diferenças entre `9ba5a58` e o publicado

| | `9ba5a58` (M25.3) | Publicado (`2977fdd`) |
| --- | --- | --- |
| JS | 71.111 bytes | ~101.000 bytes |
| Catálogo de 17 + PERSONALIZADO | sim | **sim** |
| Chips de sigla e complementos | sim | **sim** |
| Assinatura manuscrita | não | sim (M25.4) |
| Fila por unidade | não | sim (M25.6) |
| VIDaaS | não | sim (M25.7) |
| Lote externo | não | sim (M25.8) |

**O publicado é superconjunto da M25.3.** Voltar para `9ba5a58` apagaria
cinco etapas — por isso não foi feito, como você mesmo pediu.

## 8. As 17 conclusões + PERSONALIZADO

```
NORMAL
DVO_LEVE  DVO_MODERADO  DVO_MOD_GRAVE  DVO_GRAVE  DVO_MUITO_GRAVE
DVR_SUG_LEVE  DVR_SUG_MODERADO  DVR_SUG_MOD_GRAVE  DVR_SUG_GRAVE  DVR_SUG_MUITO_GRAVE
DVM_SUG_LEVE  DVM_SUG_MODERADO  DVM_SUG_MOD_GRAVE  DVM_SUG_GRAVE  DVM_SUG_MUITO_GRAVE
PERSONALIZADO
```

Teste `test_catalogo_tem_o_conjunto_fechado…` trava o conjunto em 18 itens
(17 + PERSONALIZADO), com `NORMAL` primeiro e `PERSONALIZADO` último.

## 9. Os 5 complementos pós-broncodilatador

`RBD+`, `RBD−`, `REV completa`, `REV parcial`, `BD não realizado` —
em `BRONCHODILATOR_BY_CODE`. Os incompatíveis não são oferecidos quando o
exame não tem fase pós-BD.

## 10. Sobre os cartões vermelhos "PROVISÓRIO"

São reais: seis templates em `draft`, zero aprovados. Mas **são legado da
M24 e não bloqueiam o laudo** — o fluxo nativo usa o catálogo de conclusões,
não os templates.

**Não os marquei como aprovados.** Fazer isso esconderia o problema e
afirmaria uma aprovação clínica que não houve. Na M25.10 a área
administrativa passou a vir **recolhida**, então eles não competem mais com
o fluxo clínico.

## 11. Por que a fila não aparecia na sua conta

`if (explicit("medico")) blocks.push(renderPhysicianWorkspace());`

`explicit` exige o papel **literal**. Sua conta tem só `admin`, e o
`security.py` declara que *"admin deliberadamente NÃO implica medico"*. Na
M25.10 a tela passou a **dizer isso** em vez de simplesmente não mostrar
nada.

---

# ENTREGA

## 12. Testes

| Suíte | Resultado |
| --- | --- |
| Regressão do perfil (2 testes novos) | passaram |
| Módulo do laudo nativo | 45 passaram |
| Proxy do Command Center | 46 passaram |
| Suíte JS do painel | todos os casos |
| `node --check` | limpo |

## 13. Backup

```
/opt/soprolife/backups/m25-11/20260808T235228Z/m15.dump
```

Verificado: **45 tabelas com dados**. Nenhuma migration nova — schema segue
em `d4a71c88b2e6`.

## 14. Commits e deploy

| | |
| --- | --- |
| Commit criado | `2977fdd` |
| Commit implantado | `2977fdd2e2f8c25f7bf36894656c0e5eb4fffff0` |
| Branches | ambas em `2977fdd`, por fast-forward |
| Cache-bust | `2026080801` → **`2026080802`** |

## 15. Evidência da produção

```
health ......... status ok, ambiente prod, banco ok
servido ........ report-workflow.js?v=2026080802
JS entregue .... IDÊNTICO ao commit publicado
```

Marcadores no arquivo que o navegador recebe:

| Marcador | Presente |
| --- | --- |
| `verification_reference` (4 ocorrências) | sim |
| `Referência da verificação` (campo novo) | sim |
| `report-profile-error` (erro fixo) | sim |
| `0,25:1` (ajuda corrigida) | sim |
| `report-conclusion-chip` (siglas) | sim |
| `report-bd-chip` (complementos) | sim |

**Sobre captura de tela:** não consigo produzir. Não tenho senha de nenhuma
conta de produção e não vou pedir nem redefinir. A evidência acima é do
arquivo efetivamente entregue pelo servidor, que é o que determina a tela.

## 16. URL

```
https://soprolife-painel-01.tailcaf0e4.ts.net/painel-soprolife/
```

## 17. Pendências humanas

1. **Entregar a senha de primeiro acesso** — em
   `/opt/soprolife/secrets/ana-primeiro-acesso.txt` na VPS, por canal seguro.
   Apagar o arquivo depois.
2. **Cadastrar a assinatura manuscrita** dela (PNG, já extraído).
3. **Configurar `M15_REPORTS_VALIDATION_BASE_URL`** — ausente; o laudo sai
   sem QR.
4. **Confirmar o endereço da Pastore Ipanema** — há duas unidades
   cadastradas.
5. **Primeiro laudo com paciente fictício**, antes de qualquer exame real.

## 18. Rollback

```bash
ssh root@100.87.98.100
cd /opt/soprolife/soprolife-site
git checkout painel-soprolife-v01 && git reset --hard 7c890cc
systemctl restart soprolife-m15-api.service
```

Esta etapa não alterou schema. Para desfazer a ativação do perfil, basta
voltar `verification_status='pending'` e `active=false` — ou restaurar o
dump da seção 13.

## 19. Caminho completo do relatório

```
/home/adeildo/soprolife-site/RELATORIO_M25_11_PERFIL_ANA_E_INTERFACE_EXATA_20260808.md
```
