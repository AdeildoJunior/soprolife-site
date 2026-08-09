#!/usr/bin/env python3
"""M25.3 — cenário FICTÍCIO isolado para testar o Laudo Online localmente.

Cria, reutilizando exclusivamente os cadastros já existentes do Núcleo M15
(pessoas, espirometrias, parceiros/unidades, usuários, médicos, permissões,
documentos), o cenário pedido para o teste visual:

    Paciente : João da Silva Teste            (FICTÍCIO)
    Exame    : espirometria com fase pré e pós-broncodilatador
    Local    : Clínica Pastore — Unidade Ipanema
    Médica   : perfil profissional ativo e verificado, papel `medico`

NÃO cria sistema paralelo: pessoa entra em `people`, exame em
`spirometry_exams`, unidade em `partner_units`, laudo em `report_documents`
com a versão `original` gravada pelo mesmo `report_storage` de sempre.

Segurança
---------
- Fail-closed: só roda com `M15_ENV=dev`, banco local SQLite e a confirmação
  explícita `--confirmar`. Recusa qualquer URL PostgreSQL/remota.
- Todos os dados são fictícios e marcados. Nenhum dado real de paciente.
- Idempotente: reexecutar reaproveita o que já existe.

Uso:
    cd painel-soprolife/nucleo-m15
    .venv/bin/python scripts/seed_m25_3_laudo_demo.py --confirmar
"""

from __future__ import annotations

import argparse
import os
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import get_engine  # noqa: E402
from app.ids import allocate_public_code  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import (  # noqa: E402
    Partner,
    PartnerUnit,
    Person,
    SpirometryExam,
    User,
)
from app.security import (  # noqa: E402
    ROLE_ADMIN,
    ROLE_MEDICO,
    ROLE_OPERACIONAL,
    ensure_roles_exist,
    get_role,
    hash_password,
    issue_token,
)

# --------------------------------------------------------------- constantes

# Credenciais EXCLUSIVAMENTE locais e fictícias. Nunca reutilizar fora daqui.
ADMIN_EMAIL = "admin.teste@soprolife.local"
ADMIN_SENHA = "teste-admin-m25-3"
OPERACIONAL_EMAIL = "operacional.teste@soprolife.local"
OPERACIONAL_SENHA = "teste-operacional-m25-3"
MEDICA_EMAIL = "medica.teste@soprolife.local"
MEDICA_SENHA = "teste-medica-m25-3"

PACIENTE_NOME = "João da Silva Teste"
# Telefone sintético reservado para documentação/teste (faixa 5555 do padrão
# usado pelas fixtures do projeto). Não pertence a ninguém.
PACIENTE_TELEFONE = "+5521955550101"

PARTNER_NOME = "Clínica Pastore"
UNIDADE_NOME = "Unidade Ipanema"
UNIDADE_LOGRADOURO = "Rua Teixeira de Melo, 54"
UNIDADE_BAIRRO = "Ipanema"
UNIDADE_CIDADE = "Rio de Janeiro"
UNIDADE_UF = "RJ"
UNIDADE_TELEFONE = "(21) 2508-9001"


def _mir_pdf_ficticio() -> bytes:
    """PDF mínimo válido representando o documento técnico da MIR.

    É um substituto sintético: não contém traçado, curva nem dado clínico
    real. Serve apenas para exercitar o "documento 1" (que permanece intacto,
    sem assinatura e com download próprio).
    """
    texto = (
        "BT /F1 12 Tf 60 760 Td (DOCUMENTO TECNICO DO EQUIPAMENTO - AMOSTRA "
        "FICTICIA) Tj ET\n"
        "BT /F1 10 Tf 60 735 Td (Espirometria - pre e pos broncodilatador) Tj ET\n"
        "BT /F1 10 Tf 60 715 Td (Paciente: Joao da Silva Teste - DADOS FICTICIOS) Tj ET\n"
        "BT /F1 9 Tf 60 690 Td (Arquivo sintetico gerado para teste local M25.3.) Tj ET\n"
    ).encode("latin-1")
    objetos = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(texto)).encode() + b" >>\nstream\n" + texto + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, corpo in enumerate(objetos, start=1):
        offsets.append(len(out))
        out += str(i).encode() + b" 0 obj\n" + corpo + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 " + str(len(objetos) + 1).encode() + b"\n0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        b"trailer\n<< /Size " + str(len(objetos) + 1).encode() + b" /Root 1 0 R >>\n"
        b"startxref\n" + str(xref).encode() + b"\n%%EOF\n"
    )
    # zlib só entra aqui para manter a dependência explícita do módulo usada
    # na checagem de integridade abaixo.
    assert zlib.crc32(bytes(out)) != 0
    return bytes(out)


def _soltar(db) -> None:
    """Devolve a conexão ao pool antes de chamar a API.

    O seed mistura acesso ORM direto (campos sem tela de cadastro) com
    chamadas HTTP reais. No SQLite as duas rotas disputam o mesmo arquivo:
    uma transação ORM aberta faz o commit do endpoint falhar com
    "database is locked". Encerrar a transação aqui é o que mantém as duas
    rotas coexistindo — os objetos seguem utilizáveis porque a sessão usa
    `expire_on_commit=False`.
    """
    db.commit()


# ------------------------------------------------------------------ guardas

def _guarda_ambiente(settings) -> None:
    problemas = []
    if settings.env != "dev":
        problemas.append(f"M15_ENV={settings.env!r} (exigido 'dev')")
    url = str(settings.database_url)
    if not url.startswith("sqlite"):
        problemas.append("banco não-SQLite: este seed nunca toca PostgreSQL")
    if "://" in url and any(
        host in url for host in ("@", "postgres", "mysql")
    ) and not url.startswith("sqlite"):
        problemas.append("URL de banco remota detectada")
    if not settings.reports_enabled or settings.reports_mode != "pilot":
        problemas.append(
            "laudos desabilitados (defina M15_REPORTS_ENABLED=true e "
            "M15_REPORTS_MODE=pilot no .env local)"
        )
    if problemas:
        print("ABORTADO — ambiente inseguro para o seed fictício:", file=sys.stderr)
        for p in problemas:
            print(f"  - {p}", file=sys.stderr)
        raise SystemExit(2)


# ------------------------------------------------------------------ cadastros

def _usuario(db, *, email: str, nome: str, senha: str, papel: str) -> User:
    ensure_roles_exist(db)
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if user is None:
        user = User(email=email, nome=nome, password_hash=hash_password(senha))
        user.roles.append(get_role(db, papel))
        db.add(user)
        db.commit()
        print(f"  + usuário criado: {email} (papel={papel})")
    else:
        nomes = {r.name for r in user.roles}
        if papel not in nomes:
            user.roles.append(get_role(db, papel))
        # Garante que a senha fictícia documentada continua válida mesmo se o
        # registro já existia de uma execução anterior.
        user.password_hash = hash_password(senha)
        user.ativo = True
        db.commit()
        print(f"  = usuário reaproveitado: {email} (papel={papel})")
    return user


def _unidade_pastore(db) -> PartnerUnit:
    """Parceiro + unidade com endereço institucional estruturado.

    É dado da CLÍNICA, nunca do paciente. O template do PDF não fixa nenhum
    endereço: ele lê daqui (app/services/report_locations.py).
    """
    partner = db.execute(
        select(Partner).where(Partner.nome == PARTNER_NOME)
    ).scalar_one_or_none()
    if partner is None:
        partner = Partner(
            public_code=allocate_public_code(db, "partners"),
            nome=PARTNER_NOME,
        )
        db.add(partner)
        db.flush()
        print(f"  + parceiro criado: {PARTNER_NOME}")
    unit = db.execute(
        select(PartnerUnit).where(
            PartnerUnit.partner_id == partner.id, PartnerUnit.nome == UNIDADE_NOME
        )
    ).scalar_one_or_none()
    if unit is None:
        unit = PartnerUnit(
            public_code=allocate_public_code(db, "partner_units"),
            partner_id=partner.id,
            nome=UNIDADE_NOME,
        )
        db.add(unit)
        print(f"  + unidade criada: {UNIDADE_NOME}")
    unit.logradouro = UNIDADE_LOGRADOURO
    unit.bairro = UNIDADE_BAIRRO
    unit.cidade = UNIDADE_CIDADE
    unit.uf = UNIDADE_UF
    unit.telefone_central = UNIDADE_TELEFONE
    unit.ativo = True
    db.commit()
    print(f"  = endereço da unidade preenchido: {UNIDADE_LOGRADOURO} — {UNIDADE_BAIRRO}")
    return unit


def _paciente(client, headers, db) -> dict:
    existente = db.execute(
        select(Person).where(Person.nome_completo == PACIENTE_NOME)
    ).scalar_one_or_none()
    _soltar(db)
    if existente is None:
        resp = client.post(
            "/api/v1/pessoas",
            json={
                "nome_completo": PACIENTE_NOME,
                "data_nascimento": "1972-04-18",
                "contatos": [
                    {"tipo": "whatsapp", "valor": PACIENTE_TELEFONE, "principal": True}
                ],
                "consentimento_whatsapp": "concedido",
            },
            headers=headers,
        )
        if resp.status_code != 201:
            raise SystemExit(f"falha ao criar paciente fictício: {resp.text}")
        existente = db.get(Person, resp.json()["id"])
        print(f"  + paciente fictício criado: {PACIENTE_NOME}")
    else:
        print(f"  = paciente fictício reaproveitado: {PACIENTE_NOME}")
    # M25.2 criou a coluna; ainda não há tela de cadastro para ela.
    if not existente.sexo:
        existente.sexo = "masculino"
    if not existente.data_nascimento:
        from datetime import date

        existente.data_nascimento = date(1972, 4, 18)
    db.commit()
    return {"id": existente.id, "public_code": existente.public_code}


def _exame(client, headers, db, person, unit, *, forcar_novo=False) -> SpirometryExam:
    exame = None
    if not forcar_novo:
        exame = db.execute(
            select(SpirometryExam)
            .where(SpirometryExam.person_id == person["id"])
            .order_by(SpirometryExam.created_at.desc())
        ).scalars().first()
    _soltar(db)
    if exame is None:
        resp = client.post(
            "/api/v1/atendimentos",
            json={
                "person_id": person["id"],
                "tipo": "espirometria_soprolife",
                "espirometria": {
                    "data_exame": "2026-08-03",
                    "status": "Realizado",
                    # Fase pré E pós-broncodilatador — habilita os
                    # complementos RBD+/RBD−/REV no painel.
                    "broncodilatador": True,
                    "modalidade": "clinica_parceira",
                    "local_atendimento": UNIDADE_NOME,
                    # Sem partner_id/partner_unit_id de propósito: o domínio
                    # reserva esse vínculo ao tipo `espirometria_pastore`
                    # (rateio/fechamento). O local de realização do laudo vem
                    # de `report_documents.origin_partner_unit_id`, que é a
                    # prioridade nº 1 documentada em report_locations.py e é
                    # gravada no upload logo abaixo.
                },
            },
            headers=headers,
        )
        if resp.status_code != 201:
            raise SystemExit(f"falha ao criar exame fictício: {resp.text}")
        exame = db.get(SpirometryExam, resp.json()["espirometria"]["id"])
        print(f"  + espirometria criada: {exame.public_code} (com pós-BD)")
    else:
        print(f"  = espirometria reaproveitada: {exame.public_code}")
    # Campos M25.2 que ainda não têm tela de cadastro (ver relatório M25.3).
    # O vínculo com a unidade NÃO é forçado aqui: ele pertence ao documento.
    exame.modalidade = "clinica_parceira"
    exame.local_atendimento = UNIDADE_NOME
    exame.broncodilatador = True
    if not exame.hora_exame:
        exame.hora_exame = "09:20"
    if not exame.indicacao_clinica:
        exame.indicacao_clinica = "Tosse crônica e dispneia aos esforços."
    db.commit()
    return exame


def _perfil_medica(client, admin_headers, medica: User) -> dict:
    resp = client.patch(
        f"/api/v1/laudos/admin/medicos/{medica.id}",
        json={
            "grant_physician_role": True,
            # O tratamento faz parte do nome profissional cadastrado: o
            # template do PDF não fixa "Dr."/"Dra.", justamente para não
            # presumir o tratamento de nenhum profissional.
            "professional_name": "Dra. Ana Cristina do Nascimento Cunha",
            "crm_number": "52623075",
            # M25.12 — o CRM formatado é `52.62307-5`. O seed gravava
            # `5262307-5` (sem o ponto), então o laudo saía com um formato que
            # não é o do documento e a checagem 25 do roteiro E2E falhava.
            # `crm_display` é só apresentação: os dígitos continuam idênticos
            # a `crm_number`, que é o que `crm_display_matches` exige.
            "crm_display": "52.62307-5",
            "crm_state": "RJ",
            "rqe": "58224",
            "especialidade": "Médica Pneumologista",
            "verification_status": "verified",
            "verification_reference": "CADASTRO-LOCAL-M25-3",
            "active": True,
        },
        headers=admin_headers,
    )
    if resp.status_code != 200:
        raise SystemExit(f"falha ao configurar perfil da médica: {resp.text}")
    perfil = resp.json()["profile"]
    print(f"  = perfil médico ativo e verificado: {perfil['id']}")
    return perfil


def _documento(client, operacional_headers, db, exame, perfil, unit) -> dict:
    from app.models import ReportDocument

    existente = db.execute(
        select(ReportDocument).where(ReportDocument.spirometry_exam_id == exame.id)
    ).scalars().first()
    _soltar(db)
    if existente is not None:
        print(f"  = laudo já existente reaproveitado: {existente.public_code} "
              f"(status={existente.status})")
        return {"id": existente.id, "public_code": existente.public_code}
    _soltar(db)
    resp = client.post(
        "/api/v1/laudos",
        data={
            "exam_code": exame.public_code,
            "physician_profile_id": perfil["id"],
            "origin_type": "clinica_parceira",
            "origin_label": "pastore-ipanema",
            "origin_partner_unit_id": unit.id,
        },
        files={
            "file": (
                "exame-tecnico-mir-ficticio.pdf",
                _mir_pdf_ficticio(),
                "application/pdf",
            )
        },
        headers=operacional_headers,
    )
    if resp.status_code != 201:
        raise SystemExit(f"falha ao registrar o laudo: {resp.text}")
    doc = resp.json()
    print(f"  + laudo criado e atribuído: {doc['public_code']} (status={doc['status']})")
    return doc


# ---------------------------------------------------------------------- main

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--confirmar",
        action="store_true",
        help="confirma que este é um ambiente LOCAL de teste com dados fictícios",
    )
    parser.add_argument(
        "--novo-laudo",
        action="store_true",
        help="cria um exame e um laudo NOVOS para o mesmo paciente fictício "
             "(útil para repetir o fluxo depois de já ter liberado um laudo)",
    )
    args = parser.parse_args()
    if not args.confirmar:
        print(
            "Este script cria dados FICTÍCIOS de demonstração.\n"
            "Rode novamente com --confirmar se este é o ambiente local de teste.",
            file=sys.stderr,
        )
        return 2

    os.environ.setdefault("M15_AUTH_SECRET", "m25-3-seed-local-somente-dev-0123456789")
    get_settings.cache_clear()
    settings = get_settings()
    _guarda_ambiente(settings)

    engine = get_engine()
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    db = SessionLocal()
    app = create_app()

    print("\n== M25.3 — seed do cenário fictício de Laudo Online ==\n")
    try:
        with TestClient(app) as client:
            print("[1/6] usuários e permissões")
            admin = _usuario(
                db, email=ADMIN_EMAIL, nome="Admin Teste M25.3",
                senha=ADMIN_SENHA, papel=ROLE_ADMIN,
            )
            operacional = _usuario(
                db, email=OPERACIONAL_EMAIL, nome="Operacional Teste M25.3",
                senha=OPERACIONAL_SENHA, papel=ROLE_OPERACIONAL,
            )
            medica = _usuario(
                db, email=MEDICA_EMAIL, nome="Ana Cristina (conta de teste)",
                senha=MEDICA_SENHA, papel=ROLE_MEDICO,
            )
            admin_h = {"Authorization": f"Bearer {issue_token(admin.id, admin.password_hash)}"}
            oper_h = {
                "Authorization": f"Bearer {issue_token(operacional.id, operacional.password_hash)}"
            }

            print("[2/6] clínica parceira e unidade (local de realização)")
            unit = _unidade_pastore(db)

            print("[3/6] paciente fictício")
            person = _paciente(client, oper_h, db)

            print("[4/6] espirometria com fase pós-broncodilatador")
            exame = _exame(client, oper_h, db, person, unit,
                           forcar_novo=args.novo_laudo)

            print("[5/6] perfil profissional da médica")
            _soltar(db)
            perfil = _perfil_medica(client, admin_h, medica)

            print("[6/6] laudo pendente com o PDF técnico da MIR")
            doc = _documento(client, oper_h, db, exame, perfil, unit)
    finally:
        db.close()

    print(
        "\n== Cenário pronto ==\n"
        f"  Paciente ...... {PACIENTE_NOME} ({person['public_code']})\n"
        f"  Exame ......... {exame.public_code} — pré e pós-BD\n"
        f"  Local ......... {PARTNER_NOME} — {UNIDADE_NOME}\n"
        f"  Laudo ......... {doc['public_code']}\n"
        "\n== Credenciais FICTÍCIAS de teste local ==\n"
        f"  Médica ........ {MEDICA_EMAIL} / {MEDICA_SENHA}\n"
        f"  Admin ......... {ADMIN_EMAIL} / {ADMIN_SENHA}\n"
        f"  Operacional ... {OPERACIONAL_EMAIL} / {OPERACIONAL_SENHA}\n"
        "\nAbra http://127.0.0.1:8765/painel-soprolife/ e entre com a médica\n"
        "para ver o laudo na fila 'Laudos de espirometria'.\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
