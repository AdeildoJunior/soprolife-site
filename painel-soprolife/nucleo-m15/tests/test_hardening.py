"""Hardening M15.1A: config de produção, PCMSO por todos os canais,
dados sintéticos não discáveis, identidade sem merge, logs sem PII."""

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.config import Settings


# ------------------------------------------------------------------ config

def _settings(**kwargs):
    return Settings(_env_file=None, **kwargs)


def test_prod_exige_segredo_forte():
    with pytest.raises(ValidationError, match="AUTH_SECRET"):
        _settings(env="prod", auth_secret="x")
    with pytest.raises(ValidationError, match="AUTH_SECRET"):
        _settings(env="prod", auth_secret="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    ok = _settings(env="prod", auth_secret="f" * 8 + "0123456789abcdefghij" + "Z" * 8)
    assert ok.env == "prod"


def test_ttl_fora_da_faixa_rejeitado():
    with pytest.raises(ValidationError):
        _settings(token_ttl_minutes=0)
    with pytest.raises(ValidationError):
        _settings(token_ttl_minutes=100000)
    assert _settings(token_ttl_minutes=120).token_ttl_minutes == 120


def test_env_invalido_rejeitado():
    with pytest.raises(ValidationError):
        _settings(env="staging")


def test_cors_wildcard_rejeitado():
    with pytest.raises(ValidationError, match="explícitas"):
        _settings(cors_origins=["*"])
    with pytest.raises(ValidationError):
        _settings(cors_origins=["ftp://x"])
    with pytest.raises(ValidationError):
        _settings(cors_origins=[])
    with pytest.raises(ValidationError):
        _settings(cors_origins=["https://painel.example/caminho"])


def test_prod_cors_remoto_exige_https():
    secret = "f" * 8 + "0123456789abcdefghij" + "Z" * 8
    with pytest.raises(ValidationError, match="HTTPS"):
        _settings(
            env="prod",
            auth_secret=secret,
            cors_origins=["http://painel.example"],
        )
    ok = _settings(
        env="prod",
        auth_secret=secret,
        cors_origins=["https://painel.example"],
    )
    assert ok.cors_origins == ["https://painel.example"]


def test_prod_bind_publico_rejeitado():
    secret = "f" * 8 + "0123456789abcdefghij" + "Z" * 8
    with pytest.raises(ValidationError, match="loopback"):
        _settings(env="prod", auth_secret=secret, api_host="0.0.0.0")
    ok = _settings(env="prod", auth_secret=secret, api_host="0.0.0.0",
                   allow_nonlocal_bind="eu-entendo-o-risco")
    assert ok.api_host == "0.0.0.0"


def test_serve_impoe_host_porta_e_desliga_access_log(monkeypatch):
    """Entrypoint oficial consome M15_API_HOST/PORT e nunca liga access log."""
    from app import serve

    captured = {}

    def fake_run(app, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(serve.uvicorn, "run", fake_run)
    serve.main()
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 8015
    assert captured["access_log"] is False


# ------------------------------------------------------------------ PCMSO

PCMSO_CASES = [
    ("/api/v1/leads", {"origem": "empresa PCMSO"}),
    ("/api/v1/leads", {"canal_entrada": "medicina do trabalho"}),
    ("/api/v1/leads", {"observacao": "cliente veio de PCMSO"}),
    ("/api/v1/espirometrias", {"local_atendimento": "unidade ocupacional"}),
    ("/api/v1/espirometrias", {"origem": "aso admissional pcmso"}),
    ("/api/v1/consultas", {"observacao": "encaminhado pelo PCMSO"}),
]


@pytest.mark.parametrize("endpoint,extra", PCMSO_CASES)
def test_pcmso_bloqueado_em_todos_os_canais(client, auth, person, endpoint, extra):
    payload = {"person_id": person["id"]}
    payload.update(extra)
    resp = client.post(endpoint, json=payload, headers=auth("operacional"))
    assert resp.status_code == 422, resp.text
    assert resp.json()["erro"]["mensagem"]["codigo"] == "pcmso_fora_da_operacao"


def test_pcmso_parceiro_bloqueado(client, auth):
    resp = client.post(
        "/api/v1/parceiros",
        json={"nome": "Clínica Ocupacional PCMSO Ltda"},
        headers=auth("operacional"),
    )
    assert resp.status_code == 422
    assert resp.json()["erro"]["mensagem"]["codigo"] == "pcmso_fora_da_operacao"


def test_pcmso_rejeicao_e_auditada(client, auth, person):
    client.post(
        "/api/v1/leads",
        json={"person_id": person["id"], "origem": "PCMSO"},
        headers=auth("operacional"),
    )
    trilha = client.get(
        "/api/v1/auditoria?acao=pcmso.rejeitado", headers=auth("gestor")
    ).json()
    assert trilha["total"] >= 1


def test_pcmso_seed_institucional_rejeitado(db):
    from app.seed import seed_institutional

    result = seed_institutional(db, {"parceiros": [{"nome": "Empresa PCMSO X"}]})
    assert result["resultados"][0]["status"] == "rejeitado_pcmso"


# ------------------------------------------------------- dados sintéticos

def test_seed_nao_discavel_sem_whatsapp(db):
    from app.models import Followup, Person, PersonContact
    from app.seed import seed_demo

    seed_demo(db)
    db.commit()
    contatos = db.execute(select(PersonContact)).scalars().all()
    whatsapp = [c for c in contatos if c.tipo in ("whatsapp", "telefone")]
    assert whatsapp, "seed deveria ter contatos de telefone"
    assert all(c.nao_discavel for c in whatsapp)
    # números começam com prefixo local 0000 (não atribuído — não discável)
    assert all(c.valor_normalizado.startswith("55210000") for c in whatsapp)


def test_seed_whatsapp_url_bloqueada(client, auth, engine):
    """Nenhuma URL wa.me é criada para registro sintético."""
    from sqlalchemy.orm import sessionmaker

    from app.models import Followup
    from app.seed import seed_demo

    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    seed_demo(session)
    session.commit()
    fup = session.execute(select(Followup).where(
        Followup.status == "pendente"
    )).scalars().first()
    session.close()
    resp = client.get(
        f"/api/v1/followups/{fup.id}/whatsapp-url", headers=auth("operacional")
    )
    assert resp.status_code == 409
    assert resp.json()["erro"]["mensagem"]["codigo"] in (
        "registro_sintetico", "sem_consentimento"
    )


# ------------------------------------------------------------- identidade

def test_decisao_identidade_sem_merge(client, auth, person):
    from tests.conftest import SYNTH_PHONE

    client.post(
        "/api/v1/pessoas",
        json={"nome_completo": "Possível Duplicata 001",
              "contatos": [{"tipo": "whatsapp", "valor": SYNTH_PHONE}]},
        headers=auth("operacional"),
    )
    candidato = client.get(
        "/api/v1/identidade/candidatos", headers=auth("gestor")
    ).json()["itens"][0]
    # decisão exige gestor
    denied = client.post(
        f"/api/v1/identidade/candidatos/{candidato['id']}/decisao",
        json={"decisao": "pessoas_diferentes"},
        headers=auth("operacional"),
    )
    assert denied.status_code == 403
    decided = client.post(
        f"/api/v1/identidade/candidatos/{candidato['id']}/decisao",
        json={"decisao": "pessoas_diferentes", "observacao": "conferido por telefone"},
        headers=auth("gestor"),
    )
    assert decided.status_code == 200
    assert decided.json()["status"] == "pessoas_diferentes"
    # nada foi fundido: as duas pessoas continuam existindo
    pessoas = client.get("/api/v1/pessoas", headers=auth("leitura")).json()
    assert pessoas["total"] == 2
    # decidir de novo -> 409 (append-only, sem regravação)
    again = client.post(
        f"/api/v1/identidade/candidatos/{candidato['id']}/decisao",
        json={"decisao": "adiar"},
        headers=auth("gestor"),
    )
    assert again.status_code == 409


def test_candidato_pendente_nao_duplica(db):
    from app.ids import allocate_public_code
    from app.models import IdentityCandidate, Person
    from app.normalize import normalize_name
    from app.services.identity import register_candidates

    def make_person(nome):
        person = Person(
            public_code=allocate_public_code(db, "people"),
            nome_completo=nome, nome_normalizado=normalize_name(nome),
        )
        db.add(person)
        db.flush()
        return person

    a = make_person("Pessoa Candidata A")
    b = make_person("Pessoa Candidata B")
    register_candidates(db, a, [(b, "telefone_igual")])
    db.commit()
    # repetir o registro do MESMO par/motivo não duplica o pendente
    register_candidates(db, a, [(b, "telefone_igual")])
    db.commit()
    total = db.execute(select(IdentityCandidate)).scalars().all()
    assert len(total) == 1


# ------------------------------------------------------------ logs sem PII

def test_nome_nao_vai_em_query_string(client, auth):
    """Contrato de logs: buscas por nome só via POST/corpo."""
    resp = client.post(
        "/api/v1/pessoas/busca",
        json={"q": "Nome Sensível"},
        headers=auth("leitura"),
    )
    assert resp.status_code == 200
    assert resp.request.url.query == b""  # nada de PII na URL enviada


def test_paginacao_da_fila(client, auth):
    for i in range(3):
        pessoa = client.post(
            "/api/v1/pessoas",
            json={"nome_completo": f"Fila Paginada {i:02d}"},
            headers=auth("operacional"),
        ).json()
        client.post(
            "/api/v1/followups",
            json={"person_id": pessoa["id"], "tipo": "manual",
                  "due_date": "2020-01-01"},
            headers=auth("operacional"),
        )
    page = client.get(
        "/api/v1/followups/fila?fila=atrasado&pagina=2&tamanho=2",
        headers=auth("leitura"),
    ).json()
    assert page["total"] == 3
    assert page["pagina"] == 2
    assert len(page["itens"]) == 1


def test_consentimentos_paginados(client, auth, person):
    for status in ("revogado", "concedido", "revogado"):
        client.post(
            f"/api/v1/pessoas/{person['id']}/consentimentos",
            json={"canal": "whatsapp", "status": status},
            headers=auth("operacional"),
        )
    page = client.get(
        f"/api/v1/pessoas/{person['id']}/consentimentos?pagina=1&tamanho=2",
        headers=auth("leitura"),
    ).json()
    assert page["total"] == 4  # 1 do cadastro + 3 novos
    assert len(page["itens"]) == 2
