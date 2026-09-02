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
#   nginx     instala o vhost HTTP e recarrega o nginx
#   tls       certbot --nginx (exige o DNS já resolvendo)
#   verificar smoke local, sem paciente real
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
M15_REPORTS_STORAGE_DIR=/opt/soprolife/private/m15-reports
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
  echo "health da API interna: $(curl -fsS http://127.0.0.1:8015/api/v1/health)"

  # O portal NÃO pode estar escutando fora de loopback.
  if ss -tlnp | grep -E ':8016\b' | grep -qv '127.0.0.1'; then
    fail "FATAL: o portal está escutando fora de loopback"
  fi
  echo "OK: 8016 apenas em 127.0.0.1."
}

# ------------------------------------------------------------------- nginx
etapa_nginx() {
  titulo "vhost público"
  command -v nginx >/dev/null || fail "nginx não instalado"
  install -d -m 0755 /var/www/html
  install -m 0644 "$VHOST_FONTE" "/etc/nginx/sites-available/$VHOST_NOME"
  ln -sfn "/etc/nginx/sites-available/$VHOST_NOME" \
          "/etc/nginx/sites-enabled/$VHOST_NOME"
  nginx -t
  systemctl reload nginx
  echo "vhost habilitado. Portas públicas em escuta:"
  ss -tlnp | grep -E ':(80|443)\b' || true
  # Nenhum vhost pode publicar as portas internas.
  if grep -rn "8015\|8765" /etc/nginx/sites-enabled/ 2>/dev/null; then
    fail "FATAL: algum vhost referencia porta interna do Command Center"
  fi
  echo "OK: nenhum vhost publica 8015 nem 8765."
}

# --------------------------------------------------------------------- tls
etapa_tls() {
  titulo "certificado TLS"
  command -v certbot >/dev/null || fail "certbot não instalado (apt install certbot python3-certbot-nginx)"
  getent hosts "$VHOST_NOME" >/dev/null || \
    fail "$VHOST_NOME ainda não resolve — crie o registro DNS antes"
  certbot --nginx -d "$VHOST_NOME" --non-interactive --agree-tos \
    -m contato@soprolife.com.br --redirect
  nginx -t && systemctl reload nginx
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
  echo -n "docs no portal (espera 404): "
  curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8016/docs
  echo "cabeçalhos do portal:"
  curl -sI http://127.0.0.1:8016/p/v1/health | \
    grep -iE 'cache-control|x-robots-tag|x-frame|referrer|content-security'
  systemctl is-active soprolife-portal-resultados.service soprolife-m15-api.service
}

case "$ETAPA" in
  segredos)  etapa_segredos ;;
  banco)     etapa_banco ;;
  servico)   etapa_servico ;;
  nginx)     etapa_nginx ;;
  tls)       etapa_tls ;;
  verificar) etapa_verificar ;;
  todas)
    etapa_segredos
    etapa_banco
    etapa_servico
    etapa_nginx
    etapa_verificar
    titulo "falta apenas"
    cat <<'FIM'
1. DNS (painel do Registro.br), registro EXATO:

     Nome:  resultados-api
     Tipo:  A
     Dado:  <IPv4 público desta VPS>
     TTL:   3600

2. Depois que `getent hosts resultados-api.soprolife.com.br` resolver:

     sudo ./deploy-portal-resultados.sh tls
FIM
    ;;
  *) fail "etapa desconhecida: $ETAPA" ;;
esac
