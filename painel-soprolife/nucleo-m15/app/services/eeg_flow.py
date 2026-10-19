"""Máquina de estados do exame de EEG.

POR QUE UMA MÁQUINA, E NÃO UM CAMPO LIVRE
-----------------------------------------
"Exame realizado" e "laudo concluído" são eventos diferentes, e confundi-los
é o erro mais caro deste domínio: dá a impressão de que o paciente já tem
resultado quando o traçado nem saiu do aparelho. Aqui cada evento tem o seu
estado, a sua data própria, e uma transição que o servidor confere.

Transições inválidas são RECUSADAS — não corrigidas em silêncio. Pular de
`agendado` direto para `laudo_disponivel` apagaria a existência do exame, do
envio e do recebimento; recusar é o que mantém a trilha honesta.

`cancelado` é alcançável de quase toda parte, porque a operação real cancela.
Só não se cancela o que já foi entregue ao paciente: desfazer isso é outro
problema (retratação de laudo), que não se resolve mudando um enum.
"""

from __future__ import annotations

CADASTRO = "cadastro"
AGENDADO = "agendado"
CONFIRMADO = "confirmado"
REALIZADO = "realizado"
AGUARDANDO_ENVIO = "aguardando_envio_laudo"
ENVIADO = "enviado_para_laudo"
EM_LAUDO = "em_laudo"
LAUDO_RECEBIDO = "laudo_recebido"
LAUDO_DISPONIVEL = "laudo_disponivel"
CANCELADO = "cancelado"

ESTADOS = (
    CADASTRO, AGENDADO, CONFIRMADO, REALIZADO, AGUARDANDO_ENVIO,
    ENVIADO, EM_LAUDO, LAUDO_RECEBIDO, LAUDO_DISPONIVEL, CANCELADO,
)

# Para cada estado, os estados que podem vir depois.
TRANSICOES: dict[str, frozenset[str]] = {
    CADASTRO: frozenset({AGENDADO, CANCELADO}),
    AGENDADO: frozenset({CONFIRMADO, REALIZADO, AGENDADO, CANCELADO}),
    CONFIRMADO: frozenset({REALIZADO, AGENDADO, CANCELADO}),
    REALIZADO: frozenset({AGUARDANDO_ENVIO, CANCELADO}),
    AGUARDANDO_ENVIO: frozenset({ENVIADO, CANCELADO}),
    ENVIADO: frozenset({EM_LAUDO, LAUDO_RECEBIDO, ENVIADO, CANCELADO}),
    EM_LAUDO: frozenset({LAUDO_RECEBIDO, CANCELADO}),
    LAUDO_RECEBIDO: frozenset({LAUDO_DISPONIVEL, LAUDO_RECEBIDO, CANCELADO}),
    # Entregue ao paciente é fim de linha nesta fase.
    LAUDO_DISPONIVEL: frozenset(),
    CANCELADO: frozenset(),
}

# A data que cada estado carimba ao ser alcançado. Estados sem carimbo
# próprio (aguardando_envio, em_laudo) não inventam data.
CARIMBO: dict[str, str] = {
    AGENDADO: "agendado_para",
    REALIZADO: "realizado_em",
    ENVIADO: "enviado_para_laudo_em",
    LAUDO_RECEBIDO: "laudo_recebido_em",
    LAUDO_DISPONIVEL: "disponibilizado_em",
    CANCELADO: "cancelado_em",
}


class TransicaoInvalidaError(ValueError):
    """Recusa explícita: o estado pedido não sucede o estado atual."""

    def __init__(self, atual: str, pedido: str) -> None:
        self.atual = atual
        self.pedido = pedido
        super().__init__(
            f"Transição inválida: {atual} → {pedido}. "
            f"A partir de '{atual}' só é possível: "
            f"{', '.join(sorted(TRANSICOES.get(atual, frozenset()))) or 'nenhum estado'}."
        )


def estado_valido(estado: str | None) -> bool:
    return estado in ESTADOS


def pode_transitar(atual: str, pedido: str) -> bool:
    return pedido in TRANSICOES.get(atual, frozenset())


def garantir_transicao(atual: str, pedido: str) -> None:
    """Levanta `TransicaoInvalidaError` quando a transição não é permitida."""
    if not estado_valido(pedido):
        raise TransicaoInvalidaError(atual, pedido)
    if not pode_transitar(atual, pedido):
        raise TransicaoInvalidaError(atual, pedido)


# Estados em que o exame já aconteceu — a fila de laudo olha para cá.
EXAME_JA_REALIZADO = frozenset({
    REALIZADO, AGUARDANDO_ENVIO, ENVIADO, EM_LAUDO, LAUDO_RECEBIDO, LAUDO_DISPONIVEL,
})

# Estados em que o laudo já voltou do laudador.
LAUDO_PRESENTE = frozenset({LAUDO_RECEBIDO, LAUDO_DISPONIVEL})
