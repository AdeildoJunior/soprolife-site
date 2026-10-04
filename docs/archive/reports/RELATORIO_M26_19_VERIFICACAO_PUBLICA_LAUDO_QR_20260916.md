# M26.19 — Verificação pública de laudo por código (QR)

16/09/2026. Duas branches: `painel-soprolife-v01` (worktree
`claude-m26-19-verificacao-publica-laudo`, base `50f3853`, já com a M26.18) e `main` (worktree
`claude-m26-19-verificar-pagina-publica`, base `0454ab1`).

Entregue:
- `painel-soprolife-v01`: commit `9195425` (merge da M26.18 + `d81bea4`), integrado por fast-forward na
  oficial, deploy na VPS confirmado.
- `main`: commit `aaab18e`, publicado via GitHub Pages, confirmado no ar.
- Grant incremental aplicado no PostgreSQL de produção (idempotente, só leitura por coluna nova).
- `soprolife-m15-api.service` e `soprolife-portal-resultados.service` reiniciados. Health 200 nos dois,
  mais o endpoint público real (`resultados-api.soprolife.com.br/p/v1/health`) confirmado 200 da
  internet.

## Contexto real

Pedido original: gerar QR code automático ao lado do código de verificação do laudo, para o paciente (ou
terceiro — convênio, empregador) atestar a autenticidade do documento.

## O que foi descoberto

1. **O desenho de QR já existia**, desde a M25.2 (`_draw_qr`/`reportlab`, no gerador do laudo) —
   ninguém tinha ligado porque faltava uma URL pública de verificação configurada.
2. **A verificação existente (`GET /laudos/validacao/{codigo}`) exige sessão do Command Center**, por
   desenho explícito — inútil para um paciente escaneando o QR pelo celular.
3. **O portal público de resultados (M26.4) tem um teste que CONGELA a lista de rotas públicas em
   exatamente cinco**, com o comentário "Não há uma sexta" — construir a verificação ali é uma decisão
   consciente, não uma adição silenciosa.
4. **O papel de banco do portal (`soprolife_portal`) tinha ZERO acesso a `report_documents` e
   `physician_profiles`** — precisou de extensão de GRANT, sempre por coluna.

## Decisão tomada (com o usuário)

Reaproveitar a infraestrutura pública já existente e testada em produção (domínio
`resultados-api.soprolife.com.br`, serviço `soprolife-portal-resultados`, papel de banco
`soprolife_portal`) em vez de criar um serviço público novo do zero — menor custo, menor risco (mesma
infra que já levou duas missões inteiras, com incidentes reais documentados, para acertar da primeira
vez).

## O que foi construído

- **Nova rota pública `GET /p/v1/verificar/{codigo}`**, sem sessão, sem 2º fator — o próprio código (alta
  entropia, impresso no papel) autoriza a consulta. Resposta só institucional: código do laudo, data de
  liberação, nome/CRM/RQE da médica, hash SHA-256 do documento. Nunca nome de paciente, código de exame
  ou conteúdo clínico. Mesma resposta genérica para código inexistente e para laudo ainda não liberado
  (prévia, corretiva em elaboração) — não é um oráculo de "existe mas não terminou".
- **Extensão do papel de banco** (`m26-4-portal-db-role.sql`, mantido como fonte ÚNICA de GRANT por
  teste próprio): leitura por coluna em `report_documents` (7 colunas) e `physician_profiles` (6
  colunas) — nada além do necessário para esta única pergunta.
- **Dataclasses e funções estreitas** em `patient_results.py`, mesmo padrão já usado no resto do portal
  (SELECT explícito por coluna, nunca `db.get` da entidade inteira).
- **Nova página pública `soprolife.com.br/verificar/`**, mesma postura de `resultados/index.html`
  (M26.4): sem indexação (`robots.txt` + meta robots), CSP restrita a uma única origem de rede, sem
  analytics, sem CDN, sem fonte externa. O código vem no FRAGMENTO da URL
  (`.../verificar/#/CODIGO`) — nunca um caminho de servidor, então o GitHub Pages (site estático, sem
  rota dinâmica) serve sempre o mesmo arquivo, e o JavaScript decide o que mostrar.
- **`M15_REPORTS_VALIDATION_BASE_URL` configurado em produção** pela primeira vez
  (`https://soprolife.com.br/verificar/#`) — o laudo passa a nascer com QR code funcional a partir de
  agora (documentos já emitidos antes desta mudança não ganham QR retroativamente; a próxima versão
  gerada de qualquer laudo, sim).

## Testes

- Novo arquivo backend (`test_m26_19_verificacao_publica_laudo.py`, 5 testes): código real e liberado
  confirma sem vazar paciente/exame; código inexistente e código de laudo não liberado dão a mesma
  mensagem genérica; formato inválido é recusado sem consultar o banco; portal desligado recusa.
- Novo arquivo de conteúdo estático (`test_m26_19_pagina_verificar.py`, 10 testes): página existe,
  robots.txt bloqueia, meta robots não indexa, CSP restrita à origem certa, sem terceiro de analytics,
  chamada de API bate com a CSP, separação erro-de-rede/erro-de-servidor, código lido do fragmento (não
  de um caminho), formato validado no cliente, sem campo de data de nascimento (diferente do portal
  pessoal — aqui não há 2º fator).
- Teste que congela a lista de rotas públicas (`test_18_e_23_...`) atualizado de 5 para 6 rotas, na
  MESMA mudança que criou a sexta — a fricção deliberada que ele existe para forçar.
- **Confirmação de que os testes pegam o bug**: a rota nova foi removida temporariamente e os 5 testes
  correspondentes (mais o teste de congelamento) falharam exatamente como esperado; código restaurado,
  testes voltam a passar.
- Suíte completa do backend: **1767 passaram**, as 12 falhas restantes são as mesmas pré-existentes do
  Google Sheets (confirmadas ambientais em missões anteriores desta sessão).
- `quality-gate-safe.sh`: as duas únicas falhas (M21, M25.26) são as mesmas já confirmadas
  pré-existentes, fora do escopo desta missão.

## Verificação em produção (dados reais, somente leitura)

Depois do deploy, chamada real à internet pública:

```
GET https://resultados-api.soprolife.com.br/p/v1/verificar/X3XFQWFHV4NZ
→ 200 {
    "laudo": "LAU-000037",
    "codigo_verificacao": "X3XFQWFHV4NZ",
    "liberado_em": "2026-09-16T19:15:38...",
    "medica_nome": "Dra. Ana Cristina do Nascimento Cunha",
    "medica_crm": "CRM-RJ 52.62307-5",
    "medica_rqe": "58224",
    "documento_sha256": "7cdfb679...",
    "instituicao": "SoproLife Diagnósticos e Soluções em Saúde"
  }
```

Código inexistente confirmado com a mensagem genérica (`404 laudo_nao_localizado`).
Página `https://soprolife.com.br/verificar/` confirmada no ar (200) e `robots.txt` bloqueando a área.
GRANT confirmado via `information_schema.role_column_grants` — exatamente as colunas pretendidas, nada
a mais. `get_settings().reports_validation_base_url` confirmado no processo real da VPS.
