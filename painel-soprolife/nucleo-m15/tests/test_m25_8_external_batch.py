"""M25.8 — laudar em lote, baixar para assinar fora e reimportar assinado.

Tudo fictício. A "assinatura externa" é feita por uma AC de teste gerada em
memória, que assina ANEXANDO ao PDF — como Adobe Reader e VIDaaS fazem —
porque simular reescrevendo daria um teste verde e um fluxo real quebrado.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from datetime import datetime, timezone

import pytest
from pypdf import PdfWriter

from app.services.external_signature import (
    VALIDATION_ALTERADO,
    VALIDATION_ASSINATURA_INVALIDA,
    VALIDATION_NAO_ENCONTRADO,
    VALIDATION_OK,
    VALIDATION_OUTRO_CERTIFICADO,
    VALIDATION_SEM_ASSINATURA,
    VALIDATION_VERSAO_DIVERGENTE,
    BatchEntry,
    ExternalSignatureError,
    SigningMarker,
    build_signing_package,
    extract_pdfs,
    read_signing_marker,
    safe_filename,
    stamp_signing_metadata,
    summarize,
    verify_signed_pdf,
)
from tests.pades_fakes import SigningAuthorityFake, sign_incrementally


def _pdf_base(texto: str = "laudo sintetico") -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    writer.add_metadata({"/Producer": texto})
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _preparado(codigo: str, versao: int = 1, texto: str | None = None) -> bytes:
    return stamp_signing_metadata(
        _pdf_base(texto or codigo),
        document_code=codigo,
        version_number=versao,
        physician_name="Dra. Ana Cristina do Nascimento Cunha",
        crm="CRM-RJ 5262307-5",
    )


def _entrada(codigo: str, pdf: bytes, versao: int = 1) -> BatchEntry:
    return BatchEntry(
        document_code=codigo,
        version_number=versao,
        patient_reference=f"PES-{codigo[-6:]}",
        prepared_sha256=hashlib.sha256(pdf).hexdigest(),
        exam_code=f"ESP-{codigo[-6:]}",
        physician_name="Dra. Ana Cristina do Nascimento Cunha",
        crm="CRM-RJ 5262307-5",
        rqe="58224",
        prepared_at=datetime.now(timezone.utc),
        pdf=pdf,
    )


@pytest.fixture()
def tres_laudos():
    codigos = ["LAU-000101", "LAU-000102", "LAU-000103"]
    return {c: _preparado(c) for c in codigos}


@pytest.fixture()
def autoridade():
    return SigningAuthorityFake("Dra Ana Cristina do Nascimento Cunha")


# ------------------------------------------------------------- carimbo


def test_carimbo_identifica_o_laudo_sem_depender_do_nome_do_arquivo():
    pdf = _preparado("LAU-000101", versao=2)
    marcador = read_signing_marker(pdf)
    assert marcador.document_code == "LAU-000101"
    assert marcador.version_number == 2
    assert len(marcador.content_sha256) == 64


def test_carimbo_guarda_o_hash_do_conteudo_e_nao_o_do_arquivo():
    """Um arquivo não pode conter o próprio hash — o carimbo é do conteúdo."""

    base = _pdf_base("LAU-000101")
    pdf = stamp_signing_metadata(
        base, document_code="LAU-000101", version_number=1,
        physician_name="Dra. Ana", crm="CRM-RJ 5262307-5",
    )
    marcador = read_signing_marker(pdf)
    assert marcador.content_sha256 == hashlib.sha256(base).hexdigest()
    assert marcador.content_sha256 != hashlib.sha256(pdf).hexdigest()


def test_pdf_sem_carimbo_nao_tem_marcador():
    assert read_signing_marker(_pdf_base()) is None


def test_nome_de_arquivo_nao_usa_nome_do_paciente():
    nome = safe_filename(
        document_code="LAU-000101", version_number=2,
        patient_reference="PES-000001",
    )
    assert nome == "LAU-000101_v2_PES-000001.pdf"
    # Acentos e espaços jamais chegam ao nome do arquivo.
    assert safe_filename(
        document_code="LAU-000101", version_number=1,
        patient_reference="João da Silva",
    ) == "LAU-000101_v1_Joao-da-Silva.pdf"


# ---------------------------------------------------------- download


def test_zip_traz_os_tres_laudos_e_os_dois_manifestos(tres_laudos):
    entradas = [_entrada(c, p) for c, p in tres_laudos.items()]
    pacote = build_signing_package(
        entradas, validation_base_url="https://exemplo.invalido/validar"
    )
    with zipfile.ZipFile(io.BytesIO(pacote)) as z:
        nomes = z.namelist()
        assinaveis = [n for n in nomes if n.startswith("assinar/")]
        assert len(assinaveis) == 3
        assert "manifesto.json" in nomes
        assert "manifesto.csv" in nomes
        assert "COMO-ASSINAR.txt" in nomes

        manifesto = json.loads(z.read("manifesto.json"))
        assert manifesto["total"] == 3
        assert {item["codigo_laudo"] for item in manifesto["laudos"]} == set(
            tres_laudos
        )
        for item in manifesto["laudos"]:
            assert len(item["hash_preparado_sha256"]) == 64


def test_manifesto_nao_expoe_cpf_nem_dado_clinico(tres_laudos):
    """Procura o VALOR proibido, não a palavra.

    A primeira versão deste teste procurava a string "CPF" e falhava contra
    a própria nota do manifesto ("Nenhum CPF ou dado clínico consta deste
    manifesto"). Procurar a palavra prova nada; o que importa é que não
    exista nenhum número com forma de CPF nem texto clínico.
    """

    import re

    entradas = [_entrada(c, p) for c, p in tres_laudos.items()]
    pacote = build_signing_package(entradas)
    with zipfile.ZipFile(io.BytesIO(pacote)) as z:
        texto = z.read("manifesto.json").decode() + z.read("manifesto.csv").decode()

    # Nenhuma sequência com forma de CPF (com ou sem pontuação).
    assert not re.search(r"\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b", texto)
    # Nenhum conteúdo clínico.
    for termo in ("conclus", "distúrbio", "broncodilatador", "espirometri"):
        assert termo.casefold() not in texto.casefold()
    # E o que DEVE estar continua lá.
    assert "hash_preparado_sha256" in texto
    assert "codigo_laudo" in texto


def test_mir_fica_fora_do_pacote_por_padrao(tres_laudos):
    codigo, pdf = next(iter(tres_laudos.items()))
    entrada = _entrada(codigo, pdf)
    entrada.mir_pdf = _pdf_base("tecnico mir")

    with zipfile.ZipFile(io.BytesIO(build_signing_package([entrada]))) as z:
        assert not [n for n in z.namelist() if "MIR" in n.upper()]

    with zipfile.ZipFile(
        io.BytesIO(build_signing_package([entrada], include_mir=True))
    ) as z:
        mir = [n for n in z.namelist() if "MIR" in n.upper()]
        assert len(mir) == 1
        # Pasta separada e com nome que diz o que não fazer.
        assert mir[0].startswith("exame-tecnico-mir-NAO-ASSINAR/")


def test_lote_vazio_e_recusado():
    with pytest.raises(ExternalSignatureError) as erro:
        build_signing_package([])
    assert erro.value.codigo == "lote_vazio"


def test_instrucoes_nao_prometem_assinatura_em_lote(tres_laudos):
    """Não afirmar o que não foi comprovado com a Valid."""

    entradas = [_entrada(c, p) for c, p in tres_laudos.items()]
    with zipfile.ZipFile(io.BytesIO(build_signing_package(entradas))) as z:
        texto = z.read("COMO-ASSINAR.txt").decode()
    assert "Nao foi possivel confirmar" in texto
    assert "arquivo por arquivo" in texto


# ------------------------------------------------------- reimportação


def test_envio_de_zip_e_de_pdfs_soltos_sao_equivalentes(tres_laudos):
    entradas = [_entrada(c, p) for c, p in tres_laudos.items()]
    pacote = build_signing_package(entradas)
    do_zip = extract_pdfs([("assinados.zip", pacote)])
    assert len(do_zip) == 3
    soltos = extract_pdfs([(f"{c}.pdf", p) for c, p in tres_laudos.items()])
    assert len(soltos) == 3


def test_tres_enviados_dois_validos_e_um_invalido(tres_laudos, autoridade):
    """O caso central: uma falha não pode derrubar o lote."""

    codigos = list(tres_laudos)
    veredictos = []

    for codigo in codigos[:2]:
        preparado = tres_laudos[codigo]
        assinado = sign_incrementally(preparado, autoridade)
        veredictos.append(verify_signed_pdf(
            filename=f"{codigo}.pdf",
            signed=assinado,
            prepared=preparado,
            expected_marker=read_signing_marker(preparado),
            expected_signer_subject=None,
        ))

    # O terceiro volta sem assinatura nenhuma.
    terceiro = codigos[2]
    veredictos.append(verify_signed_pdf(
        filename=f"{terceiro}.pdf",
        signed=tres_laudos[terceiro],
        prepared=tres_laudos[terceiro],
        expected_marker=read_signing_marker(tres_laudos[terceiro]),
        expected_signer_subject=None,
    ))

    assert [v.outcome for v in veredictos] == [
        VALIDATION_OK, VALIDATION_OK, VALIDATION_SEM_ASSINATURA
    ]
    assert summarize(veredictos) == {"total": 3, "validados": 2, "com_erro": 1}


def test_reenvio_do_arquivo_corrigido_passa(tres_laudos, autoridade):
    """Depois de corrigir, o mesmo laudo entra sem precisar refazer o lote."""

    codigo = next(iter(tres_laudos))
    preparado = tres_laudos[codigo]
    marcador = read_signing_marker(preparado)

    ruim = verify_signed_pdf(
        filename=f"{codigo}.pdf", signed=preparado, prepared=preparado,
        expected_marker=marcador, expected_signer_subject=None,
    )
    assert ruim.outcome == VALIDATION_SEM_ASSINATURA

    bom = verify_signed_pdf(
        filename=f"{codigo}.pdf",
        signed=sign_incrementally(preparado, autoridade),
        prepared=preparado,
        expected_marker=marcador, expected_signer_subject=None,
    )
    assert bom.ok


def test_pdf_de_outro_laudo_e_recusado(tres_laudos, autoridade):
    codigos = list(tres_laudos)
    assinado = sign_incrementally(tres_laudos[codigos[0]], autoridade)
    veredicto = verify_signed_pdf(
        filename="trocado.pdf",
        signed=assinado,
        prepared=tres_laudos[codigos[1]],
        expected_marker=read_signing_marker(tres_laudos[codigos[1]]),
        expected_signer_subject=None,
    )
    assert veredicto.outcome == VALIDATION_NAO_ENCONTRADO


def test_versao_antiga_e_recusada(autoridade):
    antigo = _preparado("LAU-000200", versao=1)
    assinado = sign_incrementally(antigo, autoridade)
    veredicto = verify_signed_pdf(
        filename="antigo.pdf", signed=assinado, prepared=antigo,
        expected_marker=SigningMarker(
            "LAU-000200", 2, read_signing_marker(antigo).content_sha256
        ),
        expected_signer_subject=None,
    )
    assert veredicto.outcome == VALIDATION_VERSAO_DIVERGENTE
    assert "versão anterior" in veredicto.message


def test_documento_reescrito_em_vez_de_assinado_e_recusado(autoridade):
    """Reimprimir/exportar quebra o prefixo — e precisa ser detectado."""

    preparado = _preparado("LAU-000201")
    assinado = sign_incrementally(preparado, autoridade)
    # Simula uma reescrita: um byte a mais no começo do arquivo.
    reescrito = b"%PDF-1.7\n" + assinado[9:]
    veredicto = verify_signed_pdf(
        filename="reescrito.pdf", signed=reescrito, prepared=preparado,
        expected_marker=read_signing_marker(preparado),
        expected_signer_subject=None,
    )
    assert veredicto.outcome in (VALIDATION_ALTERADO, VALIDATION_NAO_ENCONTRADO)


def test_assinatura_de_outro_certificado_e_recusada(autoridade):
    preparado = _preparado("LAU-000202")
    outra_pessoa = SigningAuthorityFake("Dr Outro Medico Sintetico")
    assinado = sign_incrementally(preparado, outra_pessoa)

    veredicto = verify_signed_pdf(
        filename="outro.pdf", signed=assinado, prepared=preparado,
        expected_marker=read_signing_marker(preparado),
        # Certificado já vinculado à Dra. Ana neste ambiente.
        expected_signer_subject=(
            "CN=Dra Ana Cristina do Nascimento Cunha,"
            "O=AC DE TESTE SOPROLIFE,C=BR"
        ),
    )
    assert veredicto.outcome == VALIDATION_OUTRO_CERTIFICADO
    assert "não é o mesmo" in veredicto.message


def test_certificado_vinculado_correto_e_aceito(autoridade):
    preparado = _preparado("LAU-000203")
    assinado = sign_incrementally(preparado, autoridade)
    esperado = autoridade.certificate.subject.rfc4514_string()
    veredicto = verify_signed_pdf(
        filename="ok.pdf", signed=assinado, prepared=preparado,
        expected_marker=read_signing_marker(preparado),
        expected_signer_subject=esperado,
    )
    assert veredicto.ok
    assert veredicto.signer_subject == esperado


def test_assinatura_corrompida_e_recusada(autoridade):
    preparado = _preparado("LAU-000204")
    assinado = bytearray(sign_incrementally(preparado, autoridade))
    # Corrompe um byte DENTRO do CMS, sem mexer no conteúdo coberto.
    posicao = assinado.rfind(b"/Contents <") + 40
    assinado[posicao] = 0x41 if assinado[posicao] != 0x41 else 0x42
    veredicto = verify_signed_pdf(
        filename="corrompido.pdf", signed=bytes(assinado), prepared=preparado,
        expected_marker=read_signing_marker(preparado),
        expected_signer_subject=None,
    )
    assert veredicto.outcome in (
        VALIDATION_ASSINATURA_INVALIDA, VALIDATION_SEM_ASSINATURA
    )


def test_arquivo_sem_identificacao_soprolife_e_recusado(autoridade):
    estranho = sign_incrementally(_pdf_base("de outro sistema"), autoridade)
    veredicto = verify_signed_pdf(
        filename="estranho.pdf", signed=estranho, prepared=_pdf_base(),
        expected_marker=SigningMarker("LAU-000205", 1, "0" * 64),
        expected_signer_subject=None,
    )
    assert veredicto.outcome == VALIDATION_NAO_ENCONTRADO


def test_contadores_do_lote():
    from app.services.external_signature import FileVerdict

    veredictos = [
        FileVerdict(filename="a", outcome=VALIDATION_OK),
        FileVerdict(filename="b", outcome=VALIDATION_OK),
        FileVerdict(filename="c", outcome=VALIDATION_SEM_ASSINATURA),
        FileVerdict(filename="d", outcome=VALIDATION_VERSAO_DIVERGENTE),
    ]
    assert summarize(veredictos) == {"total": 4, "validados": 2, "com_erro": 2}


# ------------------------------------------------ fluxo completo pela API


from tests.test_m25_2_native_report import (  # noqa: E402
    RELEASE_CONFIRMATION,
    _make_case,
    _preview,
    reports_enabled,  # noqa: F401  (fixture autouse)
)


def _finalizar(client, caso, previa):
    return client.post(
        f"/api/v1/laudos/{caso['document']['id']}/finalizar-revisao",
        json={
            "confirmacao": RELEASE_CONFIRMATION,
            "expected_version_id": previa["preview_version_id"],
            "expected_text_sha256": previa["final_text_sha256"],
        },
        headers=caso["doctor_auth"],
    )


@pytest.fixture()
def tres_casos(client, auth, db, person):
    """Três laudos da MESMA médica, revisados e prontos para assinatura.

    O lote é por médica: três laudos de três médicas diferentes não formam
    um lote, formam três lotes de um. Reaproveitar o mesmo perfil aqui é o
    que faz o teste exercitar o caso real.
    """

    from tests.test_m25_2_native_report import (
        _configure_profile, _create_exam, _physician, _upload,
    )

    doctor, doctor_auth = _physician(db, suffix="850")
    profile = _configure_profile(client, auth, doctor, crm="00850")

    casos = []
    for _ in range(3):
        exam = _create_exam(client, auth, person, com_bd=True)
        enviado = _upload(client, auth, exam, profile)
        assert enviado.status_code == 201, enviado.text
        caso = {
            "doctor": doctor,
            "doctor_auth": doctor_auth,
            "profile": profile,
            "exam": exam,
            "document": enviado.json(),
        }
        previa = _preview(client, caso).json()
        resposta = _finalizar(client, caso, previa)
        assert resposta.status_code == 201, resposta.text
        caso["finalizacao"] = resposta.json()
        casos.append(caso)
    return casos


def test_finalizar_revisao_congela_sem_liberar(client, auth, db, person):
    caso = _make_case(client, auth, db, person, com_bd=True, suffix="810")
    previa = _preview(client, caso).json()
    resposta = _finalizar(client, caso, previa)
    assert resposta.status_code == 201, resposta.text
    corpo = resposta.json()

    # Aguardando assinatura NÃO é liberado.
    assert corpo["status"] == "assinatura_pendente"
    assert len(corpo["hash_preparado_sha256"]) == 64

    detalhe = client.get(
        f"/api/v1/laudos/{caso['document']['id']}", headers=caso["doctor_auth"]
    ).json()
    assert detalhe["status"] == "assinatura_pendente"
    assert detalhe.get("released_at") in (None, "")


def test_finalizar_duas_vezes_e_recusado(client, auth, db, person):
    caso = _make_case(client, auth, db, person, com_bd=True, suffix="811")
    previa = _preview(client, caso).json()
    assert _finalizar(client, caso, previa).status_code == 201
    segunda = _finalizar(client, caso, previa)
    assert segunda.status_code == 409
    assert segunda.json()["erro"]["codigo"] == "laudo_ja_aguardando_assinatura"


def test_download_em_lote_traz_os_tres_e_o_manifesto(client, tres_casos):
    resposta = client.post(
        "/api/v1/laudos/lote/baixar",
        json={"document_ids": [], "incluir_mir": False},
        headers=tres_casos[0]["doctor_auth"],
    )
    assert resposta.status_code == 200, resposta.text
    assert resposta.headers["content-type"] == "application/zip"
    assert "attachment;" in resposta.headers["content-disposition"]

    with zipfile.ZipFile(io.BytesIO(resposta.content)) as z:
        assinaveis = [n for n in z.namelist() if n.startswith("assinar/")]
        assert len(assinaveis) == 3
        manifesto = json.loads(z.read("manifesto.json"))
        assert manifesto["total"] == 3
        # O PDF técnico da MIR não entra sem ser pedido.
        assert not [n for n in z.namelist() if "MIR" in n.upper()]


def test_exame_nao_revisado_nunca_entra_no_lote(
    client, auth, db, person, tres_casos
):
    """Um exame ainda em elaboração não pode ser baixado para assinatura."""

    cru = _make_case(client, auth, db, person, com_bd=True, suffix="812")
    _preview(client, cru)  # gera prévia, mas NÃO finaliza a revisão

    resposta = client.post(
        "/api/v1/laudos/lote/baixar",
        json={"document_ids": [cru["document"]["id"]]},
        headers=cru["doctor_auth"],
    )
    # A médica do caso cru não tem nenhum laudo finalizado.
    assert resposta.status_code == 409
    assert resposta.json()["erro"]["codigo"] == "lote_vazio"


def test_lote_de_uma_medica_nao_alcanca_laudo_de_outra(
    client, auth, db, person, tres_casos
):
    """Mandar o id de um laudo alheio na lista não amplia a seleção."""

    outra = _make_case(client, auth, db, person, com_bd=True, suffix="860")
    previa = _preview(client, outra).json()
    assert _finalizar(client, outra, previa).status_code == 201

    # A médica dos três casos pede explicitamente o laudo da outra.
    resposta = client.post(
        "/api/v1/laudos/lote/baixar",
        json={"document_ids": [outra["document"]["id"]]},
        headers=tres_casos[0]["doctor_auth"],
    )
    assert resposta.status_code == 409
    assert resposta.json()["erro"]["codigo"] == "lote_vazio"


def test_envio_de_tres_dois_validos_e_um_invalido(client, db, tres_casos):
    """O caso central, agora pela API: só os válidos são liberados."""

    autoridade = SigningAuthorityFake("Dra Ana Cristina do Nascimento Cunha")
    baixado = client.post(
        "/api/v1/laudos/lote/baixar",
        json={"document_ids": []},
        headers=tres_casos[0]["doctor_auth"],
    )
    assert baixado.status_code == 200, baixado.text

    preparados: dict[str, bytes] = {}
    with zipfile.ZipFile(io.BytesIO(baixado.content)) as z:
        for nome in z.namelist():
            if nome.startswith("assinar/"):
                preparados[nome.split("/", 1)[1]] = z.read(nome)
    assert len(preparados) == 3

    nomes = sorted(preparados)
    envio = []
    for indice, nome in enumerate(nomes):
        conteudo = preparados[nome]
        if indice < 2:
            conteudo = sign_incrementally(conteudo, autoridade)
        # O terceiro sobe sem assinatura nenhuma.
        envio.append(("arquivos", (nome, conteudo, "application/pdf")))

    resposta = client.post(
        "/api/v1/laudos/lote/enviar",
        files=envio,
        headers=tres_casos[0]["doctor_auth"],
    )
    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert corpo["resumo"] == {"total": 3, "validados": 2, "com_erro": 1}

    resultados = {item["arquivo"]: item for item in corpo["arquivos"]}
    assert sum(1 for r in resultados.values() if r["ok"]) == 2
    reprovado = [r for r in resultados.values() if not r["ok"]][0]
    assert reprovado["resultado"] == VALIDATION_SEM_ASSINATURA

    # Só os dois validados mudaram de estado.
    liberados = 0
    for caso in tres_casos:
        detalhe = client.get(
            f"/api/v1/laudos/{caso['document']['id']}",
            headers=caso["doctor_auth"],
        ).json()
        # Assinatura qualificada termina em 'assinado' — 'liberado' é a
        # liberação institucional, sem certificado.
        if detalhe["status"] == "assinado":
            liberados += 1
        else:
            # O que falhou continua esperando, sem perder a versão anterior.
            assert detalhe["status"] == "assinatura_pendente"
    assert liberados == 2


def test_reenvio_do_mesmo_laudo_e_marcado_como_duplicado(client, tres_casos):
    autoridade = SigningAuthorityFake()
    baixado = client.post(
        "/api/v1/laudos/lote/baixar",
        json={"document_ids": [tres_casos[0]["document"]["id"]]},
        headers=tres_casos[0]["doctor_auth"],
    )
    with zipfile.ZipFile(io.BytesIO(baixado.content)) as z:
        nome = [n for n in z.namelist() if n.startswith("assinar/")][0]
        assinado = sign_incrementally(z.read(nome), autoridade)
    curto = nome.split("/", 1)[1]

    primeiro = client.post(
        "/api/v1/laudos/lote/enviar",
        files=[("arquivos", (curto, assinado, "application/pdf"))],
        headers=tres_casos[0]["doctor_auth"],
    )
    assert primeiro.json()["resumo"]["validados"] == 1

    segundo = client.post(
        "/api/v1/laudos/lote/enviar",
        files=[("arquivos", (curto, assinado, "application/pdf"))],
        headers=tres_casos[0]["doctor_auth"],
    )
    assert segundo.json()["resumo"]["validados"] == 0
    assert segundo.json()["arquivos"][0]["resultado"] == "arquivo_duplicado"


def test_envio_aceita_zip_com_os_assinados(client, tres_casos):
    autoridade = SigningAuthorityFake()
    baixado = client.post(
        "/api/v1/laudos/lote/baixar",
        json={"document_ids": []},
        headers=tres_casos[0]["doctor_auth"],
    )
    saida = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(baixado.content)) as origem:
        with zipfile.ZipFile(saida, "w") as destino:
            for nome in origem.namelist():
                if not nome.startswith("assinar/"):
                    continue
                destino.writestr(
                    nome.split("/", 1)[1],
                    sign_incrementally(origem.read(nome), autoridade),
                )

    resposta = client.post(
        "/api/v1/laudos/lote/enviar",
        files=[("arquivos", ("assinados.zip", saida.getvalue(), "application/zip"))],
        headers=tres_casos[0]["doctor_auth"],
    )
    assert resposta.status_code == 200, resposta.text
    assert resposta.json()["resumo"]["validados"] == 3


def test_operacional_nao_baixa_lote(client, auth, tres_casos):
    """Download em lote é exclusivo de quem tem papel médico."""

    resposta = client.post(
        "/api/v1/laudos/lote/baixar",
        json={"document_ids": []},
        headers=auth("operacional"),
    )
    assert resposta.status_code in (401, 403)


def test_auditoria_registra_o_lote_sem_dado_clinico(client, db, tres_casos):
    from app.models import AuditLog
    from sqlalchemy import select as _select

    client.post(
        "/api/v1/laudos/lote/baixar",
        json={"document_ids": []},
        headers=tres_casos[0]["doctor_auth"],
    )
    registros = db.execute(
        _select(AuditLog).where(
            AuditLog.acao == "laudos_baixados_para_assinatura"
        )
    ).scalars().all()
    assert registros
    texto = json.dumps(
        [r.detalhes for r in registros], ensure_ascii=False, default=str
    )
    for proibido in ("conclus", "distúrbio", "nome_completo"):
        assert proibido.casefold() not in texto.casefold()
