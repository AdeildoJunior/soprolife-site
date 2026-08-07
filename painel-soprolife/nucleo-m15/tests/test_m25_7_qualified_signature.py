"""M25.7 — assinatura qualificada ICP-Brasil via VIDaaS/IntegraICP.

NENHUMA chamada real é feita: a autoridade certificadora é um servidor falso
em memória (`httpx.MockTransport`) e a cadeia de certificados é gerada na
hora pelos testes. Nada aqui é credencial da Valid, e um teste verde NÃO
significa que o fluxo real foi exercitado — significa que a nossa metade do
protocolo está correta.

Pacientes, médicas, CRMs e exames são sintéticos.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json

import httpx
import pytest
from pypdf import PdfWriter

from app.config import get_settings
from app.services import qualified_signature as qs
from app.services.integraicp_client import (
    IntegraICPClient,
    IntegraICPError,
    digest_to_base64,
    generate_pkce,
)
from app.services.report_pades import (
    PADES_LEVEL_ACHIEVED,
    PadesError,
    inject_cms,
    prepare_pades,
    rebuild_prepared,
    validate_pades,
)
from app.services.transient_secrets import (
    TransientSecretError,
    open_transient_secret,
    seal_transient_secret,
)
from tests.pades_fakes import SigningAuthorityFake

SEGREDO = "m25-7-teste-sintetico-0123456789-abcdefghij"


# ----------------------------------------------------------------- apoio


def _pdf(paginas: int = 1) -> bytes:
    writer = PdfWriter()
    for _ in range(paginas):
        writer.add_blank_page(width=595, height=842)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _preparado(pdf: bytes | None = None):
    return prepare_pades(
        pdf or _pdf(), reason="Laudo de espirometria", location="Rio de Janeiro"
    )


class FakeIntegraICP:
    """Autoridade falsa. Cada instância decide como vai se comportar."""

    def __init__(
        self,
        autoridade: SigningAuthorityFake,
        *,
        status_credencial: int = 200,
        status_assinatura: int = 200,
        corpo_malformado: bool = False,
        estourar_tempo: bool = False,
        assinar_digest_alheio: bool = False,
        **defeitos_cms,
    ):
        self.autoridade = autoridade
        self.status_credencial = status_credencial
        self.status_assinatura = status_assinatura
        self.corpo_malformado = corpo_malformado
        self.estourar_tempo = estourar_tempo
        self.assinar_digest_alheio = assinar_digest_alheio
        self.defeitos_cms = defeitos_cms
        self.digests_recebidos: list[str] = []
        self.verifiers_recebidos: list[str] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        if self.estourar_tempo:
            raise httpx.TimeoutException("tempo esgotado", request=request)
        if request.url.path.startswith("/credentials/"):
            if self.status_credencial != 200:
                return httpx.Response(self.status_credencial, json={})
            self.verifiers_recebidos.append(
                request.url.params.get("code_verifier", "")
            )
            return httpx.Response(200, json={
                "status": "ATIVA",
                "subjectName": "TESTE APAGAR Assinante Sintetico",
                "certificate": base64.b64encode(
                    self.autoridade.certificate_der
                ).decode(),
            })
        if request.url.path == "/signatures":
            if self.status_assinatura != 200:
                return httpx.Response(self.status_assinatura, json={})
            if self.corpo_malformado:
                return httpx.Response(200, content=b"nao e json")
            corpo = json.loads(request.content.decode())
            recebido = corpo["hashes"][0]
            self.digests_recebidos.append(recebido)
            digest_hex = base64.b64decode(recebido).hex()
            if self.assinar_digest_alheio:
                digest_hex = hashlib.sha256(b"outro conteudo").hexdigest()
            cms = self.autoridade.build_cms(digest_hex, **self.defeitos_cms)
            return httpx.Response(200, json={
                "transactionId": "TESTE-TX-0001",
                "signatures": [base64.b64encode(cms).decode()],
            })
        return httpx.Response(404, json={})


@pytest.fixture()
def autoridade():
    return SigningAuthorityFake()


@pytest.fixture()
def cliente_factory():
    def build(fake: FakeIntegraICP) -> IntegraICPClient:
        return IntegraICPClient(
            base_url="https://ac-de-teste.invalido",
            channel_id="CANAL-TESTE",
            callback_url="https://painel-teste.invalido/retorno",
            signature_policy="POLITICA-TESTE",
            timeout_seconds=5.0,
            transport=fake.transport(),
        )
    return build


# ------------------------------------------------------------ PKCE / digest


def test_pkce_segue_a_rfc_7636():
    par = generate_pkce()
    assert par.method == "S256"
    assert 43 <= len(par.verifier) <= 128
    esperado = base64.urlsafe_b64encode(
        hashlib.sha256(par.verifier.encode()).digest()
    ).decode().rstrip("=")
    assert par.challenge == esperado
    # Sem preenchimento e sem caracteres fora do alfabeto unreserved.
    assert "=" not in par.challenge and "+" not in par.challenge
    assert "/" not in par.challenge


def test_pkce_nunca_repete():
    valores = {generate_pkce().verifier for _ in range(20)}
    assert len(valores) == 20


def test_digest_vai_em_base64_padrao_e_nao_url_safe():
    """A API espera Base64 padrão. Mandar a variante URL-safe assina bytes
    diferentes e só quebra lá na frente, na validação do PDF."""

    bruto = bytes(range(256))[:32]
    # Este digest contém bytes que codificam para "+" e "/" em Base64 padrão.
    digest_hex = hashlib.sha256(b"soprolife-m25-7").hexdigest()
    codificado = digest_to_base64(digest_hex)
    assert base64.b64decode(codificado).hex() == digest_hex
    assert codificado == base64.b64encode(bytes.fromhex(digest_hex)).decode()


def test_digest_invalido_e_recusado():
    for ruim in ("nao-hex", "ab", ""):
        with pytest.raises(IntegraICPError) as erro:
            digest_to_base64(ruim)
        assert erro.value.codigo == "digest_invalido"


# ------------------------------------------------------------------ PAdES


def test_preparacao_produz_byte_range_que_cobre_tudo_menos_a_assinatura():
    prep = _preparado()
    inicio1, tam1, inicio2, tam2 = prep.byte_range
    assert inicio1 == 0
    assert inicio2 + tam2 == len(prep.data)
    # A lacuna entre os dois trechos é exatamente o campo de assinatura.
    lacuna = prep.data[tam1:inicio2]
    assert lacuna.startswith(b"<") and lacuna.endswith(b">")


def test_os_dois_hashes_sao_diferentes_por_construcao(autoridade):
    prep = _preparado()
    final = inject_cms(prep, autoridade.build_cms(prep.signed_digest_sha256))
    validacao = validate_pades(
        final, expected_signed_digest_sha256=prep.signed_digest_sha256
    )
    assert prep.prepared_sha256 != validacao.final_sha256
    # O digest assinável NÃO muda com a injeção — é esse o ponto.
    assert validacao.signed_digest_sha256 == prep.signed_digest_sha256


def test_injecao_preserva_o_tamanho_do_arquivo(autoridade):
    prep = _preparado()
    final = inject_cms(prep, autoridade.build_cms(prep.signed_digest_sha256))
    assert len(final) == len(prep.data)


def test_pdf_assinado_e_verificavel(autoridade):
    prep = _preparado()
    final = inject_cms(prep, autoridade.build_cms(prep.signed_digest_sha256))
    validacao = validate_pades(final)
    assert validacao.level == PADES_LEVEL_ACHIEVED
    assert "TESTE APAGAR" in validacao.signer_subject
    assert validacao.signer_serial


def test_reconstruir_preparado_recupera_tudo_dos_bytes():
    prep = _preparado()
    refeito = rebuild_prepared(prep.data)
    assert refeito.signed_digest_sha256 == prep.signed_digest_sha256
    assert refeito.byte_range == prep.byte_range
    assert refeito.placeholder_bytes == prep.placeholder_bytes


def test_cms_maior_que_o_espaco_e_recusado(autoridade):
    prep = prepare_pades(
        _pdf(), reason="r", location="l", placeholder_bytes=2048
    )
    with pytest.raises(PadesError) as erro:
        inject_cms(prep, b"\x30" * 4096)
    assert erro.value.codigo == "cms_maior_que_o_espaco"


def test_digest_divergente_e_recusado_na_validacao(autoridade):
    prep = _preparado()
    final = inject_cms(prep, autoridade.build_cms(prep.signed_digest_sha256))
    with pytest.raises(PadesError) as erro:
        validate_pades(final, expected_signed_digest_sha256="0" * 64)
    assert erro.value.codigo == "digest_divergente"


def test_cms_que_assina_outro_conteudo_e_recusado(autoridade):
    prep = _preparado()
    outro = hashlib.sha256(b"conteudo diferente").hexdigest()
    final = inject_cms(prep, autoridade.build_cms(outro))
    with pytest.raises(PadesError) as erro:
        validate_pades(final)
    assert erro.value.codigo == "digest_assinado_divergente"


def test_assinatura_corrompida_e_recusada(autoridade):
    prep = _preparado()
    final = inject_cms(
        prep,
        autoridade.build_cms(prep.signed_digest_sha256, corrupt_signature=True),
    )
    with pytest.raises(PadesError) as erro:
        validate_pades(final)
    assert erro.value.codigo == "assinatura_nao_confere"


def test_cms_sem_certificado_e_recusado(autoridade):
    prep = _preparado()
    final = inject_cms(
        prep,
        autoridade.build_cms(prep.signed_digest_sha256, omit_certificate=True),
    )
    with pytest.raises(PadesError) as erro:
        validate_pades(final)
    assert erro.value.codigo == "cms_sem_certificado"


def test_cms_sem_message_digest_e_recusado(autoridade):
    prep = _preparado()
    final = inject_cms(
        prep,
        autoridade.build_cms(
            prep.signed_digest_sha256, omit_message_digest=True
        ),
    )
    with pytest.raises(PadesError) as erro:
        validate_pades(final)
    assert erro.value.codigo == "cms_sem_message_digest"


def test_cms_com_dois_signatarios_e_recusado(autoridade):
    prep = _preparado()
    final = inject_cms(
        prep, autoridade.build_cms(prep.signed_digest_sha256, two_signers=True)
    )
    with pytest.raises(PadesError) as erro:
        validate_pades(final)
    assert erro.value.codigo == "cms_signatarios_inesperados"


def test_cms_lixo_e_recusado():
    prep = _preparado()
    final = inject_cms(prep, b"\x30\x03\x02\x01\x00")
    with pytest.raises(PadesError) as erro:
        validate_pades(final)
    assert erro.value.codigo in ("cms_ilegivel", "cms_nao_e_signed_data")


def test_pdf_alterado_depois_de_assinado_e_detectado(autoridade):
    """Alterar um byte coberto pelo ByteRange precisa invalidar tudo."""

    prep = _preparado()
    final = bytearray(
        inject_cms(prep, autoridade.build_cms(prep.signed_digest_sha256))
    )
    final[10] = final[10] ^ 0xFF
    with pytest.raises(PadesError) as erro:
        validate_pades(bytes(final))
    assert erro.value.codigo in ("digest_assinado_divergente", "pdf_ilegivel")


def test_pdf_que_nao_e_pdf_e_recusado():
    with pytest.raises(PadesError) as erro:
        prepare_pades(b"nao sou um pdf", reason="r", location="l")
    assert erro.value.codigo == "pdf_invalido"


# -------------------------------------------------------- cliente HTTP


def test_cliente_envia_somente_o_digest(autoridade, cliente_factory):
    """Nenhum byte do PDF e nenhum dado de paciente pode sair daqui."""

    fake = FakeIntegraICP(autoridade)
    cliente = cliente_factory(fake)
    prep = _preparado()
    resultado = cliente.request_signature(
        credential_id="CRED-1",
        digest_hex=prep.signed_digest_sha256,
        pkce_verifier="verificador-de-teste",
    )
    assert resultado.cms_der
    assert len(fake.digests_recebidos) == 1
    # O que trafegou é exatamente 32 bytes de digest — nada mais.
    assert len(base64.b64decode(fake.digests_recebidos[0])) == 32


def test_url_de_autorizacao_carrega_pkce_e_state(cliente_factory, autoridade):
    cliente = cliente_factory(FakeIntegraICP(autoridade))
    par = generate_pkce()
    url = cliente.build_authentication_url(
        pkce=par, state="estado-1234567890", nonce="nonce-123"
    )
    assert "code_challenge=" + par.challenge in url
    assert "code_challenge_method=S256" in url
    assert "state=estado-1234567890" in url
    # O verifier NUNCA pode aparecer na URL para onde a médica é enviada.
    assert par.verifier not in url


def test_timeout_e_recuperavel(autoridade, cliente_factory):
    cliente = cliente_factory(FakeIntegraICP(autoridade, estourar_tempo=True))
    with pytest.raises(IntegraICPError) as erro:
        cliente.request_signature(
            credential_id="C", digest_hex="ab" * 32, pkce_verifier="v"
        )
    assert erro.value.codigo == "tempo_esgotado"
    assert erro.value.recuperavel is True


def test_recusa_da_medica_nao_e_recuperavel(autoridade, cliente_factory):
    cliente = cliente_factory(
        FakeIntegraICP(autoridade, status_assinatura=403)
    )
    with pytest.raises(IntegraICPError) as erro:
        cliente.request_signature(
            credential_id="C", digest_hex="ab" * 32, pkce_verifier="v"
        )
    assert erro.value.codigo == "autorizacao_recusada"
    assert erro.value.recuperavel is False


def test_indisponibilidade_e_recuperavel(autoridade, cliente_factory):
    cliente = cliente_factory(
        FakeIntegraICP(autoridade, status_assinatura=503)
    )
    with pytest.raises(IntegraICPError) as erro:
        cliente.request_signature(
            credential_id="C", digest_hex="ab" * 32, pkce_verifier="v"
        )
    assert erro.value.codigo == "autoridade_indisponivel"
    assert erro.value.recuperavel is True


def test_resposta_malformada_e_recusada(autoridade, cliente_factory):
    cliente = cliente_factory(
        FakeIntegraICP(autoridade, corpo_malformado=True)
    )
    with pytest.raises(IntegraICPError) as erro:
        cliente.request_signature(
            credential_id="C", digest_hex="ab" * 32, pkce_verifier="v"
        )
    assert erro.value.codigo == "resposta_malformada"


def test_erro_do_cliente_nunca_vaza_a_url_nem_o_verifier(
    autoridade, cliente_factory
):
    """A URL carrega canal e verifier: a mensagem de erro não pode repeti-la."""

    cliente = cliente_factory(FakeIntegraICP(autoridade, estourar_tempo=True))
    with pytest.raises(IntegraICPError) as erro:
        cliente.fetch_credential(
            credential_id="CRED", pkce_verifier="SEGREDO-DO-VERIFIER"
        )
    texto = f"{erro.value.codigo} {erro.value.mensagem}"
    assert "SEGREDO-DO-VERIFIER" not in texto
    assert "CANAL-TESTE" not in texto
    assert "ac-de-teste.invalido" not in texto


def test_cliente_sem_configuracao_nao_e_construido():
    with pytest.raises(IntegraICPError) as erro:
        IntegraICPClient(base_url="", channel_id="", callback_url="")
    assert erro.value.codigo == "integracao_incompleta"


# ------------------------------------------------- segredo transitório


def test_verificador_persistido_fica_cifrado():
    selado = seal_transient_secret("verificador-secreto", auth_secret=SEGREDO)
    assert "verificador-secreto" not in selado
    assert open_transient_secret(selado, auth_secret=SEGREDO) == (
        "verificador-secreto"
    )


def test_verificador_nao_abre_com_outra_chave():
    selado = seal_transient_secret("v", auth_secret=SEGREDO)
    with pytest.raises(TransientSecretError):
        open_transient_secret(selado, auth_secret="outro-segredo-qualquer-x")


def test_verificador_ausente_falha_fechado():
    with pytest.raises(TransientSecretError):
        open_transient_secret(None, auth_secret=SEGREDO)


# ------------------------------------------------------ configuração


def test_integracao_desligada_por_padrao(monkeypatch):
    """Sem configuração explícita nada é chamado — e a mensagem é a exigida."""

    get_settings.cache_clear()
    from app.config import Settings

    settings = Settings(_env_file=None)
    assert settings.integraicp_enabled is False
    assert settings.report_signature_provider == "unconfigured"
    assert settings.integraicp_ready() is False
    assert qs.diagnostics(settings)["mensagem"] == (
        "Integração aguardando credencial da Valid."
    )


def test_configuracao_incompleta_nao_fica_pronta():
    from app.config import Settings

    settings = Settings(
        _env_file=None,
        integraicp_enabled=True,
        report_signature_provider="integraicp",
        integraicp_base_url="https://ac.invalido",
        # canal e callback ausentes de propósito
    )
    assert settings.integraicp_ready() is False


def test_base_url_http_e_recusada():
    from app.config import Settings

    with pytest.raises(Exception):
        Settings(_env_file=None, integraicp_base_url="http://ac.invalido")


def test_provedor_permanece_nulo_sem_configuracao():
    from app.services.signature_provider import (
        UnconfiguredSignatureProvider,
        get_signature_provider,
    )

    get_settings.cache_clear()
    provedor = get_signature_provider()
    assert isinstance(provedor, UnconfiguredSignatureProvider)
    assert provedor.name == "unconfigured"


def test_provedor_integraicp_nunca_assina_sem_interacao():
    """Mesmo selecionado, o adapter recusa uso não interativo."""

    from app.services.signature_provider import IntegraICPSignatureProvider

    resultado = IntegraICPSignatureProvider().request_signature(
        document_bytes=b"%PDF-1.7", document_sha256="ab" * 32,
        requested_by_user_id="u1",
    )
    assert resultado.status == "assinatura_pendente"
    assert "consciente" in (resultado.error_message or "")


def test_diagnostico_nunca_expoe_valores():
    from app.config import Settings

    settings = Settings(
        _env_file=None,
        integraicp_enabled=True,
        report_signature_provider="integraicp",
        integraicp_base_url="https://ac-secreta.invalido",
        integraicp_channel_id="CANAL-SUPER-SECRETO",
        integraicp_callback_url="https://painel.invalido/retorno",
        integraicp_signature_policy="POLITICA-X",
    )
    diag = qs.diagnostics(settings)
    texto = json.dumps(diag, ensure_ascii=False)
    assert "CANAL-SUPER-SECRETO" not in texto
    assert "ac-secreta.invalido" not in texto
    assert "POLITICA-X" not in texto
    # Mas informa que estão configurados.
    assert diag["canal_configurado"] is True
    assert diag["callback_configurado"] is True
    assert diag["integracao_pronta"] is True


def test_cms_terminado_em_zero_nao_e_truncado():
    """Regressão: o preenchimento era removido com `rstrip(b"\\x00")`.

    Quando a assinatura RSA termina em 0x00 — o que acontece em cerca de uma
    a cada 256 assinaturas —, o `rstrip` comia um byte REAL e o CMS deixava
    de ser decodificável. A falha era aleatória: passava dezenas de vezes e
    quebrava sem aviso. O corte agora lê o comprimento declarado no próprio
    cabeçalho ASN.1.
    """

    from app.services.report_pades import _trim_der

    # Comprimento curto (um byte), terminando em 0x00.
    curto = bytes([0x30, 0x06, 0x02, 0x01, 0x05, 0x02, 0x01, 0x00])
    assert curto.endswith(b"\x00")
    assert _trim_der(curto + b"\x00" * 5000) == curto
    # E a prova de que a abordagem anterior perdia o byte:
    assert (curto + b"\x00" * 5000).rstrip(b"\x00") != curto

    # Comprimento longo (dois bytes), também terminando em 0x00.
    corpo = bytes(range(256))[:-1] + b"\x00"
    longo = bytes([0x30, 0x82]) + len(corpo).to_bytes(2, "big") + corpo
    assert _trim_der(longo + b"\x00" * 3000) == longo


def test_cms_que_declara_mais_bytes_do_que_existem_e_recusado():
    from app.services.report_pades import PadesError, _trim_der

    with pytest.raises(PadesError) as erro:
        _trim_der(bytes([0x30, 0x82, 0xFF, 0xFF]) + b"\x00" * 10)
    assert erro.value.codigo == "cms_truncado"
