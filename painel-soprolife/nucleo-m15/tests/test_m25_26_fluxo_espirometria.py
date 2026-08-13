"""M25.26 — o fluxo de Espirometria SoproLife passa a se explicar sozinho.

O teste real da Dra. Ana travou assim: cadastraram o paciente, tentaram
lançar a espirometria e o sistema recusou dizendo que faltavam dados, sem
dizer QUAIS nem ONDE. A causa não era uma regra errada — era a resposta:
toda falha de validação virava a frase `Payload inválido.`, e a lista de
campos que já existia na resposta saía em caminho técnico
(`body.espirometria.data_exame`), que a tela descartava.

Os testes aqui protegem as correções dessa missão:

1. o 422 diz o campo, com o rótulo do formulário e sem eco do que foi
   digitado;
2. data ilegível é RECUSADA em vez de virar `NULL` silencioso (era o defeito
   mais caro: o exame nascia sem data, sem follow-up, e só aparecia como
   problema semanas depois, na emissão do laudo);
3. paciente novo + atendimento numa transação só, sem paciente órfão;
4. modalidade × unidade conferidas no CADASTRO com as MESMAS regras que a
   emissão do laudo aplica (M25.17);
5. o valor de tabela é configuração do servidor, e continua sendo sugestão —
   nada é inferido.

Regressão obrigatória: Pastore permanece intocado.

Somente dados sintéticos, em banco isolado por teste. Nenhum telefone aqui é
discável (prefixo 0000 não é atribuído no Brasil).
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.config import Settings
from app.field_labels import (
    caminho_normalizado,
    mensagem_de_dominio,
    rotulo_do_campo,
)
from app.models import (
    FinancialEntry,
    Partner,
    PartnerUnit,
    Person,
    SpirometryExam,
)
from app.services.report_origin import (
    OriginDerivationError,
    derive_report_origin,
    validar_combinacao_no_cadastro,
)

API = "/api/v1"

FONE_SINTETICO = "(21) 0000-9301"
CPF_SINTETICO = "529.982.247-25"  # válido nos verificadores, sem dono real


@pytest.fixture()
def pastore_unidade(db):
    """Parceiro Pastore canônico + unidade ativa, sintéticos."""

    partner = Partner(
        public_code="CLI-M2526", nome="Pastore", tipo="clinica",
        status="ativa", arquivado=False,
    )
    db.add(partner)
    db.flush()
    unit = PartnerUnit(
        public_code="UNI-M2526", partner_id=partner.id,
        nome="Pastore Ipanema", ativo=True,
    )
    db.add(unit)
    db.commit()
    return partner, unit


def _pessoa(client, auth, nome, **extra):
    corpo = {"nome_completo": nome, "contatos": [], **extra}
    resp = client.post(f"{API}/pessoas", json=corpo, headers=auth("operacional"))
    assert resp.status_code == 201, resp.text
    return resp.json()


def _espirometria(**campos):
    bloco = {"data_exame": "12/08/2026", "status": "Realizado"}
    bloco.update(campos)
    return bloco


# ══════════════════════════════════════════ 1. o erro passa a dizer o que falta

def test_falta_de_campo_devolve_rotulo_do_formulario(client, auth):
    """O defeito original: a tela mostrava `Payload inválido.` e mais nada."""

    pessoa = _pessoa(client, auth, "Paciente Sintetico M2526 A")
    resp = client.post(
        f"{API}/atendimentos",
        json={
            "person_id": pessoa["id"],
            "tipo": "espirometria_soprolife",
            "espirometria": {"status": "Realizado"},  # sem data_exame
        },
        headers=auth("operacional"),
    )
    assert resp.status_code == 422
    erro = resp.json()["erro"]
    assert erro["codigo"] == "validacao"
    # A frase opaca não pode voltar.
    assert erro["mensagem"] != "Payload inválido."
    assert "Data do exame" in erro["mensagem"]
    # E o campo vem em formato de máquina, para a tela poder destacá-lo.
    assert erro["campos_faltantes"] == [
        {"campo": "espirometria.data_exame", "rotulo": "Data do exame"}
    ]


def test_regra_de_dominio_chega_inteira_na_mensagem(client, auth):
    """Antes, `value_error` virava só `{"campo": "body", "tipo": "value_error"}`.

    A explicação real ("use o tipo espirometria_pastore") era descartada pelo
    handler, que só olhava `loc` e `type`.
    """

    pessoa = _pessoa(client, auth, "Paciente Sintetico M2526 B")
    resp = client.post(
        f"{API}/atendimentos",
        json={
            "person_id": pessoa["id"],
            "tipo": "espirometria_soprolife",
            "espirometria": _espirometria(partner_id="qualquer"),
        },
        headers=auth("operacional"),
    )
    assert resp.status_code == 422
    assert "espirometria_pastore" in resp.json()["erro"]["mensagem"]


def test_erro_nunca_ecoa_o_valor_digitado(client, auth):
    """Guarda de PII: o motivo sai do TIPO do erro, nunca do input.

    Um CPF ou telefone impresso numa mensagem de erro acaba em captura de
    tela e em log de suporte.
    """

    pessoa = _pessoa(client, auth, "Paciente Sintetico M2526 C")
    resp = client.post(
        f"{API}/atendimentos",
        json={
            "person_id": pessoa["id"],
            "tipo": "espirometria_soprolife",
            "espirometria": _espirometria(modalidade="21999998888"),
        },
        headers=auth("operacional"),
    )
    assert resp.status_code == 422
    corpo = resp.text
    assert "21999998888" not in corpo


@pytest.mark.parametrize(
    "texto,esperado",
    [
        ("Value error, Combinação recusada.", "Combinação recusada."),
        # Descartadas pela guarda: poderiam carregar dado pessoal.
        ("CPF 52998224725 inválido", None),
        ("E-mail joao@exemplo.com inválido", None),
    ],
)
def test_guarda_de_pii_da_mensagem_de_dominio(texto, esperado):
    assert mensagem_de_dominio({"type": "value_error", "msg": texto}) == esperado


def test_caminho_e_rotulo_sao_estaveis():
    assert caminho_normalizado(("body", "espirometria", "data_exame")) == \
        "espirometria.data_exame"
    # índice de lista não polui a chave do rótulo
    assert caminho_normalizado(("body", "contatos", 0, "valor")) == "contatos.valor"
    assert rotulo_do_campo("espirometria.data_exame") == "Data do exame"
    # o específico ganha do genérico
    assert rotulo_do_campo("financeiro.espirometria.status") == "Status do pagamento"
    assert rotulo_do_campo("espirometria.status") == "Status do exame"


# ═══════════════════════════════════ 2. data ilegível não vira NULL silencioso

def test_data_sem_barras_e_recusada_e_nao_grava_exame_sem_data(client, auth):
    """O defeito mais caro da missão.

    `parse_incomplete_date("12082026")` devolve `value=None` sem levantar
    erro. O exame era criado com data em branco, precisão "desconhecida" e
    follow-up desligado — e o operador via "criado com sucesso".
    """

    pessoa = _pessoa(client, auth, "Paciente Sintetico M2526 D")
    resp = client.post(
        f"{API}/atendimentos",
        json={
            "person_id": pessoa["id"],
            "tipo": "espirometria_soprolife",
            "espirometria": _espirometria(data_exame="12082026"),
        },
        headers=auth("operacional"),
    )
    assert resp.status_code == 422
    assert resp.json()["erro"]["mensagem"]["codigo"] == "data_ilegivel"

    lista = client.get(f"{API}/espirometrias", headers=auth("leitura")).json()
    assert lista["total"] == 0, "exame ilegível não pode ter sido gravado"


@pytest.mark.parametrize("bruto", ["12/08/2026", "08/2026", "2026"])
def test_precisao_parcial_continua_aceita(client, auth, bruto):
    """A recusa é só do ILEGÍVEL — data parcial é contrato do domínio."""

    pessoa = _pessoa(client, auth, f"Paciente Sintetico M2526 E {bruto[-4:]}")
    resp = client.post(
        f"{API}/atendimentos",
        json={
            "person_id": pessoa["id"],
            "tipo": "espirometria_soprolife",
            "espirometria": _espirometria(data_exame=bruto),
        },
        headers=auth("operacional"),
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["espirometria"]["data_exame"] is not None


def test_data_vazia_continua_sendo_ausencia_legitima(client, auth):
    """Campo em branco ≠ campo ilegível. A competência é opcional."""

    pessoa = _pessoa(client, auth, "Paciente Sintetico M2526 F")
    resp = client.post(
        f"{API}/atendimentos",
        json={
            "person_id": pessoa["id"],
            "tipo": "espirometria_soprolife",
            "espirometria": _espirometria(),
            "financeiro": {"espirometria": {
                "valor": "220.00", "status": "Pendente",
            }},
        },
        headers=auth("operacional"),
    )
    assert resp.status_code == 201, resp.text


# ═════════════════════════════ 3. paciente novo + atendimento em UMA transação

def test_novo_paciente_e_espirometria_numa_unica_operacao(client, auth):
    resp = client.post(
        f"{API}/atendimentos/novo-paciente",
        json={
            "pessoa": {
                "nome_completo": "Paciente Sintetico M2526 G",
                "contatos": [
                    {"tipo": "whatsapp", "valor": FONE_SINTETICO, "principal": True}
                ],
                "data_nascimento": "1980-05-10",
                "cpf": CPF_SINTETICO,
                "sexo": "feminino",
            },
            "tipo": "espirometria_soprolife",
            "espirometria": _espirometria(
                modalidade="residencial", local_atendimento="Domicílio do paciente"
            ),
            "financeiro": {"espirometria": {
                "valor": "220.00", "status": "Recebido",
                "data_recebimento": "2026-08-12",
            }},
        },
        headers=auth("operacional"),
    )
    assert resp.status_code == 201, resp.text
    dados = resp.json()
    assert dados["pessoa_criada"] is True
    assert dados["espirometria"]["public_code"].startswith("ESP-")
    assert dados["cadastro_pendencias"] == [], "cadastro completo não tem pendência"
    assert len(dados["lancamentos"]) == 1
    assert Decimal(dados["lancamentos"][0]["valor"]) == Decimal("220.00")


def test_falha_no_exame_nao_deixa_paciente_orfao(client, auth, db):
    """Atomicidade — a razão de o endpoint existir.

    Com duas chamadas separadas (o fluxo antigo), a pessoa já estava
    persistida quando o exame falhava. O operador recomeçava do zero e criava
    a mesma pessoa de novo.
    """

    resp = client.post(
        f"{API}/atendimentos/novo-paciente",
        json={
            "pessoa": {"nome_completo": "Paciente Sintetico M2526 H", "contatos": []},
            "tipo": "espirometria_soprolife",
            "espirometria": _espirometria(data_exame="99/99/9999"),
        },
        headers=auth("operacional"),
    )
    assert resp.status_code == 422
    restantes = db.execute(
        select(Person).where(Person.nome_completo == "Paciente Sintetico M2526 H")
    ).scalars().all()
    assert restantes == [], "nenhum paciente pode sobreviver à falha"


def test_duplicado_e_avisado_e_so_nasce_com_confirmacao_humana(client, auth):
    # A contagem vem pela API, e não por uma sessão aberta em paralelo: manter
    # uma transação de leitura viva enquanto o cliente escreve trava o SQLite
    # do teste sem nada dizer sobre o comportamento sob prova.
    def quantos(nome):
        return client.post(
            f"{API}/pessoas/busca", json={"q": nome}, headers=auth("leitura")
        ).json()["total"]

    corpo = {
        "pessoa": {
            "nome_completo": "Paciente Sintetico M2526 I",
            "contatos": [
                {"tipo": "whatsapp", "valor": FONE_SINTETICO, "principal": True}
            ],
        },
        "tipo": "espirometria_soprolife",
        "espirometria": _espirometria(),
    }
    assert client.post(
        f"{API}/atendimentos/novo-paciente", json=corpo, headers=auth("operacional")
    ).status_code == 201

    repetido = client.post(
        f"{API}/atendimentos/novo-paciente", json=corpo, headers=auth("operacional")
    )
    assert repetido.status_code == 409
    detalhe = repetido.json()["erro"]["mensagem"]
    assert detalhe["codigo"] == "possivel_duplicado"
    assert detalhe["candidatos"], "a tela precisa dos candidatos para oferecer escolha"

    # Nada foi criado enquanto o humano não decidiu.
    assert quantos("Paciente Sintetico M2526 I") == 1

    corpo["confirmar_duplicado"] = True
    assert client.post(
        f"{API}/atendimentos/novo-paciente", json=corpo, headers=auth("operacional")
    ).status_code == 201
    assert quantos("Paciente Sintetico M2526 I") == 2


# ══════════════════════════════════════ 4. modalidade × local/unidade coerentes

def test_coerencia_do_cadastro_usa_as_regras_do_laudo(client, auth):
    """Fase D — a contradição para no cadastro, não semanas depois.

    Existe em produção um exame gravado como `clinica_parceira` sem unidade:
    combinação que `derive_report_origin` recusa desde a M25.17, mas que o
    cadastro aceitava.
    """

    pessoa = _pessoa(client, auth, "Paciente Sintetico M2526 J")
    resp = client.post(
        f"{API}/atendimentos",
        json={
            "person_id": pessoa["id"],
            "tipo": "espirometria_soprolife",
            "espirometria": _espirometria(modalidade="clinica_parceira"),
        },
        headers=auth("operacional"),
    )
    assert resp.status_code == 422
    detalhe = resp.json()["erro"]["mensagem"]
    assert detalhe["codigo"] == "exame_sem_unidade_parceira"
    # A mensagem sozinha deixa o operador parado; o conserto vem junto.
    assert "Espirometria Pastore" in detalhe["como_corrigir"]


@pytest.mark.parametrize("modalidade", ["residencial", "cowork"])
def test_modalidades_soprolife_sao_aceitas(client, auth, modalidade):
    pessoa = _pessoa(client, auth, f"Paciente Sintetico M2526 K {modalidade}")
    resp = client.post(
        f"{API}/atendimentos",
        json={
            "person_id": pessoa["id"],
            "tipo": "espirometria_soprolife",
            "espirometria": _espirometria(
                modalidade=modalidade, local_atendimento="Local Sintetico"
            ),
        },
        headers=auth("operacional"),
    )
    assert resp.status_code == 201, resp.text


def test_ausencia_de_modalidade_continua_permitida(client, auth):
    """13 dos exames em produção vieram de importação sem modalidade.

    Ausência não é contradição: bloqueá-los agora quebraria o histórico.
    """

    pessoa = _pessoa(client, auth, "Paciente Sintetico M2526 L")
    resp = client.post(
        f"{API}/atendimentos",
        json={
            "person_id": pessoa["id"],
            "tipo": "espirometria_soprolife",
            "espirometria": _espirometria(),
        },
        headers=auth("operacional"),
    )
    assert resp.status_code == 201, resp.text


def test_cadastro_e_laudo_julgam_a_mesma_combinacao_do_mesmo_jeito(db, person):
    """A garantia de que as duas regras não vão divergir com o tempo."""

    casos = [
        (None, False, None),
        ("residencial", False, None),
        ("cowork", False, None),
        ("clinica_parceira", False, "exame_sem_unidade_parceira"),
        ("residencial", True, "unidade_incompativel_com_modalidade"),
        (None, True, "unidade_sem_modalidade"),
    ]
    for modalidade, tem_unidade, codigo in casos:
        if codigo is None:
            validar_combinacao_no_cadastro(modalidade, tem_unidade)
            continue
        with pytest.raises(OriginDerivationError) as erro:
            validar_combinacao_no_cadastro(modalidade, tem_unidade)
        assert erro.value.codigo == codigo
        assert erro.value.como_corrigir


# ═══════════════════════════════════════════════ 5. valor de tabela SoproLife

def test_valor_padrao_vem_da_configuracao(client, auth):
    resp = client.get(f"{API}/atendimentos/configuracao", headers=auth("leitura"))
    assert resp.status_code == 200
    cfg = resp.json()["espirometria_soprolife"]
    assert cfg["valor_padrao"] == "220.00"
    assert cfg["valor_padrao_editavel"] is True
    oferecidas = [m["valor"] for m in cfg["modalidades"]]
    assert oferecidas == ["residencial", "cowork"]
    # clínica parceira é declarada indisponível COM o motivo, em vez de
    # simplesmente sumir sem explicação.
    indisponivel = cfg["modalidades_indisponiveis"][0]
    assert indisponivel["valor"] == "clinica_parceira"
    assert "Pastore" in indisponivel["motivo"]


def test_valor_de_tabela_e_configuravel_sem_tocar_codigo():
    assert Settings(
        espirometria_soprolife_valor_padrao="199.90"
    ).espirometria_soprolife_valor_padrao == Decimal("199.90")


def test_valor_editado_pelo_operador_e_o_que_vale(client, auth):
    """O padrão é sugestão. Quem manda é o que ficou no campo."""

    pessoa = _pessoa(client, auth, "Paciente Sintetico M2526 M")
    resp = client.post(
        f"{API}/atendimentos",
        json={
            "person_id": pessoa["id"],
            "tipo": "espirometria_soprolife",
            "espirometria": _espirometria(),
            "financeiro": {"espirometria": {
                "valor": "150.00", "status": "Recebido",
                "data_recebimento": "2026-08-12",
            }},
        },
        headers=auth("operacional"),
    )
    assert resp.status_code == 201
    assert Decimal(resp.json()["lancamentos"][0]["valor"]) == Decimal("150.00")


def test_valor_ausente_nao_cria_lancamento(client, auth):
    """Ausência permanece ausência: o servidor NUNCA completa com 220."""

    pessoa = _pessoa(client, auth, "Paciente Sintetico M2526 N")
    resp = client.post(
        f"{API}/atendimentos",
        json={
            "person_id": pessoa["id"],
            "tipo": "espirometria_soprolife",
            "espirometria": _espirometria(),
        },
        headers=auth("operacional"),
    )
    assert resp.status_code == 201
    assert resp.json()["lancamentos"] == []


# ═══════════════════════════════════════════ 6. pendências de cadastro (Fase C)

def test_pendencias_listam_campo_a_campo_e_marcam_o_bloqueante(client, auth):
    pessoa = _pessoa(client, auth, "Paciente Sintetico M2526 O")
    detalhe = client.get(
        f"{API}/pessoas/{pessoa['id']}", headers=auth("leitura")
    ).json()
    campos = {p["campo"]: p for p in detalhe["cadastro_pendencias"]}
    assert set(campos) == {"cpf", "data_nascimento", "sexo", "contato"}
    # Só o CPF bloqueia a entrega oficial (CFM 2.381/2024), e ele vem primeiro.
    assert campos["cpf"]["bloqueia_laudo"] is True
    assert detalhe["cadastro_pendencias"][0]["campo"] == "cpf"
    assert campos["data_nascimento"]["bloqueia_laudo"] is False
    for p in campos.values():
        assert p["rotulo"] and p["por_que"], "pendência sem rótulo/motivo é inútil"


def test_corrigir_cadastro_zera_as_pendencias(client, auth):
    """O caminho que o botão "Corrigir cadastro" percorre."""

    pessoa = _pessoa(client, auth, "Paciente Sintetico M2526 P")
    assert client.patch(
        f"{API}/pessoas/{pessoa['id']}",
        json={"cpf": CPF_SINTETICO, "data_nascimento": "1975-03-02", "sexo": "masculino"},
        headers=auth("operacional"),
    ).status_code == 200
    assert client.post(
        f"{API}/pessoas/{pessoa['id']}/contatos",
        json={"tipo": "whatsapp", "valor": FONE_SINTETICO, "principal": True},
        headers=auth("operacional"),
    ).status_code == 201

    detalhe = client.get(
        f"{API}/pessoas/{pessoa['id']}", headers=auth("leitura")
    ).json()
    assert detalhe["cadastro_pendencias"] == []
    assert detalhe["sexo"] == "masculino"


def test_cadastro_incompleto_nao_impede_o_atendimento(client, auth):
    """Deliberado: o exame ACONTECEU.

    Recusá-lo por falta de CPF tiraria da operação um dado real e empurraria
    o operador para inventar um número só para destravar a tela.
    """

    pessoa = _pessoa(client, auth, "Paciente Sintetico M2526 Q")
    resp = client.post(
        f"{API}/atendimentos",
        json={
            "person_id": pessoa["id"],
            "tipo": "espirometria_soprolife",
            "espirometria": _espirometria(),
        },
        headers=auth("operacional"),
    )
    assert resp.status_code == 201
    pendencias = [p["campo"] for p in resp.json()["cadastro_pendencias"]]
    assert "cpf" in pendencias, "a pendência acompanha o sucesso, em vez de sumir"


def test_sexo_deixou_de_ser_coluna_morta(client, auth):
    """`people.sexo` existia desde a M25.2, é impresso no laudo, e NENHUM
    schema o aceitava — o laudo dizia "não informado" para todo mundo sem
    haver forma de corrigir."""

    pessoa = _pessoa(client, auth, "Paciente Sintetico M2526 R", sexo="feminino")
    assert pessoa["sexo"] == "feminino"


# ══════════════════════════════════════════ 7. Pastore — regressão obrigatória

def test_pastore_continua_derivando_tudo_do_parceiro_canonico(
    client, auth, db, pastore_unidade
):
    partner, unit = pastore_unidade
    pessoa = _pessoa(client, auth, "Paciente Sintetico M2526 S")
    resp = client.post(
        f"{API}/atendimentos",
        json={
            "person_id": pessoa["id"],
            "tipo": "espirometria_pastore",
            "espirometria": {
                "data_exame": "12/08/2026",
                "status": "Realizado",
                "partner_id": partner.id,
                "partner_unit_id": unit.id,
            },
        },
        headers=auth("operacional"),
    )
    assert resp.status_code == 201, resp.text
    exame = resp.json()["espirometria"]
    # Domínio derivado, nunca escolha do cliente.
    assert exame["modalidade"] == "clinica_parceira"
    assert exame["local_atendimento"] == unit.nome
    assert exame["origem"] == partner.nome
    assert exame["partner_unit_id"] == unit.id
    # Nenhuma receita individual: Pastore fecha por competência mensal.
    assert resp.json()["lancamentos"] == []
    assert db.execute(select(FinancialEntry)).scalars().all() == []


def test_pastore_continua_recusando_pagamento_direto(client, auth, pastore_unidade):
    partner, unit = pastore_unidade
    pessoa = _pessoa(client, auth, "Paciente Sintetico M2526 T")
    resp = client.post(
        f"{API}/atendimentos",
        json={
            "person_id": pessoa["id"],
            "tipo": "espirometria_pastore",
            "espirometria": {
                "data_exame": "12/08/2026",
                "partner_id": partner.id,
                "partner_unit_id": unit.id,
            },
            "financeiro": {"espirometria": {"valor": "220.00"}},
        },
        headers=auth("operacional"),
    )
    assert resp.status_code == 422
    assert resp.json()["erro"]["codigo"] == "pagamento_direto_pastore_proibido"


def test_exame_pastore_criado_agora_emite_laudo_sem_contradicao(
    client, auth, db, pastore_unidade
):
    """A coerência nova não pode ter quebrado o caminho do laudo."""

    partner, unit = pastore_unidade
    pessoa = _pessoa(client, auth, "Paciente Sintetico M2526 U")
    resp = client.post(
        f"{API}/atendimentos",
        json={
            "person_id": pessoa["id"],
            "tipo": "espirometria_pastore",
            "espirometria": {
                "data_exame": "12/08/2026",
                "partner_id": partner.id,
                "partner_unit_id": unit.id,
            },
        },
        headers=auth("operacional"),
    )
    exame = db.get(SpirometryExam, resp.json()["espirometria"]["id"])
    derivado = derive_report_origin(db, exame)
    assert derivado.origin_type == "clinica_parceira"
    assert derivado.partner_unit_id == unit.id
