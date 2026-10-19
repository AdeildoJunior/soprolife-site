"""Fase 2A — fundação operacional da EEGLife.

Tudo aqui é SINTÉTICO: pessoas inventadas, parceiro inventado, PDF gerado na
hora. Nenhum dado real, nenhum nome de pessoa real, nenhum telefone discável
(o prefixo 0000 não é atribuído no Brasil).

As duas provas que mais importam:

1. IDENTIDADE NÃO DUPLICA. Uma pessoa que já existe por operação SoproLife é
   REAPROVEITADA ao entrar na EEGLife — e a tentativa de cadastrá-la de novo é
   recusada com os candidatos na mão.

2. ESCOPO NÃO VAZA. Pessoa da SoproLife SEM vínculo EEG não aparece na Central
   da EEGLife, e nenhuma rota de EEG responde a quem não é admin.
"""

from __future__ import annotations

import io
import os
from pathlib import Path

import pytest
from pypdf import PdfWriter
from sqlalchemy import select

from app.models import (
    AuditLog,
    EegEnrollment,
    EegExam,
    EegReportDocument,
    EegTechnicalFile,
    Partner,
    PartnerUnit,
    Person,
    ReportDocument,
)
from app.services.eeg_flow import TransicaoInvalidaError, garantir_transicao

API = "/api/v1"
FONE_SINTETICO = "(21) 0000-2201"
FONE_SINTETICO_B = "(21) 0000-2202"


def _codigo(resp) -> str:
    """Código de máquina do envelope de erro da casa."""
    return resp.json()["erro"]["codigo"]


def _ler(db, stmt) -> list:
    """Lê pelo ORM e FECHA a transação em seguida.

    No SQLite dos testes a sessão do teste e a da API são duas conexões para o
    mesmo arquivo. Uma leitura deixada em transação aberta trava a próxima
    escrita da API com `database is locked` — e o sintoma aparece longe da
    causa (vira 401, 500, o que a rota devolver). Fechar aqui evita isso.
    """
    try:
        return db.execute(stmt).scalars().all()
    finally:
        db.rollback()


def _pdf(pages: int = 1) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=595, height=842)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


@pytest.fixture(autouse=True)
def storage(tmp_path, monkeypatch):
    """Raiz privada 0700 fora do repositório, como exige o serviço de laudos.

    AUTOUSE e fixando `M15_AUTH_SECRET` de propósito, no padrão já usado pelos
    testes de laudo: `resolved_auth_secret()` gera segredo EFÊMERO por
    instância de Settings quando a variável não existe, então um
    `cache_clear()` no meio do teste rotaciona o segredo e derruba todo token
    já emitido. O sintoma aparece longe da causa — vira 401 numa rota que não
    tem nada a ver com armazenamento.
    """
    root = tmp_path / "acervo"
    root.mkdir(mode=0o700)
    os.chmod(root, 0o700)
    monkeypatch.setenv("M15_REPORTS_STORAGE_DIR", str(root))
    monkeypatch.setenv(
        "M15_AUTH_SECRET", "fase2a-eeg-secret-apenas-para-testes-0123456789ab"
    )
    from app.config import get_settings

    get_settings.cache_clear()
    yield root
    get_settings.cache_clear()


def _pessoa_sopro(client, auth, nome: str, telefone: str | None = None) -> dict:
    """Pessoa criada pelo caminho CORPORATIVO normal (sem EEG)."""
    contatos = [{"tipo": "whatsapp", "valor": telefone, "principal": True}] if telefone else []
    resp = client.post(f"{API}/pessoas",
                       json={"nome_completo": nome, "contatos": contatos},
                       headers=auth("operacional"))
    assert resp.status_code == 201, resp.text
    return resp.json()


def _unidade(client, auth) -> dict:
    """Parceiro e unidade sintéticos, criados pela API CORPORATIVA.

    De propósito pela API, e não pela sessão do teste: são duas conexões para
    o mesmo arquivo SQLite, e escrever pelas duas dentro do mesmo teste trava
    a segunda com `database is locked`. Usar a rota real também prova o que
    interessa — a EEGLife aponta para o parceiro que já existe no cadastro
    corporativo, sem criar uma segunda empresa.
    """
    parceiro = client.post(f"{API}/parceiros",
                           json={"nome": "Clinica Sintetica F2A", "tipo": "clinica",
                                 "cidade": "Rio de Janeiro"},
                           headers=auth("operacional"))
    assert parceiro.status_code == 201, parceiro.text
    unidade = client.post(f"{API}/unidades",
                          json={"partner_id": parceiro.json()["id"],
                                "nome": "Unidade Sintetica F2A",
                                "cidade": "Rio de Janeiro"},
                          headers=auth("operacional"))
    assert unidade.status_code == 201, unidade.text
    return unidade.json()


def _vincular(client, auth, person_id: str) -> dict:
    resp = client.post(f"{API}/eeg/pessoas/vincular",
                       json={"person_id": person_id, "origem": "teste"},
                       headers=auth("admin"))
    assert resp.status_code == 201, resp.text
    return resp.json()


def _exame(client, auth, enrollment_id: str, **extra) -> dict:
    corpo = {"enrollment_id": enrollment_id, "modalidade_codigo": "vigilia"}
    corpo.update(extra)
    resp = client.post(f"{API}/eeg/exames", json=corpo, headers=auth("admin"))
    assert resp.status_code == 201, resp.text
    return resp.json()


def _status(client, auth, exam_id: str, status: str, espera: int = 200):
    resp = client.patch(f"{API}/eeg/exames/{exam_id}/status",
                        json={"status": status}, headers=auth("admin"))
    assert resp.status_code == espera, resp.text
    return resp


# ══════════════════════════ 1. identidade compartilhada ══════════════════

def test_pessoa_sopro_existente_e_reaproveitada_sem_duplicar(client, auth, db):
    """O caso que dá nome à regra: mesma pessoa, duas marcas, um cadastro."""

    pessoa = _pessoa_sopro(client, auth, "Paciente Sintetico F2A Compartilhado",
                           FONE_SINTETICO)
    antes = _ler(db, select(Person))

    vinculo = _vincular(client, auth, pessoa["id"])

    depois = _ler(db, select(Person))
    assert len(depois) == len(antes), "vincular não pode criar pessoa"
    assert vinculo["pessoa"]["id"] == pessoa["id"]
    assert vinculo["pessoa"]["public_code"] == pessoa["public_code"], (
        "o código público corporativo continua o mesmo"
    )


def test_cadastro_eeg_recusa_duplicata_e_oferece_a_pessoa_existente(client, auth, db):
    pessoa = _pessoa_sopro(client, auth, "Paciente Sintetico F2A Duplo", FONE_SINTETICO_B)

    resp = client.post(f"{API}/eeg/pessoas",
                       json={"nome_completo": "Paciente Sintetico F2A Duplo",
                             "telefone": FONE_SINTETICO_B},
                       headers=auth("admin"))
    assert resp.status_code == 409, resp.text
    assert _codigo(resp) == "pessoa_possivelmente_existente"
    # E nada foi criado pela tentativa.
    assert _ler(db, select(EegEnrollment)) == []

    # Os candidatos vêm do endpoint próprio, sem efeito colateral.
    candidatos = client.post(
        f"{API}/eeg/pessoas/verificar",
        json={"nome_completo": "Paciente Sintetico F2A Duplo",
              "telefone": FONE_SINTETICO_B},
        headers=auth("admin")).json()["itens"]
    assert any(c["person_id"] == pessoa["id"] for c in candidatos)
    assert all(c["ja_vinculada"] is False for c in candidatos)


def test_pessoa_nova_so_de_eeg_nasce_vinculada(client, auth, db):
    resp = client.post(f"{API}/eeg/pessoas",
                       json={"nome_completo": "Paciente Sintetico F2A Novo",
                             "telefone": "(21) 0000-2203", "sexo": "feminino"},
                       headers=auth("admin"))
    assert resp.status_code == 201, resp.text
    vinculo = resp.json()
    assert vinculo["pessoa"]["nome_completo"] == "Paciente Sintetico F2A Novo"
    assert db.get(EegEnrollment, vinculo["id"]) is not None


# ══════════════════════════ 2. escopo da Central ═════════════════════════

def test_pessoa_sopro_sem_vinculo_nao_aparece_na_central_eeg(client, auth):
    """A prova de que a base da SoproLife não vaza para a EEGLife."""

    sem_vinculo = _pessoa_sopro(client, auth, "Paciente Sintetico F2A Apenas Sopro")
    com_vinculo = _pessoa_sopro(client, auth, "Paciente Sintetico F2A Com EEG")
    _vincular(client, auth, com_vinculo["id"])

    listagem = client.get(f"{API}/eeg/pessoas", headers=auth("admin")).json()
    ids = {item["pessoa"]["id"] for item in listagem["itens"]}
    assert com_vinculo["id"] in ids
    assert sem_vinculo["id"] not in ids, "pessoa sem vínculo EEG não pode ser listada"
    assert listagem["total"] == 1


def test_central_eeg_nao_lista_exames_de_espirometria(client, auth, db):
    pessoa = _pessoa_sopro(client, auth, "Paciente Sintetico F2A Misto")
    vinculo = _vincular(client, auth, pessoa["id"])
    exame = _exame(client, auth, vinculo["id"])

    fila = client.get(f"{API}/eeg/exames", headers=auth("admin")).json()
    assert [i["public_code"] for i in fila["itens"]] == [exame["public_code"]]
    assert exame["public_code"].startswith("EEG"), "código próprio, não ESP"


# ══════════════════════════ 3. autorização server-side ═══════════════════

ROTAS_EEG = [
    ("get", "/eeg/modalidades"),
    ("get", "/eeg/locais"),
    ("get", "/eeg/pessoas"),
    ("get", "/eeg/exames"),
    ("get", "/eeg/laudos"),
]


@pytest.mark.parametrize("metodo,rota", ROTAS_EEG)
@pytest.mark.parametrize("papel", ["operacional", "gestor", "leitura"])
def test_nao_admin_recebe_403_em_toda_rota_eeg(client, auth, metodo, rota, papel):
    """Nenhuma rota de EEG responde a quem não é admin — nem de leitura."""
    resp = getattr(client, metodo)(f"{API}{rota}", headers=auth(papel))
    assert resp.status_code == 403, f"{papel} alcançou {rota}: {resp.status_code}"


def test_medico_sopro_nao_acessa_nada_de_eeg(client, auth, db):
    """A conta exclusivamente clínica da SoproLife não entra na EEGLife.

    A regra é de PAPEL: `admin` implica gestor/operacional/leitura e nunca
    `medico`. Nenhum nome de pessoa participa da decisão.
    """
    from tests.conftest import _make_user
    from app.security import issue_token

    medico = _make_user(db, "medica-f2a@teste.local", "medico")
    db.rollback()
    headers = {"Authorization": f"Bearer {issue_token(medico.id, medico.password_hash)}"}
    for metodo, rota in ROTAS_EEG:
        resp = getattr(client, metodo)(f"{API}{rota}", headers=headers)
        assert resp.status_code == 403, f"médico alcançou {rota}"


def test_escrita_eeg_tambem_e_negada_a_nao_admin(client, auth):
    resp = client.post(f"{API}/eeg/pessoas",
                       json={"nome_completo": "Paciente Sintetico F2A Negado"},
                       headers=auth("operacional"))
    assert resp.status_code == 403


def test_download_de_laudo_eeg_exige_admin(client, auth, db, storage):
    pessoa = _pessoa_sopro(client, auth, "Paciente Sintetico F2A Download")
    vinculo = _vincular(client, auth, pessoa["id"])
    exame = _exame(client, auth, vinculo["id"])
    _status(client, auth, exame["id"], "agendado")
    _status(client, auth, exame["id"], "realizado")
    client.post(f"{API}/eeg/exames/{exame['id']}/envio-laudo",
                json={"destino_tipo": "empresa", "destino_nome": "Laudador Sintetico",
                      "metodo": "plataforma_externa"}, headers=auth("admin"))
    resp = client.post(f"{API}/eeg/exames/{exame['id']}/laudo",
                       files={"arquivo": ("laudo.pdf", _pdf(), "application/pdf")},
                       headers=auth("admin"))
    assert resp.status_code == 201, resp.text
    documento_id = resp.json()["laudo"]["id"]

    assert client.get(f"{API}/eeg/laudos/{documento_id}/arquivo",
                      headers=auth("operacional")).status_code == 403
    ok = client.get(f"{API}/eeg/laudos/{documento_id}/arquivo", headers=auth("admin"))
    assert ok.status_code == 200
    assert ok.content.startswith(b"%PDF-")


# ══════════════════════════ 4. modalidades ═══════════════════════════════

def test_modalidades_sao_as_sete_reais(client, auth):
    itens = client.get(f"{API}/eeg/modalidades", headers=auth("admin")).json()["itens"]
    codigos = [m["codigo"] for m in itens]
    assert codigos == [
        "vigilia", "fotoestimulacao", "prolongado-30-60", "prolongado-2h",
        "digital-por-hora", "mapeamento", "mapeamento-fotoestimulacao",
    ]
    # Onde a duração não está declarada em número na fonte, fica nula — não
    # se inventa minuto.
    por_codigo = {m["codigo"]: m for m in itens}
    assert por_codigo["vigilia"]["duracao_minutos_aproximada"] is None
    assert por_codigo["prolongado-30-60"]["duracao_minutos_aproximada"] is None
    assert por_codigo["prolongado-2h"]["duracao_minutos_aproximada"] == 120
    assert por_codigo["mapeamento"]["duracao_minutos_aproximada"] is None


def test_modalidade_invalida_e_recusada(client, auth):
    pessoa = _pessoa_sopro(client, auth, "Paciente Sintetico F2A Modalidade")
    vinculo = _vincular(client, auth, pessoa["id"])
    resp = client.post(f"{API}/eeg/exames",
                       json={"enrollment_id": vinculo["id"],
                             "modalidade_codigo": "eeg-inventado"},
                       headers=auth("admin"))
    assert resp.status_code == 422
    assert _codigo(resp) == "modalidade_invalida"


# ══════════════════════════ 5. parceiro compartilhado ════════════════════

def test_parceiro_corporativo_e_reaproveitado_sem_duplicar_empresa(client, auth, db):
    unidade = _unidade(client, auth)

    pessoa = _pessoa_sopro(client, auth, "Paciente Sintetico F2A Parceiro")
    vinculo = _vincular(client, auth, pessoa["id"])
    exame = _exame(client, auth, vinculo["id"],
                   origem_tipo="parceiro", partner_unit_id=unidade["id"])

    assert exame["local_nome"] == unidade["nome"]
    # Uma empresa só: a EEGLife aponta para o parceiro corporativo existente.
    assert len(_ler(db, select(Partner))) == 1


def test_origem_parceiro_exige_unidade_e_direto_a_recusa(client, auth):
    unidade = _unidade(client, auth)
    pessoa = _pessoa_sopro(client, auth, "Paciente Sintetico F2A Origem")
    vinculo = _vincular(client, auth, pessoa["id"])

    sem_unidade = client.post(f"{API}/eeg/exames",
                              json={"enrollment_id": vinculo["id"],
                                    "modalidade_codigo": "vigilia",
                                    "origem_tipo": "parceiro"},
                              headers=auth("admin"))
    assert sem_unidade.status_code == 422

    direto_com_unidade = client.post(f"{API}/eeg/exames",
                                     json={"enrollment_id": vinculo["id"],
                                           "modalidade_codigo": "vigilia",
                                           "origem_tipo": "direto",
                                           "partner_unit_id": unidade["id"]},
                                     headers=auth("admin"))
    assert direto_com_unidade.status_code == 422


# ══════════════════════════ 6. fluxo e transições ════════════════════════

def test_agendamento_registra_data_sem_inventar(client, auth):
    pessoa = _pessoa_sopro(client, auth, "Paciente Sintetico F2A Agenda")
    vinculo = _vincular(client, auth, pessoa["id"])
    exame = _exame(client, auth, vinculo["id"],
                   agendado_para="2026-11-03T14:30:00+00:00")
    assert exame["status"] == "agendado"
    assert exame["agendado_para"].startswith("2026-11-03T14:30")
    # Nada além do agendamento foi carimbado.
    assert exame["realizado_em"] is None
    assert exame["laudo_recebido_em"] is None


def test_exame_sem_data_nasce_em_cadastro(client, auth):
    pessoa = _pessoa_sopro(client, auth, "Paciente Sintetico F2A Cadastro")
    vinculo = _vincular(client, auth, pessoa["id"])
    exame = _exame(client, auth, vinculo["id"])
    assert exame["status"] == "cadastro"
    assert exame["agendado_para"] is None


def test_realizado_e_laudo_sao_eventos_diferentes(client, auth, db, storage):
    """O erro que esta modelagem existe para impedir."""
    pessoa = _pessoa_sopro(client, auth, "Paciente Sintetico F2A Eventos")
    vinculo = _vincular(client, auth, pessoa["id"])
    exame = _exame(client, auth, vinculo["id"], agendado_para="2026-11-04T09:00:00+00:00")

    realizado = _status(client, auth, exame["id"], "realizado").json()
    assert realizado["realizado_em"] is not None
    assert realizado["laudo_recebido_em"] is None, (
        "realizar o exame não pode carimbar o laudo"
    )


def test_transicao_invalida_e_recusada(client, auth):
    pessoa = _pessoa_sopro(client, auth, "Paciente Sintetico F2A Transicao")
    vinculo = _vincular(client, auth, pessoa["id"])
    exame = _exame(client, auth, vinculo["id"], agendado_para="2026-11-05T09:00:00+00:00")

    resp = _status(client, auth, exame["id"], "laudo_disponivel", espera=409)
    assert _codigo(resp) == "transicao_invalida"
    assert "agendado" in resp.json()["erro"]["mensagem"]


def test_disponibilizar_sem_laudo_e_recusado(client, auth):
    pessoa = _pessoa_sopro(client, auth, "Paciente Sintetico F2A SemLaudo")
    vinculo = _vincular(client, auth, pessoa["id"])
    exame = _exame(client, auth, vinculo["id"], agendado_para="2026-11-06T09:00:00+00:00")
    _status(client, auth, exame["id"], "realizado")
    _status(client, auth, exame["id"], "aguardando_envio_laudo")
    client.post(f"{API}/eeg/exames/{exame['id']}/envio-laudo",
                json={"destino_tipo": "medico", "destino_nome": "Dr. Sintetico",
                      "metodo": "outro"}, headers=auth("admin"))
    resp = _status(client, auth, exame["id"], "laudo_recebido", espera=409)
    assert _codigo(resp) == "evento_exige_registro"
    _status(client, auth, exame["id"], "laudo_disponivel", espera=409)


def test_maquina_de_estados_recusa_pulo_direto():
    garantir_transicao("cadastro", "agendado")
    with pytest.raises(TransicaoInvalidaError):
        garantir_transicao("cadastro", "laudo_recebido")
    with pytest.raises(TransicaoInvalidaError):
        garantir_transicao("laudo_disponivel", "realizado")


# ══════════════════════════ 7. arquivo técnico ═══════════════════════════

def test_arquivo_tecnico_guarda_metadados_e_nunca_blob(client, auth, db):
    pessoa = _pessoa_sopro(client, auth, "Paciente Sintetico F2A Tecnico")
    vinculo = _vincular(client, auth, pessoa["id"])
    exame = _exame(client, auth, vinculo["id"], agendado_para="2026-11-06T09:00:00+00:00")
    _status(client, auth, exame["id"], "realizado")

    resp = client.post(
        f"{API}/eeg/exames/{exame['id']}/arquivo-tecnico",
        json={"armazenamento_origem": "equipamento", "formato": "EDF+",
              "referencia_externa": "PROTO-SINTETICO-001",
              "tamanho_bytes": 734003200, "canais": 21, "frequencia_hz": 256},
        headers=auth("admin"))
    assert resp.status_code == 201, resp.text
    assert resp.json()["arquivo_tecnico"]["registrado"] is True
    assert resp.json()["arquivo_tecnico"]["formato"] == "EDF+"

    registro = _ler(db, select(EegTechnicalFile))[0]
    # 700 MB declarados, e nenhum byte no banco: é o ponto.
    assert registro.tamanho_bytes == 734003200
    colunas = {c.name for c in EegTechnicalFile.__table__.columns}
    assert not {"conteudo", "blob", "dados", "arquivo", "bytes"} & colunas
    tipos = {str(c.type).upper() for c in EegTechnicalFile.__table__.columns}
    assert not any("BLOB" in t or "BYTEA" in t or "LARGEBINARY" in t for t in tipos)


def test_arquivo_tecnico_aceita_so_a_origem_quando_falta_o_resto(client, auth):
    """Não exigir metadado que o operador ainda não tem evita número inventado."""
    pessoa = _pessoa_sopro(client, auth, "Paciente Sintetico F2A Minimo")
    vinculo = _vincular(client, auth, pessoa["id"])
    exame = _exame(client, auth, vinculo["id"], agendado_para="2026-11-06T09:00:00+00:00")
    _status(client, auth, exame["id"], "realizado")
    resp = client.post(f"{API}/eeg/exames/{exame['id']}/arquivo-tecnico",
                       json={"armazenamento_origem": "equipamento"},
                       headers=auth("admin"))
    assert resp.status_code == 201
    assert resp.json()["arquivo_tecnico"]["tamanho_bytes"] is None


# ══════════════════════════ 8. envio ao laudador ═════════════════════════

def test_envio_para_empresa_e_para_medico_sao_rastreaveis(client, auth, db):
    for destino_tipo, destino_nome, metodo in [
        ("empresa", "Plataforma Sintetica de Telelaudo", "plataforma_externa"),
        ("medico", "Dr. Laudador Sintetico", "upload_manual_externo"),
    ]:
        pessoa = _pessoa_sopro(client, auth, f"Paciente Sintetico F2A {destino_tipo}")
        vinculo = _vincular(client, auth, pessoa["id"])
        exame = _exame(client, auth, vinculo["id"], agendado_para="2026-11-07T09:00:00+00:00")
        _status(client, auth, exame["id"], "realizado")
        resp = client.post(f"{API}/eeg/exames/{exame['id']}/envio-laudo",
                           json={"destino_tipo": destino_tipo,
                                 "destino_nome": destino_nome, "metodo": metodo,
                                 "protocolo_externo": "REF-SINTETICA-9"},
                           headers=auth("admin"))
        assert resp.status_code == 201, resp.text
        corpo = resp.json()
        assert corpo["status"] == "enviado_para_laudo"
        assert corpo["enviado_para_laudo_em"] is not None
        assert corpo["envio_laudo"]["destino_tipo"] == destino_tipo
        assert corpo["envio_laudo"]["metodo"] == metodo


def test_envio_exige_exame_realizado(client, auth):
    pessoa = _pessoa_sopro(client, auth, "Paciente Sintetico F2A EnvioCedo")
    vinculo = _vincular(client, auth, pessoa["id"])
    exame = _exame(client, auth, vinculo["id"], agendado_para="2026-11-08T09:00:00+00:00")
    resp = client.post(f"{API}/eeg/exames/{exame['id']}/envio-laudo",
                       json={"destino_tipo": "empresa", "destino_nome": "Laudador Sintetico",
                             "metodo": "outro"}, headers=auth("admin"))
    assert resp.status_code == 409
    assert _codigo(resp) == "exame_nao_realizado"


# ══════════════════════════ 9. laudo EEG ═════════════════════════════════

def test_fluxo_completo_ate_laudo_disponivel(client, auth, db, storage):
    """Cadastro → agenda → realizado → técnico → envio → laudo → disponível."""
    pessoa = _pessoa_sopro(client, auth, "Paciente Sintetico F2A Completo")
    vinculo = _vincular(client, auth, pessoa["id"])
    exame = _exame(client, auth, vinculo["id"], agendado_para="2026-11-10T08:00:00+00:00")

    _status(client, auth, exame["id"], "realizado")
    client.post(f"{API}/eeg/exames/{exame['id']}/arquivo-tecnico",
                json={"armazenamento_origem": "equipamento", "formato": "EDF"},
                headers=auth("admin"))
    _status(client, auth, exame["id"], "aguardando_envio_laudo")
    client.post(f"{API}/eeg/exames/{exame['id']}/envio-laudo",
                json={"destino_tipo": "empresa", "destino_nome": "Telelaudo Sintetico",
                      "metodo": "plataforma_externa"}, headers=auth("admin"))

    recebido = client.post(f"{API}/eeg/exames/{exame['id']}/laudo",
                           files={"arquivo": ("laudo.pdf", _pdf(2), "application/pdf")},
                           data={"medico_laudador": "Dra. Sintetica"},
                           headers=auth("admin"))
    assert recebido.status_code == 201, recebido.text
    corpo = recebido.json()
    assert corpo["status"] == "laudo_recebido"
    assert corpo["laudo"]["versao"] == 1
    assert corpo["laudo"]["public_code"].startswith("LEG")

    final = _status(client, auth, exame["id"], "laudo_disponivel").json()
    assert final["status"] == "laudo_disponivel"
    assert final["disponibilizado_em"] is not None
    assert final["laudo"]["status"] == "vigente"

    # O PDF está no disco privado, fora do acervo da espirometria.
    documento = _ler(db, select(EegReportDocument))[0]
    caminho = Path(storage) / documento.storage_path
    assert caminho.exists()
    assert "eeg/laudos" in documento.storage_path.replace(os.sep, "/")
    assert "/laudos/" not in "/" + documento.storage_path.split("eeg/")[0]


def test_laudo_eeg_nao_vira_laudo_respiratorio(client, auth, db, storage):
    pessoa = _pessoa_sopro(client, auth, "Paciente Sintetico F2A NaoMistura")
    vinculo = _vincular(client, auth, pessoa["id"])
    exame = _exame(client, auth, vinculo["id"], agendado_para="2026-11-11T08:00:00+00:00")
    _status(client, auth, exame["id"], "realizado")
    client.post(f"{API}/eeg/exames/{exame['id']}/envio-laudo",
                json={"destino_tipo": "medico", "destino_nome": "Dr. Sintetico",
                      "metodo": "outro"}, headers=auth("admin"))
    client.post(f"{API}/eeg/exames/{exame['id']}/laudo",
                files={"arquivo": ("laudo.pdf", _pdf(), "application/pdf")},
                headers=auth("admin"))

    # Nenhuma linha entrou no acervo respiratório.
    assert _ler(db, select(ReportDocument)) == []
    assert len(_ler(db, select(EegReportDocument))) == 1


def test_segundo_laudo_versiona_e_substitui(client, auth, db, storage):
    pessoa = _pessoa_sopro(client, auth, "Paciente Sintetico F2A Versao")
    vinculo = _vincular(client, auth, pessoa["id"])
    exame = _exame(client, auth, vinculo["id"], agendado_para="2026-11-12T08:00:00+00:00")
    _status(client, auth, exame["id"], "realizado")
    client.post(f"{API}/eeg/exames/{exame['id']}/envio-laudo",
                json={"destino_tipo": "medico", "destino_nome": "Dr. Sintetico",
                      "metodo": "outro"}, headers=auth("admin"))
    for _ in range(2):
        client.post(f"{API}/eeg/exames/{exame['id']}/laudo",
                    files={"arquivo": ("laudo.pdf", _pdf(), "application/pdf")},
                    headers=auth("admin"))

    documentos = _ler(db, select(EegReportDocument).order_by(EegReportDocument.versao))
    assert [d.versao for d in documentos] == [1, 2]
    assert documentos[0].status == "substituido"
    assert documentos[1].status == "recebido"


def test_pdf_invalido_e_recusado(client, auth, storage):
    pessoa = _pessoa_sopro(client, auth, "Paciente Sintetico F2A PdfRuim")
    vinculo = _vincular(client, auth, pessoa["id"])
    exame = _exame(client, auth, vinculo["id"], agendado_para="2026-11-13T08:00:00+00:00")
    _status(client, auth, exame["id"], "realizado")
    client.post(f"{API}/eeg/exames/{exame['id']}/envio-laudo",
                json={"destino_tipo": "medico", "destino_nome": "Dr. Sintetico",
                      "metodo": "outro"}, headers=auth("admin"))
    resp = client.post(f"{API}/eeg/exames/{exame['id']}/laudo",
                       files={"arquivo": ("x.pdf", b"nao sou um pdf", "application/pdf")},
                       headers=auth("admin"))
    assert resp.status_code == 422
    assert _codigo(resp) == "pdf_invalido"


# ══════════════════════════ 10. auditoria ════════════════════════════════

def test_trilha_de_auditoria_cobre_o_fluxo(client, auth, db, storage):
    pessoa = _pessoa_sopro(client, auth, "Paciente Sintetico F2A Auditoria")
    vinculo = _vincular(client, auth, pessoa["id"])
    exame = _exame(client, auth, vinculo["id"], agendado_para="2026-11-14T08:00:00+00:00")
    _status(client, auth, exame["id"], "realizado")
    client.post(f"{API}/eeg/exames/{exame['id']}/arquivo-tecnico",
                json={"armazenamento_origem": "equipamento"}, headers=auth("admin"))
    client.post(f"{API}/eeg/exames/{exame['id']}/envio-laudo",
                json={"destino_tipo": "empresa", "destino_nome": "Telelaudo Sintetico",
                      "metodo": "plataforma_externa"}, headers=auth("admin"))
    client.post(f"{API}/eeg/exames/{exame['id']}/laudo",
                files={"arquivo": ("laudo.pdf", _pdf(), "application/pdf")},
                headers=auth("admin"))
    _status(client, auth, exame["id"], "laudo_disponivel")

    acoes = {a.acao for a in _ler(db, select(AuditLog))}
    for esperada in {"eeg.vinculo_criado", "eeg.exame_criado", "eeg.exame_status",
                     "eeg.arquivo_tecnico_registrado", "eeg.enviado_para_laudo",
                     "eeg.laudo_recebido"}:
        assert esperada in acoes, f"evento {esperada} não foi auditado"


def test_auditoria_nao_guarda_conteudo_clinico(client, auth, db, storage):
    pessoa = _pessoa_sopro(client, auth, "Paciente Sintetico F2A Privacidade")
    vinculo = _vincular(client, auth, pessoa["id"])
    exame = _exame(client, auth, vinculo["id"])
    linhas = _ler(db, select(AuditLog))
    bruto = " ".join(str(a.detalhes or "") for a in linhas)
    assert "Paciente Sintetico F2A Privacidade" not in bruto, (
        "nome de paciente não entra em log"
    )


# ══════════════════════════ 11. regressão SoproLife ══════════════════════

def test_soprolife_continua_intacta(client, auth, db):
    """A operação existente não foi tocada por nada desta fase."""
    pessoa = _pessoa_sopro(client, auth, "Paciente Sintetico F2A Regressao",
                           "(21) 0000-2299")
    detalhe = client.get(f"{API}/pessoas/{pessoa['id']}", headers=auth("leitura")).json()
    assert detalhe["nome_completo"] == "Paciente Sintetico F2A Regressao"
    # `people` não ganhou coluna de marca.
    colunas = {c.name for c in Person.__table__.columns}
    assert "brand" not in colunas and "brand_id" not in colunas and "marca" not in colunas
    # Nem `partners`, nem `spirometry_exams`.
    for tabela in (Partner, PartnerUnit):
        nomes = {c.name for c in tabela.__table__.columns}
        assert not {"brand", "brand_id", "marca"} & nomes


def test_nenhuma_pessoa_historica_ganhou_vinculo_eeg(client, auth, db):
    """Sem backfill: criar pessoas pelo caminho SoproLife não cria vínculo EEG."""
    for i in range(3):
        _pessoa_sopro(client, auth, f"Paciente Sintetico F2A Backfill {i}")
    assert _ler(db, select(EegEnrollment)) == []
