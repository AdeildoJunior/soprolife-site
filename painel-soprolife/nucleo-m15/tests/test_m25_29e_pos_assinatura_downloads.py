"""M25.29E — depois da assinatura: quem faz o quê, e o que o download entrega.

Dois relatos reais da operação originam estes testes.

**O primeiro é de linguagem.** A médica devolve o PDF assinado, o sistema
responde "a validação da assinatura segue pendente" e ela entende que ainda
falta *ela* certificar alguma coisa. Não falta: o estado
``recebido_validacao_pendente`` é correto e a etapa seguinte é da
administração. O que estava errado era a frase descrever o estado do sistema
em vez de dizer de quem é a próxima ação.

**O segundo é de download.** Ao rebaixar documentos na área administrativa, o
navegador entregou um arquivo chamado ``conteúdo 5.jsold``. O backend nunca
esteve errado — ele já devolvia ``application/pdf`` com ``Content-Disposition``
e ``X-Content-Type-Options: nosniff``. Errado estava o frontend, que baixava
por ``<a download href=...>``: uma âncora crua salva QUALQUER resposta como
arquivo — o 401 em JSON, o ``/offline.html`` de um service worker, um asset
velho de cache. O nome vinha do último segmento da URL (``/conteudo``).

Somava-se a isso um service worker fantasma: ``sw.js`` continuava servido na
raiz do site, com escopo ``/`` cobrindo ``/painel-soprolife/``, interceptando
todo GET e guardando tudo num cache chamado ``sl-$V`` — um placeholder nunca
substituído, que portanto jamais invalidava.

O que estes testes travam:

1. ``recebido_validacao_pendente`` é etapa ADMINISTRATIVA, e a médica lê que
   o trabalho dela terminou.
2. A nomenclatura não simula ICP-Brasil, e ``qualified_signature`` continua
   falso.
3. Os dois downloads administrativos entregam PDF de verdade — tipo, nome e
   *magic bytes* — e erro nenhum vira arquivo.
4. O service worker não intercepta mais nada.

Todos os pacientes, médicas, CRMs e PDFs são sintéticos.
"""

from __future__ import annotations

import pathlib

import pytest
from sqlalchemy import select

from app.models import ExternalSignedDocument
from app.services.download_names import SUFIXO_ASSINADO, SUFIXO_MIR

# O `--import-mode=importlib` do pytest 9 não põe `tests/` no `sys.path`, e
# um módulo de teste não enxerga o outro pelo nome. Carregamos a M25.29D pelo
# caminho: o estado de partida daqui é EXATAMENTE o de lá, e duplicar as
# fixtures deixaria as duas etapas livres para divergirem em silêncio.
def _modulo_da_m25_29d():
    import importlib.util

    caminho = pathlib.Path(__file__).with_name(
        "test_m25_29d_fluxo_conclusao_assinatura.py"
    )
    spec = importlib.util.spec_from_file_location("_m25_29d", caminho)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


_M29D = _modulo_da_m25_29d()

_assinar_por_fora = _M29D._assinar_por_fora
_caso_em_elaboracao = _M29D._caso_em_elaboracao
_concluir = _M29D._concluir
reports_enabled = _M29D.reports_enabled  # fixture autouse

PANEL_ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOW_JS = (PANEL_ROOT / "js" / "report-workflow.js").read_text()
WORKFLOW_CSS = (PANEL_ROOT / "css" / "report-workflow.css").read_text()
SITE_ROOT = PANEL_ROOT.parent
SW_JS = (SITE_ROOT / "sw.js").read_text()
INDEX_HTML = (PANEL_ROOT / "index.html").read_text()


# ------------------------------------------------------------ utilidades


def _sem_acento(texto: str) -> str:
    import unicodedata

    decomposto = unicodedata.normalize("NFKD", texto)
    return "".join(
        c for c in decomposto if not unicodedata.combining(c)
    ).upper()


def _bloco_media(consulta: str, *, ancora: str) -> str:
    """O bloco `@media` que contém `ancora`, delimitado por contagem de chaves.

    Há várias `@media` com a mesma consulta no arquivo; cortar por texto
    pegaria a errada.
    """

    inicio = 0
    while True:
        inicio = WORKFLOW_CSS.find(consulta, inicio)
        assert inicio != -1, f"bloco {consulta} com {ancora} não encontrado"
        abre = WORKFLOW_CSS.index("{", inicio)
        nivel, fim = 0, abre
        for pos in range(abre, len(WORKFLOW_CSS)):
            if WORKFLOW_CSS[pos] == "{":
                nivel += 1
            elif WORKFLOW_CSS[pos] == "}":
                nivel -= 1
                if nivel == 0:
                    fim = pos
                    break
        bloco = WORKFLOW_CSS[abre:fim]
        if ancora in bloco:
            return bloco
        inicio = fim


def _js_sem_comentarios() -> str:
    """O contrato é o CÓDIGO, não o comentário que explica o código.

    Estes testes citam as frases antigas ao explicar o incidente; sem
    remover comentários, um assert de ausência passaria a bater no próprio
    texto explicativo.
    """

    import re

    sem_bloco = re.sub(r"/\*.*?\*/", "", WORKFLOW_JS, flags=re.S)
    return re.sub(r"^\s*//.*$", "", sem_bloco, flags=re.M)


@pytest.fixture()
def caso(client, auth, db):
    return _caso_em_elaboracao(
        client, auth, db, nome_paciente="TESTE APAGAR Helena Prado"
    )


@pytest.fixture()
def assinado(client, caso):
    """Um laudo concluído, assinado por fora e devolvido — estado real."""

    _concluir(client, caso)
    baixado = client.post(
        "/api/v1/laudos/assinatura-externa/baixar",
        json={"document_ids": [caso["document_id"]]},
        headers=caso["doctor_auth"],
    )
    assert baixado.status_code == 200, baixado.text

    enviado = client.post(
        "/api/v1/laudos/assinatura-externa/enviar",
        files={
            "arquivos": (
                "TESTE APAGAR - assinado.pdf",
                _assinar_por_fora(baixado.content),
                "application/pdf",
            )
        },
        headers=caso["doctor_auth"],
    )
    assert enviado.status_code == 200, enviado.text
    revisao = enviado.json()
    assert revisao["resumo"]["identificados"] == 1
    # M25.29H — não há segundo passo. O envio já aplicou as guardas e
    # aceitou; a médica terminou o trabalho aqui.
    assert revisao["aceitos"] == 1, revisao
    caso["signed_document_id"] = revisao["identificados"][0][
        "signed_document_id"
    ]
    return caso


# =====================================================================
# 1-3. De quem é a próxima ação
# =====================================================================


def test_recebido_e_estado_administrativo_nao_tarefa_da_medica(
    client, auth, assinado, db
):
    """Item 1 — o estado existe, está correto, e NÃO foi removido.

    M25.29H — o que este teste protege continua valendo: depois do envio, a
    próxima ação NÃO é da médica. O que mudou é que também não é de ninguém
    da administração: o documento já chega pronto para entrega.
    """

    registro = db.execute(select(ExternalSignedDocument)).scalars().one()
    assert registro.status == "recebido_assinado"

    fila = client.get(
        "/api/v1/laudos/assinatura-externa/fila",
        headers=auth("operacional"),
    )
    assert fila.status_code == 200, fila.text
    alvo = [
        item for item in fila.json()["itens"]
        if item["document_id"] == assinado["document_id"]
    ]
    assert alvo, "o laudo assinado precisa aparecer na fila administrativa"
    assert alvo[0]["estado"] == "pronto_para_entrega"


def test_medica_le_que_o_trabalho_dela_terminou(assinado):
    """Item 2 — a frase que ela vê depois de enviar o arquivo assinado.

    M25.29H — a frase encurtou junto com o fluxo. "Aguardando conferência
    administrativa" saiu porque não há mais conferência administrativa a
    aguardar; o trabalho dela termina no envio, e a frase diz isso.
    """

    codigo = _js_sem_comentarios()
    assert "Seu trabalho neste exame terminou." in codigo
    assert "Aguardando conferência administrativa" not in codigo
    # E a frase que a fazia procurar uma tarefa inexistente segue fora.
    assert "validação da assinatura segue pendente" not in codigo


def test_rotulo_da_versao_diz_de_quem_e_a_pendencia():
    """A médica não pode ler "validação pendente" e achar que é dela.

    M25.29H — o rótulo perdeu a segunda metade porque a pendência inteira
    deixou de existir. Continua sem dizer "validado".
    """

    codigo = _js_sem_comentarios()
    assert 'laudo_assinado_externo_recebido: "Assinado recebido"' in codigo
    assert "aguardando conferência da SoproLife" not in codigo
    assert "Assinado validado" not in codigo


# =====================================================================
# 4-5. Nomenclatura que não simula ICP-Brasil
# =====================================================================


def test_botao_administrativo_fala_em_conferencia_nao_em_validacao():
    """Item 5 — o sistema não verifica cadeia ICP-Brasil, e não finge.

    M25.29H — o botão inteiro saiu da tela: ele registrava um testemunho
    humano que a evidência documental resolve melhor e mais cedo. O que este
    teste protege é o essencial, que não mudou: nada na tela administrativa
    afirma validação de assinatura.
    """

    codigo = _js_sem_comentarios()
    assert "Registrar conferência do PDF assinado" not in codigo
    assert "Registrar validação da assinatura" not in codigo
    assert "validado_externamente" not in codigo.replace(
        'PRONTOS_PARA_ENTREGA = ["recebido_assinado", "validado_externamente"]',
        "",
    )


def test_fila_administrativa_fala_em_conferencia():
    """M25.29H — o estado virou balcão de exceção, e o rótulo diz isso.

    O VALOR persistido continua o mesmo: documentos históricos vivem nele e
    a auditoria precisa encontrá-los pelo nome de sempre.
    """

    from app.routers.reports import FILA_ASSINADO_RECEBIDO, FILA_ROTULOS

    rotulo = FILA_ROTULOS[FILA_ASSINADO_RECEBIDO]
    assert "exceção" in rotulo.lower()
    assert "validado" not in rotulo.lower()
    assert FILA_ASSINADO_RECEBIDO == "assinado_recebido_validacao_pendente"


def test_qualified_signature_continua_falso(assinado, db):
    """Item 15 — receber um PDF assinado não é prova de assinatura qualificada."""

    registro = db.execute(select(ExternalSignedDocument)).scalars().one()
    assert getattr(registro, "qualified_signature", False) is False


# =====================================================================
# 6-11. Os downloads entregam PDF — e só PDF
# =====================================================================


@pytest.mark.parametrize(
    "rota, sufixo",
    [
        ("exame-tecnico", SUFIXO_MIR),
        ("assinado", SUFIXO_ASSINADO),
    ],
)
def test_download_administrativo_entrega_pdf_de_verdade(
    client, auth, assinado, rota, sufixo
):
    """Itens 6-10 — tipo, magic bytes, disposition e extensão."""

    resposta = client.get(
        f"/api/v1/laudos/{assinado['document_id']}/{rota}/conteudo",
        headers=auth("operacional"),
    )
    assert resposta.status_code == 200, resposta.text
    assert resposta.headers["content-type"] == "application/pdf"
    assert resposta.content[:4] == b"%PDF"

    disposicao = resposta.headers["content-disposition"]
    assert disposicao.startswith("attachment;")
    assert ".pdf" in disposicao
    # O cabeçalho traz o nome em ASCII e, ao lado, o `filename*` em UTF-8.
    # Comparar sem acento cobre os dois sem depender do encoding do header.
    assert _sem_acento(sufixo) in _sem_acento(disposicao)
    assert "filename*=UTF-8" in disposicao
    # Item 11 — nunca, em hipótese alguma.
    assert ".jsold" not in disposicao
    # E o navegador é proibido de adivinhar o tipo.
    assert resposta.headers["x-content-type-options"] == "nosniff"


def test_nenhum_download_administrativo_usa_ancora_crua():
    """Item 12 — a causa raiz do ``conteúdo 5.jsold``.

    Um ``<a download href>`` salva a resposta seja ela qual for. O painel
    tem desde a M25.18 o caminho certo — ``apiBlob`` exige
    ``application/pdf`` e transforma erro em mensagem — e a fila
    administrativa agora usa ele.
    """

    codigo = _js_sem_comentarios()
    # O caminho é montado a partir do argumento `qual`, então o que precisa
    # existir no fonte são os dois valores possíveis e o `/conteudo`.
    assert '"exame-tecnico"' in codigo
    assert '"assinado"' in codigo
    assert "/conteudo`" in codigo
    # Nenhuma âncora com `download` sobrou apontando para a API.
    assert "<a class=\"m15-btn\" download" not in codigo
    # E o helper morto que só existia para montar aquele href se foi.
    assert "function apiHref(" not in codigo
    assert "data-delivery-download-mir" in codigo
    assert "data-delivery-download-assinado" in codigo


def test_erro_de_download_vira_mensagem_e_nao_arquivo(client, auth, caso):
    """Item 12 — 404 tem que ser erro controlado, não um arquivo na pasta.

    Aqui o laudo existe, mas nenhum PDF assinado foi recebido: é exatamente
    o clique que o sócio deu na fila.
    """

    resposta = client.get(
        f"/api/v1/laudos/{caso['document_id']}/assinado/conteudo",
        headers=auth("operacional"),
    )
    assert resposta.status_code == 404
    assert resposta.headers["content-type"].startswith("application/json")
    # Nenhum byte de PDF, e nada que o navegador salvaria como anexo.
    assert resposta.content[:4] != b"%PDF"
    assert "content-disposition" not in resposta.headers


def test_mir_e_laudo_assinado_sao_arquivos_distintos(client, auth, assinado):
    """Item 17 — nunca misturar MIR e laudo."""

    mir = client.get(
        f"/api/v1/laudos/{assinado['document_id']}/exame-tecnico/conteudo",
        headers=auth("operacional"),
    )
    laudo = client.get(
        f"/api/v1/laudos/{assinado['document_id']}/assinado/conteudo",
        headers=auth("operacional"),
    )
    assert mir.content != laudo.content
    assert _sem_acento(SUFIXO_MIR) in _sem_acento(
        mir.headers["content-disposition"]
    )
    assert _sem_acento(SUFIXO_ASSINADO) in _sem_acento(
        laudo.headers["content-disposition"]
    )


# =====================================================================
# 13-14. RBAC
# =====================================================================


def test_medica_nao_acessa_os_downloads_administrativos(client, assinado):
    """Item 13 — a fila de entrega é da operação, não da médica."""

    for rota in ("exame-tecnico", "assinado"):
        resposta = client.get(
            f"/api/v1/laudos/{assinado['document_id']}/{rota}/conteudo",
            headers=assinado["doctor_auth"],
        )
        assert resposta.status_code == 403, (rota, resposta.text)


def test_medica_nao_registra_conferencia_nem_entrega(client, assinado):
    """A etapa administrativa não fica ao alcance de quem assinou."""

    signed_id = assinado["signed_document_id"]
    conferencia = client.post(
        f"/api/v1/laudos/assinatura-externa/{signed_id}/validacao-externa",
        json={"metodo": "validar_iti", "confirmacao": "Confirmo a conferência externa"},
        headers=assinado["doctor_auth"],
    )
    assert conferencia.status_code == 403, conferencia.text

    entrega = client.post(
        f"/api/v1/laudos/assinatura-externa/{signed_id}/entrega",
        json={"canal": "whatsapp"},
        headers=assinado["doctor_auth"],
    )
    assert entrega.status_code == 403, entrega.text


def test_admin_continua_acessando(client, auth, assinado):
    """Item 14 — a correção não pode ter trancado a própria operação."""

    resposta = client.get(
        "/api/v1/laudos/assinatura-externa/fila",
        headers=auth("operacional"),
    )
    assert resposta.status_code == 200, resposta.text


# =====================================================================
# 16. Nada acontece sozinho
# =====================================================================


def test_nada_e_marcado_como_entregue_automaticamente(assinado, db):
    """Item 16 — receber o assinado não entrega o documento ao paciente."""

    registro = db.execute(select(ExternalSignedDocument)).scalars().one()
    # M25.29H — "pronto para entrega" não é "entregue". O aceite automático
    # dispensa a conferência, nunca a entrega: quem entrega é uma pessoa, e
    # o registro disso continua sendo um ato explícito.
    assert registro.status == "recebido_assinado"
    assert registro.delivered_at is None
    assert getattr(registro, "entregue_em", None) is None


# =====================================================================
# 20. O service worker fantasma
# =====================================================================


def test_service_worker_nao_intercepta_mais_nada():
    """Sem handler de `fetch`, o navegador ignora o worker e vai à rede."""

    assert "addEventListener('fetch'" not in SW_JS
    assert 'addEventListener("fetch"' not in SW_JS


def test_service_worker_se_desinstala():
    """As instalações fantasmas precisam sumir sozinhas."""

    assert "registration.unregister()" in SW_JS
    assert "caches.delete" in SW_JS


def test_cache_com_placeholder_nao_substituido_desapareceu():
    """`sl-$V` nunca virava um nome novo — o cache jamais invalidava."""

    assert "sl-$V" not in SW_JS


# =====================================================================
# 18-19. Celular e desktop
# =====================================================================


def test_botoes_da_fila_tem_alvo_de_toque_no_celular():
    """Item 18 — no iPhone o download só existe por estes botões."""

    bloco = _bloco_media(
        "@media (max-width: 640px)", ancora=".report-delivery-actions .m15-btn"
    )
    assert "min-height: 48px" in bloco
    # `flex: 1 1 auto` viraria ALTURA quando a linha empilha — foi o defeito
    # que a M25.29D mediu no navegador e corrigiu na tela da médica.
    assert "flex: none" in bloco
    assert "width: 100%" in bloco


def test_fila_empilha_e_nao_depende_de_hover():
    """Item 19 — nada de ação escondida atrás de `:hover`."""

    bloco = _bloco_media(
        "@media (max-width: 640px)", ancora=".report-delivery-actions"
    )
    assert "flex-direction: column" in bloco

    codigo = _js_sem_comentarios()
    # Os dois downloads são <button> com handler de clique, não <a href>.
    assert "data-delivery-download-mir" in codigo
    assert "data-delivery-download-assinado" in codigo


def test_nenhum_dado_real_nos_testes():
    """Item 20 — só fixture sintética, sempre marcada."""

    fonte = pathlib.Path(__file__).read_text()
    assert "TESTE APAGAR" in fonte
    # Montados por concatenação de propósito: escritos por extenso, eles
    # apareceriam no arquivo e o próprio teste passaria a se acusar.
    proibidos = ("LAU-" + "000013", "ESP-" + "000028", "Ana " + "Cristina")
    for proibido in proibidos:
        assert proibido not in fonte


# =====================================================================
# A auditoria read-only da fila real
# =====================================================================


def test_script_de_auditoria_da_fila_roda_e_nao_escreve(assinado, db, capsys):
    """O script que vai rodar em produção precisa rodar AQUI primeiro.

    Um script de auditoria que quebra na VPS custa uma ida e volta humana
    com privilégio de root — e é exatamente o tipo de coisa que não se
    descobre na hora.
    """

    import importlib.util

    caminho = (
        pathlib.Path(__file__).resolve().parents[1]
        / "scripts"
        / "auditar_fila_assinados.py"
    )
    spec = importlib.util.spec_from_file_location("_auditar_fila", caminho)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)

    saida_antes = db.execute(select(ExternalSignedDocument)).scalars().all()
    estados_antes = [(item.id, item.status) for item in saida_antes]

    problemas = modulo.auditar(db)
    texto = capsys.readouterr().out

    assert problemas == 0, texto
    assert "recebido_assinado" in texto
    assert "todos íntegros e legíveis pelo backend" in texto
    assert "backend relê o PDF" in texto

    # Nada mudou de estado, que é a única promessa que importa aqui.
    depois = db.execute(select(ExternalSignedDocument)).scalars().all()
    assert [(item.id, item.status) for item in depois] == estados_antes

    # E nenhum nome de paciente vazou para a saída.
    assert "Helena" not in texto


# =====================================================================
# Lotes repetidos
# =====================================================================


def test_downloads_em_lote_tem_trava_de_reentrancia():
    """Cada clique frustrado abria um lote de auditoria novo.

    A auditoria de produção mostrou lotes nascendo com menos de um segundo
    de diferença e o mesmo número de documentos — o que nenhuma pessoa
    consegue re-selecionar nesse tempo. O `finally` reabilitava o botão a
    cada erro com a seleção preservada, e o clique seguinte virava outro
    lote.

    A trava não apaga nada: o histórico das tentativas reais continua onde
    está. Ela só impede que um segundo clique entre enquanto o primeiro
    ainda está no ar.
    """

    codigo = _js_sem_comentarios()
    assert "if (state.signatureBusy) return;" in codigo
    assert "if (state.batchBusy) return;" in codigo
    assert "if (!documentId || state.deliveryBusy) return;" in codigo


# =====================================================================
# Cache busting — o deploy tem que CHEGAR ao navegador
# =====================================================================


def test_assets_alterados_tem_cache_busting_atual():
    """Publicar não é entregar.

    O painel carrega os dois arquivos com `?v=` fixo. A M25.29D mudou o JS
    e o CSS sem mexer nesse número — então um navegador que já tinha a
    versão anterior em cache continuaria rodando o fluxo antigo, com o
    servidor já atualizado. É o pior tipo de bug: o deploy "funcionou" e a
    tela não mudou.

    Se este teste falhar depois de alterar report-workflow.js ou .css, a
    correção é subir o `?v=`, não relaxar o teste.
    """

    import re

    versoes = set(
        re.findall(r"report-workflow\.(?:js|css)\?v=(\d+)", INDEX_HTML)
    )
    assert versoes, "os assets precisam continuar versionados"
    assert versoes == {"2026082001"}, (
        "JS e CSS têm que subir juntos, na versão desta etapa: " + str(versoes)
    )
