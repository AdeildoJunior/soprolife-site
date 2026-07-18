# M15.3A — Administração e Operação Essencial

Data: 18/07/2026 · Base: M15.2 (commit bf655d5) · Branch: `fable-m15-3a-operacao-essencial`

## O que esta etapa entrega

1. **Administração de usuários pelo painel** (aba "Administração", exclusiva
   do papel `admin`):
   - listar, criar, ver papel/estado, alterar papel, desativar, reativar;
   - redefinir credencial com segurança (senha só em corpo POST; nunca em
     URL, log, Git ou auditoria);
   - usuário inativo não autentica **nem continua operando**: o token é
     validado contra o estado do usuário a cada requisição;
   - redefinir senha revoga na hora os tokens antigos (o token carrega um
     fingerprint derivado do **hash** da senha — sem tabela de sessões);
   - anti-lockout: ninguém se auto-inativa, rebaixa o próprio papel ou
     remove o último admin ativo;
   - `gestor` **não** herda administração de usuários; `operacional` e
     `leitura` não veem a aba (e o servidor devolve 403 de qualquer forma);
   - bootstrap inicial continua deliberado e local (CLI `criar-usuario`);
     nenhuma conta é criada automaticamente.

2. **Operação diária sem curl** na seção Núcleo M15:
   - login por e-mail+senha (ou token da CLI); sessão apenas em memória;
   - criação e edição de: pessoa (incl. contatos e consentimentos), lead,
     espirometria, consulta, follow-up manual, interação, parceiro, unidade,
     contato de parceiro, parceria (gestor), encaminhamento (+painel
     financeiro do encaminhamento, gestor) e lançamento financeiro (gestor);
   - estados de carregamento, vazio, erro e sucesso (toast);
   - ações fora do papel ficam ocultas na UI (via `GET /auth/me`) — a
     autorização real permanece no servidor.

3. **Contratos aditivos de API** (sem migration; modelo existente bastou):
   - `GET /auth/me`;
   - `GET|POST /admin/usuarios`, `GET|PATCH /admin/usuarios/{id}`,
     `POST /admin/usuarios/{id}/redefinir-senha`;
   - `PATCH /parceiros/{id}`, `PATCH /unidades/{id}`,
     `PATCH /contatos-parceiros/{id}`, `PATCH /parcerias/{id}` (gestor);
   - `PATCH /lancamentos/{id}` (gestor; **valor e tipo imutáveis** —
     correção monetária é um novo lançamento);
   - `PATCH /pessoas/{id}` agora aceita `nome_completo` (re-normaliza o
     nome para busca) e `data_nascimento`.

## Regras preservadas

- Financeiro_Lancamentos segue como única fonte monetária; CRM e
  encaminhamentos não criam segunda fonte de valores.
- PCMSO permanece somente histórico (guardas ativas em todos os writes).
- Follow-up continua 6 meses após atendimento; filas e "não contatar"
  inalterados; WhatsApp permanece 100% humano (consentimento → revisão →
  link → registro só após confirmação).
- Pessoas nunca são vinculadas só pelo nome; telefone é candidato.
- Datas incompletas preservam original, precisão e dia assumido.
- Token somente em memória no navegador; proxy same-origin; API e
  PostgreSQL nunca expostos; payload extra rejeitado (`extra="forbid"`).
- Feature flag `data/m15-config.json` continua `enabled: false` no Git.

## Mudança de formato do token (interna)

O token passou de `user_id.exp.assinatura` para
`user_id.exp.fingerprint.assinatura`. Tokens emitidos antes da M15.3A
deixam de validar (TTL curto; basta emitir/entrar de novo). É isso que
permite revogação imediata em troca de senha sem sistema de sessões.

## Como validar localmente

```bash
cd painel-soprolife/nucleo-m15
.venv/bin/python -m pytest tests -q          # suíte SQLite completa
python3 ../scripts/test_command_center_m15_proxy.py  # proxy + flag
node --check ../js/m15-nucleo.js
```
