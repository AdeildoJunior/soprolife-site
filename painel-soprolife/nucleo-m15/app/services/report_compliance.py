"""M25.15 — prontidão do laudo eletrônico para uso real (CFM 2.381/2024).

Este módulo NÃO gera nada e NÃO conserta nada: ele olha para o conteúdo que
já foi montado para o PDF e responde uma pergunta só — *este documento reúne
o que a Resolução CFM 2.381/2024 exige de um documento médico eletrônico?*

A razão de existir é a regra que a missão M25.15 fixou: nenhum campo pode
ser inventado. Sem um lugar que enumere os requisitos e diga qual falta, a
alternativa prática seria preencher lacuna com texto plausível ("não
informado" onde deveria haver CPF, um endereço institucional genérico onde
falta o da unidade). Uma lista explícita de pendências torna a falta
visível em vez de disfarçada.

Cada requisito é avaliado a partir do que EXISTE no conteúdo, e três coisas
são deliberadas:

* ``blocking`` separa o que impede a ENTREGA ELETRÔNICA OFICIAL do que é
  desejável. Só bloqueia o que a resolução exige de todo documento;
* requisitos condicionais ("quando houver") ficam satisfeitos quando o dado
  não existe no caso — RQE de quem não tem especialidade registrada não é
  pendência;
* o CPF do paciente é caso à parte e está documentado em
  ``PENDENCIA_CPF``: o cadastro do núcleo não possui o campo, então a
  ausência não é um dado faltando no registro, é um dado que o sistema
  ainda não sabe guardar. Inventá-lo seria o pior desfecho possível.
"""

from __future__ import annotations

from dataclasses import dataclass

from .report_native_pdf import (
    NativeReportContent,
    SIGNATURE_KIND_QUALIFIED_ICP,
)

# Conclusão institucional exigida pela missão M25.15, seção 10.
VEREDITO_PRONTO = "PRONTO PARA PRODUÇÃO OFICIAL"
VEREDITO_AGUARDANDO = (
    "OPERACIONAL PRONTO — ENTREGA ELETRÔNICA OFICIAL AGUARDA ASSINATURA "
    "QUALIFICADA"
)

# O núcleo M15 não possui campo de CPF em `people` (nem coluna, nem
# migration, nem entrada de formulário). Enquanto isso for verdade, o laudo
# não pode imprimir CPF de forma alguma, e esta constante é o registro
# explícito da pendência para o relatório e para a próxima missão.
PENDENCIA_CPF = (
    "O cadastro de pessoas não possui campo de CPF. Enquanto a coluna não "
    "existir, nenhum laudo pode imprimir CPF, e nada deve ser preenchido no "
    "lugar dele."
)


@dataclass(frozen=True)
class Requisito:
    """Um item da CFM 2.381/2024 conferido contra o documento montado."""

    chave: str
    exigencia: str
    atendido: bool
    bloqueia_entrega_oficial: bool
    detalhe: str

    def as_payload(self) -> dict:
        return {
            "chave": self.chave,
            "exigencia": self.exigencia,
            "atendido": self.atendido,
            "bloqueia_entrega_oficial": self.bloqueia_entrega_oficial,
            "detalhe": self.detalhe,
        }


def _preenchido(valor) -> bool:
    return bool(valor is not None and str(valor).strip())


def avaliar_cfm_2381(content: NativeReportContent) -> list[Requisito]:
    """Confere um conteúdo de laudo já montado contra a resolução.

    Recebe o MESMO ``NativeReportContent`` que vai para o gerador de PDF —
    não o banco. É o que garante que a conferência fale do documento que
    será realmente impresso, e não de um estado do banco que o gerador
    poderia interpretar de outro jeito.
    """

    paciente = content.patient
    exame = content.exam
    medico = content.physician
    local = content.location
    qualificada = content.signature_kind == SIGNATURE_KIND_QUALIFIED_ICP

    requisitos = [
        Requisito(
            chave="identificacao_medico",
            exigencia="Identificação do médico responsável",
            atendido=_preenchido(medico.professional_name),
            bloqueia_entrega_oficial=True,
            detalhe=medico.professional_name or "ausente",
        ),
        Requisito(
            chave="crm_uf",
            exigencia="Número de inscrição no CRM e UF",
            atendido=(
                _preenchido(medico.crm_display)
                and _preenchido(medico.crm_state)
            ),
            bloqueia_entrega_oficial=True,
            detalhe=(
                f"CRM-{medico.crm_state} {medico.crm_display}"
                if _preenchido(medico.crm_display)
                else "ausente"
            ),
        ),
        Requisito(
            chave="rqe",
            exigencia="RQE quando o médico possui especialidade registrada",
            # Condicional: sem especialidade registrada não há RQE a exigir.
            atendido=(
                _preenchido(medico.rqe)
                or not _preenchido(medico.specialty)
            ),
            bloqueia_entrega_oficial=False,
            detalhe=(
                f"RQE {medico.rqe}"
                if _preenchido(medico.rqe)
                else "sem especialidade registrada — RQE não exigível"
            ),
        ),
        Requisito(
            chave="identificacao_paciente",
            exigencia="Identificação do paciente",
            atendido=_preenchido(paciente.full_name),
            bloqueia_entrega_oficial=True,
            detalhe=(
                "nome e registro impressos"
                if _preenchido(paciente.full_name)
                else "ausente"
            ),
        ),
        Requisito(
            chave="cpf_paciente",
            exigencia="CPF do paciente, quando houver",
            # NUNCA atendido hoje, e de propósito: declarar atendido porque
            # "não há CPF cadastrado" transformaria uma limitação do sistema
            # em conformidade aparente.
            atendido=False,
            bloqueia_entrega_oficial=True,
            detalhe=PENDENCIA_CPF,
        ),
        Requisito(
            chave="data_emissao",
            exigencia="Data de emissão do documento",
            atendido=content.issued_at_local is not None,
            bloqueia_entrega_oficial=True,
            detalhe=(
                content.issued_at_local.isoformat()
                if content.issued_at_local
                else "ausente"
            ),
        ),
        Requisito(
            chave="data_realizacao_exame",
            exigencia="Data de realização do exame",
            atendido=exame.exam_date is not None,
            bloqueia_entrega_oficial=True,
            detalhe=(
                exame.exam_date.isoformat()
                if exame.exam_date
                else "ausente"
            ),
        ),
        Requisito(
            chave="endereco_profissional",
            exigencia="Endereço profissional do local de atendimento",
            atendido=_preenchido(local.address_line),
            bloqueia_entrega_oficial=True,
            detalhe=(
                local.address_line
                or "a unidade do exame não tem endereço cadastrado"
            ),
        ),
        Requisito(
            chave="contato_profissional",
            exigencia="Contato profissional do local de atendimento",
            atendido=_preenchido(local.contact_line),
            bloqueia_entrega_oficial=True,
            detalhe=(
                local.contact_line
                or "a unidade do exame não tem contato cadastrado"
            ),
        ),
        Requisito(
            chave="assinatura_qualificada",
            exigencia=(
                "Assinatura eletrônica qualificada (ICP-Brasil) do médico"
            ),
            atendido=qualificada,
            bloqueia_entrega_oficial=True,
            detalhe=(
                "assinatura qualificada comprovada"
                if qualificada
                else (
                    "documento liberado institucionalmente; nenhuma prova "
                    "criptográfica de assinatura qualificada existe"
                )
            ),
        ),
    ]
    return requisitos


def pendencias_bloqueantes(requisitos: list[Requisito]) -> list[Requisito]:
    return [
        item
        for item in requisitos
        if item.bloqueia_entrega_oficial and not item.atendido
    ]


def veredito(requisitos: list[Requisito]) -> str:
    """Uma das duas conclusões possíveis — nunca uma terceira formulação.

    Só devolve ``VEREDITO_PRONTO`` quando NENHUM requisito bloqueante está
    pendente. Qualquer pendência, inclusive a assinatura qualificada,
    resulta na conclusão que mantém a entrega oficial suspensa.
    """

    return (
        VEREDITO_PRONTO
        if not pendencias_bloqueantes(requisitos)
        else VEREDITO_AGUARDANDO
    )


def relatorio_conformidade(content: NativeReportContent) -> dict:
    """Payload de diagnóstico: requisitos, pendências e veredito."""

    requisitos = avaliar_cfm_2381(content)
    pendentes = pendencias_bloqueantes(requisitos)
    return {
        "norma": "Resolução CFM 2.381/2024",
        "requisitos": [item.as_payload() for item in requisitos],
        "pendencias_bloqueantes": [item.chave for item in pendentes],
        "veredito": veredito(requisitos),
    }
