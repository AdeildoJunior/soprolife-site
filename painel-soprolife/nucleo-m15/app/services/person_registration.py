"""M25.26 — cadastro de pessoa reaproveitável e o que ainda falta nele.

Duas coisas moram aqui:

* **A criação da pessoa como função**, e não como corpo de rota. Até esta
  missão, criar paciente + atendimento eram DUAS chamadas HTTP feitas pelo
  navegador em sequência (``POST /pessoas`` e depois ``POST /atendimentos``).
  Entre as duas cabe uma queda de rede, um F5 ou um erro de validação do
  exame — e o desfecho era um paciente órfão criado sem o atendimento que
  justificava o cadastro. Com a criação extraída, o fluxo atômico
  (``POST /atendimentos/novo-paciente``) faz as duas coisas numa transação só.

* **As pendências do cadastro como DADO**, não como frase. A tela precisa
  saber quais campos faltam para poder destacá-los e oferecer "Corrigir
  cadastro"; uma mensagem em prosa obrigaria o navegador a interpretar texto
  humano para descobrir campo, que é exatamente o acoplamento frágil que esta
  missão foi escrita para eliminar.

O que este módulo NÃO faz: bloquear atendimento por cadastro incompleto.
Espirometria de paciente sem CPF continua sendo lançada — a pendência é
informada e endereçável, porque impedir o lançamento tiraria da operação um
exame que já aconteceu de verdade.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import Consent, Person, PersonContact
from ..normalize import normalize_name, normalize_phone
from ..ids import allocate_public_code
from .cpf import normalizar_cpf

# Campo do cadastro -> como a pendência é apresentada e o quanto ela pesa.
#
# `bloqueia_laudo` acompanha a lista da CFM 2.381/2024 já implementada em
# `services/report_compliance.py`: lá, só o CPF do paciente é requisito
# bloqueante que depende do cadastro da pessoa. Nascimento, sexo e contato
# entram como pendência operacional — aparecem para quem opera, mas não
# impedem a entrega oficial, e por isso NUNCA travam o atendimento.
PENDENCIAS_CADASTRO = {
    "cpf": {
        "rotulo": "CPF",
        "bloqueia_laudo": True,
        "por_que": (
            "A CFM 2.381/2024 pede o CPF do paciente no laudo. Sem ele o "
            "documento sai, mas fica marcado como pendente para entrega "
            "oficial."
        ),
    },
    "data_nascimento": {
        "rotulo": "Data de nascimento",
        "bloqueia_laudo": False,
        "por_que": "Identifica o paciente no laudo e no prontuário.",
    },
    "sexo": {
        "rotulo": "Sexo",
        "bloqueia_laudo": False,
        "por_que": (
            "Entra na identificação impressa do laudo; sem cadastro, sai "
            "como 'não informado'."
        ),
    },
    "contato": {
        "rotulo": "WhatsApp ou telefone",
        "bloqueia_laudo": False,
        "por_que": "Sem contato não há como avisar o paciente nem acompanhar.",
    },
}


def add_contact(db: Session, person: Person, tipo: str, valor: str, principal: bool):
    """Contato com valor normalizado — telefone vira dígitos, e-mail vira minúsculas."""

    normalizado = (
        normalize_phone(valor)
        if tipo in ("whatsapp", "telefone")
        else valor.strip().lower()
    )
    contact = PersonContact(
        person_id=person.id,
        tipo=tipo,
        valor=valor,
        valor_normalizado=normalizado,
        principal=principal,
    )
    db.add(contact)
    db.flush()
    return contact


def build_person(
    db: Session,
    *,
    nome_completo: str,
    cpf: str | None,
    data_nascimento,
    sexo: str | None,
    observacao: str | None,
    contatos,
    consentimento_whatsapp: str | None,
    registrado_por: str,
) -> Person:
    """Cria a pessoa, contatos e consentimento — sem commit.

    Não faz commit de propósito: quem chama decide o limite da transação. É o
    que permite o atendimento inteiro (pessoa + exame + financeiro) ser tudo
    ou nada.

    O CPF passa por `normalizar_cpf`, que continua sendo a única porta de
    entrada do valor — inclusive neste caminho novo.
    """

    person = Person(
        public_code=allocate_public_code(db, "people"),
        nome_completo=nome_completo,
        nome_normalizado=normalize_name(nome_completo),
        cpf=normalizar_cpf(cpf),
        data_nascimento=data_nascimento,
        sexo=sexo,
        observacao=observacao,
    )
    db.add(person)
    db.flush()
    for contato in contatos:
        add_contact(db, person, contato.tipo, contato.valor, contato.principal)
    if consentimento_whatsapp:
        db.add(
            Consent(
                person_id=person.id,
                canal="whatsapp",
                status=consentimento_whatsapp,
                origem="cadastro",
                registrado_por=registrado_por,
            )
        )
    db.flush()
    return person


def cadastro_pendencias(person: Person) -> list[dict]:
    """O que falta no cadastro desta pessoa, em formato de máquina.

    Devolve lista vazia quando o cadastro está completo. A ordem é estável
    (bloqueantes primeiro) para a tela não embaralhar os campos a cada
    consulta.
    """

    faltando: list[str] = []
    if not (person.cpf or "").strip():
        faltando.append("cpf")
    if person.data_nascimento is None:
        faltando.append("data_nascimento")
    if not (person.sexo or "").strip():
        faltando.append("sexo")
    contatos_uteis = [
        c for c in (person.contacts or [])
        if c.ativo and c.tipo in ("whatsapp", "telefone")
    ]
    if not contatos_uteis:
        faltando.append("contato")

    pendencias = [
        {"campo": campo, **PENDENCIAS_CADASTRO[campo]} for campo in faltando
    ]
    pendencias.sort(key=lambda p: (not p["bloqueia_laudo"], p["campo"]))
    return pendencias
