# Relatório M25.8 — Publicação e prontidão final

Data: 2026-08-07

> **O deploy NÃO foi executado.** O código está publicado no GitHub, nas
> duas branches, e verificado. A execução na VPS parou num bloqueio de
> acesso que exige você — está detalhado na seção 6, com os comandos exatos.

---

## 1. Branch e commits publicados

| Etapa | Commit |
| --- | --- |
| M25.5 — laudo enxuto, selos, assinatura | `0b74a66` |
| M25.6 — fila por unidade, tipografia, CRM | `c11a395` |
| M25.7 — VIDaaS/IntegraICP (desligado) | `ccfc79c` |
| M25.8 — núcleo do lote externo | `3c65694` |
| M25.8 — endpoints e interface | **`ecc8ca4`** |

Confirmado: o commit da M25.8 é **`ecc8ca4`**, e é o HEAD local.

Publicado com `git push` em duas branches:

```
codex-m25a-search-console-reconciliation   c11a395..ecc8ca4
painel-soprolife-v01 (produção)            9b8ae96..ecc8ca4   (fast-forward)
```

`git ls-remote` confirma as duas em `ecc8ca42b99d202924d83acfbda17c71cba289bb`.

## 2. Commit final da produção

Branch de produção no GitHub: **`ecc8ca4`**.
Commit rodando na VPS: **ainda o antigo** — nada foi implantado.

### Por que a integração é segura (verificado, não presumido)

- `9b8ae96` é ancestral de `ecc8ca4` → avanço por **fast-forward**, sem merge.
- `ff5b6e5`, `a79b0af` e `0b74a66` — a faixa que a VPS roda hoje — estão
  **todos contidos** em `ecc8ca4`.
- Os commits do trabalho descartado (`c6fdc3d`…`e069e7d`) **não** foram
  restaurados. O que existe neles e não em `ecc8ca4` é apenas
  `RELATORIO_M25_5_PRODUCAO_LAUDO_LOGIN_ANA.md` e
  `tests/test_finance_duplicate_revenue_postgres.py` — um relatório e um
  arquivo de teste. **Nenhum código de produção.**

Logo: implantar `ecc8ca4` não regride nada.

## 3. Backup

| Item | Situação |
| --- | --- |
| Banco local de desenvolvimento | **feito** — cópia em `/tmp/.../scratchpad/m15_antes_m258.db` antes de migrar |
| Banco PostgreSQL da VPS | **NÃO FEITO** — exige acesso à VPS |
| Arquivos persistentes da VPS (raiz privada dos PDFs) | **NÃO FEITO** — exige acesso à VPS |

O backup de produção é o **primeiro passo** do script oficial de deploy
(`deploy-producao-vps.sh` grava em `/opt/soprolife/backups/m15/<STAMP>`), então
ele acontece junto com o deploy, na seção 6.

## 4. Migrations

Revisão na VPS hoje: `a3f1d7c25e90` (M25.2).
Pendentes, nesta ordem:

1. `b6e2f94a17c3` — M25.7, cria `qualified_signature_requests`
2. `d4a71c88b2e6` — M25.8, colunas de rastreio + ajuste de constraint

### Ensaio real em PostgreSQL — feito

Não confiei no SQLite. Subi um PostgreSQL 16 descartável, desci até
**exatamente** a revisão da VPS e apliquei só as pendentes:

```
upgrade a3f1d7c25e90  →  b6e2f94a17c3  →  d4a71c88b2e6   OK
downgrade -1 e upgrade de volta                          OK
```

Conferido no banco depois: a tabela `qualified_signature_requests` existe, as
colunas `signature_prepared_at`/`signature_downloaded_at` existem, e a
constraint ficou com a definição pretendida:

```sql
CHECK (status = 'liberado' OR (released_at IS NULL AND released_by_user_id IS NULL
  AND released_physician_profile_id IS NULL
  AND (validation_code IS NULL OR status = ANY('{assinatura_pendente,assinado}'))))
```

O relaxamento é **mais permissivo** que o anterior, então nenhuma linha
existente pode violá-lo. Não há risco de a migration falhar por dados atuais.

## 5. Serviços e healthchecks (VPS, hoje)

```
painel   HTTP 200
health   {"status":"ok","ambiente":"prod","banco":"ok"}
config   enabled=true, reports_enabled=true, reports_mode=pilot
frontend report-workflow.js?v=2026080503   ← ainda a versão M25.4
```

PostgreSQL responde (`banco: ok`). O `v=2026080503` confirma que o código
novo ainda não subiu — a M25.8 serve `v=2026080603`.

**Nenhum deploy automático foi disparado pelo push.** Verifiquei: a única
tarefa agendada na VPS (`soprolife-update-data.timer`) roda
`update-local-data.sh`, que **não faz nenhuma operação git**. O push para
`painel-soprolife-v01` fica inerte até alguém executar o script de deploy.

## 6. O BLOQUEIO — por que parei antes do deploy

**Não consigo abrir sessão SSH na VPS.**

```
ssh soprolife@soprolife-painel-01.tailcaf0e4.ts.net   → Host key verification failed
ssh soprolife@100.87.98.100                           → trava sem resposta (timeout)
```

Pelo IP a chave do host **é conhecida** (`ssh-keygen -F` encontra), e mesmo
assim a conexão fica pendurada sem sequer negar autenticação. É o Tailscale
SSH em *check mode*, aguardando aprovação humana no navegador. O
`tailscale ping` responde em 28 ms, então não é rede.

Além disso, o próprio script diz na primeira linha: *"Execute no terminal da
própria VPS"*, e exige `sudo` para systemd, venvs e PostgreSQL.

### Comandos exatos para você executar

```bash
ssh soprolife@soprolife-painel-01.tailcaf0e4.ts.net

# 1. Conferir o que está implantado ANTES de mexer
cd /opt/soprolife/soprolife-site
git -C . rev-parse HEAD
sudo -u soprolife /opt/soprolife/venvs/m15/bin/python -m alembic \
  -c painel-soprolife/nucleo-m15/alembic.ini current

# 2. Trazer o código publicado
git fetch origin
git checkout painel-soprolife-v01
git merge --ff-only origin/painel-soprolife-v01
git rev-parse HEAD      # deve ser ecc8ca42b99d202924d83acfbda17c71cba289bb

# 3. Deploy oficial (faz backup, migra, reinicia e valida)
sudo SOPROLIFE_M15_GO_LIVE=YES \
     SOPROLIFE_M15_HTTPS_BASE_URL=https://soprolife-painel-01.tailcaf0e4.ts.net/ \
     SOPROLIFE_REPORTS_PILOT_AUTHORIZATION="HABILITAR PILOTO DE LAUDOS" \
     painel-soprolife/nucleo-m15/scripts/deploy-producao-vps.sh \
     ecc8ca42b99d202924d83acfbda17c71cba289bb \
     painel-soprolife-v01
```

**Obstáculo provável:** se `git fetch` falhar por permissão, é a propriedade
mista do repositório da VPS (arquivos de `root` misturados com os de
`soprolife`, de deploys anteriores rodados como root). **Não faça `chown` em
massa sem revisão** — me mande a saída.

### Conferência depois do deploy

```bash
curl -s https://soprolife-painel-01.tailcaf0e4.ts.net/painel-soprolife/api/m15/health
curl -s https://soprolife-painel-01.tailcaf0e4.ts.net/painel-soprolife/ | grep -o 'report-workflow.js?v=[0-9]*'
#   deve passar a mostrar v=2026080603
sudo journalctl -u soprolife-m15-api.service -n 40 --no-pager
```

## 7. Testes realizados e resultados

### Fumaça do fluxo completo — ambiente LOCAL, paciente fictício

Executado contra a API local com o cenário fictício semeado. **16 de 16
passos, zero falhas:**

| # | Passo | Resultado |
| --- | --- | --- |
| 1 | login médico | OK |
| 2 | fila de laudos | OK — 12 itens |
| 3 | exame disponível | OK — LAU-000009 |
| 4 | abertura do exame | OK |
| 5 | conclusão e prévia | OK |
| 6 | finalizar revisão | OK |
| 7 | estado "aguardando assinatura" | OK |
| 8 | download em lote (ZIP) | OK — 118.376 bytes |
| 9 | manifesto correto | OK |
| 10 | PDF da MIR fora do pacote | OK |
| 11 | instruções não prometem lote | OK |
| 12 | rejeita PDF sem assinatura | OK — `assinatura_ausente` |
| 13 | aceita PDF assinado | OK — `validado_e_liberado` |
| 14 | laudo passa a "assinado" | OK |
| 15 | downloads separados (MIR + laudo) | OK |

**O passo 13 usa uma autoridade certificadora de TESTE, gerada em memória.
Não houve assinatura VIDaaS real** — isso só será possível quando a Dra. Ana
assinar com o certificado dela.

### Suítes automatizadas

| Suíte | Resultado |
| --- | --- |
| Backend completo | **961 passaram**, 22 puladas, 1 falha |
| Módulo do lote (M25.8) | 30 passaram |
| Módulo VIDaaS (M25.7) | 38 passaram |
| Migrations | 11 passaram |
| Proxy do Command Center | 46 passaram |
| Suíte JS do painel | todos os casos |
| `git diff --check` | limpo |

## 8. Falhas existentes

**`test_m24d_pilot_deployment::test_preparacao_mantem_backup_em_terminal_interativo`**
— falha só na execução completa, passa isolada em árvore limpa e com a M25.8.
Usa PTY e testa backup de script de deploy; sem relação com assinatura. Já
falhava antes desta etapa. **Continua em aberto.**

## 9. Situação da conta da Dra. Ana

**Na VPS: desconhecida.** Não consegui entrar para verificar se existe conta,
papel `medico`, perfil ativo e verificado.

**No ambiente local** existe apenas a conta **fictícia** de teste
(`medica.teste@soprolife.local`). Nenhuma senha, certificado ou dado real da
Dra. Ana foi cadastrado ou exposto em lugar nenhum.

### Descoberta operacional importante

Ao corrigir o CRM para `5262307-5`, o perfil médico foi **automaticamente
desativado e marcado como pendente de verificação**. Isso não é defeito: é
uma regra de segurança deliberada — **mudar um campo de identidade força
reverificação**. Tive de reverificar como admin para os testes rodarem.

**Consequência para o go-live:** se alguém corrigir o CRM, o nome ou o RQE da
Dra. Ana na VPS, ela **para de conseguir laudar** até um admin reverificar o
perfil. Isso precisa estar previsto no dia da virada.

## 10. Situação da assinatura manuscrita

Cadastrada **apenas no ambiente local**, na raiz privada, com permissão 0600.
**Não está na VPS** e não viaja pelo Git — por desenho.

Precisa ser cadastrada lá em **Administração → Contas médicas → Ana →
"Assinatura manuscrita (imagem)"**, depois do deploy.

Sem ela o laudo continua sendo emitido: sai com a identificação profissional
completa, sem a imagem.

## 11. O que ainda exige ação humana

1. **Aprovar o acesso SSH** (Tailscale check mode) e rodar o deploy da seção 6.
2. **Cadastrar a assinatura manuscrita** da Dra. Ana na VPS.
3. **Conferir a conta dela** na VPS: existe, papel `medico`, perfil ativo e
   verificado, CRM `5262307-5`, RQE `58224`.
4. **Confirmar o domínio real** da URL de validação. Hoje o `.env` local usa
   `https://painel.soprolife.com.br/validar`, que é **placeholder** — ele
   entra no QR e no rodapé do laudo.
5. **Confirmar com a Valid** se a ferramenta oficial do VIDaaS assina vários
   PDFs numa sessão. Não foi possível comprovar; e testar o Assinador SERPRO
   (que documenta lote) com o certificado em nuvem dela.
6. **Decidir sobre dado real de paciente** — LGPD, retenção e validação
   pública anônima seguem pendentes.

## 12. URL correta do painel

```
https://soprolife-painel-01.tailcaf0e4.ts.net/painel-soprolife/
```

Acessível apenas por dentro da tailnet. O login com credencial real só
funciona em HTTPS — origens HTTP remotas são bloqueadas por desenho.

## 13. Roteiro do primeiro laudo real

**Antes**, com a médica presente:

1. Confirmar que ela entra com a conta dela e vê "Laudos de espirometria".
2. Confirmar CRM `5262307-5`, RQE `58224` e a assinatura manuscrita no laudo.
3. Fazer **um** laudo de ponta a ponta com **paciente fictício** e conferir o
   PDF antes de tocar em exame real.

**O primeiro laudo real:**

1. Operacional cadastra paciente e espirometria, e sobe o PDF da MIR.
2. O laudo aparece na fila dela, filtrando pela unidade.
3. Ela abre, revisa, escolhe a conclusão e o complemento pós-BD.
4. Confere a prévia — é exatamente o PDF final.
5. **Finalizar revisão** → o laudo fica "Laudado — aguardando assinatura".
   Ainda não vai ao paciente.
6. Repete para os demais exames do dia.
7. Seleciona os laudos e clica em **Baixar para assinatura** → ZIP.
8. Assina os PDFs com o certificado dela no VIDaaS, **sem renomear nem
   reimprimir** — assinar anexa, reimprimir invalida.
9. Volta ao painel, **Enviar laudos assinados** (vários de uma vez ou o ZIP).
10. Confere o resultado por arquivo: os validados passam a **"Assinado"**.
11. Baixa o laudo assinado e, separadamente, o PDF técnico da MIR.

**Se algum arquivo for recusado**, o laudo continua esperando, sem perder
nada: basta reassinar aquele arquivo e reenviar só ele.

⚠️ A faixa **"PILOTO INTERNO — NÃO LIBERAR AO PACIENTE"** continua impressa
enquanto `reports_mode=pilot`. Decidir conscientemente quando sair do piloto.

## 14. git status final

```
 M .claude/skills/soprolife-audit-patterns/SKILL.md
 M .claude/skills/soprolife-marketing-seo/SKILL.md
 M .claude/skills/soprolife-medical-docs-pop/SKILL.md
 M CLAUDE.md
?? RELATORIO_M25_5_M25_6_LAUDO_VISUAL.md
```

As quatro primeiras são do `/doctor` e **não pertencem ao projeto** — ficaram
fora de todos os commits, como pedido. A quinta é o relatório da M25.5/M25.6.

## 15. Caminho completo do relatório

```
/home/adeildo/soprolife-site/RELATORIO_M25_8_DEPLOY_E_PRONTIDAO_FINAL.md
```
