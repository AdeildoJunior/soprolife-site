# M15.5A — Go-live controlado do Núcleo M15 (runbook)

Roteiro curto para ligar o Núcleo Operacional M15 em produção com segurança.
Sem credenciais e sem nomes de tailnet neste documento — o endereço HTTPS
privado é comunicado por canal interno.

## Pré-requisito obrigatório: HTTPS privado

1. O endpoint HTTPS privado (Tailscale Serve → proxy loopback :8765) deve
   estar publicado e validado ANTES de usar credenciais reais.
2. A guarda de contexto seguro (`js/m15-security.js`) só libera login em:
   - HTTPS (qualquer hostname servido por TLS);
   - `localhost` / `127.x.x.x` / `::1` em desenvolvimento local.
3. Em HTTP remoto (inclusive IP Tailscale `100.x` sem TLS) o painel NÃO
   renderiza campo de senha, NÃO aceita token e NÃO envia requisição de
   autenticação — aparece o aviso para abrir o endereço HTTPS privado.
   **É proibido digitar credencial real ou dado real de paciente em página
   HTTP.** As telas legadas (sem dado clínico) continuam acessíveis.

## Ativação global

- A ativação é versionada: `painel-soprolife/data/m15-config.json` com
  `"enabled": true` (estado deste branch). Com a flag ligada, o menu
  "Núcleo M15" aparece para todos, sem o opt-in local `soproM15`.
- `api_base` permanece `/painel-soprolife/api/m15` (mesma origem, via proxy
  loopback). Nada disso muda no go-live.
- Publicação: merge deste branch + atualização do checkout servido pela VPS
  seguindo o fluxo padrão (skills soprolife-vps-deploy-safe / soprolife-vps-safe).
  Nenhum restart de serviço é necessário só para a flag (arquivos estáticos).
- Atenção: `nucleo-m15/scripts/deploy-producao-vps.sh` (deploy de
  infraestrutura) mantém, por segurança, a exigência histórica de
  `enabled=false` e passará a recusar execução após este merge. Atualizar
  essa trava é uma etapa própria, autorizada separadamente — não é
  necessária para o go-live do frontend.

## Cache do navegador

- Os scripts M15 foram versionados (`?v=2026071902`); ainda assim, no
  primeiro acesso após a publicação faça um refresh forçado
  (Ctrl+Shift+R) para descartar cache antigo.

## Primeiro login seguro

1. Abra o endereço HTTPS privado do painel.
2. Confira o selo no cabeçalho do Núcleo M15: deve dizer
   "Acesso seguro (HTTPS)". Se aparecer "HTTP inseguro — login bloqueado",
   PARE e corrija o endereço/publicação TLS antes de continuar.
3. Entre com e-mail e senha. O token de sessão vive apenas em memória:
   recarregar a página exige novo login; "Sair" encerra a sessão.

## Troca de senha

- Após o primeiro login de cada pessoa, um admin redefine a senha inicial
  em Administração → Senha (mín. 10 caracteres). Redefinir revoga
  imediatamente os tokens antigos do usuário.

## Smoke test sintético (sem dado real)

1. Visão geral carrega os contadores sem erro.
2. Pessoas: criar "Paciente Sintético M15-5A" com observação contendo
   "SINTÉTICO", conferir o selo "sintético", depois editar/inativar.
3. Conferir que a Auditoria (papel gestor/admin) registrou as ações.
4. Nenhum dado real de paciente entra nesta fase de validação.

## Rollback

- Reverter é 1 linha versionada: `"enabled": false` em
  `painel-soprolife/data/m15-config.json` (commit + publicação do arquivo).
  O painel volta exatamente ao estado pré-M15 para todos; o opt-in local
  `soproM15='on'` continua funcionando para diagnóstico individual.
- Backend, banco, proxy e systemd não precisam ser tocados no rollback.

## Proibições permanentes

- Nunca digitar credencial real ou dado real de paciente em página HTTP.
- Nunca colar token da CLI fora de HTTPS/loopback (a UI bloqueia, e a
  regra vale também para qualquer outro cliente).
- Nunca registrar hostname da tailnet, senha ou token neste repositório.
