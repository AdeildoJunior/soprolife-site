#!/usr/bin/env python3
"""M26.5 — monta o vhost público do portal a partir da fonte versionada.

Antes desta etapa o vhost instalado tinha um dono só: o certbot. A fonte em
`painel-soprolife/nginx/` descrevia o estado PRÉ-TLS, o `certbot --nginx`
promovia o bloco para 443 no arquivo instalado, e a partir dali o deploy se
recusava a tocar nele — reinstalar a fonte por cima apagaria os caminhos do
certificado e derrubaria o HTTPS. O efeito colateral é que **mudar uma
regra do vhost deixava de ser possível sem trabalho manual na VPS**, e o
arquivo que está de fato na internet parava de ser reproduzível a partir do
Git.

Este renderizador desfaz esse nó. Ele lê o `server { … }` da fonte, LÊ os
caminhos do certificado de onde eles já estiverem (o arquivo instalado, ou
`/etc/letsencrypt/live/<nome>/`) e reemite o vhost inteiro. O certificado
nunca é inventado nem movido; só é reencontrado.

E ele resolve, de passagem, três coisas medidas em produção na M26.4:

1. **`listen 443` curinga.** O bloco do portal era o único a escutar 443 e,
   por omissão do nginx, virava o `default_server` — atendia qualquer SNI
   apontado para o IP. Agora existe um `default_server` explícito com
   `ssl_reject_handshake on`, e o portal atende só o próprio nome.
2. **Vazamento de versão na porta 80.** O bloco de redirecionamento gerado
   pelo certbot não tem `server_tokens off`; `curl -I http://<ip>/`
   devolvia `Server: nginx/1.24.0 (Ubuntu)`. O bloco emitido aqui tem.
3. **404 sem cabeçalhos.** Tratado na fonte (cabeçalhos no nível do
   `server`), e verificado aqui: um render sem eles é recusado.

Uso:
    nginx_portal_vhost.py --fonte F --saida S [--instalado I]
                          [--nome N] [--letsencrypt-raiz R]

Sai com 0 e escreve o render em `--saida`. Qualquer inconsistência é erro
com mensagem, e nada é escrito.
"""

from __future__ import annotations

import argparse
import dataclasses
import re
import sys
from pathlib import Path

NOME_PADRAO = "resultados-api.soprolife.com.br"

# Todo cabeçalho que o render PRECISA declarar no bloco do portal. É a
# mesma lista de `app/portal/security.py::CABECALHOS_SEGUROS`; o teste
# `test_borda_e_aplicacao_nao_divergem` compara as duas.
CABECALHOS_OBRIGATORIOS = (
    "Cache-Control",
    "Pragma",
    "X-Content-Type-Options",
    "X-Frame-Options",
    "Referrer-Policy",
    "X-Robots-Tag",
    "Content-Security-Policy",
    "Permissions-Policy",
    "Cross-Origin-Resource-Policy",
)

# Portas internas que jamais podem aparecer num arquivo publicado.
PORTAS_PROIBIDAS = ("8015", "8765", "5432")


class ErroDeRender(Exception):
    """Falha fechada: melhor não gerar vhost nenhum do que gerar um errado."""


# Um `listen` de 443 SEM endereço (`443`, `[::]:443`, `0.0.0.0:443`) faz o
# nginx tentar 0.0.0.0:443 — e nesta VPS a porta 443 do endereço do tailnet
# pertence ao `tailscaled`, que serve o painel por `tailscale serve`. Um
# curinga aqui colide com ele: ou o nginx não sobe, ou o painel privado sai
# do ar. Por isso o render EXIGE endereço explícito na 443, e recusa emitir
# qualquer coisa que não seja isso.
CURINGAS_443 = ("443", "*:443", "0.0.0.0:443", "[::]:443", "[::0]:443")


@dataclasses.dataclass(frozen=True)
class MaterialTLS:
    certificado: str
    chave: str
    opcoes: str | None = None
    dhparam: str | None = None
    # ex.: ("187.127.39.5:443", "[2a02:4780:6e:665::1]:443")
    enderecos: tuple[str, ...] = ()


# --------------------------------------------------------------- leitura


def _sem_comentarios(texto: str) -> str:
    """Máscara do MESMO comprimento, com todo comentário virado espaço.

    Os índices continuam valendo para o texto original, então dá para achar
    e contar chaves aqui e fatiar lá. Sem isso, `ssl_certificate` comentado
    (o estado em que a M26.4 encontrou o vhost) seria lido como certificado
    presente, e um `server {` citado num comentário viraria um bloco.
    """

    saida = []
    em_comentario = False
    em_aspas = False
    for caractere in texto:
        if caractere == "\n":
            em_comentario = False
            saida.append(caractere)
            continue
        if em_comentario:
            saida.append(" ")
            continue
        if caractere == '"':
            em_aspas = not em_aspas
        elif caractere == "#" and not em_aspas:
            em_comentario = True
            saida.append(" ")
            continue
        saida.append(caractere)
    return "".join(saida)


def _bloco_server(texto: str) -> tuple[str, str]:
    """Devolve (prefixo, corpo-do-único-server) da fonte.

    O corpo sai sem as linhas `listen` e `server_name`: quem decide em que
    porta e com que nome o bloco vive é o render, não a fonte.
    """

    codigo = _sem_comentarios(texto)
    inicio = codigo.find("server {")
    if inicio < 0:
        raise ErroDeRender("a fonte não tem bloco `server {`")
    if codigo.find("server {", inicio + 1) >= 0:
        raise ErroDeRender("a fonte tem mais de um bloco `server` — não sei qual publicar")

    # O cabeçalho em prosa da fonte fala do arquivo-fonte, não do arquivo
    # instalado — e cita as portas 8015/8765 para explicar que elas NÃO são
    # publicadas. Copiado para o destino, faria a guarda literal do deploy
    # (`grep -rn "8015\|8765" /etc/nginx/sites-enabled/`) abortar com FATAL.
    # Do prefixo só interessam as diretivas: as zonas de limite.
    prefixo = "\n".join(
        linha
        for linha in texto[:inicio].splitlines()
        if linha.strip() and not linha.lstrip().startswith("#")
    ).strip()
    profundidade = 0
    fim = -1
    for pos in range(inicio, len(codigo)):
        if codigo[pos] == "{":
            profundidade += 1
        elif codigo[pos] == "}":
            profundidade -= 1
            if profundidade == 0:
                fim = pos
                break
    if fim < 0:
        raise ErroDeRender("bloco `server` da fonte não fecha")

    interior = texto[codigo.index("{", inicio) + 1 : fim]
    linhas = [
        linha
        for linha in interior.splitlines()
        if not re.match(r"^\s*(listen|server_name)\s", linha)
    ]
    corpo = "\n".join(linhas).strip("\n")
    return prefixo, corpo


def _valor(texto: str, diretiva: str) -> str | None:
    achado = re.search(rf"^\s*{diretiva}\s+([^;]+);", _sem_comentarios(texto), re.MULTILINE)
    return achado.group(1).strip() if achado else None


def enderecos_443(texto: str) -> tuple[str, ...]:
    """Os endereços em que este vhost escuta 443, na ordem em que aparecem.

    São dado de MÁQUINA, não de repositório: dependem do IP público que a
    VPS tem hoje. Por isso são relidos do que está instalado em vez de
    ficarem escritos na fonte versionada.
    """

    achados: list[str] = []
    for linha in re.findall(r"^\s*listen\s+([^;]+);", _sem_comentarios(texto), re.MULTILINE):
        alvo = linha.split()[0]
        if ":443" not in alvo and alvo != "443":
            continue
        if alvo in CURINGAS_443:
            raise ErroDeRender(
                f"o vhost instalado escuta `listen {alvo}` (curinga) na 443. "
                "Nesta VPS a 443 do endereço do tailnet é do tailscaled; "
                "reemitir um curinga derrubaria o `tailscale serve`."
            )
        if alvo not in achados:
            achados.append(alvo)
    return tuple(achados)


def tls_do_instalado(texto: str) -> MaterialTLS | None:
    """Recupera o material TLS de um vhost já instalado (posto lá pelo certbot)."""

    certificado = _valor(texto, "ssl_certificate")
    chave = _valor(texto, "ssl_certificate_key")
    if not certificado or not chave:
        return None
    opcoes = None
    achado = re.search(
        r"^\s*include\s+(\S*options-ssl-nginx\.conf);", _sem_comentarios(texto), re.MULTILINE
    )
    if achado:
        opcoes = achado.group(1)
    return MaterialTLS(
        certificado, chave, opcoes, _valor(texto, "ssl_dhparam"), enderecos_443(texto)
    )


def tls_do_letsencrypt(raiz: Path, nome: str) -> MaterialTLS | None:
    """Acha o certificado pelo caminho canônico, quando não há vhost instalado."""

    vivo = raiz / "live" / nome
    certificado = vivo / "fullchain.pem"
    chave = vivo / "privkey.pem"
    if not (certificado.exists() and chave.exists()):
        return None
    opcoes = raiz / "options-ssl-nginx.conf"
    dhparam = raiz / "ssl-dhparams.pem"
    return MaterialTLS(
        str(certificado),
        str(chave),
        str(opcoes) if opcoes.exists() else None,
        str(dhparam) if dhparam.exists() else None,
        (),  # quem sabe os endereços é o vhost instalado, ou o `--listen-443`
    )


# ----------------------------------------------------------------- render


_CABECALHO_CATCH_ALL = """\
# ---------------------------------------------------------------------
# CATCH-ALL EXPLÍCITO — M26.5.
#
# Sem estes blocos, o vhost do portal é o primeiro (e único) a escutar as
# portas públicas e o nginx o elege `default_server`: ele passaria a
# responder a qualquer `Host` e a qualquer SNI que aponte para este IP,
# inclusive nomes de terceiros. Aqui a porta 80 fecha a conexão sem
# resposta (444) e a 443 recusa o handshake antes de haver requisição.
# ---------------------------------------------------------------------
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;
    server_tokens off;
    access_log off;
    return 444;
}"""

def _catch_all_443(enderecos: tuple[str, ...]) -> str:
    escutas = "\n".join(f"    listen {alvo} ssl default_server;" for alvo in enderecos)
    return (
        "server {\n"
        "    # `ssl_reject_handshake` recusa o SNI desconhecido sem precisar de\n"
        "    # certificado nenhum — nada deste servidor é apresentado a quem não\n"
        "    # pediu pelo nome certo.\n"
        "    #\n"
        "    # Os endereços são explícitos porque a 443 do endereço do tailnet\n"
        "    # pertence ao tailscaled (`tailscale serve` publica o painel ali).\n"
        "    # Um curinga aqui tiraria o painel privado do ar.\n"
        f"{escutas}\n"
        "    server_name _;\n"
        "    ssl_reject_handshake on;\n"
        "    access_log off;\n"
        "}"
    )


def _bloco_tls(tls: MaterialTLS) -> str:
    linhas = [
        f"    ssl_certificate {tls.certificado};",
        f"    ssl_certificate_key {tls.chave};",
    ]
    if tls.opcoes:
        linhas.append(f"    include {tls.opcoes};")
    if tls.dhparam:
        linhas.append(f"    ssl_dhparam {tls.dhparam};")
    return "\n".join(linhas)


def renderizar(fonte: str, nome: str, tls: MaterialTLS | None) -> str:
    prefixo, corpo = _bloco_server(fonte)
    partes = [
        "# ARQUIVO GERADO por nucleo-m15/scripts/nginx_portal_vhost.py (M26.5).",
        "# Não edite aqui: edite painel-soprolife/nginx/"
        f"{nome}.conf e rode",
        "#   sudo ./scripts/deploy-portal-resultados.sh nginx",
        "# Os caminhos do certificado abaixo foram LIDOS do que já existe na",
        "# máquina; este arquivo pode ser regerado sem derrubar o HTTPS.",
        "",
        prefixo,
        "",
        _CABECALHO_CATCH_ALL,
    ]

    if tls is None:
        # Estado pré-TLS: o portal mora na 80, e o desafio ACME que já vem
        # no corpo é o que permite o certbot trabalhar.
        partes += [
            "",
            "# Estado PRÉ-TLS: sem certificado nesta máquina ainda. Rode",
            "# `deploy-portal-resultados.sh tls` depois que o DNS resolver.",
            "server {",
            "    listen 80;",
            "    listen [::]:80;",
            f"    server_name {nome};",
            "",
            corpo,
            "}",
            "",
        ]
        return "\n".join(partes)

    if not tls.enderecos:
        raise ErroDeRender(
            "há material TLS mas nenhum endereço explícito de escuta na 443. "
            "Informe com --listen-443 (ex.: --listen-443 187.127.39.5:443): "
            "emitir `listen 443 ssl` colidiria com o tailscaled."
        )

    partes += [
        "",
        _catch_all_443(tls.enderecos),
        "",
        "# Porta 80 do portal: só o desafio do Let's Encrypt e o",
        "# redirecionamento. Diferente do bloco que o `certbot --nginx`",
        "# gera, este tem `server_tokens off` — o do certbot devolvia",
        "# `Server: nginx/1.24.0 (Ubuntu)` a quem varre.",
        "server {",
        "    listen 80;",
        "    listen [::]:80;",
        f"    server_name {nome};",
        "    server_tokens off;",
        "    access_log off;",
        "    error_log /var/log/nginx/resultados-api.error.log warn;",
        "",
        "    location ^~ /.well-known/acme-challenge/ {",
        "        root /var/www/html;",
        "    }",
        "",
        "    location / {",
        "        return 301 https://$host$request_uri;",
        "    }",
        "}",
        "",
        "# O portal. Escuta nos endereços PÚBLICOS, um a um: a 443 do endereço",
        "# do tailnet é do tailscaled, e um curinga a tomaria dele.",
        "server {",
        *[f"    listen {alvo} ssl;" for alvo in tls.enderecos],
        f"    server_name {nome};",
        _bloco_tls(tls),
        "",
        corpo,
        "}",
        "",
    ]
    return "\n".join(partes)


# -------------------------------------------------------------- validação


def _blocos_server(texto: str) -> list[str]:
    codigo = _sem_comentarios(texto)
    blocos = []
    for achado in re.finditer(r"^server\s*\{", codigo, re.MULTILINE):
        inicio = achado.start()
        profundidade = 0
        for pos in range(inicio, len(codigo)):
            if codigo[pos] == "{":
                profundidade += 1
            elif codigo[pos] == "}":
                profundidade -= 1
                if profundidade == 0:
                    blocos.append(texto[inicio : pos + 1])
                    break
    return blocos


def validar(render: str, nome: str) -> list[str]:
    """Lista de problemas. Vazia = o render pode ser instalado."""

    problemas: list[str] = []
    blocos = _blocos_server(render)
    if not blocos:
        return ["o render não tem nenhum bloco `server`"]

    codigo = _sem_comentarios(render)
    # De propósito no texto CRU, comentários inclusive: é assim que a guarda
    # literal do script de deploy procura, e recusar aqui é melhor que
    # descobrir com o vhost já instalado.
    for porta in PORTAS_PROIBIDAS:
        if re.search(rf"\b{porta}\b", render):
            problemas.append(
                f"o render menciona a porta interna {porta} — nem em comentário"
            )

    portal = [b for b in blocos if "/p/v1/" in b]
    if len(portal) != 1:
        problemas.append(f"esperava exatamente 1 bloco com /p/v1/, achei {len(portal)}")

    for bloco in blocos:
        # Toda checagem daqui para baixo olha o CÓDIGO, nunca o comentário:
        # um `listen 443` citado numa explicação não é um listener.
        nu = _sem_comentarios(bloco)
        listens = re.findall(r"^\s*listen\s+([^;]+);", nu, re.MULTILINE)
        eh_portal = "/p/v1/" in nu
        tem_443 = any("443" in linha for linha in listens)
        rejeita = "ssl_reject_handshake on" in nu
        server_name = _valor(bloco, "server_name")
        eh_default = any("default_server" in linha for linha in listens)

        if server_name is None:
            problemas.append("bloco `server` sem `server_name` explícito")
        if tem_443 and not rejeita and not _valor(bloco, "ssl_certificate"):
            problemas.append("bloco escuta 443 sem `ssl_certificate` e sem `ssl_reject_handshake`")
        for linha in listens:
            alvo = linha.split()[0]
            if (":443" in alvo or alvo == "443") and alvo in CURINGAS_443:
                problemas.append(
                    f"`listen {alvo}` é curinga na 443 — colide com o tailscaled, "
                    "que serve o painel privado nessa porta no endereço do tailnet"
                )
        if eh_portal:
            if eh_default:
                problemas.append(
                    "o bloco do portal está marcado `default_server` — ele voltaria "
                    "a atender qualquer Host/SNI"
                )
            if server_name != nome:
                problemas.append(f"o bloco do portal responde por `{server_name}`, não por `{nome}`")
            for cabecalho in CABECALHOS_OBRIGATORIOS:
                if not re.search(rf"^\s*add_header\s+{re.escape(cabecalho)}\s", nu, re.MULTILINE):
                    problemas.append(f"o bloco do portal não declara add_header {cabecalho}")
            if not re.search(r"^\s*location\s+/\s*\{\s*\n\s*return 404;", nu, re.MULTILINE):
                problemas.append("o bloco do portal não tem o catch-all `location / { return 404; }`")
        elif eh_default and not rejeita and "return 444" not in nu and "return 301" not in nu:
            problemas.append("bloco `default_server` que não recusa nem redireciona")

    if tem_tls := ("ssl_certificate " in codigo):
        if not any("ssl_reject_handshake on" in _sem_comentarios(b) for b in blocos):
            problemas.append("há TLS mas não há `default_server` de 443 recusando SNI desconhecido")
    if not tem_tls and any("443" in _sem_comentarios(b) for b in blocos):
        problemas.append("há `listen 443` sem certificado em lugar nenhum do render")

    return problemas


# -------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fonte", required=True, type=Path)
    parser.add_argument("--saida", required=True, type=Path)
    parser.add_argument("--instalado", type=Path)
    parser.add_argument("--nome", default=NOME_PADRAO)
    parser.add_argument("--letsencrypt-raiz", type=Path, default=Path("/etc/letsencrypt"))
    parser.add_argument(
        "--listen-443",
        action="append",
        default=[],
        metavar="ENDERECO:443",
        help=(
            "endereço explícito de escuta na 443 (repetível). Só é preciso "
            "quando não há vhost instalado de onde reler; nunca use curinga."
        ),
    )
    args = parser.parse_args(argv)

    fonte = args.fonte.read_text(encoding="utf-8")

    tls = None
    origem_tls = "nenhuma (estado pré-TLS)"
    if args.instalado and args.instalado.exists():
        tls = tls_do_instalado(args.instalado.read_text(encoding="utf-8"))
        if tls:
            origem_tls = f"vhost instalado ({args.instalado})"
    if tls is None:
        tls = tls_do_letsencrypt(args.letsencrypt_raiz, args.nome)
        if tls:
            origem_tls = f"{args.letsencrypt_raiz}/live/{args.nome}"

    if tls is not None and args.listen_443:
        tls = dataclasses.replace(tls, enderecos=tuple(args.listen_443))

    try:
        render = renderizar(fonte, args.nome, tls)
    except ErroDeRender as erro:
        print(f"ERRO: {erro}", file=sys.stderr)
        return 2

    problemas = validar(render, args.nome)
    if problemas:
        print("ERRO: o render não passou na validação:", file=sys.stderr)
        for problema in problemas:
            print(f"  - {problema}", file=sys.stderr)
        return 3

    args.saida.write_text(render, encoding="utf-8")
    escutas = ", ".join(tls.enderecos) if tls else "—"
    print(f"render escrito em {args.saida}; material TLS: {origem_tls}; escuta 443: {escutas}")
    return 0


if __name__ == "__main__":  # pragma: no cover - entrada de linha de comando
    raise SystemExit(main())
