"""M25.17 — o contexto do laudo sai do exame, não do operador.

No primeiro uso real (Geoffrey Kirk Barnes / ESP-000017) o envio do PDF
falhou com ``unidade_origem_incompativel``: o formulário pedia "Origem do
exame" e "Unidade parceira" como dois campos livres, e a combinação
escolhida — origem ``pastore`` + unidade ``Pastore Ipanema`` — é inválida,
porque unidade só se aplica a ``clinica_parceira``. Só depois de trocar a
origem para "clínica parceira" o upload passou.

Isso é um enigma que o sistema criou e mandou o operador resolver: o exame
JÁ sabia onde foi realizado (`modalidade`, `partner_id`, `partner_unit_id`
são campos estruturados do atendimento). Este módulo lê esses campos e
devolve a combinação correta, para que a tela pare de perguntar.

Duas regras não negociáveis:

* **Nada de texto livre.** `exam.origem` e `exam.local_atendimento` são
  campos digitados por pessoas ("Pastore", "Pastore Ipanema - TESTE"). Casar
  a palavra "Pastore" ali dentro para escolher uma unidade FINANCEIRA seria
  ligar dinheiro a uma heurística de string. Só `modalidade`, `partner_id` e
  `partner_unit_id` decidem.

* **Fail closed em inconsistência.** Um exame que se diz de clínica parceira
  sem unidade vinculada (o caso de ESP-000016) não vira "clínica parceira
  sem unidade" nem cai num `outro` genérico: ele para o envio e diz onde o
  cadastro precisa ser corrigido. Adivinhar aqui produz laudo com o endereço
  errado impresso.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from ..models import Partner, PartnerUnit, SpirometryExam

# Modalidade do atendimento -> origem controlada do laudo. É a única
# tradução admitida, e é total: modalidade fora deste mapa não tem origem
# derivável e por isso interrompe o fluxo.
MODALIDADE_PARA_ORIGEM = {
    "clinica_parceira": "clinica_parceira",
    "cowork": "coworking",
    "residencial": "residencial",
}

# Modalidades que EXIGEM unidade parceira vinculada.
MODALIDADES_COM_UNIDADE = frozenset({"clinica_parceira"})


class OriginDerivationError(ValueError):
    """Contexto não derivável — com `codigo` estável e o que corrigir.

    `como_corrigir` existe porque a mensagem sozinha ("exame sem unidade")
    deixa o operador parado: ele precisa saber que o conserto é no cadastro
    do atendimento, não no formulário do laudo.
    """

    def __init__(self, codigo: str, mensagem: str, como_corrigir: str):
        self.codigo = codigo
        self.mensagem = mensagem
        self.como_corrigir = como_corrigir
        super().__init__(mensagem)


@dataclass(frozen=True)
class DerivedOrigin:
    """Contexto do laudo derivado do exame — pronto para gravar e exibir."""

    origin_type: str
    partner_unit_id: str | None
    unit_name: str | None
    partner_name: str | None
    # Como a tela apresenta o local ao operador, em modo somente leitura.
    display_name: str
    address_line: str | None
    # De qual campo estruturado o contexto veio; entra na auditoria.
    source: str
    # False quando o exame não registra local nenhum. O laudo é permitido —
    # sem endereço, nunca com um inventado —, mas a tela avisa e o portão
    # CFM da M25.15 continua contando isso como pendência.
    completo: bool = True

    def as_payload(self) -> dict:
        return {
            "origin_type": self.origin_type,
            "partner_unit_id": self.partner_unit_id,
            "unit_name": self.unit_name,
            "partner_name": self.partner_name,
            "display_name": self.display_name,
            "address_line": self.address_line,
            "origem_do_dado": self.source,
            "completo": self.completo,
        }


def _address_line(unit: PartnerUnit) -> str | None:
    partes = [
        getattr(unit, campo, None)
        for campo in ("logradouro", "bairro", "cidade", "uf")
    ]
    texto = ", ".join(p.strip() for p in partes if p and str(p).strip())
    return texto or None


def derive_report_origin(
    db: Session, exam: SpirometryExam
) -> DerivedOrigin:
    """Origem e unidade do laudo a partir dos dados estruturados do exame.

    Levanta `OriginDerivationError` sempre que os campos estruturados não
    contarem uma história coerente. Nunca devolve um palpite.
    """

    modalidade = (exam.modalidade or "").strip().lower()
    unidade_id = (exam.partner_unit_id or "").strip() or None

    if not modalidade or modalidade not in MODALIDADE_PARA_ORIGEM:
        # AUSÊNCIA não é o mesmo que CONTRADIÇÃO, e as duas não podem ter o
        # mesmo desfecho. Contradição (adiante) significa que os campos
        # apontam para lugares diferentes: seguir em frente imprimiria o
        # endereço de uma clínica onde o exame não aconteceu, e por isso
        # para o fluxo. Aqui não há informação nenhuma — 13 dos 18 exames em
        # produção vieram de importação assim.
        #
        # Bloquear esses seria trocar um laudo sem endereço por nenhum laudo,
        # e ainda deixaria a operação parada num dado que ela pode completar
        # depois. O caminho honesto é a origem genérica que o domínio já
        # tinha: o laudo sai sem linha de endereço (nunca com um inventado),
        # `completo=False` avisa a tela, e o portão CFM da M25.15 continua
        # marcando endereço/contato como pendência.
        if unidade_id:
            raise OriginDerivationError(
                "unidade_sem_modalidade",
                "O exame tem unidade parceira vinculada, mas não registra a "
                "modalidade do atendimento.",
                "Abra o atendimento e informe a modalidade; hoje a unidade "
                "aponta para uma clínica e a modalidade não confirma que o "
                "exame foi feito nela.",
            )
        return DerivedOrigin(
            origin_type="outro",
            partner_unit_id=None,
            unit_name=None,
            partner_name=None,
            display_name="Local não informado",
            address_line=None,
            source="exame_sem_local_registrado",
            completo=False,
        )

    origem = MODALIDADE_PARA_ORIGEM[modalidade]

    if modalidade in MODALIDADES_COM_UNIDADE:
        if not unidade_id:
            raise OriginDerivationError(
                "exame_sem_unidade_parceira",
                "O exame está marcado como de clínica parceira, mas não tem "
                "unidade vinculada.",
                "Abra o atendimento deste exame e vincule a unidade parceira "
                "onde ele foi realizado. É dela que sai o endereço impresso "
                "no laudo.",
            )
        unit = db.get(PartnerUnit, unidade_id)
        if unit is None or not unit.ativo:
            raise OriginDerivationError(
                "unidade_do_exame_indisponivel",
                "A unidade parceira vinculada ao exame não está ativa.",
                "Reative a unidade em Parcerias ou vincule o atendimento à "
                "unidade correta.",
            )
        # Coerência entre parceiro do exame e dono da unidade: se os dois
        # estão preenchidos e discordam, o cadastro tem um erro que mudaria
        # o endereço impresso e o rateio financeiro.
        if exam.partner_id and unit.partner_id != exam.partner_id:
            raise OriginDerivationError(
                "unidade_de_outro_parceiro",
                "A unidade vinculada ao exame pertence a outro parceiro.",
                "Corrija o parceiro ou a unidade no atendimento; hoje os "
                "dois campos apontam para clínicas diferentes.",
            )
        partner = db.get(Partner, unit.partner_id) if unit.partner_id else None
        partner_name = partner.nome if partner else None
        display = (
            f"{partner_name} — {unit.nome}"
            if partner_name and partner_name != unit.nome
            else unit.nome
        )
        return DerivedOrigin(
            origin_type=origem,
            partner_unit_id=unit.id,
            unit_name=unit.nome,
            partner_name=partner_name,
            display_name=display,
            address_line=_address_line(unit),
            source="exame_unidade_parceira",
        )

    # Fora de clínica parceira NÃO existe unidade. Um exame domiciliar com
    # unidade vinculada é contradição de cadastro, não um detalhe a ignorar:
    # ignorar imprimiria o endereço de uma clínica num atendimento que não
    # aconteceu nela.
    if unidade_id:
        raise OriginDerivationError(
            "unidade_incompativel_com_modalidade",
            "O exame tem unidade parceira vinculada, mas a modalidade "
            "registrada não é de clínica parceira.",
            "Abra o atendimento e deixe modalidade e unidade coerentes: ou "
            "a modalidade passa a ser clínica parceira, ou a unidade sai.",
        )

    rotulos = {
        "coworking": "Espaço de atendimento SoproLife",
        "residencial": "Atendimento domiciliar",
    }
    return DerivedOrigin(
        origin_type=origem,
        partner_unit_id=None,
        unit_name=None,
        partner_name=None,
        display_name=rotulos[origem],
        address_line=None,
        source="exame_modalidade",
    )


def derived_origin_payload(db: Session, exam: SpirometryExam) -> dict:
    """Versão para a API: nunca levanta, devolve o erro como dado.

    O localizador precisa LISTAR exames com cadastro inconsistente — é como
    o operador descobre que existe algo a corrigir. Levantar aqui esconderia
    justamente a linha que precisa aparecer.
    """

    try:
        derived = derive_report_origin(db, exam)
    except OriginDerivationError as exc:
        return {
            "ok": False,
            "codigo": exc.codigo,
            "mensagem": exc.mensagem,
            "como_corrigir": exc.como_corrigir,
        }
    return {"ok": True, **derived.as_payload()}
