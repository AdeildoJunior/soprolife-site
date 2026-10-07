"""M26.20 — detalhamento das conclusões personalizadas.

Dois eixos: a regra de agrupamento é conservadora e literal, e o payload não
duplica nem vaza. Os textos usados aqui reproduzem as REDAÇÕES reais
encontradas na auditoria de 07/10/2026 (sem nenhum dado de paciente): é o
único jeito de provar que a regra cobre o que a médica escreve de verdade.
"""
import importlib.util
from pathlib import Path

import pytest

from app.services import custom_conclusion_groups as groups


def _module(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


native = _module('test_m25_2_native_report')
corrections = _module('test_m26_13_laudos_efetivos_producao')
case = native.case
reports_enabled = native.reports_enabled
URL = '/api/v1/laudos/estatisticas'

# As oito redações reais dos laudos personalizados vigentes em produção.
REDACOES_REAIS = [
    ('Redução CVF e vef1 isolados Prova bd negativa Se possível, complementar com volumes pulmonares', 'reducao_cvf_vef1'),
    ('Redução de CVF e VEF1 isolados.', 'reducao_cvf_vef1'),
    ('Redução de CVF e VEF1 isolados. Sugerido complementar com volumes pulmonares. Prova broncodilatadora negativa', 'reducao_cvf_vef1'),
    ('Redução de CVF e Vef1 isolados . Sugiro complementar com volumes pulmonares,', 'reducao_cvf_vef1'),
    ('Redução de vef1 e Cvf isolado', 'reducao_cvf_vef1'),
    ('Redução isolada de VEF1 e CVF Prova broncodilatadora negativa Sugiro complementar com medidas de volumes pulmonares', 'reducao_cvf_vef1'),
    ('Redução isolada de vef1 e cvf Sugiro realização de volumes pulmonares', 'reducao_cvf_vef1'),
    ('Redução discreta de CVF', 'reducao_cvf'),
]


@pytest.mark.parametrize('texto,esperado', REDACOES_REAIS)
def test_redacoes_reais_caem_na_categoria_certa(texto, esperado):
    assert groups.classify(texto) == esperado


def test_distribuicao_das_redacoes_reais_bate_com_a_auditoria():
    from collections import Counter
    contagem = Counter(groups.classify(texto) for texto, _ in REDACOES_REAIS)
    assert contagem['reducao_cvf_vef1'] == 7
    assert contagem['reducao_cvf'] == 1
    assert contagem['reducao_vef1'] == 0
    assert contagem[groups.NOT_CLASSIFIABLE] == 0


@pytest.mark.parametrize('texto', [
    None, '', '   ',
    # Sem termo de redução: nenhuma categoria, por mais sugestivo que pareça.
    'CVF e VEF1 abaixo do previsto',
    'CVF 62% do previsto, VEF1 58% do previsto',
    'Alteração de CVF e VEF1',
    'Comprometimento de CVF',
    'Exame tecnicamente insatisfatório, paciente não colaborou',
    'Sugiro complementar com volumes pulmonares',
    'Prova broncodilatadora negativa',
    # Negação derruba a classificação inteira.
    'Sem redução de CVF e VEF1',
    'Ausência de redução de CVF',
    'Não houve redução de VEF1 após broncodilatador',
    'Redução de CVF; sem redução de VEF1',
    # Redução sem nomear parâmetro algum.
    'Redução importante dos fluxos',
])
def test_ambiguo_fica_em_nao_classificavel(texto):
    assert groups.classify(texto) == groups.NOT_CLASSIFIABLE


def test_nao_inventa_disturbio_nem_padrao():
    """Texto livre NUNCA produz uma categoria de distúrbio ou padrão."""
    proibidas = ('obstrutiv', 'restritiv', 'mist', 'inespecific', 'distúrbio', 'padrão')
    rotulos = ' '.join(g.rotulo.lower() for g in groups.GROUPS)
    for termo in proibidas:
        assert termo not in rotulos, f'rótulo clínico inferido: {termo}'
    # E o classificador não reage a essas palavras criando categoria nova.
    for texto in ('Distúrbio ventilatório obstrutivo leve',
                  'Padrão sugestivo de restrição',
                  'Distúrbio obstrutivo com redução de VEF1 e CVF'):
        assert groups.classify(texto) in {g.chave for g in groups.GROUPS}
    # O último tem redução + os dois parâmetros: cai na categoria LITERAL,
    # e o rótulo dela não afirma distúrbio nenhum.
    chave = groups.classify('Distúrbio obstrutivo com redução de VEF1 e CVF')
    assert chave == 'reducao_cvf_vef1'
    assert 'obstrut' not in groups.GROUPS_BY_KEY[chave].rotulo.lower()


def test_variacoes_de_grafia_nao_criam_categoria_nova():
    for texto in ('REDUCAO DE CVF E VEF1', 'redução de cvf e vef 1',
                  'Reduzida  a  CVF  e  o  VEF-1', 'Diminuição de CVF e VEF1'):
        assert groups.classify(texto) == 'reducao_cvf_vef1'


def test_token_inteiro_evita_falso_positivo():
    """`cvf`/`vef1` só contam como termo inteiro."""
    assert groups.classify('Redução de PCVFX e VEF12') == groups.NOT_CLASSIFIABLE
    assert groups.classify('Redução de CVF') == 'reducao_cvf'


def test_uma_categoria_por_laudo():
    """Nenhum texto cai em dois baldes: a classificação é exclusiva."""
    for texto, _ in REDACOES_REAIS:
        chave = groups.classify(texto)
        assert sum(1 for g in groups.GROUPS if g.chave == chave) == 1


def test_payload_reconcilia_e_nao_leva_texto():
    from collections import Counter
    contagem = Counter(groups.classify(texto) for texto, _ in REDACOES_REAIS)
    payload = groups.payload(contagem, total=8)
    assert payload['total'] == 8
    assert sum(c['quantidade'] for c in payload['categorias']) == 8
    assert {c['chave'] for c in payload['categorias']} == {g.chave for g in groups.GROUPS}
    assert all(set(c) == {'chave', 'rotulo', 'descricao', 'criterio', 'quantidade', 'percentual'}
               for c in payload['categorias'])
    por_chave = {c['chave']: c for c in payload['categorias']}
    assert por_chave['reducao_cvf_vef1']['percentual'] == 87.5
    assert por_chave['reducao_cvf']['percentual'] == 12.5
    # Nenhuma redação da médica aparece no payload.
    serializado = str(payload).lower()
    for texto, _ in REDACOES_REAIS:
        assert texto.lower()[:25] not in serializado


def test_payload_vazio_sem_divisao_por_zero():
    from collections import Counter
    payload = groups.payload(Counter(), total=0)
    assert payload['total'] == 0
    assert all(c['quantidade'] == 0 and c['percentual'] == 0.0 for c in payload['categorias'])


# ------------------------------------------------------------------ endpoint


def _get(client, headers, **params):
    result = client.get(URL, headers=headers, params=params)
    assert result.status_code == 200, result.text
    return result.json()


def _detalhe(data):
    return {c['chave']: c['quantidade'] for c in data['personalizados']['categorias']}


def _liberar_personalizado(client, case, texto, *, document=None):
    """Libera um laudo com conclusão PERSONALIZADO pela API real.

    O `_liberar` de M26.13 não repassa o texto livre; aqui ele é obrigatório,
    porque é justamente o que a classificação lê.
    """
    alvo = {**case, 'document': document} if document else case
    previa = native._preview(client, alvo, conclusion_code='PERSONALIZADO',
                             conclusion_custom_text=texto,
                             bronchodilator_code='BD_NAO_REALIZADO').json()
    liberado = native._release(client, alvo, previa)
    assert liberado.status_code == 200, liberado.text
    return liberado.json()


def test_endpoint_detalha_personalizado_sem_vazar_texto(client, auth, case):
    # O marcador existe só nesta redação: se vazar, aparece na resposta.
    texto = ('Redução de CVF e VEF1 isolados. Sugiro complementar com volumes '
             'pulmonares. Marcador sintetico ZQX7K.')
    _liberar_personalizado(client, case, texto)
    data = _get(client, auth('admin'))
    assert data['totais']['laudos'] == 1
    assert next(x['quantidade'] for x in data['resultados'] if x['chave'] == 'personalizado') == 1
    assert data['personalizados']['total'] == 1
    assert _detalhe(data)['reducao_cvf_vef1'] == 1
    assert _detalhe(data)[groups.NOT_CLASSIFIABLE] == 0
    # O texto escrito pela médica não chega ao navegador em nenhuma forma.
    bruto = str(data)
    # "volumes pulmonares" consta do aviso FIXO de critério ignorado, que é
    # texto nosso — a prova de não vazamento usa o marcador e a redação.
    for fragmento in ('ZQX7K', 'Sugiro complementar', 'isolados', texto,
                      'conclusion_text', 'interpretation_text'):
        assert fragmento not in bruto


def test_endpoint_total_do_detalhe_bate_com_a_fatia_personalizado(client, auth, case):
    _liberar_personalizado(client, case, 'Exame com achado não descrito no catálogo')
    data = _get(client, auth('admin'))
    fatia = next(x['quantidade'] for x in data['resultados'] if x['chave'] == 'personalizado')
    assert data['personalizados']['total'] == fatia == 1
    assert sum(c['quantidade'] for c in data['personalizados']['categorias']) == fatia
    # Sem correspondência explícita, o laudo fica no bucket residual.
    assert _detalhe(data)[groups.NOT_CLASSIFIABLE] == 1


def test_corretiva_nao_duplica_o_detalhe(client, auth, case):
    _liberar_personalizado(client, case, 'Redução de CVF e VEF1 isolados.')
    assert _get(client, auth('admin'))['personalizados']['total'] == 1
    corrigido = corrections._abrir_corretiva(client, case)
    # Enquanto a corretiva não é concluída, nenhum resultado publicado conta.
    data = _get(client, auth('admin'))
    assert data['personalizados']['total'] == 0
    _liberar_personalizado(client, case, 'Redução discreta de CVF', document=corrigido)
    data = _get(client, auth('admin'))
    assert data['totais']['laudos'] == 1
    assert data['personalizados']['total'] == 1
    detalhe = _detalhe(data)
    assert detalhe['reducao_cvf'] == 1
    assert detalhe['reducao_cvf_vef1'] == 0


def test_catalogo_nao_entra_no_detalhe(client, auth, case):
    """Conclusão de catálogo não vira laudo personalizado."""
    corrections._liberar(client, case, conclusion_code='DVO_MODERADO')
    data = _get(client, auth('admin'))
    assert data['personalizados']['total'] == 0
    assert all(c['quantidade'] == 0 for c in data['personalizados']['categorias'])


def test_detalhe_respeita_filtro_de_periodo(client, auth, case, db):
    from datetime import date
    from app.models import SpirometryExam
    _liberar_personalizado(client, case, 'Redução de CVF e VEF1 isolados.')
    exam = db.get(SpirometryExam, case['exam']['id'])
    exam.data_exame = date(2026, 7, 15)
    exam.data_exame_precisao = 'dia'
    db.commit()
    assert _get(client, auth('admin'), inicio='2026-07-01', fim='2026-07-31')['personalizados']['total'] == 1
    assert _get(client, auth('admin'), inicio='2026-08-01')['personalizados']['total'] == 0


def test_detalhe_respeita_recorte_da_medica(client, auth, case, db, person):
    outro = native._make_case(client, auth, db, person, suffix='002')
    _liberar_personalizado(client, case, 'Redução de CVF e VEF1 isolados.')
    _liberar_personalizado(client, outro, 'Redução discreta de CVF')
    assert _get(client, auth('admin'))['personalizados']['total'] == 2
    minha = _get(client, case['doctor_auth'])
    assert minha['escopo'] == 'meus_laudos'
    assert minha['personalizados']['total'] == 1
    assert _detalhe(minha)['reducao_cvf_vef1'] == 1
    assert _detalhe(minha)['reducao_cvf'] == 0


def test_detalhe_segue_o_rbac_existente(client, auth):
    for papel in ('leitura', 'operacional', 'gestor'):
        assert client.get(URL, headers=auth(papel)).status_code == 403
    assert client.get(URL).status_code == 401
