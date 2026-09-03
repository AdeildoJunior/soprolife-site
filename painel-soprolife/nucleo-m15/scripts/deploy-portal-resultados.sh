#!/usr/bin/env bash
# M26.4 — implantação do PORTAL DE RESULTADOS DO PACIENTE na VPS.
#
# Roda no terminal da própria VPS, como root, e é idempotente: repetir uma
# etapa não duplica segredo, não recria papel de banco e não reescreve um
# EnvironmentFile que já existe.
#
# O que ele NÃO faz, de propósito:
#   * não abre porta do Command Center — a 8015 e a 8765 continuam em
#     loopback, e o vhost publicado só conhece 127.0.0.1:8016;
#   * não cria registro DNS (o DNS da SoproLife é do Registro.br, painel
#     externo) e não emite certificado sozinho — a etapa `tls` existe para
#     ser rodada DEPOIS que o nome resolver;
#   * não envia nada a paciente nenhum.
#
# Etapas (na ordem):
#   segredos  gera os segredos e escreve os dois EnvironmentFiles
#   banco     backup + migration + papel de banco restrito do portal
#   servico   instala/atualiza a unit, sobe e confere o health em loopback
#   ip-publico imprime o IPv4 público desta VPS (o que vai no registro A)
#   nginx     RECONSTRÓI o vhost a partir da fonte versionada e recarrega
#   tls       certbot --nginx (exige o DNS já resolvendo)
#   verificar smoke local + smoke na borda, sem paciente real
#
# M26.5 — a etapa `nginx` deixou de ser "instala se ainda não houver TLS".
# Ela agora monta o vhost com nginx_portal_vhost.py, que relê os caminhos do
# certificado de onde já estiverem: o vhost voltou a ser reproduzível a
# partir do Git, sem que reconstruí-lo derrube o HTTPS.
#
# Uso:
#   sudo ./deploy-portal-resultados.sh todas      # tudo menos tls
#   sudo ./deploy-portal-resultados.sh tls
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

readonly ETAPA="${1:-todas}"
readonly REPO="/opt/soprolife/soprolife-site"
readonly M15_DIR="$REPO/painel-soprolife/nucleo-m15"
readonly PAINEL="$REPO/painel-soprolife"
readonly VENV="/opt/soprolife/venvs/m15/bin/python"
readonly ENV_INTERNO="/opt/soprolife/secrets/m15.env"
readonly ENV_PORTAL="/opt/soprolife/secrets/m26-4-portal.env"
readonly UNIT_FONTE="$PAINEL/systemd/soprolife-portal-resultados.service"
readonly UNIT_DESTINO="/etc/systemd/system/soprolife-portal-resultados.service"
readonly VHOST_NOME="resultados-api.soprolife.com.br"
readonly VHOST_FONTE="$PAINEL/nginx/$VHOST_NOME.conf"
readonly SQL_PAPEL="$M15_DIR/scripts/sql/m26-4-portal-db-role.sql"
readonly RENDER_VHOST="$M15_DIR/scripts/nginx_portal_vhost.py"
readonly REDE_PUBLICA="$M15_DIR/scripts/rede_publica.py"
readonly DOMINIO_PUBLICO="https://soprolife.com.br/resultados"
readonly DB_NAME="soprolife_m15"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
readonly STAMP
readonly BACKUP_DIR="/opt/soprolife/backups/m26-4"

fail() { echo "ERRO: $*" >&2; exit 1; }
titulo() { echo; echo "===== $* ====="; }

[[ $EUID -eq 0 ]] || fail "execute como root (sudo)"
[[ -d "$REPO" ]] || fail "repositório ausente em $REPO"
[[ -x "$VENV" ]] || fail "venv de produção ausente em $VENV"
[[ -f "$UNIT_FONTE" ]] || fail "unit ausente: $UNIT_FONTE"
[[ -f "$VHOST_FONTE" ]] || fail "vhost ausente: $VHOST_FONTE"
[[ -f "$SQL_PAPEL" ]] || fail "SQL do papel ausente: $SQL_PAPEL"
[[ -f "$RENDER_VHOST" ]] || fail "renderizador do vhost ausente: $RENDER_VHOST"
[[ -f "$REDE_PUBLICA" ]] || fail "seletor de IP público ausente: $REDE_PUBLICA"

segredo() { python3 -c "import secrets; print(secrets.token_hex(32))"; }

# ---------------------------------------------------------------- segredos
#
# Dois segredos NOVOS e um terceiro descartável:
#
#   M15_PORTAL_TOKEN_KEY      deriva o link do paciente. Vai SOMENTE para o
#                             EnvironmentFile do serviço interno.
#   M15_PORTAL_SESSION_SECRET assina o cookie do paciente. Vai SOMENTE para
#                             o EnvironmentFile do portal.
#   M15_AUTH_SECRET (portal)  aleatório e descartável. Existe porque a
#                             validação de produção do Settings o exige em
#                             qualquer processo; ele NÃO é o segredo do
#                             Command Center e não abre absolutamente nada.
etapa_segredos() {
  titulo "segredos e EnvironmentFiles"
  [[ -f "$ENV_INTERNO" ]] || fail "EnvironmentFile interno ausente: $ENV_INTERNO"

  if grep -q '^M15_PORTAL_TOKEN_KEY=' "$ENV_INTERNO"; then
    echo "M15_PORTAL_TOKEN_KEY já existe no serviço interno — preservado."
  else
    cp -a "$ENV_INTERNO" "$ENV_INTERNO.bak-$STAMP"
    {
      echo ""
      echo "# M26.4 — portal de resultados do paciente."
      echo "# A chave abaixo DERIVA os links. Nunca copie para o portal."
      echo "M15_PORTAL_ENABLED=true"
      echo "M15_PORTAL_PUBLIC_BASE_URL=$DOMINIO_PUBLICO"
      echo "M15_PORTAL_TOKEN_KEY=$(segredo)"
    } >>"$ENV_INTERNO"
    chmod 0600 "$ENV_INTERNO"
    echo "M15_PORTAL_* acrescentado a $ENV_INTERNO (backup em .bak-$STAMP)."
  fi

  if [[ -f "$ENV_PORTAL" ]]; then
    echo "EnvironmentFile do portal já existe — preservado."
  else
    # A raiz dos PDFs vem do EnvironmentFile INTERNO, e não de um caminho
    # escrito à mão aqui: os dois processos leem os mesmos arquivos, e
    # divergir nisso daria um 503 silencioso no download do paciente.
    local storage
    storage="$(grep -m1 '^M15_REPORTS_STORAGE_DIR=' "$ENV_INTERNO" | cut -d= -f2-)"
    [[ -n "$storage" ]] || fail "M15_REPORTS_STORAGE_DIR ausente em $ENV_INTERNO"
    grep -q "^ReadOnlyPaths=$storage\$" "$UNIT_FONTE" || \
      fail "a unit do portal não libera exatamente $storage em ReadOnlyPaths"
    local db_url senha_portal
    senha_portal="$(segredo)"
    db_url="postgresql+psycopg://soprolife_portal:${senha_portal}@127.0.0.1:5432/${DB_NAME}"
    umask 077
    cat >"$ENV_PORTAL" <<EOF
# M26.4 — superfície PÚBLICA. Este arquivo NÃO contém, e nunca pode conter:
#   M15_PORTAL_TOKEN_KEY  (derivaria links de resultado)
#   o M15_AUTH_SECRET do Command Center (abriria o painel)
M15_ENV=prod
M15_DATABASE_URL=$db_url
M15_AUTH_SECRET=$(segredo)
M15_PORTAL_ENABLED=true
M15_PORTAL_SESSION_SECRET=$(segredo)
M15_PORTAL_PUBLIC_BASE_URL=$DOMINIO_PUBLICO
M15_PORTAL_CORS_ORIGINS=["https://soprolife.com.br"]
M15_PORTAL_SESSION_TTL_MINUTES=30
M15_PORTAL_ACCESS_TTL_DAYS=90
M15_REPORTS_STORAGE_DIR=$storage
EOF
    chown root:root "$ENV_PORTAL"
    chmod 0600 "$ENV_PORTAL"
    echo "Criado $ENV_PORTAL (0600, root:root)."
  fi

  # Sanidade: os dois segredos existem e são DIFERENTES entre si.
  local token_key sessao
  token_key="$(grep -m1 '^M15_PORTAL_TOKEN_KEY=' "$ENV_INTERNO" | cut -d= -f2-)"
  sessao="$(grep -m1 '^M15_PORTAL_SESSION_SECRET=' "$ENV_PORTAL" | cut -d= -f2-)"
  [[ -n "$token_key" && -n "$sessao" ]] || fail "segredos do portal ausentes"
  [[ "$token_key" != "$sessao" ]] || fail "os dois segredos do portal são iguais"
  if grep -q '^M15_PORTAL_TOKEN_KEY=' "$ENV_PORTAL"; then
    fail "FATAL: a chave de derivação vazou para o EnvironmentFile público"
  fi
  echo "OK: chave de derivação ausente do arquivo público; segredos distintos."
}

# ------------------------------------------------------------------- banco
etapa_banco() {
  titulo "backup, migration e papel de banco restrito"
  install -d -m 0700 "$BACKUP_DIR"
  local dump="$BACKUP_DIR/m26-4-pre-$STAMP.dump"
  sudo -u postgres pg_dump -Fc "$DB_NAME" >"$dump"
  chmod 0600 "$dump"
  echo "Backup: $dump ($(stat -c%s "$dump") bytes)"

  ( set -a; . "$ENV_INTERNO"; set +a
    cd "$M15_DIR"
    echo "head antes: $("$VENV" -m alembic current 2>&1 | tail -1)"
    "$VENV" -m alembic upgrade head
    echo "head depois: $("$VENV" -m alembic current 2>&1 | tail -1)"
  )

  # A senha do papel vive num lugar só: o EnvironmentFile do portal. Guardar
  # uma segunda cópia num arquivo auxiliar seria criar um segredo a mais
  # para esquecer de apagar.
  local senha
  senha="$(sed -n 's|^M15_DATABASE_URL=postgresql+psycopg://soprolife_portal:\([^@]*\)@.*|\1|p' "$ENV_PORTAL")"
  [[ -n "$senha" ]] || fail "não achei a senha do papel em $ENV_PORTAL"
  sudo -u postgres psql -d "$DB_NAME" -v "senha='$senha'" -f "$SQL_PAPEL"
  echo "Papel soprolife_portal criado/atualizado."

  # Prova de contenção: o papel do portal NÃO lê o financeiro.
  if PGPASSWORD="$senha" psql -h 127.0.0.1 -U soprolife_portal -d "$DB_NAME" \
      -tAc 'SELECT count(*) FROM financial_entries' >/dev/null 2>&1; then
    fail "FATAL: o papel do portal conseguiu ler financial_entries"
  fi
  echo "OK: o papel do portal NÃO consegue ler financial_entries."
  if PGPASSWORD="$senha" psql -h 127.0.0.1 -U soprolife_portal -d "$DB_NAME" \
      -tAc 'SELECT cpf FROM people LIMIT 1' >/dev/null 2>&1; then
    fail "FATAL: o papel do portal conseguiu ler people.cpf"
  fi
  echo "OK: o papel do portal NÃO consegue ler people.cpf."
  PGPASSWORD="$senha" psql -h 127.0.0.1 -U soprolife_portal -d "$DB_NAME" \
    -tAc 'SELECT count(*) FROM patient_result_accesses' >/dev/null \
    || fail "o papel do portal não consegue ler a própria tabela"
  echo "OK: o papel do portal lê patient_result_accesses."
}

# ----------------------------------------------------------------- serviço
etapa_servico() {
  titulo "unit do portal e reinício da API interna"
  install -m 0644 "$UNIT_FONTE" "$UNIT_DESTINO"
  systemctl daemon-reload
  systemctl enable --now soprolife-portal-resultados.service
  systemctl restart soprolife-portal-resultados.service
  # A API interna precisa reler o EnvironmentFile: é ela que passa a criar
  # o acesso quando um PDF assinado é aceito.
  systemctl restart soprolife-m15-api.service

  local tentativa
  for tentativa in 1 2 3 4 5 6 7 8 9 10; do
    if curl -fsS --max-time 3 http://127.0.0.1:8016/p/v1/health >/dev/null; then
      break
    fi
    [[ $tentativa -lt 10 ]] || fail "portal não respondeu no health em loopback"
    sleep 2
  done
  echo "health do portal: $(curl -fsS http://127.0.0.1:8016/p/v1/health)"

  # A API interna também precisa de espera. Ela carrega um mapa bem maior e
  # leva alguns segundos a mais para atender; medir uma vez, logo depois do
  # restart, produz um "falhou" que na verdade é "ainda não subiu".
  for tentativa in 1 2 3 4 5 6 7 8 9 10; do
    if curl -fsS --max-time 3 http://127.0.0.1:8015/api/v1/health >/dev/null; then
      break
    fi
    [[ $tentativa -lt 10 ]] || fail "API interna não respondeu no health"
    sleep 3
  done
  echo "health da API interna: $(curl -fsS http://127.0.0.1:8015/api/v1/health)"

  # O portal NÃO pode estar escutando fora de loopback.
  if ss -tlnp | grep -E ':8016\b' | grep -qv '127.0.0.1'; then
    fail "FATAL: o portal está escutando fora de loopback"
  fi
  echo "OK: 8016 apenas em 127.0.0.1."
}

# ------------------------------------------------------- tailscale intacto
#
# O `tailscale serve` publica o painel privado em HTTPS no endereço do
# tailnet, e para isso o `tailscaled` ESCUTA a porta 443 desse endereço.
# O nginx convive com ele porque escuta a 443 em endereços explícitos (os
# públicos), nunca em curinga. Um `listen 443 ssl` sozinho faria o nginx
# disputar 0.0.0.0:443: ou ele não sobe, ou o painel privado sai do ar.
#
# Esta função não muda nada. Ela só afirma, depois de cada mexida no nginx,
# que quem manda na 443 do tailnet continua sendo o tailscaled.
etapa_tailscale_intacto() {
  titulo "tailscale serve intacto"
  command -v tailscale >/dev/null || { echo "(tailscale não instalado — nada a conferir)"; return 0; }
  local ip_tailnet
  # `tailscale ip -4` aqui NÃO escolhe endereço público: escolhe justamente
  # o endereço que o nginx não pode tomar.
  ip_tailnet="$(tailscale ip -4 2>/dev/null | head -n 1 || true)"
  [[ -n "$ip_tailnet" ]] || { echo "(sem IP de tailnet — nada a conferir)"; return 0; }

  local dono
  dono="$(ss -tlnpH | awk -v alvo="$ip_tailnet:443" '$4 == alvo {print $0}')"
  if [[ -z "$dono" ]]; then
    echo "AVISO: ninguém escuta $ip_tailnet:443 — o painel por tailscale serve pode estar fora." >&2
  else
    grep -q "tailscaled" <<<"$dono" || \
      fail "FATAL: $ip_tailnet:443 deixou de ser do tailscaled — o painel privado foi tomado"
    echo "OK: $ip_tailnet:443 continua do tailscaled."
  fi

  # E o nginx não pode ter aberto curinga na 443 em lugar nenhum.
  if nginx -T 2>/dev/null | grep -E '^\s*listen\s+(443|\*:443|0\.0\.0\.0:443|\[::\]:443)\s' >/dev/null; then
    fail "FATAL: algum vhost escuta a 443 em curinga — isso disputa a porta com o tailscaled"
  fi
  echo "OK: nenhum vhost escuta a 443 em curinga."
}

# -------------------------------------------------------------- ip público
#
# O endereço que vai para o registro A. NÃO é `hostname -I | head -n 1` nem
# `tailscale ip -4`: os dois entregariam 100.87.98.100, que é CGNAT do
# tailnet — não roteia da internet e ainda publicaria, num registro DNS
# permanente, o endereço interno de administração do Command Center.
etapa_ip_publico() {
  titulo "IPv4 público desta VPS"
  local ip
  ip="$("$VENV" "$REDE_PUBLICA")" || \
    fail "não foi possível determinar um único IPv4 público (veja acima)"
  echo "$ip"
}

# ------------------------------------------------------------------- nginx
etapa_nginx() {
  titulo "vhost público (reconstruído a partir da fonte versionada)"
  command -v nginx >/dev/null || fail "nginx não instalado"
  install -d -m 0755 /var/www/html
  local instalado="/etc/nginx/sites-available/$VHOST_NOME"
  local habilitado="/etc/nginx/sites-enabled/$VHOST_NOME"
  local candidato="/etc/nginx/.$VHOST_NOME.candidato-$STAMP"

  # O render lê os caminhos do certificado do arquivo que já está instalado
  # (é lá que o certbot os escreveu). Sem esse passo, reinstalar a fonte por
  # cima apagaria o TLS — que foi exatamente por que a M26.4 teve de
  # congelar o arquivo e deixar de poder mudá-lo.
  local args=(--fonte "$VHOST_FONTE" --saida "$candidato" --nome "$VHOST_NOME")
  if [[ -f "$instalado" ]]; then
    args+=(--instalado "$instalado")
  fi
  "$VENV" "$RENDER_VHOST" "${args[@]}" || {
    rm -f "$candidato"
    fail "não consegui montar o vhost — nada foi tocado"
  }

  # Troca com rede: o arquivo só fica se o `nginx -t` aprovar a configuração
  # INTEIRA com ele no lugar. Reprovou, o anterior volta e o nginx não chega
  # a ser recarregado.
  local backup=""
  if [[ -f "$instalado" ]]; then
    backup="$instalado.bak-$STAMP"
    cp -a "$instalado" "$backup"
  fi
  install -m 0644 "$candidato" "$instalado"
  rm -f "$candidato"
  ln -sfn "$instalado" "$habilitado"

  # O site padrão do nginx responderia a qualquer Host na porta 80. Não há
  # nada para ele servir nesta máquina, e agora existe um default_server
  # nosso, explícito, no lugar dele.
  # Guardado FORA de sites-enabled (o nginx lê sites-enabled/*), para poder
  # voltar exatamente como estava se o teste de configuração reprovar.
  local default_guardado=""
  if [[ -e /etc/nginx/sites-enabled/default ]]; then
    default_guardado="/etc/nginx/default.sites-enabled.bak-$STAMP"
    mv /etc/nginx/sites-enabled/default "$default_guardado"
  fi

  if ! nginx -t; then
    if [[ -n "$backup" ]]; then
      cp -a "$backup" "$instalado"
    else
      rm -f "$instalado" "$habilitado"
    fi
    if [[ -n "$default_guardado" ]]; then
      mv "$default_guardado" /etc/nginx/sites-enabled/default
    fi
    nginx -t >/dev/null 2>&1 || \
      echo "AVISO: a configuração anterior também não passa em nginx -t" >&2
    fail "nginx -t reprovou o vhost reconstruído — configuração anterior restaurada"
  fi
  if [[ -n "$backup" ]]; then
    echo "backup do vhost anterior: $backup"
  fi

  # Abre 80/443 no ufw — e SOMENTE elas. As portas internas (8015, 8016,
  # 8765, 5432) continuam sem regra: escutam em loopback ou na tailscale0.
  if command -v ufw >/dev/null && ufw status | grep -q "^Status: active"; then
    ufw allow 80/tcp >/dev/null
    ufw allow 443/tcp >/dev/null
    echo "ufw: 80/tcp e 443/tcp liberadas."
  fi
  systemctl reload nginx
  echo "vhost habilitado. Portas públicas em escuta:"
  ss -tlnp | grep -E ':(80|443)\b' || true

  # Nenhum vhost pode publicar as portas internas.
  if grep -rn "8015\|8765" /etc/nginx/sites-enabled/ 2>/dev/null; then
    fail "FATAL: algum vhost referencia porta interna do Command Center"
  fi
  echo "OK: nenhum vhost publica 8015 nem 8765."

  # E o bloco do portal não pode ser o servidor padrão de porta nenhuma: se
  # for, ele atende qualquer Host e qualquer SNI apontado para este IP.
  local efetiva
  efetiva="$(nginx -T 2>/dev/null)"
  if ! grep -q 'listen 80 default_server' <<<"$efetiva"; then
    fail "FATAL: não há default_server explícito na porta 80"
  fi
  if grep -q 'ssl_certificate ' <<<"$efetiva"; then
    grep -q 'ssl_reject_handshake on' <<<"$efetiva" || \
      fail "FATAL: há TLS sem default_server de 443 recusando SNI desconhecido"
    echo "OK: 443 tem default_server com ssl_reject_handshake."
  else
    echo "Estado pré-TLS: ainda não há certificado. Rode a etapa \`tls\`."
  fi
  echo "OK: 80 tem default_server explícito."

  etapa_tailscale_intacto
}

# --------------------------------------------------------------------- tls
etapa_tls() {
  titulo "certificado TLS"
  command -v certbot >/dev/null || fail "certbot não instalado (apt install certbot python3-certbot-nginx)"
  getent hosts "$VHOST_NOME" >/dev/null || \
    fail "$VHOST_NOME ainda não resolve — crie o registro DNS antes"

  # O registro A precisa apontar para um endereço PÚBLICO. Se alguém tiver
  # cadastrado o IP do tailnet (o que é fácil: é o endereço com que a VPS é
  # administrada), o desafio HTTP-01 falharia de qualquer jeito — mas antes
  # disso o registro já teria publicado, para qualquer um que consulte o
  # DNS, o endereço interno de administração do Command Center. Melhor
  # recusar aqui, antes de o certbot sequer tentar.
  # `ahostsv4` já devolve só IPv4, então não há o que filtrar aqui — e um
  # `[[ ... ]] && continue` de cauda derrubaria o script no caso normal, que
  # é justamente quando o teste é falso (`set -e`).
  local resolvido enderecos
  enderecos="$(getent ahostsv4 "$VHOST_NOME" | awk '{print $1}' | sort -u)"
  [[ -n "$enderecos" ]] || fail "$VHOST_NOME não resolve para nenhum IPv4"
  while read -r resolvido; do
    "$VENV" "$REDE_PUBLICA" --verificar "$resolvido" >/dev/null || \
      fail "$VHOST_NOME resolve para $resolvido, que não é um IPv4 público — corrija o registro A antes"
  done <<<"$enderecos"
  echo "OK: $VHOST_NOME resolve apenas para endereço público ($(tr '\n' ' ' <<<"$enderecos"))."

  certbot --nginx -d "$VHOST_NOME" --non-interactive --agree-tos \
    -m contato@soprolife.com.br --redirect

  # O certbot reescreve o vhost à maneira dele — sem `server_tokens off` no
  # bloco de redirecionamento e sem default_server nenhum. Reconstruir logo
  # em seguida devolve o arquivo à forma versionada, agora COM os caminhos
  # do certificado que ele acabou de escrever.
  etapa_nginx

  curl -fsS "https://$VHOST_NOME/p/v1/health"
  echo
  echo "OK: HTTPS respondendo."
}

# --------------------------------------------------------------- verificar
etapa_verificar() {
  titulo "smoke sem paciente real"
  echo -n "health loopback: "; curl -fsS http://127.0.0.1:8016/p/v1/health; echo
  echo -n "token inexistente (espera 401): "
  curl -s -o /dev/null -w '%{http_code}\n' -X POST \
    -H 'Content-Type: application/json' \
    -d '{"token":"naoexiste-000000000000000000000000000000","nascimento":"1900-01-01"}' \
    http://127.0.0.1:8016/p/v1/acesso
  echo -n "sem sessão (espera 401): "
  curl -s -o /dev/null -w '%{http_code}\n' \
    http://127.0.0.1:8016/p/v1/documentos/laudo-assinado
  echo -n "rota administrativa no portal (espera 404): "
  curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8016/api/v1/laudos
  echo -n "e sem falar em laudo (espera vazio): "
  curl -s http://127.0.0.1:8016/api/v1/laudos | grep -io 'laudo\|relatorio' || echo "(vazio)"
  echo -n "docs no portal (espera 404): "
  curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8016/docs
  echo "cabeçalhos do portal:"
  curl -sI http://127.0.0.1:8016/p/v1/health | \
    grep -iE 'cache-control|x-robots-tag|x-frame|referrer|content-security'
  systemctl is-active soprolife-portal-resultados.service soprolife-m15-api.service
  etapa_borda
}

# ------------------------------------------------------------------- borda
#
# O que sai pela INTERNET, e não pelo loopback. A M26.4 mediu os cabeçalhos
# só em 127.0.0.1:8016 — e por isso não viu que o 404 do catch-all, que o
# nginx responde sozinho e que nunca chega à aplicação, saía com HSTS e mais
# nada.
# Os nove de app/portal/security.py::CABECALHOS_SEGUROS, mais o HSTS, que
# só a borda tem como emitir.
CABECALHOS_ESPERADOS=(
  "cache-control" "pragma" "x-content-type-options" "x-frame-options"
  "referrer-policy" "x-robots-tag" "content-security-policy"
  "permissions-policy" "cross-origin-resource-policy"
  "strict-transport-security"
)

etapa_borda() {
  titulo "smoke na borda (nginx), sem paciente real"
  local base cabecalhos
  if curl -fsS --max-time 8 "https://$VHOST_NOME/p/v1/health" >/dev/null 2>&1; then
    base="https://$VHOST_NOME"
  else
    echo "(sem TLS ainda — medindo pela porta 80, com Host explícito)"
    base="http://127.0.0.1"
  fi

  echo -n "health pela borda: "
  curl -fsS --max-time 8 -H "Host: $VHOST_NOME" "$base/p/v1/health"; echo

  local caminho
  for caminho in /qualquercoisa /api/v1/laudos /docs; do
    echo -n "404 do catch-all em $caminho: "
    curl -s -o /dev/null -w '%{http_code}\n' --max-time 8 \
      -H "Host: $VHOST_NOME" "$base$caminho"
    cabecalhos="$(curl -sI --max-time 8 -H "Host: $VHOST_NOME" "$base$caminho" | tr 'A-Z' 'a-z')"
    local esperado
    for esperado in "${CABECALHOS_ESPERADOS[@]}"; do
      grep -q "^$esperado:" <<<"$cabecalhos" || \
        fail "FATAL: o 404 de $caminho saiu sem o cabeçalho $esperado"
    done
  done
  echo "OK: o 404 do catch-all leva os ${#CABECALHOS_ESPERADOS[@]} cabeçalhos de segurança."

  # Exatamente UMA cópia de cada: o `proxy_hide_header` das rotas proxiadas
  # descarta a cópia da aplicação, e quem emite na borda é o bloco `server`.
  cabecalhos="$(curl -sI --max-time 8 -H "Host: $VHOST_NOME" "$base/p/v1/health" | tr 'A-Z' 'a-z')"
  local repetido
  for repetido in "${CABECALHOS_ESPERADOS[@]}"; do
    local quantas
    quantas="$(grep -c "^$repetido:" <<<"$cabecalhos" || true)"
    [[ "$quantas" == "1" ]] || \
      fail "FATAL: $repetido apareceu $quantas vezes na resposta do portal"
  done
  echo "OK: nenhum cabeçalho de segurança duplicado nas rotas proxiadas."

  # E a versão do nginx não é assunto de quem varre.
  if curl -sI --max-time 8 -H "Host: $VHOST_NOME" "$base/qualquercoisa" | grep -qiE '^server:.*nginx/'; then
    fail "FATAL: a borda está anunciando a versão do nginx"
  fi
  echo "OK: a borda não anuncia a versão do nginx."
}

case "$ETAPA" in
  segredos)   etapa_segredos ;;
  banco)      etapa_banco ;;
  servico)    etapa_servico ;;
  ip-publico) etapa_ip_publico ;;
  nginx)      etapa_nginx ;;
  tls)        etapa_tls ;;
  verificar)  etapa_verificar ;;
  borda)      etapa_borda ;;
  tailscale)  etapa_tailscale_intacto ;;
  todas)
    etapa_segredos
    etapa_banco
    etapa_servico
    etapa_nginx
    etapa_verificar
    if curl -fsS --max-time 8 "https://$VHOST_NOME/p/v1/health" >/dev/null 2>&1; then
      titulo "nada falta"
      echo "DNS e TLS já estão de pé: https://$VHOST_NOME/p/v1/health responde."
    else
      titulo "falta apenas"
      echo "1. DNS (painel do Registro.br), registro EXATO:"
      echo
      echo "     Nome:  resultados-api"
      echo "     Tipo:  A"
      # Impresso, e não deixado como <placeholder>: um operador que preenche
      # o registro A com `hostname -I | head -n 1` publica o IP do tailnet.
      echo "     Dado:  $("$VENV" "$REDE_PUBLICA" || echo '<indeterminado — rode a etapa ip-publico>')"
      echo "     TTL:   3600"
      echo
      echo "2. Depois que \`getent hosts $VHOST_NOME\` resolver:"
      echo
      echo "     sudo ./deploy-portal-resultados.sh tls"
    fi
    ;;
  *) fail "etapa desconhecida: $ETAPA" ;;
esac
