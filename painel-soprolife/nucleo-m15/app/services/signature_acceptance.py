"""M25.29H — guardas documentais do aceite automático do PDF assinado.

A regra operacional mudou: a médica assina fora, devolve, e o documento fica
pronto para entrega sem nenhum clique administrativo. O que sustenta essa
automação é este módulo — um conjunto de verificações objetivas sobre os
BYTES do arquivo devolvido, sem opinião e sem intervenção humana.

**Por que a conferência humana saiu.** Ela não acrescentava validação
criptográfica nenhuma, e a auditoria da M25.29H encontrou o custo disso: o
LAU-000015 estava marcado `validado_externamente` sendo byte a byte igual ao
PDF final e sem nenhuma estrutura de assinatura dentro. Uma pessoa confirmou
como conferido um arquivo que qualquer uma destas funções recusaria em
milissegundos. Automação aqui não é conveniência: é a checagem que de fato
existe substituindo a que só parecia existir.

**O que este módulo NÃO faz, e não pode ser lido como se fizesse.** Ele não
verifica cadeia ICP-Brasil, certificado, revogação, integridade
criptográfica do digest nem identidade do assinante. Tudo o que ele afirma é
documental: este arquivo descende do PDF final deste laudo, e traz dentro de
si uma estrutura de assinatura. `qualified_signature` continua falso em toda
a trilha, deliberadamente.

**A associação fraca que causou o incidente histórico.** Encontrar
`LAU-XXXXXX` no texto NUNCA basta: a prévia carrega o mesmo código impresso
do documento final, e foi exatamente por esse caminho que uma prévia
assinada foi pareada como se fosse o laudo (LAU-000014). Aqui, associação
forte exige o carimbo coerente com a versão final, ou o código de
verificação — que a prévia não imprime —, ou o final inteiro contido byte a
byte no arquivo devolvido.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from .signature_batch import (
    ESTADO_CONCLUIDO,
    looks_like_preview,
    read_codes_from_content,
    read_markers_from_metadata,
)

# O código de verificação como impresso no laudo concluído: alfabeto sem
# caracteres ambíguos, doze posições. A prévia imprime "—" nesse lugar.
VALIDATION_CODE_RE = re.compile(r"\b[ABCDEFGHJKMNPQRSTUVWXYZ23456789]{12}\b")

# Valores que ocupam o lugar do código de verificação sem serem um código.
_NAO_E_CODIGO = {"", "-", "—", "--", "N/A", "NA"}

# Motivos de recusa. São códigos estáveis: entram no audit_log, no retorno da
# API e nos scripts de manutenção, e não mudam quando o texto da tela mudar.
MOTIVO_SEM_VERSAO_FINAL = "laudo_sem_versao_final"
MOTIVO_ORIGEM_DIVERGENTE = "origem_nao_e_a_versao_final"
MOTIVO_PREVIA = "documento_e_previa"
MOTIVO_SEM_ASSINATURA = "documento_sem_assinatura_externa"
MOTIVO_IDENTICO_AO_FINAL = "documento_identico_ao_final"
MOTIVO_ASSOCIACAO_FRACA = "associacao_com_o_laudo_insuficiente"

# As frases que a médica lê. Cada uma diz o que fazer, não o que falhou.
MENSAGENS = {
    MOTIVO_SEM_VERSAO_FINAL: (
        "Este laudo ainda não tem uma versão final concluída. Conclua o "
        "laudo antes de assinar."
    ),
    MOTIVO_ORIGEM_DIVERGENTE: (
        "Este arquivo não corresponde à versão final atual do laudo. Baixe o "
        "PDF final e assine novamente."
    ),
    MOTIVO_PREVIA: (
        "Este arquivo corresponde a uma prévia. Baixe o PDF final e assine "
        "novamente."
    ),
    MOTIVO_SEM_ASSINATURA: (
        "Este arquivo é igual ao PDF final sem assinatura. Assine o PDF "
        "final e envie novamente."
    ),
    MOTIVO_IDENTICO_AO_FINAL: (
        "Este arquivo é igual ao PDF final sem assinatura. Assine o PDF "
        "final e envie novamente."
    ),
    MOTIVO_ASSOCIACAO_FRACA: (
        "Não foi possível confirmar que este arquivo é o PDF final deste "
        "laudo. Baixe o PDF final e assine novamente."
    ),
}


def tem_estrutura_de_assinatura(pdf: bytes) -> bool:
    """O PDF traz um campo de assinatura?

    Procura a estrutura que um assinador grava: o `/ByteRange` que delimita
    o intervalo assinado e o dicionário de assinatura. Uma IMAGEM de
    assinatura colada na página não produz nenhum dos dois — e é justamente
    o engano que esta função existe para pegar.

    Isto NÃO é validação criptográfica. Não confere cadeia, certificado,
    revogação nem digest. Diz apenas que a estrutura existe.
    """

    return b"/ByteRange" in pdf and (b"/Sig" in pdf or b"/Adbe.pkcs7" in pdf)


@dataclass(frozen=True)
class GuardasDocumentais:
    """A evidência inteira de UM arquivo devolvido, e o veredito dela.

    Os campos são a evidência bruta; `aceito` e `motivo` são derivados dela.
    Guardar os dois separados é o que permite auditar por que um documento
    passou — e não só que passou.
    """

    tem_versao_final: bool
    origem_e_a_versao_final: bool
    parece_previa: bool
    identico_ao_final: bool
    tem_estrutura_assinatura: bool
    contem_o_final: bool
    metadado_coerente: bool
    codigo_validacao_coerente: bool

    @property
    def associacao_forte(self) -> bool:
        """A ligação com o laudo é forte o bastante para dispensar humano?

        Três caminhos, qualquer um basta, e nenhum deles é o código LAU
        impresso — que a prévia também carrega:

        * o PDF final inteiro é prefixo do arquivo devolvido (assinar anexa);
        * o carimbo da SoproLife bate com a versão final, hash de origem
          incluído;
        * o código de verificação da versão final está no arquivo. A prévia
          imprime "—" nesse lugar, então ele não pode vir de uma.
        """

        return (
            self.contem_o_final
            or self.metadado_coerente
            or self.codigo_validacao_coerente
        )

    @property
    def motivo(self) -> str | None:
        """O primeiro motivo que impede o aceite. `None` quando aceita.

        A ordem é deliberada e vai do mais grave documentalmente para o mais
        técnico: um arquivo que é prévia não interessa se tem assinatura, e
        um arquivo sem assinatura nenhuma não interessa a quem ele pertence.
        """

        if not self.tem_versao_final:
            return MOTIVO_SEM_VERSAO_FINAL
        if not self.origem_e_a_versao_final:
            return MOTIVO_ORIGEM_DIVERGENTE
        if self.parece_previa:
            return MOTIVO_PREVIA
        if not self.tem_estrutura_assinatura:
            return MOTIVO_SEM_ASSINATURA
        if self.identico_ao_final:
            return MOTIVO_IDENTICO_AO_FINAL
        if not self.associacao_forte:
            return MOTIVO_ASSOCIACAO_FRACA
        return None

    @property
    def aceito(self) -> bool:
        return self.motivo is None

    @property
    def mensagem(self) -> str | None:
        motivo = self.motivo
        return MENSAGENS.get(motivo) if motivo else None

    def para_auditoria(self) -> dict:
        """A evidência como ela vai para o `audit_log` — só booleanos."""

        return {
            "origem_e_a_versao_final": self.origem_e_a_versao_final,
            "parece_previa": self.parece_previa,
            "identico_ao_final": self.identico_ao_final,
            "tem_estrutura_assinatura": self.tem_estrutura_assinatura,
            "contem_o_final": self.contem_o_final,
            "metadado_coerente": self.metadado_coerente,
            "codigo_validacao_coerente": self.codigo_validacao_coerente,
            # Dito em todo registro, porque a ausência é que se esquece.
            "qualified_signature": False,
        }


def _codigo_util(codigo: str | None) -> str | None:
    if codigo is None:
        return None
    limpo = codigo.strip().upper()
    return None if limpo in _NAO_E_CODIGO else limpo


def avaliar(
    recebido: bytes,
    *,
    final: bytes | None,
    document_code: str,
    validation_code: str | None,
    final_version_number: int | None,
    origem_e_a_versao_final: bool,
) -> GuardasDocumentais:
    """Aplica todas as guardas a UM arquivo devolvido.

    `final` são os bytes exatos da versão final corrente — o mesmo blob que
    a médica baixou para assinar. Sem ele nada pode ser comparado, e o
    resultado é uma recusa honesta em vez de um aceite otimista.
    """

    if final is None:
        return GuardasDocumentais(
            tem_versao_final=False,
            origem_e_a_versao_final=False,
            parece_previa=looks_like_preview(recebido),
            identico_ao_final=False,
            tem_estrutura_assinatura=tem_estrutura_de_assinatura(recebido),
            contem_o_final=False,
            metadado_coerente=False,
            codigo_validacao_coerente=False,
        )

    marcadores = read_markers_from_metadata(recebido)
    marcadores_do_final = read_markers_from_metadata(final)

    # O carimbo do final guarda o hash do conteúdo ANTES de ser carimbado —
    # um arquivo não consegue conter o próprio hash. Comparar o carimbo do
    # devolvido com o carimbo do final é, por isso, a comparação certa: os
    # dois citam o mesmo conteúdo de origem.
    metadado_coerente = bool(
        marcadores.report_code == document_code
        and marcadores.version_number is not None
        and marcadores.version_number == final_version_number
        and marcadores.source_sha256
        and marcadores.source_sha256 == marcadores_do_final.source_sha256
        and marcadores.document_state == ESTADO_CONCLUIDO
    )

    esperado = _codigo_util(validation_code)
    do_arquivo = _codigo_util(marcadores.validation_code)
    if do_arquivo is None:
        # Laudos concluídos antes do carimbo trazem o código só impresso.
        do_arquivo = _codigo_util(
            read_codes_from_content(
                recebido, validation_code_pattern=VALIDATION_CODE_RE
            ).validation_code
        )
    codigo_validacao_coerente = bool(
        esperado and do_arquivo and esperado == do_arquivo
    )

    return GuardasDocumentais(
        tem_versao_final=True,
        origem_e_a_versao_final=origem_e_a_versao_final,
        parece_previa=looks_like_preview(recebido),
        identico_ao_final=(
            hashlib.sha256(recebido).hexdigest()
            == hashlib.sha256(final).hexdigest()
        ),
        tem_estrutura_assinatura=tem_estrutura_de_assinatura(recebido),
        # Assinar ANEXA: o preparado continua sendo prefixo exato do
        # assinado. É a prova documental mais forte que existe sem
        # criptografia — e não depende de metadado nenhum sobreviver.
        contem_o_final=recebido.startswith(final),
        metadado_coerente=metadado_coerente,
        codigo_validacao_coerente=codigo_validacao_coerente,
    )
