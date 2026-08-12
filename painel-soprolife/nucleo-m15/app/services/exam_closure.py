"""M25.24 — encerramento operacional de exame.

O problema que isto resolve, em uma frase: a operação tinha uma fila de
"espirometrias sem laudo" cheia de exames antigos cujos pacientes JÁ
receberam o laudo — por fora da plataforma, antes de o Centro de Comando
existir. Eles não são pendência de ninguém, mas continuavam cobrando
trabalho todo dia.

As três saídas erradas, e por que foram descartadas:

1. **Apagar o exame.** Destrói prontuário e a rastreabilidade do
   atendimento. Nunca.
2. **Fabricar um laudo.** Faria o sistema afirmar que produziu um documento
   médico que ele nunca produziu. É falsificação, mesmo com boa intenção.
3. **Regra por data** ("esconder o que for anterior a X"). Silenciaria
   sozinha exames futuros que ninguém autorizou silenciar. O encerramento é
   marcado exame a exame, deliberadamente, e um exame novo nunca herda a
   decisão tomada sobre um lote antigo.

O que resta — e é o que está aqui — é registrar a VERDADE: houve laudo, ele
não passou por este sistema, e não há ação pendente. Com quem decidiu,
quando, por qual motivo estruturado e com uma observação curta. Reversível
por gestor.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..models import SpirometryExam

# Motivos FECHADOS. Texto livre num campo que decide o que some da fila
# viraria, em três meses, vinte grafias da mesma coisa e nenhuma consulta
# possível. A observação é onde o caso concreto é descrito.
CLOSURE_REASONS: dict[str, str] = {
    "laudo_externo_ja_entregue": (
        "Laudo externo já entregue — histórico anterior à operação na "
        "plataforma"
    ),
    # Os cinco atendimentos Pastore: o paciente já tinha o laudo dele por
    # fora, e o que existe aqui dentro foi a médica exercitando o fluxo. Os
    # documentos gerados nesse exercício são preservados como evidência; o
    # que este motivo diz é que eles não são trabalho a entregar.
    "laudo_externo_e_teste_do_fluxo": (
        "Histórico — laudo já entregue externamente; os laudos deste exame "
        "no Centro de Comando são teste do fluxo após a entrega externa"
    ),
    "duplicidade_operacional": (
        "Registro operacional duplicado — o atendimento vive em outro exame"
    ),
    "atendimento_cancelado": (
        "Atendimento cancelado — não houve exame a laudar"
    ),
}

# Rótulo curto para chip/lista, quando a frase inteira não cabe.
CLOSURE_SHORT_LABELS: dict[str, str] = {
    "laudo_externo_ja_entregue": "Laudo externo já entregue",
    "laudo_externo_e_teste_do_fluxo": "Histórico — teste após entrega externa",
    "duplicidade_operacional": "Duplicidade operacional",
    "atendimento_cancelado": "Atendimento cancelado",
}

_OBSERVACAO_MAX = 200


class ExamClosureError(Exception):
    """Recusa explicada, com código estável para a API e para a CLI."""

    def __init__(self, http_status: int, codigo: str, mensagem: str):
        super().__init__(mensagem)
        self.http_status = http_status
        self.codigo = codigo
        self.mensagem = mensagem


def is_closed(exam: SpirometryExam) -> bool:
    """Um exame está encerrado quando tem motivo. A constraint garante o resto."""

    return exam.encerramento_motivo is not None


def reason_label(motivo: str | None) -> str | None:
    if motivo is None:
        return None
    return CLOSURE_REASONS.get(motivo, motivo)


def closure_payload(exam: SpirometryExam) -> dict | None:
    """O que a tela mostra sobre o encerramento. Nunca identifica paciente."""

    if not is_closed(exam):
        return None
    return {
        "motivo": exam.encerramento_motivo,
        "motivo_label": reason_label(exam.encerramento_motivo),
        "motivo_curto": CLOSURE_SHORT_LABELS.get(
            exam.encerramento_motivo, exam.encerramento_motivo
        ),
        "encerrado_em": (
            exam.encerrado_em.isoformat() if exam.encerrado_em else None
        ),
        "observacao": exam.encerramento_observacao,
    }


def close_exam(
    db: Session,
    *,
    exam: SpirometryExam,
    motivo: str,
    observacao: str,
    user_id: str,
    now: datetime | None = None,
) -> bool:
    """Encerra UM exame. Devolve True se algo mudou.

    IDEMPOTENTE por desenho: reencerrar um exame já encerrado devolve False
    sem tocar em nada — nem na data, nem em quem encerrou. Reprocessar o lote
    inteiro por engano não pode reescrever a autoria da decisão original nem
    empilhar um segundo encerramento sobre o mesmo exame.

    Só recusa quando o pedido é de fato outro: motivo diferente do que já
    está gravado. Aí a operação precisa reabrir e encerrar de novo, com
    trilha das duas decisões, em vez de a segunda apagar a primeira em
    silêncio.
    """

    if motivo not in CLOSURE_REASONS:
        raise ExamClosureError(
            422,
            "motivo_de_encerramento_invalido",
            "Motivo de encerramento não reconhecido.",
        )
    texto = (observacao or "").strip()
    if not texto:
        raise ExamClosureError(
            422,
            "observacao_obrigatoria",
            "Descreva em uma frase por que este exame está sendo encerrado.",
        )
    if is_closed(exam):
        if exam.encerramento_motivo != motivo:
            raise ExamClosureError(
                409,
                "exame_ja_encerrado_com_outro_motivo",
                "Este exame já está encerrado por outro motivo. Reabra antes "
                "de encerrar novamente.",
            )
        return False
    exam.encerramento_motivo = motivo
    exam.encerrado_em = now or datetime.now(timezone.utc)
    exam.encerrado_por_user_id = user_id
    exam.encerramento_observacao = texto[:_OBSERVACAO_MAX]
    db.flush()
    return True


def reopen_exam(db: Session, *, exam: SpirometryExam) -> bool:
    """Devolve o exame à fila. Devolve True se algo mudou.

    Limpa os quatro campos juntos — a constraint não aceita meio
    encerramento. O histórico de quem encerrou e por quê não se perde: ele
    vive na auditoria, que é append-only, e não nestas colunas.
    """

    if not is_closed(exam):
        return False
    exam.encerramento_motivo = None
    exam.encerrado_em = None
    exam.encerrado_por_user_id = None
    exam.encerramento_observacao = None
    db.flush()
    return True
