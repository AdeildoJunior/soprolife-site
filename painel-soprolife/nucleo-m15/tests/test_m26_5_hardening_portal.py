"""M26.5 — endurecimento do portal de resultados.

Cinco defeitos que a M26.4 deixou, e que só ficaram visíveis depois de o
portal estar de pé na internet:

1. o pool do SQLAlchemy entregava conexão morta depois de ociosidade longa —
   e "ociosidade longa" é o estado normal de um portal público;
2. o vhost, uma vez sob o certbot, deixava de ser reconstruível a partir do
   Git sem derrubar o HTTPS;
3. o bloco do portal era o único a escutar 443 e virava `default_server`:
   atendia qualquer SNI apontado para o IP;
4. o 404 do catch-all e o 301 do redirecionamento saíam sem os cabeçalhos de
   segurança, e o segundo ainda anunciava `nginx/1.24.0 (Ubuntu)`;
5. o registro DNS pedia "o IPv4 público desta VPS" sem nenhuma guarda contra
   o candidato mais à mão, que é o endereço CGNAT do tailnet.

Os testes aqui não repetem o contrato da M26.4 (que vive em
`test_m26_4_portal_resultados.py`): cobrem exclusivamente o que mudou.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from app.db import POOL_RECYCLE_SEGUNDOS, build_engine
from app.portal.security import CABECALHOS_SEGUROS

NUCLEO = Path(__file__).resolve().parents[1]
SCRIPTS = NUCLEO / "scripts"
PAINEL = NUCLEO.parent
VHOST_NOME = "resultados-api.soprolife.com.br"
FONTE_VHOST = PAINEL / "nginx" / f"{VHOST_NOME}.conf"
DEPLOY = SCRIPTS / "deploy-portal-resultados.sh"
DOC = PAINEL / "docs" / "m26-4-portal-resultados-paciente.md"

sys.path.insert(0, str(SCRIPTS))
import nginx_portal_vhost as vhost  # noqa: E402
import rede_publica  # noqa: E402


# ==================================================================== pool


def _mata_a_conexao_fisica(engine) -> None:
    """Devolve uma conexão ao pool e mata o socket dela por fora.

    É o que o PostgreSQL faz com `idle_session_timeout`, e o que um firewall
    com estado faz com um fluxo TCP parado tempo demais: o pool continua
    achando que tem uma conexão boa guardada.
    """

    conexao = engine.connect()
    fisica = conexao.connection.dbapi_connection
    conexao.close()
    fisica.close()


def test_build_engine_liga_pool_pre_ping(tmp_path):
    engine = build_engine(f"sqlite:///{tmp_path}/pre_ping.db")
    try:
        assert engine.pool._pre_ping is True
    finally:
        engine.dispose()


def test_build_engine_liga_pre_ping_tambem_no_postgres():
    # `create_engine` não conecta; isto valida a fiação, não o servidor.
    engine = build_engine("postgresql+psycopg://u:p@127.0.0.1:5432/x")
    try:
        assert engine.pool._pre_ping is True
        assert engine.pool._recycle == POOL_RECYCLE_SEGUNDOS
    finally:
        engine.dispose()


def test_sqlite_nao_recicla_por_idade(tmp_path):
    """Reciclar por idade é para conexão de rede. Arquivo local não morre só."""

    engine = build_engine(f"sqlite:///{tmp_path}/recycle.db")
    try:
        assert engine.pool._recycle == -1
    finally:
        engine.dispose()


def test_conexao_stale_e_substituida_sem_erro(tmp_path):
    engine = build_engine(f"sqlite:///{tmp_path}/stale.db")
    try:
        with engine.connect() as conexao:
            assert conexao.exec_driver_sql("select 1").scalar() == 1
        _mata_a_conexao_fisica(engine)
        # Sem o pre-ping, esta é a requisição que o paciente perde.
        with engine.connect() as conexao:
            assert conexao.exec_driver_sql("select 1").scalar() == 1
    finally:
        engine.dispose()


def test_o_teste_de_stale_nao_e_vacuo(tmp_path):
    """Prova que a mesma manobra QUEBRA um engine sem pre-ping.

    Sem esta contraprova, o teste acima passaria mesmo que `pool_pre_ping`
    fosse removido do `build_engine`.
    """

    engine = create_engine(
        f"sqlite:///{tmp_path}/vacuo.db",
        future=True,
        pool_pre_ping=False,
        connect_args={"check_same_thread": False},
    )
    try:
        with engine.connect() as conexao:
            conexao.exec_driver_sql("select 1")
        _mata_a_conexao_fisica(engine)
        with pytest.raises(Exception):
            with engine.connect() as conexao:
                conexao.exec_driver_sql("select 1")
    finally:
        engine.dispose()


# ================================================================== vhost


# A forma REAL do vhost instalado na VPS. Os `listen` da 443 têm endereço
# explícito, e o comentário de quem os escreveu diz por quê: o `tailscaled`
# ocupa a 443 do endereço do tailnet, servindo o painel privado por
# `tailscale serve`. Um `listen 443 ssl` curinga colidiria com ele.
IPV4_PUBLICO = "187.127.39.5"
IPV6_PUBLICO = "[2a02:4780:6e:665::1]"
CERTBOT_INSTALADO = f"""\
server {{
    listen {IPV6_PUBLICO}:443 ssl; # M26.4: endereco publico explicito (tailscaled ocupa :443 no tailnet)
    listen {IPV4_PUBLICO}:443 ssl; # M26.4: endereco publico explicito (tailscaled ocupa :443 no tailnet)
    server_name {VHOST_NOME};
    ssl_certificate /etc/letsencrypt/live/{VHOST_NOME}/fullchain.pem; # managed by Certbot
    ssl_certificate_key /etc/letsencrypt/live/{VHOST_NOME}/privkey.pem; # managed by Certbot
    include /etc/letsencrypt/options-ssl-nginx.conf; # managed by Certbot
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem; # managed by Certbot
}}
"""


@pytest.fixture()
def fonte() -> str:
    return FONTE_VHOST.read_text(encoding="utf-8")


@pytest.fixture()
def render_pre_tls(fonte) -> str:
    return vhost.renderizar(fonte, VHOST_NOME, None)


@pytest.fixture()
def render_com_tls(fonte) -> str:
    material = vhost.tls_do_instalado(CERTBOT_INSTALADO)
    assert material is not None
    return vhost.renderizar(fonte, VHOST_NOME, material)


def test_render_pre_tls_passa_na_validacao(render_pre_tls):
    assert vhost.validar(render_pre_tls, VHOST_NOME) == []


def test_render_com_tls_passa_na_validacao(render_com_tls):
    assert vhost.validar(render_com_tls, VHOST_NOME) == []


def test_render_pre_tls_nao_tem_443(render_pre_tls):
    """O impasse da M26.4: `listen 443 ssl` sem certificado reprova em nginx -t."""

    assert "443" not in vhost._sem_comentarios(render_pre_tls)
    assert "ssl_certificate" not in vhost._sem_comentarios(render_pre_tls)


def test_render_com_tls_preserva_o_certificado_do_certbot(render_com_tls):
    """Reconstruir o vhost não pode derrubar o HTTPS."""

    assert f"ssl_certificate /etc/letsencrypt/live/{VHOST_NOME}/fullchain.pem;" in render_com_tls
    assert f"ssl_certificate_key /etc/letsencrypt/live/{VHOST_NOME}/privkey.pem;" in render_com_tls
    assert "include /etc/letsencrypt/options-ssl-nginx.conf;" in render_com_tls
    assert "ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;" in render_com_tls


def test_reconstruir_e_ponto_fixo(fonte, render_com_tls):
    """Rodar a etapa `nginx` duas vezes tem de dar exatamente o mesmo arquivo.

    O render lê o material TLS do arquivo instalado; se o instalado for o
    render anterior, o resultado precisa ser idêntico — senão cada deploy
    de rotina mexeria no vhost público.
    """

    material = vhost.tls_do_instalado(render_com_tls)
    assert material is not None
    assert vhost.renderizar(fonte, VHOST_NOME, material) == render_com_tls


def test_certificado_comentado_nao_conta_como_tls():
    """A armadilha exata da M26.4: cert comentado à espera do certbot."""

    comentado = (
        "server {\n"
        "    listen 80;\n"
        f"    server_name {VHOST_NOME};\n"
        "    # ssl_certificate /etc/letsencrypt/live/x/fullchain.pem;\n"
        "    # ssl_certificate_key /etc/letsencrypt/live/x/privkey.pem;\n"
        "}\n"
    )
    assert vhost.tls_do_instalado(comentado) is None


def test_o_portal_nunca_e_o_servidor_padrao(render_com_tls):
    blocos = vhost._blocos_server(render_com_tls)
    portal = [b for b in blocos if "/p/v1/" in vhost._sem_comentarios(b)]
    assert len(portal) == 1
    assert "default_server" not in vhost._sem_comentarios(portal[0])


def test_existe_catch_all_explicito_nas_duas_portas(render_com_tls):
    nu = vhost._sem_comentarios(render_com_tls)
    assert "listen 80 default_server;" in nu
    # O catch-all de 443 também é por endereço — pelo mesmo motivo do portal.
    assert f"listen {IPV4_PUBLICO}:443 ssl default_server;" in nu
    assert f"listen {IPV6_PUBLICO}:443 ssl default_server;" in nu
    # 443 desconhecido não ganha nem resposta: o handshake é recusado.
    assert "ssl_reject_handshake on;" in nu
    # 80 desconhecido não ganha corpo nem versão de software.
    assert "return 444;" in nu


def test_a_443_nunca_sai_como_curinga(render_com_tls):
    """O `tailscale serve` publica o painel privado na 443 do tailnet.

    `listen 443 ssl` faz o nginx tentar 0.0.0.0:443 e disputar essa porta
    com o `tailscaled`. O resultado seria o nginx não subir — ou o painel
    privado sair do ar. Nenhum dos dois é aceitável num deploy de rotina.
    """

    for linha in re.findall(
        r"^\s*listen\s+([^;]+);", vhost._sem_comentarios(render_com_tls), re.MULTILINE
    ):
        alvo = linha.split()[0]
        if ":443" in alvo or alvo == "443":
            assert alvo not in vhost.CURINGAS_443, f"`listen {alvo}` é curinga na 443"
            assert alvo in (f"{IPV4_PUBLICO}:443", f"{IPV6_PUBLICO}:443")


def test_os_enderecos_de_escuta_sao_relidos_do_instalado():
    """São dado de máquina, não de repositório: dependem do IP que a VPS tem."""

    assert vhost.enderecos_443(CERTBOT_INSTALADO) == (
        f"{IPV6_PUBLICO}:443",
        f"{IPV4_PUBLICO}:443",
    )


@pytest.mark.parametrize("curinga", ["443", "[::]:443", "0.0.0.0:443"])
def test_curinga_no_instalado_e_recusado(curinga):
    instalado = CERTBOT_INSTALADO.replace(f"{IPV4_PUBLICO}:443", curinga, 1)
    with pytest.raises(vhost.ErroDeRender) as erro:
        vhost.enderecos_443(instalado)
    assert "tailscale" in str(erro.value).lower()


def test_tls_sem_endereco_nao_rende_nada(fonte):
    """Falha fechada: melhor não gerar vhost que gerar um que toma a 443."""

    sem_endereco = vhost.MaterialTLS("/c.pem", "/k.pem", None, None, ())
    with pytest.raises(vhost.ErroDeRender) as erro:
        vhost.renderizar(fonte, VHOST_NOME, sem_endereco)
    assert "--listen-443" in str(erro.value)


def test_validar_reprova_curinga_de_443(render_com_tls):
    estragado = render_com_tls.replace(f"    listen {IPV4_PUBLICO}:443 ssl;\n",
                                       "    listen 443 ssl;\n")
    assert estragado != render_com_tls
    problemas = vhost.validar(estragado, VHOST_NOME)
    assert any("curinga" in p for p in problemas)


def test_o_redirecionamento_nao_anuncia_a_versao_do_nginx(render_com_tls):
    blocos = vhost._blocos_server(render_com_tls)
    redirecionamento = [b for b in blocos if "return 301" in vhost._sem_comentarios(b)]
    assert len(redirecionamento) == 1
    assert "server_tokens off;" in vhost._sem_comentarios(redirecionamento[0])


def test_render_nao_menciona_porta_interna_nem_em_comentario(render_com_tls, render_pre_tls):
    """A guarda do deploy é um `grep` literal; o render tem de sobreviver a ela.

    O cabeçalho em prosa da fonte cita 8015 e 8765 exatamente para dizer que
    elas NÃO são publicadas. Copiado para /etc/nginx/sites-enabled, esse
    comentário faria `deploy-portal-resultados.sh nginx` abortar com FATAL.
    """

    for render in (render_com_tls, render_pre_tls):
        for porta in ("8015", "8765", "5432"):
            assert not re.search(rf"\b{porta}\b", render), (
                f"{porta} aparece no render — a guarda literal do deploy abortaria"
            )


def test_o_render_conserva_as_zonas_de_limite(render_com_tls):
    """Tirar comentário do prefixo não pode tirar diretiva junto."""

    assert "limit_req_zone $binary_remote_addr zone=portal_resultados:10m rate=30r/m;" in render_com_tls
    assert "limit_conn_zone $binary_remote_addr zone=portal_conexoes:10m;" in render_com_tls


def test_validar_reprova_portal_sem_cabecalhos(render_com_tls):
    """A validação existe para RECUSAR; vê-la só aprovar não prova nada."""

    estragado = render_com_tls.replace(
        '    add_header Content-Security-Policy "default-src \'none\'; frame-ancestors '
        "'none'; base-uri 'none'; form-action 'none'\" always;\n",
        "",
    )
    assert estragado != render_com_tls, "a substituição não pegou — teste seria vácuo"
    problemas = vhost.validar(estragado, VHOST_NOME)
    assert any("Content-Security-Policy" in p for p in problemas)


def test_validar_reprova_portal_como_default_server(render_com_tls):
    estragado = render_com_tls.replace(
        f"    listen {IPV4_PUBLICO}:443 ssl;\n",
        f"    listen {IPV4_PUBLICO}:443 ssl default_server;\n",
    )
    problemas = vhost.validar(estragado, VHOST_NOME)
    assert any("default_server" in p for p in problemas)


def test_validar_reprova_443_sem_certificado():
    estragado = (
        "server {\n"
        f"    listen {IPV4_PUBLICO}:443 ssl;\n"
        f"    server_name {VHOST_NOME};\n"
        "    location /p/v1/ { proxy_pass http://127.0.0.1:8016; }\n"
        "    location / {\n        return 404;\n    }\n"
        "}\n"
    )
    problemas = vhost.validar(estragado, VHOST_NOME)
    assert any("ssl_certificate" in p for p in problemas)


def test_fonte_com_dois_servers_e_recusada():
    with pytest.raises(vhost.ErroDeRender):
        vhost._bloco_server("server {\n}\nserver {\n}\n")


# ====================================================== cabeçalhos na borda


def _add_headers_do_server(fonte: str) -> dict[str, str]:
    """Os `add_header` declarados no bloco `server` — fora de qualquer location."""

    _, corpo = vhost._bloco_server(fonte)
    nu = vhost._sem_comentarios(corpo)
    profundidade = 0
    encontrados: dict[str, str] = {}
    for linha in nu.splitlines():
        if profundidade == 0:
            achado = re.match(r'\s*add_header\s+(\S+)\s+"([^"]*)"\s+always;', linha)
            if achado:
                encontrados[achado.group(1)] = achado.group(2)
        profundidade += linha.count("{") - linha.count("}")
    return encontrados


def test_borda_e_aplicacao_nao_divergem():
    """O nginx e o app têm de dizer exatamente a MESMA coisa.

    Se alguém apertar a CSP em `app/portal/security.py` e esquecer o vhost,
    o 404 do catch-all — que nunca chega ao app — continuaria com a política
    antiga. Este teste é o que impede a divergência silenciosa.
    """

    do_nginx = _add_headers_do_server(FONTE_VHOST.read_text(encoding="utf-8"))
    for chave, valor in CABECALHOS_SEGUROS.items():
        assert chave in do_nginx, f"o vhost não declara {chave}"
        assert do_nginx[chave] == valor, f"{chave} diverge entre nginx e aplicação"
    assert "Strict-Transport-Security" in do_nginx


def test_nenhum_location_declara_add_header():
    """Em nginx, `add_header` num location SUBSTITUI o conjunto do server.

    Um único `add_header` dentro de `location /` faria o 404 do catch-all
    perder todos os outros oito. A garantia é: nenhum location declara
    nenhum — todos herdam.
    """

    _, corpo = vhost._bloco_server(FONTE_VHOST.read_text(encoding="utf-8"))
    nu = vhost._sem_comentarios(corpo)
    profundidade = 0
    for linha in nu.splitlines():
        if profundidade > 0 and re.match(r"\s*add_header\s", linha):
            pytest.fail(f"add_header dentro de location: {linha.strip()}")
        profundidade += linha.count("{") - linha.count("}")


def test_rotas_proxiadas_descartam_a_copia_da_aplicacao():
    """Sem `proxy_hide_header`, cada cabeçalho sairia duas vezes.

    A aplicação continua emitindo os dela — ela precisa estar correta
    sozinha para quem chega por loopback — e o nginx é quem manda uma única
    cópia para a internet.
    """

    _, corpo = vhost._bloco_server(FONTE_VHOST.read_text(encoding="utf-8"))
    nu = vhost._sem_comentarios(corpo)
    blocos_proxy = re.findall(r"location[^{]*\{((?:[^{}]|\{[^}]*\})*proxy_pass[^}]*)\}", nu)
    assert len(blocos_proxy) == 2, f"esperava 2 locations com proxy_pass, achei {len(blocos_proxy)}"
    for bloco in blocos_proxy:
        for chave in CABECALHOS_SEGUROS:
            assert f"proxy_hide_header {chave};" in bloco, (
                f"location proxiado não descarta a cópia de {chave} vinda do app"
            )


def test_o_catch_all_e_um_404_seco():
    _, corpo = vhost._bloco_server(FONTE_VHOST.read_text(encoding="utf-8"))
    nu = vhost._sem_comentarios(corpo)
    assert re.search(r"location\s+/\s*\{\s*\n\s*return 404;\s*\n\s*\}", nu)


# ============================================================== IP público


def test_o_ip_do_tailnet_nunca_e_escolhido():
    """100.87.98.100 é o endereço com que a VPS é administrada. Não vai ao DNS."""

    motivo = rede_publica.motivo_de_descarte("eth0", "100.87.98.100")
    assert motivo is not None
    assert "CGNAT" in motivo and "Tailscale" in motivo


def test_a_interface_do_tailscale_e_descartada_pelo_nome():
    assert rede_publica.motivo_de_descarte("tailscale0", "1.2.3.4") is not None


@pytest.mark.parametrize(
    "endereco",
    ["10.0.0.4", "192.168.0.92", "172.16.5.5", "127.0.0.1", "169.254.1.1", "100.64.0.1"],
)
def test_enderecos_que_nunca_sao_publicos(endereco):
    assert rede_publica.motivo_de_descarte("eth0", endereco) is not None


def test_o_ip_publico_da_vps_passa():
    assert rede_publica.motivo_de_descarte("eth0", "187.127.39.5") is None


def test_escolhe_o_unico_publico_entre_os_candidatos():
    entradas = [
        ("lo", "127.0.0.1"),
        ("eth0", "187.127.39.5"),
        ("eth0", "10.0.0.4"),
        ("tailscale0", "100.87.98.100"),
    ]
    assert rede_publica.escolher_ipv4_publico(entradas) == "187.127.39.5"


def test_falha_fechada_sem_nenhum_publico():
    with pytest.raises(rede_publica.SemIPPublico):
        rede_publica.escolher_ipv4_publico([("tailscale0", "100.87.98.100"), ("lo", "127.0.0.1")])


def test_falha_fechada_com_dois_publicos():
    """Escolher entre dois endereços públicos é adivinhar. O script não adivinha."""

    with pytest.raises(rede_publica.SemIPPublico) as erro:
        rede_publica.escolher_ipv4_publico([("eth0", "187.127.39.5"), ("eth1", "185.199.111.153")])
    assert "mais de um" in str(erro.value)


def test_le_a_saida_real_do_ip_addr():
    saida = (
        "2: eth0    inet 187.127.39.5/24 brd 187.127.39.255 scope global eth0\\       valid_lft forever\n"
        "5: tailscale0    inet 100.87.98.100/32 scope global tailscale0\\       valid_lft forever\n"
    )
    assert rede_publica.analisar_ip_addr(saida) == [
        ("eth0", "187.127.39.5"),
        ("tailscale0", "100.87.98.100"),
    ]


def test_cli_verificar_recusa_o_tailnet():
    executado = subprocess.run(
        [sys.executable, str(SCRIPTS / "rede_publica.py"), "--verificar", "100.87.98.100"],
        capture_output=True,
        text=True,
    )
    assert executado.returncode == 2
    assert "CGNAT" in executado.stderr


def test_cli_verificar_aceita_o_ip_publico():
    executado = subprocess.run(
        [sys.executable, str(SCRIPTS / "rede_publica.py"), "--verificar", "187.127.39.5"],
        capture_output=True,
        text=True,
    )
    assert executado.returncode == 0
    assert executado.stdout.strip() == "187.127.39.5"


# ========================================================= script de deploy


def _codigo_shell(texto: str) -> str:
    """Sem as linhas de comentário. Um `#` explicando `hostname -I` não é uso."""

    return "\n".join(
        linha for linha in texto.splitlines() if not linha.lstrip().startswith("#")
    )


@pytest.fixture()
def deploy() -> str:
    return _codigo_shell(DEPLOY.read_text(encoding="utf-8"))


def test_o_deploy_e_sintaticamente_valido():
    assert subprocess.run(["bash", "-n", str(DEPLOY)]).returncode == 0


def _funcao_shell(deploy: str, nome: str) -> str:
    corpo = deploy[deploy.index(f"{nome}() {{") :]
    return corpo[: corpo.index("\n}\n")]


def test_o_deploy_nao_deriva_ip_de_hostname_nem_do_tailscale(deploy):
    """O IP que vai ao DNS sai do seletor, nunca da máquina "à mão"."""

    assert "hostname -I" not in deploy
    assert "rede_publica.py" in deploy
    # `tailscale ip` tem exatamente um uso legítimo: descobrir o endereço que
    # o nginx NÃO pode tomar. Fora da guarda, é derivação de IP público.
    fora_da_guarda = deploy.replace(_funcao_shell(deploy, "etapa_tailscale_intacto"), "")
    assert "tailscale ip" not in fora_da_guarda


def test_a_etapa_nginx_confere_o_tailscale_depois_de_recarregar(deploy):
    """`tailscale serve` estava explicitamente fora de escopo — e continua.

    A afirmação "não mexi" vale mais medida do que declarada: a etapa termina
    conferindo que a 443 do tailnet ainda é do tailscaled e que nenhum vhost
    abriu curinga nessa porta.
    """

    etapa = _funcao_shell(deploy, "etapa_nginx")
    assert etapa.index("systemctl reload nginx") < etapa.index("etapa_tailscale_intacto")
    guarda = _funcao_shell(deploy, "etapa_tailscale_intacto")
    assert "tailscaled" in guarda
    assert "curinga" in guarda


def test_o_deploy_verifica_o_dns_antes_do_certbot(deploy):
    etapa = deploy[deploy.index("etapa_tls() {") :]
    etapa = etapa[: etapa.index("\n}\n")]
    assert etapa.index("--verificar") < etapa.index("certbot --nginx")


def test_o_deploy_testa_o_nginx_antes_de_recarregar(deploy):
    etapa = deploy[deploy.index("etapa_nginx() {") :]
    etapa = etapa[: etapa.index("\n}\n")]
    assert etapa.index("nginx -t") < etapa.index("systemctl reload nginx")


def test_o_deploy_restaura_o_vhost_anterior_quando_nginx_reprova(deploy):
    etapa = deploy[deploy.index("etapa_nginx() {") :]
    etapa = etapa[: etapa.index("\n}\n")]
    assert 'backup="$instalado.bak-$STAMP"' in etapa
    assert 'cp -a "$backup" "$instalado"' in etapa
    assert "configuração anterior restaurada" in etapa


def test_o_deploy_reconstroi_o_vhost_depois_do_certbot(deploy):
    """O certbot reescreve o arquivo à maneira dele; a forma versionada volta."""

    etapa = deploy[deploy.index("etapa_tls() {") :]
    etapa = etapa[: etapa.index("\n}\n")]
    assert etapa.index("certbot --nginx") < etapa.index("etapa_nginx")


def test_o_script_nao_tem_e_comercial_de_cauda(deploy):
    """`[[ ... ]] && cmd` de cauda, sob `set -Eeuo pipefail`, ENCERRA o script.

    Quando o teste é falso o status da lista é 1 e o `set -e` age — mesmo
    que a intenção fosse "se for o caso, faça". Esta etapa nasceu com quatro
    dessas, e uma delas ficava no caminho normal da etapa `tls`: derrubaria
    o deploy exatamente quando tudo estivesse certo. Um deploy que
    "termina" sem ter feito metade do trabalho é pior que um que falha.
    """

    ofensores = [
        linha.strip()
        for linha in deploy.splitlines()
        if re.match(r"^\s*\[\[.*\]\]\s*&&\s*\S", linha)
    ]
    assert not ofensores, "use `if`, não `&&` de cauda:\n" + "\n".join(ofensores)


def test_a_guarda_do_tls_nao_depende_de_um_teste_verdadeiro(deploy):
    """A guarda tem de examinar TODOS os endereços resolvidos, um por um."""

    etapa = deploy[deploy.index("etapa_tls() {") :]
    etapa = etapa[: etapa.index("\n}\n")]
    assert "getent ahostsv4" in etapa
    assert "--verificar" in etapa
    assert "continue" not in etapa


def test_toda_etapa_do_script_esta_documentada(deploy):
    """Anti-deriva: etapa nova sem linha no doc é etapa que ninguém vai rodar."""

    case = deploy[deploy.index('case "$ETAPA" in') :]
    etapas = set(re.findall(r"^\s{2}([a-z-]+)\)\s+etapa_", case, re.MULTILINE))
    assert etapas, "não achei as etapas no case"
    documentadas = DOC.read_text(encoding="utf-8")
    faltando = sorted(e for e in etapas if e not in documentadas)
    assert not faltando, f"etapas sem documentação: {faltando}"


def test_a_documentacao_nao_promete_um_placeholder():
    texto = DOC.read_text(encoding="utf-8")
    assert "<IPv4 público desta VPS>" not in texto
