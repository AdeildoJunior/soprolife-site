"""M26.7 — os dois defeitos que o teste real do portal (M26.6) revelou.

Um paciente sintético abriu o link em produção, digitou a data certa, e a
tela disse *"verifique sua internet"*. A internet estava boa. O que houve:

1. o papel `soprolife_portal` tinha `GRANT INSERT ON audit_logs` e nada
   mais. O ORM emite `INSERT ... RETURNING audit_logs.id`, e `RETURNING` é
   uma leitura: exige `SELECT` na coluna devolvida. Toda autenticação de
   paciente morria em `permission denied for table audit_logs`;
2. o 500 resultante nascia no `ServerErrorMiddleware`, que é a camada mais
   externa do Starlette — acima do CORS. Sem `Access-Control-Allow-Origin`,
   o navegador não classifica a resposta como erro HTTP: classifica como
   falha de rede. O defeito de servidor saiu disfarçado de queda de link.

Os dois são a mesma família de armadilha: uma permissão/camada que parece
suficiente até alguém exercitar o caminho feliz de verdade. O smoke da
M26.4 testava 401 e 404 — caminhos que não escrevem auditoria e não passam
por erro interno.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.db import get_db
from app.models import AuditLog

NUCLEO = Path(__file__).resolve().parents[1]
RAIZ = NUCLEO.parents[1]
SQL_PAPEL = NUCLEO / "scripts" / "sql" / "m26-4-portal-db-role.sql"
DEPLOY = NUCLEO / "scripts" / "deploy-portal-resultados.sh"
PAGINA = RAIZ / "resultados" / "index.html"

ORIGEM_BOA = "https://soprolife.com.br"
ORIGENS_MAS = [
    "https://soprolife.com.br.atacante.tld",
    "https://evil.example",
    "http://soprolife.com.br",
    "https://soprolife.com.br:8443",
    "null",
]


# ==================================================================== papel
#
# O banco de teste da suíte é SQLite; GRANT não existe lá. O que se pode
# congelar aqui é o TEXTO do script — que é a única fonte que cria o papel —
# e a coerência dele com o modelo. A prova executável contra PostgreSQL vive
# na etapa `banco` do deploy, e os testes abaixo garantem que ela existe.


@pytest.fixture(scope="module")
def sql() -> str:
    return SQL_PAPEL.read_text(encoding="utf-8")


def _sem_comentarios(sql: str) -> str:
    """Só os comandos. O arquivo explica muito, e a explicação cita `GRANT`."""

    return "\n".join(l for l in sql.splitlines() if not l.lstrip().startswith("--"))


def _grants_de(sql: str, tabela: str) -> list[str]:
    """Cada `GRANT ... ON <tabela> TO soprolife_portal;` como uma linha só."""

    achados = []
    for bruto in re.findall(r"GRANT\b.*?;", _sem_comentarios(sql), flags=re.S):
        comando = " ".join(bruto.split())
        if re.search(rf"\bON {tabela}\b", comando):
            achados.append(comando)
    return achados


def test_o_papel_recebe_select_apenas_da_coluna_id(sql):
    grants = _grants_de(sql, "audit_logs")
    selects = [g for g in grants if g.startswith("GRANT SELECT")]
    assert selects == ["GRANT SELECT (id) ON audit_logs TO soprolife_portal;"]


def test_o_papel_nunca_recebe_select_da_tabela_inteira(sql):
    """A regressão perigosa é a correção preguiçosa.

    `GRANT SELECT ON audit_logs` também faria o `RETURNING` passar — e de
    quebra entregaria ao processo exposto na internet a trilha inteira do
    sistema: quem fez o quê, quando, em qual entidade.
    """

    assert not re.search(
        r"GRANT\s+SELECT\s+ON\s+audit_logs", _sem_comentarios(sql), flags=re.I
    ), "SELECT da tabela inteira em audit_logs — o portal passaria a ler a trilha"


def test_o_insert_e_por_coluna_e_bate_com_o_modelo(sql):
    """As colunas do GRANT são exatamente as que o ORM escreve.

    Amarrar o script ao modelo faz uma coluna nova de auditoria quebrar
    ESTE teste, e não a autenticação de um paciente em produção.
    """

    inserts = [g for g in _grants_de(sql, "audit_logs") if g.startswith("GRANT INSERT")]
    assert len(inserts) == 1
    colunas = set(re.search(r"GRANT INSERT \((.*?)\)", inserts[0]).group(1).split(", "))
    do_modelo = {c.name for c in AuditLog.__table__.columns}
    assert colunas == do_modelo - {"id"}
    assert "id" not in colunas  # o número da própria linha não se escolhe


def test_o_papel_nao_ganhou_nada_alem_do_previsto_em_audit_logs(sql):
    verbos = {g.split()[1] for g in _grants_de(sql, "audit_logs")}
    assert verbos == {"SELECT", "INSERT"}


def test_o_script_continua_idempotente(sql):
    """Rodar duas vezes tem de ser igual a rodar uma."""

    assert "IF NOT EXISTS (SELECT 1 FROM pg_roles" in sql
    assert sql.index("REVOKE ALL ON ALL TABLES") < sql.index("GRANT SELECT (id)")


def test_o_script_explica_por_que_select_id_e_necessario(sql):
    assert "RETURNING" in sql
    trecho = sql[sql.index("M26.7") : sql.index("GRANT INSERT (")]
    assert "SELECT" in trecho and "RETURNING" in trecho


def test_existe_uma_unica_fonte_que_concede_privilegio_ao_papel():
    """Duas fontes divergentes seriam pior que a permissão faltando."""

    fontes = []
    for caminho in RAIZ.rglob("*"):
        if not caminho.is_file() or ".git/" in str(caminho):
            continue
        if caminho.suffix not in {".sql", ".sh", ".py"}:
            continue
        if caminho == Path(__file__):
            continue  # este arquivo cita os GRANTs para conferi-los
        try:
            texto = caminho.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if re.search(r"GRANT\s+[A-Z]+.*TO\s+soprolife_portal", texto, flags=re.S):
            fontes.append(caminho.relative_to(RAIZ))
    assert fontes == [SQL_PAPEL.relative_to(RAIZ)]


def test_o_deploy_prova_o_returning_contra_o_postgres_de_verdade():
    """A prova executável do GRANT — a que a suíte em SQLite não pode dar."""

    texto = DEPLOY.read_text(encoding="utf-8")
    assert "RETURNING id" in texto
    prova = texto[texto.index("INSERT INTO audit_logs") - 400 :]
    assert "BEGIN;" in prova and "ROLLBACK;" in prova, "a prova deixaria linha na trilha"
    assert "SELECT acao FROM audit_logs" in texto, "falta provar que o conteúdo é ilegível"


# =================================================================== fronteira


@pytest.fixture(autouse=True)
def portal_ligado(monkeypatch, tmp_path):
    monkeypatch.setenv("M15_REPORTS_ENABLED", "true")
    monkeypatch.setenv("M15_REPORTS_MODE", "pilot")
    monkeypatch.setenv("M15_REPORTS_STORAGE_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv(
        "M15_REPORTS_VALIDATION_BASE_URL",
        "https://painel-teste.soprolife.local/validar",
    )
    monkeypatch.setenv(
        "M15_AUTH_SECRET", "m26-7-painel-administrativo-secret-de-teste-0123456789"
    )
    monkeypatch.setenv("M15_PORTAL_ENABLED", "true")
    monkeypatch.setenv(
        "M15_PORTAL_TOKEN_KEY",
        "m26-7-chave-que-deriva-o-link-do-paciente-9876543210abcdef",
    )
    monkeypatch.setenv(
        "M15_PORTAL_SESSION_SECRET",
        "m26-7-segredo-do-cookie-do-portal-publico-fedcba9876543210",
    )
    monkeypatch.setenv(
        "M15_PORTAL_PUBLIC_BASE_URL", "https://soprolife.com.br/resultados"
    )
    get_settings.cache_clear()
    from app.portal.security import limitador

    limitador.limpar()
    yield
    limitador.limpar()
    get_settings.cache_clear()


@pytest.fixture()
def portal(engine):
    from app.portal.main import create_portal_app

    app = create_portal_app()
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    def _sessao():
        sessao = SessionLocal()
        try:
            yield sessao
        finally:
            sessao.close()

    app.dependency_overrides[get_db] = _sessao
    with TestClient(
        app,
        base_url="https://resultados-api.teste.local",
        raise_server_exceptions=False,
    ) as c:
        yield c


def _pedido(token: str = "x" * 43) -> dict:
    return {"token": token, "nascimento": "1980-01-01"}


def _cabecalhos_de_seguranca_presentes(resposta) -> None:
    from app.portal.security import CABECALHOS_SEGUROS

    for chave, valor in CABECALHOS_SEGUROS.items():
        assert resposta.headers.get(chave) == valor, chave


# ------------------------------------------------------------ erro interno


@pytest.fixture()
def portal_que_explode(portal, monkeypatch):
    """Reproduz a M26.6: a rota estoura no meio, como o INSERT sem permissão."""

    from app.portal import routes

    def _bomba(*_a, **_k):
        raise RuntimeError("permission denied for table audit_logs")

    monkeypatch.setattr(routes.prs, "find_by_token", _bomba)
    return portal


def test_o_500_chega_ao_navegador_como_erro_http_e_nao_como_queda_de_rede(
    portal_que_explode,
):
    """O teste que valeria a M26.6 inteira se existisse antes dela."""

    r = portal_que_explode.post(
        "/p/v1/acesso", json=_pedido(), headers={"Origin": ORIGEM_BOA}
    )
    assert r.status_code == 500
    assert r.headers.get("Access-Control-Allow-Origin") == ORIGEM_BOA
    assert r.headers.get("Access-Control-Allow-Credentials") == "true"
    assert "origin" in (r.headers.get("Vary") or "").lower()


def test_o_500_nao_conta_nada_ao_paciente(portal_que_explode):
    r = portal_que_explode.post(
        "/p/v1/acesso", json=_pedido(), headers={"Origin": ORIGEM_BOA}
    )
    corpo = r.json()
    assert corpo["erro"]["codigo"] == "interno"
    assert corpo["erro"]["mensagem"] == (
        "Não foi possível acessar o resultado agora. Tente novamente em "
        "alguns instantes."
    )
    assert corpo["erro"]["request_id"]
    inteiro = r.text
    for vazamento in (
        "Traceback",
        "permission denied",
        "audit_logs",
        "RuntimeError",
        "sqlalchemy",
        "File \"",
        "Consulte os logs",  # mensagem do Command Center, escrita p/ operador
    ):
        assert vazamento not in inteiro, vazamento


def test_o_500_mantem_os_cabecalhos_de_seguranca(portal_que_explode):
    r = portal_que_explode.post(
        "/p/v1/acesso", json=_pedido(), headers={"Origin": ORIGEM_BOA}
    )
    _cabecalhos_de_seguranca_presentes(r)


@pytest.mark.parametrize("origem", ORIGENS_MAS)
def test_o_500_nao_libera_origem_nao_autorizada(portal_que_explode, origem):
    r = portal_que_explode.post(
        "/p/v1/acesso", json=_pedido(), headers={"Origin": origem}
    )
    assert r.status_code == 500
    assert "Access-Control-Allow-Origin" not in r.headers


def test_o_traceback_continua_indo_para_o_log(portal_que_explode, caplog):
    """Engolir a exceção sem registrar trocaria um diagnóstico ruim por nenhum."""

    with caplog.at_level("ERROR"):
        portal_que_explode.post(
            "/p/v1/acesso", json=_pedido(), headers={"Origin": ORIGEM_BOA}
        )
    registros = [r for r in caplog.records if r.exc_info]
    assert registros, "o 500 saiu sem traceback no log"
    assert "permission denied for table audit_logs" in caplog.text


# ------------------------------------------------------- demais status HTTP


def test_o_404_de_fora_do_prefixo_leva_cors(portal):
    """Este 404 nasce na própria fronteira, acima do CORSMiddleware."""

    r = portal.get("/", headers={"Origin": ORIGEM_BOA})
    assert r.status_code == 404
    assert r.headers.get("Access-Control-Allow-Origin") == ORIGEM_BOA
    _cabecalhos_de_seguranca_presentes(r)


def test_o_404_nao_libera_origem_estranha(portal):
    r = portal.get("/", headers={"Origin": "https://evil.example"})
    assert r.status_code == 404
    assert "Access-Control-Allow-Origin" not in r.headers


def test_o_401_real_leva_cors(portal):
    """Token inexistente: o caminho que o smoke da M26.4 já exercitava."""

    r = portal.post("/p/v1/acesso", json=_pedido(), headers={"Origin": ORIGEM_BOA})
    assert r.status_code == 401
    assert r.headers.get("Access-Control-Allow-Origin") == ORIGEM_BOA
    _cabecalhos_de_seguranca_presentes(r)


@pytest.mark.parametrize(
    "fabrica,status",
    [("_invalido", 401), ("_expirado", 410), ("_tentativas", 429)],
)
def test_todo_erro_de_dominio_leva_cors(portal, monkeypatch, fabrica, status):
    from app.portal import routes

    def _recusa(*_a, **_k):
        raise getattr(routes, fabrica)()

    monkeypatch.setattr(routes.prs, "find_by_token", _recusa)
    r = portal.post("/p/v1/acesso", json=_pedido(), headers={"Origin": ORIGEM_BOA})
    assert r.status_code == status
    assert r.headers.get("Access-Control-Allow-Origin") == ORIGEM_BOA
    assert r.json()["erro"]["mensagem"]
    _cabecalhos_de_seguranca_presentes(r)


def test_nenhuma_resposta_do_portal_usa_curinga(portal, portal_que_explode):
    for resposta in (
        portal.get("/", headers={"Origin": ORIGEM_BOA}),
        portal.get("/p/v1/health", headers={"Origin": ORIGEM_BOA}),
        portal_que_explode.post(
            "/p/v1/acesso", json=_pedido(), headers={"Origin": ORIGEM_BOA}
        ),
    ):
        assert resposta.headers.get("Access-Control-Allow-Origin") != "*"


def test_sem_origem_nenhum_cabecalho_de_cors_e_inventado(portal):
    r = portal.get("/p/v1/health")
    assert "Access-Control-Allow-Origin" not in r.headers
    assert "origin" in (r.headers.get("Vary") or "").lower()


def test_a_lista_de_origens_e_a_da_configuracao(portal, monkeypatch):
    """A allowlist não é constante no código: é a configuração validada."""

    from app.portal.security import origem_autorizada

    class _Req:
        headers = {"origin": ORIGEM_BOA}

    assert origem_autorizada(_Req()) == ORIGEM_BOA
    assert get_settings().portal_cors_origins == [ORIGEM_BOA]


# ==================================================================== tela


@pytest.fixture(scope="module")
def pagina() -> str:
    return PAGINA.read_text(encoding="utf-8")


def test_a_tela_separa_erro_do_servidor_de_queda_de_rede(pagina):
    assert "if (status >= 500) return MSG_SERVIDOR;" in pagina
    assert "var MSG_REDE" in pagina and "var MSG_SERVIDOR" in pagina


def test_a_mensagem_de_5xx_e_generica_e_sem_termo_tecnico(pagina):
    trecho = pagina[pagina.index("var MSG_SERVIDOR") : pagina.index("function erroDaResposta")]
    assert "Tente novamente em alguns instantes" in trecho
    for termo in ("500", "CORS", "SQL", "erro interno", "servidor respondeu", "log"):
        assert termo not in trecho


def test_o_desenho_da_tela_saiu_de_dentro_do_catch_de_rede(pagina):
    """Um defeito ao renderizar não pode virar 'verifique sua internet'."""

    corpo = pagina[pagina.index('fetch(API + "/acesso"') :]
    fim_do_catch = corpo.index("avisar(MSG_REDE);")
    assert corpo.index("if (dados) renderizar(dados);") > fim_do_catch
    assert "renderizar(r.corpo)" not in corpo


def test_a_tela_continua_baixando_por_navegacao_de_topo(pagina):
    """Regressão da M25.18: blob devolveria nome aleatório ao paciente."""

    assert "configurarLink(" in pagina
    assert "createObjectURL" not in pagina
