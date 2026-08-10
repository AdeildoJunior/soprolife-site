# M25.18 — Fluxo real para assinatura qualificada externa

**Data:** 10/08/2026
**Branch oficial:** `painel-soprolife-v01`
**VPS:** `root@soprolife-painel-01` · `/opt/soprolife/soprolife-site`

> Esta missão foi executada em três sessões, interrompidas por desligamentos
> inesperados da máquina. Nenhum trabalho foi perdido ou refeito: a cada
> retomada o estado foi conferido em `git status`, no HEAD da VPS e no
> `alembic current` antes de qualquer ação.

---

## 1. Preflight e commits

| Item | Valor |
| --- | --- |
| HEAD local inicial | `e6c440e1b273f4bc2b9be8906206217e02fc7586` ✅ conforme esperado |
| `origin/painel-soprolife-v01` inicial | `e6c440e` (idêntico) |
| HEAD da VPS inicial | `e6c440e`, branch correta, `git status` limpo |
| Health inicial | `{"status":"ok","banco":"ok"}` |
| `M15_REPORTS_MODE` | `pilot` — **inalterado, como a missão determina** |

| Commit | O que resolve |
| --- | --- |
| `eb50cda` | O arquivo continuava saindo com nome aleatório — as duas causas, remoção do piloto, nova semântica, CPF |
| `094bed4` | A data acima da assinatura ainda dizia "Liberado em" |
| `20a6113` | Relatório desta missão |

Integração por **fast-forward**. Nenhum `reset --hard`, `force push`,
`force-with-lease` ou remoção de worktree.

---

## 2. A causa real do nome aleatório

A M25.17 mandou o `Content-Disposition` correto e **conferiu direto na API**
(`127.0.0.1:8015`). No uso real o arquivo saiu como `UWNAUiEo.pdf`, porque o
problema estava **entre a API e o disco do usuário** — um trecho que aquela
conferência não atravessou. Reproduzido antes de corrigir:

```
DIRETO na API (8015)   → content-disposition: attachment; filename="ANTONIO … - Assinado.pdf"; filename*=UTF-8''…
ATRAVÉS DO PROXY (8765) → (AUSENTE — cabeçalho descartado)
```

### Causa 1 — o proxy do painel descartava o cabeçalho

`command-center-local-server.py` tinha esta allowlist:

```python
r'^(?:inline|attachment); filename="[A-Za-z0-9._-]{1,180}"$'
```

O nome humano tem **espaços** e vem acompanhado de `filename*` (RFC 5987,
para os acentos). Os dois eram recusados, e o proxy então **descartava o
cabeçalho inteiro**. O navegador ficava sem nome nenhum e o Chrome gerava um.

O nome técnico anterior (`laudo-ESP-000017-v3-…`) **passava** — por isso a
regressão só apareceu depois da melhoria, e não antes.

Corrigido mantendo allowlist estrita: espaço, parênteses, vírgula e o
parâmetro estendido entram; aspas, barra, ponto-e-vírgula, CR/LF e tipos
fora de `inline|attachment` continuam bloqueados.

### Causa 2 — o visualizador de PDF baixava de uma object URL

O PDF é exibido num `<iframe>` cujo `src` era `blob:…`. O visualizador
embutido do Chrome tem o **próprio botão de download** — que é o botão à mão
de quem está lendo o laudo — e de um `blob:` não há nome a herdar. **Nenhum
ajuste no botão "Baixar" do painel alcançava esse caminho.**

Com sessão por cookie (o login normal da médica) o iframe se autentica
sozinho e passa a apontar para a própria API, então o visualizador recebe o
`Content-Disposition`. Com token da CLI, que não autentica iframe, o blob
continua sendo o caminho — é modo avançado e a visualização segue funcionando.

---

## 3. O teste que faltava

`scripts/test-m25-18-download-e2e.js` sobe **SQLite + API + proxy + Chrome
real**, faz login por senha (sessão por cookie, como a médica), abre a
bancada e lê o `suggestedFilename` de `Browser.downloadWillBegin` — o nome
que o Chrome escreveria no disco.

```
── Download real: o nome que o Chrome usaria no disco ──
  suggestedFilename = ANTONIO SINTETICO DA SILVA - Exame técnico.pdf
  PASS: download do painel sai com o nome do paciente
  PASS: nome NÃO é gerado pelo navegador (aleatório ou GUID)
```

**O teste foi verificado contra o defeito.** Restaurando temporariamente a
allowlist antiga do proxy, ele acusa:

```
  FAIL: Content-Disposition atravessa o proxy do painel — null
  suggestedFilename = e20a07bb-139a-4fe5-a6c4-19339b250a9c.pdf
  FAIL: download do painel sai com o nome do paciente
```

Um teste que passasse nos dois estados não provaria nada. Entrou no quality
gate como verificação de sintaxe (a execução exige Chrome e portas livres).

---

## 4. Nome do arquivo

| Documento | Nome | Conferido em produção, **através do proxy** |
| --- | --- | --- |
| Laudo (antes da assinatura) | `<Paciente> - Para assinatura.pdf` | `ANTONIO LOPES DA SILVA - Para assinatura.pdf` ✅ |
| Exame técnico MIR | `<Paciente> - Exame técnico.pdf` | ✅ |

"Assinado" descrevia o arquivo errado: a assinatura qualificada acontece
**fora** do sistema. O arquivo que sai daqui é o que a médica leva para
assinar. `- Assinado.pdf` é o nome do arquivo que **volta** depois, e quem o
nomeia é o fluxo externo.

A sanitização da M25.17 continua íntegra: separadores de caminho, caracteres
proibidos no Windows e controles fora; CR/LF impossíveis no cabeçalho;
acentos preservados via `filename*`; fallback pelo código institucional.

---

## 5. Piloto fora, semântica correta no lugar

| Superfície | Antes | Depois |
| --- | --- | --- |
| Faixa no topo da tela | `PILOTO INTERNO — DOCUMENTO NÃO ASSINADO — NÃO LIBERAR AO PACIENTE` | **removida** |
| Faixa no PDF | idem | **removida** (`_pilot_warning()` devolve `None`) |
| Selo do PDF | `ASSINADO ELETRONICAMENTE / LIBERAÇÃO INSTITUCIONAL` | **`CONCLUÍDO PELA MÉDICA / AGUARDANDO ASSINATURA`** |
| Data sobre a assinatura | `Liberado em …` | **`Concluído em …`** |
| Marca d'água da prévia | `PRÉVIA — DOCUMENTO NÃO LIBERADO` | `PRÉVIA — DOCUMENTO NÃO CONCLUÍDO` |
| Botão | `Assinar e liberar laudo` | **`Concluir laudo`** |
| Confirmação | `Confirmar assinatura e liberação` | **`Confirmar conclusão do laudo`** |
| Status na fila | `Liberado — aguardando assinatura qualificada` | **`Concluído — aguardando assinatura qualificada`** |

**Remover a faixa não virou silêncio.** "Assinado" num carimbo redondo é lido
como assinatura, e a distinção fina entre "eletronicamente" e "digitalmente"
não sobrevive à leitura de quem recebe o papel. O rodapé agora diz onde
conferir a assinatura de verdade:

> "Documento concluído pela médica responsável no sistema SoproLife, com
> autenticação individual e ação consciente registradas. Integridade
> verificável pelo código e pelo hash SHA-256 deste laudo. **A autenticidade
> da assinatura digital deve ser verificada no arquivo eletronicamente
> assinado**; esta conclusão não constitui, por si só, assinatura digital
> qualificada ICP-Brasil."

Versionamento, hash, auditoria, usuário, médico, data/hora e imutabilidade do
conteúdo **não mudaram** — só o vocabulário que os descreve.

O rodapé do fluxo legado M24C (anotação sobre o PDF da MIR, dentro de um
`<details>` recolhido, marcado "Fluxo anterior") mantém o texto antigo: é
artefato interno, e alterá-lo exigiria nova versão do template selado com
verificação exata no catálogo. Registrado como pendência.

---

## 6. Rubrica, CRM e conclusão clínica preservados

PDF de teste controlado gerado **em produção**, com a rubrica real e as
credenciais reais, sem gravar nada:

```
faixa PILOTO INTERNO presente?       False
NÃO LIBERAR AO PACIENTE presente?    False
selo CONCLUÍDO / PELA MÉDICA?        True
selo AGUARDANDO / ASSINATURA?        True
afirma ASSINADO DIGITALMENTE?        False
ICP-Brasil aparece N vezes:          1  (só na negativa)
rodapé novo presente?                True
CRM-RJ 52.62307-5?                   True
RQE 58224?                           True
CPF impresso (529.982.247-25)?       True
rubrica embutida?                    True
```

A rubrica **não** virou prova criptográfica: `qualified_signature` continua
`false` e o portão CFM continua contando a assinatura qualificada como
pendência, mesmo com a imagem desenhada (teste dedicado).

---

## 7. CPF do paciente

Novo `app/services/cpf.py` + migration `d1e7b9c34a25` (aditiva, reversível).

| Requisito | Como foi atendido |
| --- | --- |
| Opcional | Nulo permitido. Existe paciente sem CPF aplicável; obrigar produziria CPF inventado para destravar cadastro |
| Validação | 11 dígitos + os **dois verificadores**; sequências repetidas (`111…11`) recusadas |
| Duplicidade | Índice único; NULL não colide com NULL em SQL padrão |
| Fora das filas | `ser_person` devolve só `cpf_mascarado` (`***.982.247-**`) e `tem_cpf` |
| Fora da rota pública | `/laudos/validacao/{codigo}` continua sem qualquer dado de paciente |
| Fora dos logs/auditoria | A allowlist de `app/audit.py` não inclui `cpf`; teste varre `AuditLog` |
| Mascarado nas telas | `mascarar_cpf` |
| Impresso no laudo | Somente lá, formatado; **sem CPF cadastrado a linha não existe** — nunca "não informado" no lugar de um documento de identidade |
| Edição posterior | `PATCH /pessoas/{id}`; string vazia **desvincula** |
| Pacientes existentes | **Nenhum foi alterado**; a coluna nasce nula |

Armazenado só em dígitos — máscara é apresentação, e guardar as duas formas
tornaria a unicidade inútil.

---

## 8. Testes

**Suíte completa: 1098 passaram, 30 puladas.** Quality gate: **todos os
checks OK**. E2E de download no navegador: **OK**.

Novo `tests/test_m25_18_assinatura_externa.py` — **34 testes**:

| Grupo | Cobertura |
| --- | --- |
| Nome do arquivo | proxy deixa passar o nome humano; 5 casos de injeção continuam recusados; iframe não usa `blob:`; `apiUrl`/`hasSession`; frontend usa o nome do servidor; laudo é "Para assinatura"; MIR é "Exame técnico"; download real; cabeçalho atravessa o proxy |
| Piloto/semântica | UI sem a faixa; `Concluir laudo`; status; PDF sem `PILOTO INTERNO`; selo declara conclusão; `Concluído em`; rodapé novo; ICP-Brasil só na negativa; rubrica + CRM + RQE preservados; rubrica não vale como assinatura |
| CPF | 3 formatos válidos; 4 inválidos; 3 formas de ausência; opcional no cadastro; preenchimento e remoção posteriores; não vaza em fila/busca/rota pública; não entra em auditoria; impresso no laudo; máscara |

### Testes atualizados por mudança de política

| Arquivo | Motivo |
| --- | --- |
| `test_m25_2_native_report.py` | Selo mudou de `LIBERAÇÃO INSTITUCIONAL` para `CONCLUÍDO / PELA MÉDICA` |
| `test_m25_15_operacao_real.py` | Rótulo de status virou "Concluído" |
| `test_m25_17_operacao_limpa.py` | Sufixo virou "Para assinatura"; e o teste da rubrica versionada acusava **o próprio relatório da M25.17**, cujo nome de arquivo contém "rubrica" — falso positivo corrigido para procurar só arquivos de IMAGEM |
| `test_api_people.py` | Usava `cpf` como exemplo de campo inexistente; trocado por um que continua não existindo, e a validação do CPF ganhou teste próprio |
| `test_migrations.py` | Head esperada → `d1e7b9c34a25` |
| `scripts/test-m24a-report-workflow.js` | Faixa de piloto e revogação condicional de object URL |

---

## 9. Backup

| Item | Valor |
| --- | --- |
| Diretório | `/opt/soprolife/backups/m25-18/20260810T040532Z` |
| HEAD anterior | `e6c440e1b273f4bc2b9be8906206217e02fc7586` |
| Dump PostgreSQL | `m15.dump` (264 K) — **validado**: `pg_restore --list` → 376 entradas; `people`, `report_documents` e `physician_signature_assets` presentes |
| Env | `m15.env.bak`, modo `600` — **nenhum segredo impresso** |
| Assets privados | `private-reports.tar.gz` (1,6 M) — inclui a rubrica da Dra. Ana |

---

## 10. Deploy

`git fetch` + `git merge --ff-only`. Migration `c9d3a17f4b60 → d1e7b9c34a25`.

**Três serviços reiniciados**, e não só a API: o arquivo do proxy
(`command-center-local-server.py`) é executado tanto por
`soprolife-painel-loopback` quanto por `soprolife-painel`. Reiniciar apenas a
API deixaria a correção do `Content-Disposition` inativa — o defeito
continuaria em produção com o código já atualizado no disco.

| Verificação | Resultado |
| --- | --- |
| HEAD da VPS | `20a6113e7d1b1025b73f9758b74a4470c835773d` |
| `git status` da VPS | limpo |
| Alembic | `d1e7b9c34a25 (head)` |
| Health | `{"status":"ok","ambiente":"prod","banco":"ok"}` |
| Banco | **ok** |
| Painel | HTTP 200 |
| Serviços | `soprolife-m15-api`, `soprolife-painel`, `soprolife-painel-loopback` → `active` |
| `M15_REPORTS_MODE` | **`pilot`** — não alterado |

### Deploy em duas etapas

O deploy foi feito em duas etapas porque o acesso SSH via Tailscale passou a
exigir reautenticação humana no meio da última sessão:

| Etapa | Commits | Serviços reiniciados |
| --- | --- | --- |
| 1 | `eb50cda` — a missão inteira, com a migration | API + os **dois** que executam o proxy |
| 2 | `094bed4` + `20a6113` — rótulo do PDF e relatório | somente a API (o diff toca apenas o gerador de PDF) |

Depois da reautenticação, a etapa 2 foi aplicada por fast-forward
(`eb50cda → 20a6113`), sem migration nova — `alembic current` e
`alembic heads` já coincidiam em `d1e7b9c34a25` — e verificada.

---

## 11. Smoke de produção

Sem criar paciente e sem alterar conteúdo clínico.

| Comprovação | Resultado |
| --- | --- |
| Nome do laudo **através do proxy** | `attachment; filename="ANTONIO LOPES DA SILVA - Para assinatura.pdf"; filename*=UTF-8''…` ✅ |
| Nome inline (o que o visualizador recebe) | `inline; filename="ANTONIO LOPES DA SILVA - Para assinatura.pdf"; …` ✅ |
| Cabeçalho sobrevive ao proxy | ✅ — era exatamente onde morria |
| PDF novo sem faixa de piloto | ✅ |
| Rótulo `Concluído em` (e nunca `Liberado em`) | ✅ — conferido após a etapa 2 |
| Selo `CONCLUÍDO / PELA MÉDICA` | ✅ |
| Rodapé aponta o arquivo assinado | ✅ |
| Rubrica, CRM-RJ 52.62307-5, RQE 58224 | ✅ |
| CPF impresso quando existe | ✅ |
| ICP-Brasil só na negativa | ✅ (1 ocorrência) |

### LAU-000003 / ANTONIO LOPES DA SILVA — intacto

Reconferido ao final do deploy: `status=liberado`, `v3`,
`sha256 526e46b044ddd80a…`, conclusão `Distúrbio ventilatório obstrutivo
leve.` — os mesmos valores de antes da missão.

**Nenhuma versão foi substituída.** O PDF já liberado permanece como estava;
as mudanças de layout e semântica valem para **novos** documentos, conforme a
seção 12 da missão. Nada foi regenerado retroativamente, e nenhuma conclusão
clínica foi tocada.

### Screenshot

`scratchpad/m2518/faixa.png` — bloco de assinatura do PDF de produção com o
selo novo, a rubrica e o rodapé. **Não commitado**: contém a imagem da
rubrica, ativo privado da médica.

---

## 12. Pendências

1. **Assinatura qualificada ICP-Brasil** — continua fora do sistema, por
   decisão desta missão. É o que mantém `reports_mode=pilot`.
2. **Anexar PDF assinado externamente** (seção 10 da missão, explicitamente
   opcional) — **não implementado**, para não bloquear o hotfix do nome do
   arquivo, como a própria missão autoriza. A infraestrutura mais próxima já
   existe (`/laudos/lote/enviar`, da M25.8, com SHA-256, versão separada,
   auditoria e validação de PDF); o que falta é o caminho de documento único
   e o estado "PDF assinado externamente recebido — validação da assinatura
   pendente". Próxima etapa natural.
3. **Rodapé do fluxo legado M24C** ainda contém "PILOTO INTERNO" (seção 5).
4. **CPF dos pacientes existentes** — nenhum foi preenchido
   automaticamente; é trabalho humano de cadastro. O portão CFM continua
   contando a ausência como pendência por documento.
5. **12 exames sem local registrado** e **`test_finance_duplicate_revenue_postgres.py`**
   — pendências herdadas das M25.15/M25.17, sem mudança.

---

## Conclusão

**M25.18 — FLUXO OPERACIONAL REAL PARA ASSINATURA QUALIFICADA EXTERNA IMPLANTADO**
