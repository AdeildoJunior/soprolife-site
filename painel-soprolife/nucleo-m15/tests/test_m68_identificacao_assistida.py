"""M68 — cadastro assistido por CPF + nascimento (Consulta CPF v3 / SERPRO).

Nenhum teste sai para a rede: o provider usa `httpx.MockTransport`, e todo
CPF aqui é sintético (dígitos verificadores calculados, sem dono conhecido).

Prova, do lado do servidor:

- busca local EXATA por CPF, no corpo POST, antes de qualquer SERPRO;
- CPF já cadastrado nunca vira duplicata (nem com "criar mesmo assim");
- integração desligada → cadastro manual intacto;
- mapeamento dos códigos oficiais (200/206/400/401/403/404/422/451/5xx/504);
- token só em memória, renovado uma vez no 401, nunca devolvido;
- cache por hash e limite por usuário (anti-custo);
- nenhum log/auditoria com CPF, nascimento, nome devolvido ou token;
- comprovante: registra "confirmado" / "nome alterado" sem PII;
- nada fiscal é criado.
"""

import json
import logging
from datetime import date

import httpx
import pytest
from sqlalchemy import func, select

from app.config import Settings
from app.models import AuditLog, FiscalAttempt, FiscalDocument, FiscalIssuanceRequest, Person
from app.services import identificacao_assistida as ia
from app.services.serpro_cpf import (
    ALLOWED_HOST,
    CONSULTA_BASE_URL,
    MAX_RESPONSE_BYTES,
    TOKEN_URL,
    SerproCpfClient,
    SerproCpfError,
)

API = "/api/v1"
CONSUMER_KEY = "chave-sintetica-m68"
CONSUMER_SECRET = "segredo-sintetico-m68-nao-real"
TOKEN = "token-sintetico-m68-abcdef"
NASC = "1970-11-14"
NASC_DDMM = "14111970"
NOME_OFICIAL = "MARINA COSTA RIBEIRO"
NOME_SOCIAL = "MARINA RIBEIRO SOCIAL"


def _cpf(base9: str) -> str:
    """CPF sintético com verificadores corretos a partir de 9 dígitos."""

    def dv(digs, peso):
        resto = (sum(int(d) * p for d, p in zip(digs, range(peso, 1, -1))) * 10) % 11
        return "0" if resto == 10 else str(resto)

    d1 = dv(base9, 10)
    return base9 + d1 + dv(base9 + d1, 11)


CPF_NOVO = _cpf("284017395")
CPF_OUTRO = _cpf("590318462")
CPF_EXISTENTE = _cpf("713204859")
CPF_INVALIDO = CPF_NOVO[:-1] + str((int(CPF_NOVO[-1]) + 1) % 10)


class FakeSerpro:
    """Servidor falso em memória com roteiro por chamada."""

    def __init__(self):
        self.token_status = 200
        self.token_body = {"access_token": TOKEN, "expires_in": 3295,
                           "token_type": "Bearer", "scope": "am_application_scope default"}
        self.consultas: list = []   # roteiro: (status, body) consumidos em ordem
        self.default = (200, None)
        self.calls: list[httpx.Request] = []
        self.token_calls = 0
        self.timeout = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if str(request.url) == TOKEN_URL:
            self.token_calls += 1
            return httpx.Response(self.token_status, json=self.token_body)
        if self.timeout:
            raise httpx.ReadTimeout("timeout", request=request)
        status, body = self.consultas.pop(0) if self.consultas else self.default
        if body is None and status in (200, 206):
            body = {"ni": CPF_NOVO, "nome": NOME_OFICIAL,
                    "situacao": {"codigo": "0", "descricao": "Regular"},
                    "nascimento": NASC_DDMM, "dataInscricao": "10051976"}
        if isinstance(body, (dict, list)):
            return httpx.Response(status, json=body)
        return httpx.Response(status, content=body or b"")

    @property
    def consulta_calls(self):
        return [c for c in self.calls if str(c.url) != TOKEN_URL]


@pytest.fixture(autouse=True)
def _limpa_estado():
    ia.cache_consultas.limpar()
    ia.limite_consultas.limpar()
    yield
    ia.cache_consultas.limpar()
    ia.limite_consultas.limpar()


@pytest.fixture()
def serpro():
    return FakeSerpro()


@pytest.fixture()
def provider(serpro):
    return SerproCpfClient(consumer_key=CONSUMER_KEY, consumer_secret=CONSUMER_SECRET,
                           transport=httpx.MockTransport(serpro.handler))


@pytest.fixture()
def ativo(client, provider):
    client.app.dependency_overrides[ia.get_consulta_cpf_provider] = lambda: provider
    yield
    client.app.dependency_overrides.pop(ia.get_consulta_cpf_provider, None)


def _ident(client, auth, cpf=CPF_NOVO, nasc=NASC, papel="operacional"):
    return client.post(f"{API}/pessoas/identificacao-assistida",
                       json={"cpf": cpf, "data_nascimento": nasc}, headers=auth(papel))


def _criar(client, auth, **campos):
    corpo = {"nome_completo": "Rafael Moreira Lima", "contatos": []}
    corpo.update(campos)
    return client.post(f"{API}/pessoas", json=corpo, headers=auth("operacional"))


def _novo_paciente(client, auth, pessoa, **extra):
    corpo = {
        "pessoa": pessoa,
        "tipo": "espirometria_soprolife",
        "espirometria": {"data_exame": "2026-09-01", "status": "Realizado",
                         "broncodilatador": False, "municipio_atendimento_ibge": "3304557",
                         "modalidade": "residencial", "local_atendimento": "Domicílio do paciente"},
    }
    corpo.update(extra)
    return client.post(f"{API}/atendimentos/novo-paciente", json=corpo,
                       headers=auth("operacional"))


def _auditoria(db, acao):
    db.expire_all()
    return db.execute(select(AuditLog).where(AuditLog.acao == acao)).scalars().all()


# ═══════════════════════════════════════ busca local exata por CPF

def test_busca_cpf_encontra_so_por_cpf_exato(client, auth):
    criada = _criar(client, auth, cpf=CPF_EXISTENTE, data_nascimento="1980-02-03").json()
    r = client.post(f"{API}/pessoas/busca-cpf", json={"cpf": CPF_EXISTENTE},
                    headers=auth("operacional"))
    assert r.status_code == 200
    body = r.json()
    assert body["encontrada"] is True
    assert body["pessoa"] == {"id": criada["id"], "public_code": criada["public_code"],
                              "nome_completo": "Rafael Moreira Lima",
                              "data_nascimento": "1980-02-03"}
    # máscara é aceita; parcial nunca
    mascarado = f"{CPF_EXISTENTE[:3]}.{CPF_EXISTENTE[3:6]}.{CPF_EXISTENTE[6:9]}-{CPF_EXISTENTE[9:]}"
    assert client.post(f"{API}/pessoas/busca-cpf", json={"cpf": mascarado},
                       headers=auth("operacional")).json()["encontrada"] is True
    parcial = client.post(f"{API}/pessoas/busca-cpf", json={"cpf": CPF_EXISTENTE[:8]},
                          headers=auth("operacional"))
    assert parcial.status_code == 422


def test_busca_cpf_inexistente_e_invalido(client, auth):
    r = client.post(f"{API}/pessoas/busca-cpf", json={"cpf": CPF_NOVO}, headers=auth("operacional"))
    assert r.json() == {"encontrada": False, "pessoa": None}
    inv = client.post(f"{API}/pessoas/busca-cpf", json={"cpf": CPF_INVALIDO},
                      headers=auth("operacional"))
    assert inv.status_code == 422
    assert CPF_INVALIDO not in inv.text


def test_busca_cpf_nao_usa_heuristica_de_telefone(client, auth):
    """11 dígitos em /pessoas/busca viram telefone; a rota nova é só CPF."""
    _criar(client, auth, cpf=CPF_EXISTENTE)
    r = client.post(f"{API}/pessoas/busca", json={"q": CPF_EXISTENTE}, headers=auth("operacional"))
    assert r.json()["total"] == 0
    assert client.post(f"{API}/pessoas/busca-cpf", json={"cpf": CPF_EXISTENTE},
                       headers=auth("operacional")).json()["encontrada"] is True


def test_cpf_nunca_na_url(client, auth):
    """Não existe variante GET com CPF/nascimento em query string ou path."""
    for url in (f"{API}/pessoas/busca-cpf?cpf={CPF_NOVO}",
                f"{API}/pessoas/identificacao-assistida?cpf={CPF_NOVO}&data_nascimento={NASC}"):
        assert client.get(url, headers=auth("operacional")).status_code in (404, 405)


def test_rbac_leitura_nao_consulta(client, auth, ativo, serpro):
    assert client.post(f"{API}/pessoas/busca-cpf", json={"cpf": CPF_NOVO},
                       headers=auth("leitura")).status_code == 403
    assert _ident(client, auth, papel="leitura").status_code == 403
    assert client.post(f"{API}/pessoas/busca-cpf", json={"cpf": CPF_NOVO}).status_code == 401
    assert serpro.calls == []


# ═══════════════════════════════════════ CPF existente: portão ANTES do SERPRO

def test_cpf_existente_nao_chama_serpro(client, auth, ativo, serpro):
    criada = _criar(client, auth, cpf=CPF_EXISTENTE).json()
    r = _ident(client, auth, cpf=CPF_EXISTENTE)
    assert r.status_code == 200
    assert r.json()["resultado"] == "cpf_ja_cadastrado"
    assert r.json()["pessoa"]["public_code"] == criada["public_code"]
    assert serpro.calls == []


@pytest.mark.parametrize("confirmar", [False, True])
def test_cpf_existente_nunca_vira_duplicata(client, auth, db, confirmar):
    _criar(client, auth, cpf=CPF_EXISTENTE, nome_completo="Pessoa Original")
    r = _criar(client, auth, cpf=CPF_EXISTENTE, nome_completo="Outro Nome")
    assert r.status_code == 409
    assert "cpf_ja_cadastrado" in r.text
    r2 = _novo_paciente(client, auth, {"nome_completo": "Outro Nome", "contatos": [],
                                       "cpf": CPF_EXISTENTE},
                        confirmar_duplicado=confirmar)
    assert r2.status_code == 409
    assert "cpf_ja_cadastrado" in r2.text
    db.expire_all()
    assert db.scalar(select(func.count(Person.id)).where(Person.cpf == CPF_EXISTENTE)) == 1
    original = db.execute(select(Person).where(Person.cpf == CPF_EXISTENTE)).scalar_one()
    assert original.nome_completo == "Pessoa Original"  # nada sobrescrito


# ═══════════════════════════════════════ integração desligada

def test_desligada_por_padrao_e_fail_closed():
    assert Settings().serpro_cpf_ready() is False
    assert Settings(serpro_cpf_enabled=True).serpro_cpf_ready() is False
    assert Settings(serpro_cpf_enabled=True, serpro_cpf_consumer_key="k").serpro_cpf_ready() is False
    s = Settings(serpro_cpf_enabled=True, serpro_cpf_consumer_key="k",
                 serpro_cpf_consumer_secret="segredo-x")
    assert s.serpro_cpf_ready() is True
    assert "segredo-x" not in repr(s) and "segredo-x" not in str(s.model_dump())


def test_desligada_devolve_nao_configurada_e_manual_segue(client, auth, db):
    r = _ident(client, auth)
    assert r.status_code == 200
    assert r.json()["resultado"] == "nao_configurada"
    assert "preencha o nome manualmente" in r.json()["mensagem"]
    criada = _criar(client, auth, cpf=CPF_NOVO, data_nascimento=NASC, nome_completo="Nome Manual")
    assert criada.status_code == 201
    assert _auditoria(db, "pessoa.identificacao_assistida") == []


def test_nascimento_obrigatorio_e_valido(client, auth, ativo, serpro):
    sem = client.post(f"{API}/pessoas/identificacao-assistida", json={"cpf": CPF_NOVO},
                      headers=auth("operacional"))
    assert sem.status_code == 422
    futuro = _ident(client, auth, nasc="2999-01-01")
    assert futuro.status_code == 422
    assert _ident(client, auth, cpf=CPF_INVALIDO).status_code == 422
    assert serpro.calls == []


# ═══════════════════════════════════════ SERPRO mockado

def test_sucesso_preenche_nome_minimo_e_comprovante(client, auth, ativo, serpro, db):
    r = _ident(client, auth)
    assert r.status_code == 200
    body = r.json()
    assert body["resultado"] == "confere"
    assert body["nome_oficial"] == NOME_OFICIAL
    assert body["situacao"] == {"codigo": "0", "descricao": "Regular"}
    assert body["nome_social"] is None
    assert body["mensagem"] == "Nome confirmado na Receita Federal via SERPRO."
    assert body["comprovante"].startswith("v1.")
    # minimização: nada de ni, nascimento, dataInscricao, token
    texto = json.dumps(body)
    for proibido in (CPF_NOVO, NASC_DDMM, "10051976", TOKEN, CONSUMER_SECRET, "dataInscricao", "ni\""):
        assert proibido not in texto
    # contrato do SERPRO: CPF + nascimento ddmmaaaa no path v3, Bearer, host fixo
    (consulta,) = serpro.consulta_calls
    assert str(consulta.url) == f"{CONSULTA_BASE_URL}/cpf/{CPF_NOVO}/{NASC_DDMM}"
    assert consulta.url.host == ALLOWED_HOST
    assert consulta.headers["authorization"] == f"Bearer {TOKEN}"
    token_req = serpro.calls[0]
    assert token_req.method == "POST" and token_req.content == b"grant_type=client_credentials"
    assert token_req.headers["authorization"].startswith("Basic ")
    # auditoria: só código, nunca PII
    (linha,) = _auditoria(db, "pessoa.identificacao_assistida")
    assert linha.detalhes == {"consulta_serpro_realizada": True, "resultado": "confere"}
    # nada persistido na pessoa
    db.expire_all()
    assert db.scalar(select(func.count(Person.id))) == 0


def test_nome_social_vem_separado(client, auth, ativo, serpro):
    serpro.consultas = [(200, {"ni": CPF_NOVO, "nome": NOME_OFICIAL, "nomeSocial": NOME_SOCIAL,
                               "situacao": {"codigo": "0", "descricao": "Regular"},
                               "nascimento": NASC_DDMM})]
    body = _ident(client, auth).json()
    assert body["nome_oficial"] == NOME_OFICIAL
    assert body["nome_social"] == NOME_SOCIAL


@pytest.mark.parametrize("status, body", [
    (404, {"mensagem": "nao encontrado"}),
    (200, {"ni": "x", "nome": NOME_OFICIAL, "nascimento": "01011980",
           "situacao": {"codigo": "0", "descricao": "Regular"}}),
])
def test_nao_correspondencia(client, auth, ativo, serpro, status, body):
    serpro.consultas = [(status, body)]
    r = _ident(client, auth).json()
    assert r["resultado"] == "nao_confere"
    assert r["mensagem"] == "CPF e data de nascimento não conferem no cadastro oficial."
    assert "nome_oficial" not in r and "comprovante" not in r


@pytest.mark.parametrize("status, esperado", [
    (400, "dados_recusados"), (403, "indisponivel"), (406, "indisponivel"),
    (422, "protegido"), (451, "protegido"), (500, "indisponivel"), (504, "indisponivel"),
])
def test_codigos_oficiais_sem_retry(client, auth, ativo, serpro, status, esperado):
    serpro.consultas = [(status, {"detalhe": "x"})]
    r = _ident(client, auth)
    assert r.status_code == 200
    assert r.json()["resultado"] == esperado
    assert len(serpro.consulta_calls) == 1  # sem retry cego (5xx/504 inclusos)
    for proibido in (TOKEN, CONSUMER_SECRET, CONSUMER_KEY):
        assert proibido not in r.text


def test_timeout_indisponivel_e_manual_continua(client, auth, ativo, serpro):
    serpro.timeout = True
    r = _ident(client, auth).json()
    assert r["resultado"] == "indisponivel"
    assert "preencher o nome manualmente" in r["mensagem"]
    assert len(serpro.consulta_calls) == 1
    salvo = _novo_paciente(client, auth, {"nome_completo": "Nome Digitado", "contatos": [],
                                          "cpf": CPF_NOVO, "data_nascimento": NASC})
    assert salvo.status_code == 201, salvo.text


def test_token_recusado_nao_vaza_segredo(client, auth, ativo, serpro):
    serpro.token_status = 401
    serpro.token_body = {"error": "invalid_client"}
    r = _ident(client, auth)
    assert r.json()["resultado"] == "indisponivel"
    assert serpro.consulta_calls == []
    for proibido in (CONSUMER_SECRET, CONSUMER_KEY, "Basic"):
        assert proibido not in r.text


def test_401_na_consulta_renova_token_uma_vez(client, auth, ativo, serpro):
    serpro.consultas = [(401, {}), (200, None)]
    r = _ident(client, auth).json()
    assert r["resultado"] == "confere"
    assert serpro.token_calls == 2 and len(serpro.consulta_calls) == 2


def test_401_persistente_para_na_segunda(client, auth, ativo, serpro):
    serpro.consultas = [(401, {}), (401, {})]
    assert _ident(client, auth).json()["resultado"] == "indisponivel"
    assert len(serpro.consulta_calls) == 2


def test_206_com_e_sem_nome(client, auth, ativo, serpro):
    serpro.consultas = [(206, {"ni": CPF_NOVO, "nome": NOME_OFICIAL, "nascimento": NASC_DDMM,
                               "situacao": {"codigo": "0", "descricao": "Regular"}})]
    r = _ident(client, auth).json()
    assert r["resultado"] == "confere" and r["parcial"] is True
    serpro.consultas = [(206, {"ni": CPF_OUTRO, "nascimento": NASC_DDMM})]
    r2 = _ident(client, auth, cpf=CPF_OUTRO).json()
    assert r2["resultado"] == "indisponivel"
    assert "nome_oficial" not in r2


def test_token_reutilizado_em_memoria(client, auth, ativo, serpro):
    _ident(client, auth)
    _ident(client, auth, cpf=CPF_OUTRO)
    assert serpro.token_calls == 1
    assert len(serpro.consulta_calls) == 2


# ═══════════════════════════════════════ anti-custo

def test_mesmo_par_nao_repete_consulta(client, auth, ativo, serpro, db):
    for _ in range(4):
        assert _ident(client, auth).json()["resultado"] == "confere"
    assert len(serpro.consulta_calls) == 1
    # outro nascimento para o mesmo CPF é outro par → nova consulta
    _ident(client, auth, nasc="1970-11-15")
    assert len(serpro.consulta_calls) == 2
    assert len(_auditoria(db, "pessoa.identificacao_assistida")) == 2


def test_cache_nao_guarda_cpf_em_claro(client, auth, ativo, serpro):
    _ident(client, auth)
    chaves = list(ia.cache_consultas._itens.keys())
    assert chaves and all(CPF_NOVO not in k and NASC not in k for k in chaves)


def test_limite_por_usuario(client, auth, ativo, serpro, monkeypatch):
    from app import config
    monkeypatch.setattr(config.get_settings(), "serpro_cpf_max_consultas_por_usuario", 2)
    assert _ident(client, auth, cpf=_cpf("100200300")).status_code == 200
    assert _ident(client, auth, cpf=_cpf("100200301")).status_code == 200
    bloqueado = _ident(client, auth, cpf=_cpf("100200302"))
    assert bloqueado.status_code == 429
    assert len(serpro.consulta_calls) == 2
    # resultado em cache não conta nem é bloqueado
    assert _ident(client, auth, cpf=_cpf("100200300")).status_code == 200


# ═══════════════════════════════════════ logs e auditoria sem PII

def test_nenhum_log_com_pii_ou_token(client, auth, ativo, serpro, caplog):
    caplog.set_level(logging.DEBUG)
    serpro.consultas = [(500, {}), ]
    _ident(client, auth, cpf=CPF_OUTRO)
    serpro.consultas = [(200, {"ni": CPF_NOVO, "nome": NOME_OFICIAL, "nomeSocial": NOME_SOCIAL,
                               "nascimento": NASC_DDMM})]
    _ident(client, auth)
    texto = "\n".join(r.getMessage() for r in caplog.records)
    for proibido in (CPF_NOVO, CPF_OUTRO, NASC_DDMM, NASC, NOME_OFICIAL, NOME_SOCIAL,
                     TOKEN, CONSUMER_SECRET):
        assert proibido not in texto
    assert logging.getLogger("httpx").level == logging.WARNING


# ═══════════════════════════════════════ comprovante → auditoria sem PII

def _comprovante(client, auth):
    return _ident(client, auth).json()["comprovante"]


def test_nome_confirmado_registrado(client, auth, ativo, serpro, db):
    comp = _comprovante(client, auth)
    r = _novo_paciente(client, auth, {"nome_completo": "Marina Costa Ribeiro", "contatos": [],
                                      "cpf": CPF_NOVO, "data_nascimento": NASC,
                                      "identificacao_oficial": comp})
    assert r.status_code == 201, r.text
    (linha,) = _auditoria(db, "pessoa.identificacao_oficial")
    assert linha.detalhes == {"identificacao_oficial": "serpro_confirmado"}


def test_nome_editado_apos_confirmacao_registrado(client, auth, ativo, serpro, db):
    comp = _comprovante(client, auth)
    r = _criar(client, auth, nome_completo="Marina C. Ribeiro", cpf=CPF_NOVO,
               data_nascimento=NASC, identificacao_oficial=comp)
    assert r.status_code == 201
    (linha,) = _auditoria(db, "pessoa.identificacao_oficial")
    assert linha.detalhes == {"identificacao_oficial": "serpro_nome_alterado"}
    assert "Marina" not in json.dumps(linha.detalhes)


def test_comprovante_de_outro_par_e_invalido(client, auth, ativo, serpro, db):
    comp = _comprovante(client, auth)
    r = _criar(client, auth, nome_completo=NOME_OFICIAL, cpf=CPF_OUTRO,
               data_nascimento=NASC, identificacao_oficial=comp)
    assert r.status_code == 201
    (linha,) = _auditoria(db, "pessoa.identificacao_oficial")
    assert linha.detalhes == {"identificacao_oficial": "comprovante_invalido"}


def test_comprovante_expirado_e_adulterado():
    s = Settings(auth_secret="x" * 40 + "abcdefghij")
    nasc = date(1970, 11, 14)
    comp = ia.emitir_comprovante(CPF_NOVO, nasc, NOME_OFICIAL, settings=s, agora=1000)
    assert ia.avaliar_comprovante(comp, CPF_NOVO, nasc, NOME_OFICIAL, settings=s, agora=1001) \
        == "serpro_confirmado"
    assert ia.avaliar_comprovante(comp, CPF_NOVO, nasc, NOME_OFICIAL, settings=s,
                                  agora=1000 + ia.COMPROVANTE_VALIDADE + 1) == "comprovante_invalido"
    adulterado = comp[:-1] + ("0" if comp[-1] != "0" else "1")
    assert ia.avaliar_comprovante(adulterado, CPF_NOVO, nasc, NOME_OFICIAL, settings=s,
                                  agora=1001) == "serpro_nome_alterado"
    partes = comp.split(".")
    partes[2] = "0" * 32
    assert ia.avaliar_comprovante(".".join(partes), CPF_NOVO, nasc, NOME_OFICIAL, settings=s,
                                  agora=1001) == "comprovante_invalido"
    assert ia.avaliar_comprovante(None, CPF_NOVO, nasc, NOME_OFICIAL, settings=s) is None


# ═══════════════════════════════════════ provider: transporte

def test_provider_recusa_host_fora_da_allowlist():
    with pytest.raises(SerproCpfError):
        SerproCpfClient._conferir_host("https://consulta-falsa.example.com/cpf")
    with pytest.raises(SerproCpfError):
        SerproCpfClient._conferir_host(f"http://{ALLOWED_HOST}/token")


def test_provider_limita_tamanho_da_resposta(serpro):
    serpro.consultas = [(200, b"x" * (MAX_RESPONSE_BYTES + 10))]
    cliente = SerproCpfClient(consumer_key=CONSUMER_KEY, consumer_secret=CONSUMER_SECRET,
                              transport=httpx.MockTransport(serpro.handler))
    assert cliente.consultar(CPF_NOVO, date(1970, 11, 14)).resultado == "indisponivel"


def test_provider_nao_segue_redirect(serpro):
    def handler(request):
        if str(request.url) == TOKEN_URL:
            return httpx.Response(200, json={"access_token": TOKEN, "expires_in": 3600})
        return httpx.Response(302, headers={"Location": "https://outro.example.com/"})
    cliente = SerproCpfClient(consumer_key=CONSUMER_KEY, consumer_secret=CONSUMER_SECRET,
                              transport=httpx.MockTransport(handler))
    assert cliente.consultar(CPF_NOVO, date(1970, 11, 14)).resultado == "indisponivel"


def test_provider_repr_sem_segredo():
    c = SerproCpfClient(consumer_key=CONSUMER_KEY, consumer_secret=CONSUMER_SECRET)
    assert CONSUMER_SECRET not in repr(c) and CONSUMER_KEY not in repr(c)


def test_token_expira_antes_do_ttl_oficial(serpro):
    agora = [0.0]
    cliente = SerproCpfClient(consumer_key=CONSUMER_KEY, consumer_secret=CONSUMER_SECRET,
                              transport=httpx.MockTransport(serpro.handler), clock=lambda: agora[0])
    cliente.consultar(CPF_NOVO, date(1970, 11, 14))
    agora[0] = 3295 - 300 + 1   # passou da margem de segurança
    cliente.consultar(CPF_NOVO, date(1970, 11, 14))
    assert serpro.token_calls == 2


# ═══════════════════════════════════════ nada fiscal

def test_nada_fiscal_criado(client, auth, ativo, serpro, db):
    comp = _comprovante(client, auth)
    r = _novo_paciente(client, auth, {"nome_completo": "Marina Costa Ribeiro", "contatos": [],
                                      "cpf": CPF_NOVO, "data_nascimento": NASC,
                                      "identificacao_oficial": comp})
    assert r.status_code == 201
    db.expire_all()
    for modelo in (FiscalDocument, FiscalAttempt, FiscalIssuanceRequest):
        assert db.scalar(select(func.count()).select_from(modelo)) == 0
    assert all(c.url.host == ALLOWED_HOST for c in serpro.calls)
