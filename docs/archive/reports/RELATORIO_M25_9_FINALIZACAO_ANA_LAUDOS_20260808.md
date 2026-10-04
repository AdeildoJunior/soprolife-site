# Relatório M25.9 — Deploy concluído e prontidão da Dra. Ana

Data: 2026-08-08

## 1. Resumo executivo

**O deploy foi concluído.** A produção está rodando a M25.8, com as duas
migrations pendentes aplicadas, serviço ativo e healthcheck verde.

**A conta da Dra. Ana foi criada** com o perfil médico completo, papel
`medico` e uma senha de primeiro acesso gerada na VPS e gravada só lá.

**Ana ainda NÃO pode laudar.** Falta um ato humano que eu deliberadamente não
executei: **verificar o perfil médico dela**. Verificar exige uma referência
real de checagem do CRM junto ao conselho, e fabricar isso seria inventar uma
prova. É um comando só, na seção 16.

## 2. Branch e commits publicados

| Etapa | Commit |
| --- | --- |
| M25.5 | `0b74a66` |
| M25.6 | `c11a395` |
| M25.7 | `ccfc79c` |
| M25.8 — núcleo | `3c65694` |
| M25.8 — endpoints e interface | `ecc8ca4` |
| Relatório de publicação | `56517ce` |

Branches `codex-m25a-search-console-reconciliation` e `painel-soprolife-v01`
alinhadas em `56517ce`. Sem force push, sem rebase, sem reset destrutivo.

## 3. Commit implantado na VPS

```
56517ceee92c81a46d566c65a3ce4d8558b60622
```

Confere com o remoto. Branch na VPS: `painel-soprolife-v01`.

## 4. Data e hora do deploy

Migrations e reinício do serviço: **2026-08-08, 10:26 (America/Sao_Paulo)**.

## 5. Backup

```
/opt/soprolife/backups/m25-8/20260808T132605Z/
├── m15.dump               246.240 bytes  (pg_dump -Fc)
├── reports-storage.tar.gz     118 bytes
└── m15.env.bak                581 bytes  (0600)
```

**Verificado**, não apenas gerado: `pg_restore -l` lista **44 tabelas com
dados**. O tar do storage tem 1 entrada porque o diretório estava vazio —
nenhum laudo havia sido emitido em produção até hoje.

## 6. Migrations

| Antes | Depois |
| --- | --- |
| `a3f1d7c25e90` (M25.2) | `d4a71c88b2e6` (M25.8) |

Aplicadas na ordem: `b6e2f94a17c3` (M25.7) → `d4a71c88b2e6` (M25.8).

O erro anterior *"Path doesn't exist: migrations"* era o Alembic rodando do
diretório errado. O correto é:

```bash
cd /opt/soprolife/soprolife-site/painel-soprolife/nucleo-m15
set -a; . /opt/soprolife/secrets/m15.env; set +a
/opt/soprolife/venvs/m15/bin/python -m alembic current
```

Antes de aplicar em produção, ensaiei as duas migrations num PostgreSQL 16
descartável, descendo até exatamente `a3f1d7c25e90` e subindo só as
pendentes, com downgrade e upgrade de volta. Conferido no banco depois:
tabela `qualified_signature_requests`, colunas `signature_prepared_at` /
`signature_downloaded_at` e `icp_signer_subject` / `icp_signer_bound_at`.

## 7. Testes e resultados

| Onde | O que | Resultado |
| --- | --- | --- |
| Local | Suíte backend completa | **961 passaram**, 22 puladas, 1 falha conhecida |
| Local | Módulo do lote (M25.8) | 30 passaram |
| Local | Módulo VIDaaS (M25.7) | 38 passaram |
| Local | Migrations | 11 passaram |
| Local | Proxy do Command Center | 46 passaram |
| Local | Suíte JS do painel | todos os casos |
| Local | Fumaça ponta a ponta | **16 de 16 passos** |
| **Produção** | Migrations aplicadas | OK |
| **Produção** | Serviço systemd ativo, sem erro no journal | OK |
| **Produção** | Healthcheck | `status ok, ambiente prod, banco ok` |
| **Produção** | Frontend servido | `v=2026080603` (era `2026080503`) |
| **Produção** | Endpoints do lote montados | `401` (exigem autenticação) |
| **Produção** | **Login de médica** | OK |
| **Produção** | **Fila de laudos** | OK |

### O que NÃO foi testado em produção — e por quê

Tentei rodar o fluxo clínico completo em produção com médica e paciente
fictícios. **Interrompi de propósito.** O banco tem gatilhos *append-only*
(`m24c_guard_assignment_history`, "immutable clinical evidence") que impedem
apagar atribuições de laudo e versões. Ou seja: **fixture de teste em
produção vira permanente.**

Não é aceitável poluir a base clínica com dados de teste que não se pode
remover. Parei e neutralizei o que já havia criado.

**Resíduo do meu teste, medido:**

| Item | Estado |
| --- | --- |
| Usuário `teste-apagar-fumaca@soprolife.local` | **inativo**, sem papel médico |
| Perfil médico de teste | **inativo**, `pending` |
| 1 atribuição de laudo | **permanente** (append-only) |
| 1 laudo `LAU-TF0001` | **permanente** (append-only), sem versões |
| Storage privado | **vazio**, 0 arquivos |
| **28 pacientes reais** | **intactos** |

Também limpei arquivos que meu script havia criado no storage como `root` —
eles teriam quebrado a escrita da API, que roda como `soprolife`.

**Consequência prática:** o primeiro laudo fictício da Dra. Ana **é** o teste
de fumaça de produção. É o caminho certo — ele passa pelo fluxo real, não por
fixtures montadas à mão.

## 8. Healthcheck, serviço e endpoints

```
systemctl is-active soprolife-m15-api.service   → active
alembic current                                 → d4a71c88b2e6 (head)
GET  /api/m15/health                            → status ok, prod, banco ok
GET  /painel-soprolife/                         → 200, JS/CSS v=2026080603
POST /api/m15/laudos/lote/baixar                → 401 (montado)
POST /api/m15/laudos/lote/enviar                → 401 (montado)
```

Journal do serviço sem erros, exceções ou tracebacks após o reinício.

## 9. Fluxo que a Dra. Ana vai usar

1. Entra em <https://soprolife-painel-01.tailcaf0e4.ts.net/painel-soprolife/>
2. Menu lateral → **Laudos de espirometria**
3. Escolhe a **unidade** (Pastore Ipanema, atendimento SoproLife…)
4. Vê a fila, com filtro por estado
5. Abre o exame e confere o PDF técnico da MIR
6. Escolhe a conclusão do catálogo e o complemento pós-broncodilatador
7. Edita o texto livremente e confere a prévia
8. **Finalizar revisão** → o laudo fica *"Laudado — aguardando assinatura"*.
   **Ainda não vai ao paciente.**
9. Repete para os demais exames do dia
10. Seleciona os laudos e clica em **Baixar para assinatura** → ZIP
11. Assina os PDFs no VIDaaS, **sem renomear nem reimprimir**
12. Volta ao painel → **Enviar laudos assinados** (vários ou o ZIP)
13. Confere o resultado por arquivo; os validados passam a **"Assinado"**
14. Baixa o laudo assinado e, separadamente, o PDF da MIR

Alternativa sem certificado: o botão **"Assinar e liberar laudo"** faz a
liberação institucional (assinatura eletrônica interna), que **não é
ICP-Brasil** e o próprio laudo diz isso.

## 10. Login e situação real da conta

| Campo | Valor |
| --- | --- |
| Login | `annapec3@hotmail.com` |
| Conta | **criada e ativa** |
| Papel `medico` | **sim** |
| Nome profissional | Dra. Ana Cristina do Nascimento Cunha |
| CRM | `5262307-5` (CRM-RJ) |
| RQE | `58224` |
| Especialidade | Médica Pneumologista |
| Perfil médico | **`active=false`, `verification_status=pending`** |

**A senha de primeiro acesso foi gerada aleatoriamente na VPS** e gravada em
`/opt/soprolife/secrets/ana-primeiro-acesso.txt` (`0600`, dono root). Ela
**não** aparece neste relatório, no Git, em log ou no terminal.

⚠️ O sistema **não tem troca de senha pela própria usuária** nem fluxo de
convite. Só admin cria e admin redefine. Por isso: entregue a senha por canal
seguro, e redefina depois que ela confirmar o acesso.

⚠️ **Enquanto o perfil estiver `pending`, ela consegue entrar mas NÃO
consegue laudar.**

Sobre o CRM: o prompt trouxe `52.62307-5`; você confirmou com ela
`5262307-5`. Os dígitos são idênticos (`52623075`) — mudou só o agrupamento.
Gravei o que você confirmou com ela.

## 11. Assinatura manuscrita

**Não está cadastrada em produção.** O arquivo não viaja pelo Git, por
desenho.

| Item | Valor |
| --- | --- |
| Formato | PNG com fundo transparente |
| Tamanho máximo | 2 MiB |
| Proporção | entre 0,25 e 12 (a dela mede 0,42) |
| Onde cadastrar | Administração → Contas médicas → Ana → "Assinatura manuscrita (imagem)" |
| Onde fica | `/opt/soprolife/private/reports/assinaturas/<perfil>/<id>.png`, `0600` |

Eu tenho o PNG já extraído da foto que você enviou (só o traço de caneta,
sem o carimbo tipografado). **Não é assinatura digital** — é elemento visual,
e o código nunca a trata como certificado.

Sem ela o laudo sai normalmente, com a identificação profissional completa.

## 12. Assinatura VIDaaS / ICP-Brasil / lote externo

| Caminho | Estado real |
| --- | --- |
| Liberação institucional (interna) | **funciona** — não é ICP-Brasil, e o laudo diz isso |
| Lote externo (baixar → assinar fora → devolver) | **implantado e montado**; validação por arquivo com 9 desfechos |
| VIDaaS/IntegraICP (API comercial) | **desligado** — sem canal, credencial nem certificado configurados |

**Nenhuma assinatura ICP-Brasil real foi executada.** Os testes usam uma
autoridade certificadora sintética gerada em memória. Quando a Dra. Ana
assinar com o certificado dela, aí sim será real.

**Sobre assinar vários de uma vez: NÃO foi possível confirmar** que a
ferramenta oficial do VIDaaS assina em lote. O sistema não afirma que assina
— as instruções dentro do ZIP mandam considerar arquivo por arquivo. O
Assinador SERPRO documenta lote e é gratuito, mas a compatibilidade dele com
certificado em nuvem VIDaaS também não foi confirmada.

## 13. Local vs. produção

| Verificado em PRODUÇÃO | Verificado só LOCALMENTE |
| --- | --- |
| Migrations aplicadas | Fluxo clínico completo (16/16) |
| Serviço, journal, healthcheck | Download do ZIP e manifesto |
| Frontend `v=2026080603` | Rejeição de PDF sem assinatura |
| Endpoints do lote montados | Aceitação de PDF assinado |
| Login de médica | Estados e trilha de auditoria |
| Fila de laudos | Suíte de 961 testes |

## 14. Riscos e pendências

1. **`M15_REPORTS_VALIDATION_BASE_URL` não está configurada em produção.** Sem
   ela o laudo sai só com o código textual, sem QR nem URL. Fail-closed por
   desenho — nenhuma URL é inventada. Decida o domínio real.
2. **Resíduo append-only** do meu teste: 1 atribuição e 1 laudo `LAU-TF0001`
   permanentes, ambos neutralizados e sem versões.
3. **Corrigir identidade desativa o perfil.** Mudar CRM, nome ou RQE força
   reverificação. Se acontecer, ela para de laudar até um admin reverificar.
4. Falha conhecida em `test_m24d_pilot_deployment` — dependente de ordem,
   passa isolada, sem relação com assinatura.
5. Faixa **"PILOTO INTERNO — NÃO LIBERAR AO PACIENTE"** continua impressa
   enquanto `reports_mode=pilot`.

## 15. Rollback

```bash
# 1. Voltar o código
cd /opt/soprolife/soprolife-site
git checkout painel-soprolife-v01
git reset --hard 9b8ae96          # só se precisar voltar ao pré-M25

# 2. Voltar o schema (duas revisões)
cd painel-soprolife/nucleo-m15
set -a; . /opt/soprolife/secrets/m15.env; set +a
/opt/soprolife/venvs/m15/bin/python -m alembic downgrade a3f1d7c25e90

# 3. Ou restaurar o backup completo
sudo -u postgres pg_restore -c -d "<url-do-banco>" \
  /opt/soprolife/backups/m25-8/20260808T132605Z/m15.dump

sudo systemctl restart soprolife-m15-api.service
```

As duas migrations têm `downgrade()` testado em PostgreSQL.

---

# 16. O QUE ADEILDO PRECISA PROVIDENCIAR PARA A DRA. ANA PODER LAUDAR

## BLOQUEADORES — sem isso ela não lauda

### 16.1 Verificar o perfil médico dela

- **O que é:** marcar o perfil como `verified` e `active`, com uma referência
  técnica da checagem do CRM junto ao CREMERJ.
- **Quem:** você, como admin. **Não fiz** porque exige uma referência real de
  verificação — inventar uma seria fabricar prova de um ato que não ocorreu.
- **Onde:** Administração → Contas médicas → Ana. Ou por comando:

```bash
ssh root@100.87.98.100
cd /opt/soprolife/soprolife-site/painel-soprolife/nucleo-m15
set -a; . /opt/soprolife/secrets/m15.env; set +a
/opt/soprolife/venvs/m15/bin/python - <<'PY'
import os
from sqlalchemy import create_engine, text
e = create_engine(os.environ["M15_DATABASE_URL"])
with e.begin() as c:
    admin = c.execute(text("select id from users where email='contato@soprolife.com.br'")).scalar()
    c.execute(text("""update physician_profiles set
        verification_status='verified', active=true,
        verification_reference='<CODIGO-REAL-DA-CHECAGEM-CREMERJ>',
        verified_at=now(), verified_by_user_id=:a
        where user_id=(select id from users where email='annapec3@hotmail.com')"""), {"a": admin})
    print("perfil verificado e ativado")
PY
```

- **Substitua** `<CODIGO-REAL-DA-CHECAGEM-CREMERJ>` pelo identificador da
  consulta que você fizer. Mínimo 4 caracteres.
- **Sensível?** Não. **WhatsApp/e-mail?** Pode.
- **Se não fizer:** ela entra no painel mas não vê a fila nem lauda.

### 16.2 Entregar a senha de primeiro acesso

- **O que é:** a senha gerada na VPS, em
  `/opt/soprolife/secrets/ana-primeiro-acesso.txt`.
- **Onde conseguir:** `ssh root@100.87.98.100 && cat /opt/soprolife/secrets/ana-primeiro-acesso.txt`
- **Sensível?** **SIM.** **NÃO** mande por WhatsApp, e-mail nem Git. Entregue
  pessoalmente ou por ligação. **Apague o arquivo depois.**
- **Se não fizer:** ela não consegue entrar.

## NECESSÁRIO PARA ASSINATURA QUALIFICADA (ICP-Brasil)

### 16.3 Certificado que ela já tem

- **O que é:** o certificado ICP-Brasil no VIDaaS dela. **Nada a cadastrar no
  servidor** — a chave nunca sai do celular/HSM dela.
- **Sensível?** Sim, mas fica com ela. Você nunca deve pedir a senha.
- **Se não tiver:** ela ainda pode usar a liberação institucional, que **não
  é** ICP-Brasil.

### 16.4 Confirmar se o VIDaaS assina em lote

- **Quem:** você ou ela, com o suporte da Valid.
- **Testar também:** Assinador SERPRO (gratuito, documenta lote) com o
  certificado em nuvem dela.
- **Se não confirmar:** ela assina arquivo por arquivo. O download e o
  reenvio pelo painel continuam em lote.

### 16.5 Credenciais IntegraICP (opcional, caminho pago)

- Channel ID, Base URL, Callback URL e política de assinatura, contratados
  com a Valid.
- **Sensível?** **SIM.** Só em `/opt/soprolife/secrets/m15.env`, nunca no Git.
- **Se não providenciar:** o botão "Assinar com VIDaaS" continua mostrando
  *"Integração aguardando credencial da Valid"*. Não bloqueia o lote externo.

## DADOS E ARQUIVOS A ENVIAR OU CONFIRMAR

### 16.6 Assinatura manuscrita (PNG)

- Já extraí o PNG da foto. **Precisa ser cadastrado na VPS** pela tela de
  Administração.
- **Sensível?** Sim — é a assinatura dela. Não circule por grupo.
- **Se não fizer:** o laudo sai sem a imagem, com a identificação completa.

### 16.7 Domínio real da validação

- **O que é:** a URL pública onde o paciente confere o laudo. Hoje **não está
  configurada** em produção.
- **Onde cadastrar:** `M15_REPORTS_VALIDATION_BASE_URL` no `m15.env` (HTTPS).
- **Se não fizer:** o laudo sai só com o código textual, sem QR.

### 16.8 Endereço da Pastore Ipanema

- Conferir que os exames da Pastore usam **Rua Teixeira de Melo, 54 —
  Ipanema**, e **não** o endereço do Canal do Rio Caçambê. Há duas unidades
  cadastradas: "Pastore — Zona Sul" e "Pastore Ipanema — Zona Sul".
- **Se não conferir:** o laudo pode sair com o endereço errado.

## AÇÕES DA DRA. ANA

1. Receber a senha de primeiro acesso por canal seguro.
2. Entrar em <https://soprolife-painel-01.tailcaf0e4.ts.net/painel-soprolife/>
   (precisa estar na tailnet).
3. Avisar que entrou, para você redefinir a senha.
4. Fazer **um laudo com paciente fictício** de ponta a ponta antes de tocar
   em exame real.
5. Conferir no PDF: nome, CRM `5262307-5`, RQE `58224` e a assinatura.
6. Testar assinar um PDF no VIDaaS e devolver pelo painel.

## OPCIONAIS / FUTURO

- Carimbo do tempo e revogação para **PAdES-LTA**. Hoje o sistema produz
  **PAdES-B-B**: sem carimbo do tempo, o laudo deixa de validar quando o
  certificado dela expirar.
- Tela de autoatendimento de senha (hoje só admin redefine).
- Sair do modo piloto quando a faixa "NÃO LIBERAR AO PACIENTE" não fizer
  mais sentido.
- Corrigir a falha dependente de ordem em `test_m24d_pilot_deployment`.

---

## 17. git status final

```
 M .claude/skills/soprolife-audit-patterns/SKILL.md
 M .claude/skills/soprolife-marketing-seo/SKILL.md
 M .claude/skills/soprolife-medical-docs-pop/SKILL.md
 M CLAUDE.md
?? RELATORIO_M25_5_M25_6_LAUDO_VISUAL.md
```

As quatro primeiras são do `/doctor` e não pertencem ao projeto.

## 18. Caminho completo do relatório

```
/home/adeildo/soprolife-site/RELATORIO_M25_9_FINALIZACAO_ANA_LAUDOS_20260808.md
```
