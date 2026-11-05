"""Fronteira privada do painel de lucro, sem operações fiscais ou pacientes reais."""


def test_dashboard_requires_manager_and_keeps_empty_truth(client, auth):
    path = "/api/v1/financeiro/lucro/painel?start=2026-11-01&end=2026-11-30"
    assert client.get(path, headers=auth("operacional")).status_code == 403
    result = client.get(path, headers=auth("gestor"))
    assert result.status_code == 200, result.text
    assert result.json()["kpis"]["exames"] == 0
    assert result.json()["kpis"]["lucro"] is None
    assert result.json()["kpis"]["receita"] is None
    assert result.json()["kpis"]["custos"] is None


def test_cancelled_exam_does_not_enter_productive_count(client, auth, person):
    exam=client.post("/api/v1/espirometrias",json={"person_id":person["id"],
        "data_exame":"04/11/2026","status":"Cancelado",
        "local_atendimento":"SHOPPING DOWNTOWN"},headers=auth("operacional"))
    assert exam.status_code==201,exam.text
    result=client.get("/api/v1/financeiro/lucro/painel?start=2026-11-04&end=2026-11-04",
                      headers=auth("gestor"))
    assert result.status_code==200,result.text
    assert result.json()["exames"]==[]


def test_partner_location_without_partner_is_incomplete(client, auth, person):
    exam=client.post("/api/v1/espirometrias",json={"person_id":person["id"],
        "data_exame":"04/11/2026","status":"Realizado","local_atendimento":"MACLIN"},
        headers=auth("operacional"))
    assert exam.status_code==201,exam.text
    result=client.get("/api/v1/financeiro/lucro/painel?start=2026-11-04&end=2026-11-04",
                      headers=auth("gestor")).json()["exames"][0]
    assert result["status"]=="incompleto"
    assert "parceiro_nao_definido" in result["pendencias"]


def test_rules_are_versioned_and_admin_only(client, auth):
    path = "/api/v1/financeiro/lucro/regras"
    body = {"key":"material","effective_from":"2026-11-04","value":"10.00","reason":"Custo inicial aprovado"}
    assert client.post(path, json=body, headers=auth("gestor")).status_code == 403
    made = client.post(path, json=body, headers=auth("admin"))
    assert made.status_code == 201, made.text
    newer = client.post(path, json={**body,"value":"11.00","reason":"Nova versão no mesmo dia"}, headers=auth("admin"))
    assert newer.status_code == 201, newer.text
    listed = client.get(path, headers=auth("gestor")).json()
    assert len(listed["versoes"]) == 2
    assert listed["versoes"][0]["vigencia"] == "2026-11-04"


def test_session_requires_hours_and_one_membership(client, auth, person):
    exam_path = "/api/v1/espirometrias"
    ids=[]
    for _ in range(2):
        exam = client.post(exam_path, json={"person_id":person["id"],"data_exame":"04/11/2026",
            "status":"Realizado","local_atendimento":"SHOPPING DOWNTOWN"}, headers=auth("operacional"))
        assert exam.status_code == 201, exam.text
        ids.append(exam.json()["id"])
    path = "/api/v1/financeiro/lucro/sessoes"
    body={"place":"downtown","service_date":"2026-11-04","exam_ids":ids,"status":"confirmado"}
    assert client.post(path,json=body,headers=auth("gestor")).status_code == 403
    assert client.post(path,json=body,headers=auth("admin")).status_code == 422
    body["billable_hours"]="1.00"
    made=client.post(path,json=body,headers=auth("admin"))
    assert made.status_code == 201,made.text
    assert len(made.json()["exames"]) == 2
    assert client.post(path,json=body,headers=auth("admin")).status_code == 409
    listed=client.get(path+"?start=2026-11-01&end=2026-11-30",headers=auth("gestor"))
    assert listed.status_code == 200
    assert listed.json()["sessoes"][0]["horas_faturaveis"] == "1.00"


def test_partner_revenue_basis_requires_admin_confirmation(client, auth, person):
    partner=client.post("/api/v1/parceiros",json={"nome":"MACLIN","status":"ativa"},
                        headers=auth("operacional")).json()
    exam=client.post("/api/v1/espirometrias",json={"person_id":person["id"],
        "data_exame":"04/11/2026","status":"Realizado",
        "local_atendimento":"MACLIN","partner_id":partner["id"]},
        headers=auth("operacional")).json()
    entry=client.post("/api/v1/lancamentos",json={"tipo":"receita","categoria":"Espirometria",
        "valor":"200.00","spirometry_exam_id":exam["id"],"status":"Recebido",
        "forma_pagamento":"Pix"},headers=auth("gestor"))
    assert entry.status_code==201,entry.text
    rule=client.post("/api/v1/financeiro/lucro/regras",json={"key":"maclin.partner_percent",
        "effective_from":"2026-11-04","value":"30.00","reason":"Contrato sintético"},
        headers=auth("admin"))
    assert rule.status_code==201,rule.text
    path="/api/v1/financeiro/lucro/painel?start=2026-11-04&end=2026-11-04"
    before=client.get(path,headers=auth("gestor")).json()["exames"][0]
    assert "base_receita_parceiro_nao_confirmada" in before["pendencias"]
    confirm={"exam_id":exam["id"],"basis":"bruto","evidence_note":"Contrato sintético conferido"}
    endpoint="/api/v1/financeiro/lucro/bases-receita"
    assert client.post(endpoint,json=confirm,headers=auth("gestor")).status_code==403
    done=client.post(endpoint,json=confirm,headers=auth("admin"))
    assert done.status_code==201,done.text
    after=client.get(path,headers=auth("gestor")).json()["exames"][0]
    assert "base_receita_parceiro_nao_confirmada" not in after["pendencias"]
    assert after["fonte_repasse_parceiro"]=="regra_percentual"
