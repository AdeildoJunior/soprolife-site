# WhatsApp Business / Cloud API — ativação futura

Preparação de 14/11/2026. **Hoje permanece `WHATSAPP_INTEGRATION_ENABLED=false`.**
Nenhum onboarding, número, token Meta, App Secret ou mensagem real integra esta
entrega. O webhook existe na API loopback; a rota pública nginx **não é instalada
pelo deploy de preparação**. Painel, API privada e banco continuam privados.

## Dados necessários após onboarding

| Dado | Variável/destino no servidor |
|---|---|
| WABA ID da conta SoproLife | `WHATSAPP_WABA_ID` |
| Phone Number ID do número em coexistência | `WHATSAPP_PHONE_NUMBER_ID` |
| App Secret do app que efetivamente assina os webhooks | `WHATSAPP_APP_SECRET` |
| Verify Token forte escolhido pela SoproLife | `WHATSAPP_VERIFY_TOKEN` |
| Chave aleatória própria, estável, >=32 caracteres e >=10 distintos | `WHATSAPP_IDENTITY_KEY` |
| Callback HTTPS no nginx público existente, certificado válido | URL exata `/api/v1/integrations/whatsapp/webhook` |
| Confirmação de coexistência e assinatura dos campos `messages` e `smb_message_echoes` | Meta/EasyCoexistence; não é variável |

A chave própria faz HMAC-SHA256 de remetente e IDs opacos: é necessária para
idempotência/identificação sem guardar telefone completo nem depender da rotação
do App Secret ou do segredo de login. Gere os dois segredos escolhidos por nós
**no terminal privado**, instale no arquivo abaixo e faça backup privado. Nunca
cole em chat, argv, logs, relatórios ou Git. Não reutilize chaves fiscais/login.
Não troque a chave HMAC durante operação sem plano de migração: perderia a
continuidade da pseudonimização e da deduplicação.

**Não há access token Meta no runtime receptor.** Caso onboarding/assinatura do
app à WABA exija access token e permissões, o operador usa o fluxo oficial do
provedor/Meta, separado deste receptor; não instalar token de envio na API.
Confirmar titularidade, app assinante, permissões e disponibilidade do campo de
echo antes de habilitar. Se EasyCoexistence apenas habilitar coexistência, a
entrega deve continuar no formato oficial, com assinatura Meta original. Não
aceitar payload transformado ou segredo presumido de outro app.

## Procedimento de segunda-feira (operador autorizado)

1. **Preflight/backup:** conferir HEAD, worktree limpo e Alembic `w141126wa`;
   health API/proxy/portal; worker fiscal ocioso e sem pedidos em execução.
   Fazer backup pelo script corporativo
   `painel-soprolife/nucleo-m15/scripts/backup-postgresql-m15.sh`, bundle Git e
   cópia privada de `/opt/soprolife/secrets/m15.env` e do vhost nginx a alterar.
   Validar dump com `pg_restore --list`. Não rodar seed/import nem emissão.
2. **Instalar configurações:** na VPS, editar com `sudoedit`
   `/opt/soprolife/secrets/m15.env` (mecanismo `EnvironmentFile` já adotado,
   root, modo 0600). Adicionar as cinco configurações da tabela e a chave HMAC.
   Manter explicitamente `WHATSAPP_INTEGRATION_ENABLED=false`. Não instalar
   valores fictícios. Reiniciar somente `soprolife-m15-api.service` e conferir
   health. Um restart causa alguns segundos de indisponibilidade da API.
3. **Rota pública restrita:** confirmar o hostname HTTPS aprovado, já atendido
   pelo nginx existente. Incluir **dentro desse server HTTPS**, após backup,
   o snippet versionado `nucleo-m15/scripts/nginx-whatsapp-webhook.conf`.
   Usar caminho absoluto para o snippet no checkout da VPS. Conferir `nginx -t`
   antes de recarregar nginx. Somente o caminho exato recebe GET/POST: nenhum
   `/api/v1/*`, painel, status ou intake administrativo deve ficar público.
   Não abrir portas de API/banco nem modificar firewall. O snippet desliga
   logs de acesso/erro dessa rota, pois a verificação GET traz token na URL.
   A API oficial já sobe sem access log. Conferir também logs externos do
   provedor/CDN; não habilitar captura de corpo, query ou headers.
4. **Configurar webhook na Meta/EasyCoexistence:** cadastrar a URL HTTPS exata
   e o Verify Token por interface privada. Habilitar os campos `messages` e
   `smb_message_echoes` e assinatura do app correto à WABA pelo fluxo oficial.
   Não importar histórico, sincronizar agenda, baixar mídia nem habilitar bot.
5. **Validar challenge mantendo desligado:** a Meta faz GET com
   `hub.mode=subscribe`, `hub.verify_token` e challenge numérico. A resposta
   correta é HTTP 200 `text/plain` com o challenge; token errado retorna 403.
   Conferir apenas status/resultado, sem copiar URL/token para logs.
   GET funciona antes de habilitar; POST ainda responde 503 sem ler o corpo.
6. **Habilitar conscientemente:** conferir no painel, como admin, configuração
   Pronta e webhook configurável. Confirmar que ecos representam resposta
   humana: desabilitar saudações/ausência automáticas no Business App durante
   a validação; payloads sem sinal que distingua automação não permitem inferir
   autoria humana pelo conteúdo. Só então definir
   `WHATSAPP_INTEGRATION_ENABLED=true` no mesmo EnvironmentFile, reiniciar API
   e conferir health/status. Configuração ausente bloqueia POST com 503 sem
   derrubar os demais módulos. Não cadastrar segredo no JavaScript.
7. **UMA mensagem real controlada:** do remetente autorizado para este teste,
   enviar uma mensagem inbound. Confirmar horário original e apenas um intake
   em Leads e Agendamentos. Se houver exatamente uma pessoa ativa com contato
   normalizado idêntico, o vínculo pode ser automático. Desconhecido/ambíguo
   exige Identificar; nenhuma pessoa nasce do telefone. Conferir identidade no
   Business App, procurar pessoa existente ou cadastrar manualmente. Verificar
   o código do contato no funil. Essa escrita real pertence à ativação futura,
   não à preparação de hoje.
8. **Responder pelo Business App:** fazer uma resposta humana e confirmar
   PRIMEIRA_RESPOSTA no contato com horário do echo. Não usar delivered/read
   como evidência. Se o provedor não entregar `smb_message_echoes`, não afirmar
   captura automática; manter o registro manual existente e corrigir a
   assinatura/contrato antes de liberar essa capacidade.
9. **Retry/idempotência:** pelo recurso oficial de reentrega, repetir o mesmo
   evento/ID, com assinatura original válida. Conferir contagens estáveis e
   timestamps preservados, sem segunda pessoa/contato/resposta. Não copiar o
   payload real para fixture, relatório ou log; se reentrega não estiver
   disponível, usar ensaio sintético local, sem enviar outra mensagem real.
10. **Conferir privacidade/continuidade:** olhar somente códigos técnicos,
    contagens e datas. Não habilitar debug de payload/SQL. Validar funil,
    Pastore, fiscal, financeiro, EEG e Marketing por leitura. Canal WhatsApp
    nunca atribui origem de aquisição. Serviço desconhecido nasce como Outro;
    classificar no funil existente antes de agendar espirometria. O fluxo de
    agenda, exame, fiscal e financeiro continua o mesmo.
11. **Desabilitação imediata:** definir
    `WHATSAPP_INTEGRATION_ENABLED=false` e reiniciar `soprolife-m15-api`.
    Conferir health e estado Desativada. POST não processa nem grava; novos
    callbacks receberão 503, permitindo retry posterior da Meta. Para
    encerramento definitivo, desassinar o webhook na Meta separadamente.
    Não apagar intake/eventos, chave HMAC ou esquema para um rollback comum.
    Se necessário remover a rota pública, restaurar o vhost salvo, `nginx -t`
    e recarregar. Preservar banco, documentos e backups.

## Contratos e limites

- Configuração e status: GET interno `/api/v1/integrations/whatsapp/status`
  exige admin; fila e identificação exigem operacional. O proxy do painel usa
  `/painel-soprolife/api/m15/integrations/whatsapp/...`, sessão/CSRF normais.
- Webhook: GET/POST `/api/v1/integrations/whatsapp/webhook`, sem sessão de painel;
  POST exige `application/json`, HMAC-SHA256 `X-Hub-Signature-256` sobre bytes
  originais, comparação constante, WABA/Phone ID permitidos. Browser Origin
  presente é recusado. Não se confia em ID do JSON sem assinatura.
- 256 KiB de corpo, leitura até 5 s, lote até 100 eventos. Na transação PG:
  lock timeout 1,5 s, statement timeout 3 s, orçamento de processamento 5 s.
  Locks de remetente/pessoa/lead e PK do digest tornam retry concorrente seguro.
  Falha transacional retorna 503 e rollback integral. Sem chamada externa.
- Apenas `messages[].from` inbound e `message_echoes[].to` em
  `smb_message_echoes` outbound são interpretados. Tipo desconhecido, sistema,
  grupos, broadcast, status e sinal de automação são ignorados. Novos campos
  são tolerados. Mídia só conta como ocorrência; conteúdo e mídia são descartados.
- Telefones Meta são DDI explícito, passando pelo normalizador existente sem
  presumir Brasil. Comparação estrita em contatos ativos de pessoas ativas;
  nenhum fuzzy match, nome de perfil, cadastro histórico ou backfill.
- Reuso automático apenas de contato aberto WhatsApp compatível, anterior ou
  igual ao evento, sem atribuição Pastore/parceiro. Ambiguidade pede escolha.
  Novo contato: origem Não informado, unidade Não definido, serviço Outro.
  Não modifica a aquisição nem o primeiro horário do contato reutilizado.
- Intake desconhecido preserva menor timestamp inbound observado. Se chegar
  inbound anterior após vincular ao funil, marca revisão de cronologia e não
  reescreve fatos silenciosamente. Conferir manualmente no histórico. Ecos
  anteriores ao intake são inelegíveis; echo antecipado pode aguardar inbound
  ou identificação. Primeira resposta preenchida nunca é substituída.
- Não existe envio, chatbot, lembrete, template, download, inbox ou cliente Meta.
  IDs de mensagens e remetente são HMACs; só quatro dígitos são persistidos
  para conferência visual. São dados pseudonimizados, sujeitos às proteções e
  backups privados existentes. Não são anonimização. Definir política de
  retenção com preservação de idempotência antes de descartar metadados.
- Último evento no painel corresponde à primeira recepção de evento suportado
  persistido. Operacional indica observação do receptor, não prova remota de
  onboarding. Última falha técnica é código fixo e horário em memória por
  processo, zerada no restart; histórico técnico sanitizado fica no journal.
- Migration aditiva `w141126wa`, três tabelas vazias, sem backfill. Downgrade
  recusa remover fatos. Rollback normal conserva o schema e desliga a flag.

Referências oficiais consultadas na preparação:
[verificação e assinatura Meta](https://developers.facebook.com/docs/graph-api/webhooks/getting-started/),
[ecos do Business App](https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/reference/smb_message_echoes).
Revalidar o contrato do app/provedor no onboarding; nenhum evento real foi usado
como fixture. A disponibilidade do echo depende da coexistência efetivamente
habilitada, não apenas da instalação deste código.
