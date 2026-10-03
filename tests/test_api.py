import os, sqlite3, tempfile
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ["ADMIN_PASSWORD"] = "segredo-de-teste"

import pytest
from fastapi.testclient import TestClient
from app import main
from app.main import app

client = TestClient(app)
SEDE = {"lat": -23.55, "lng": -46.63, "radius_m": 100}

def amostra(lat=-23.5501, lng=-46.6301, acc=10):
    return {"lat": lat, "lng": lng, "accuracy_m": acc}

def bater(eid, pin="1234", samples=None, consent=True):
    return client.post("/clock", json={"employee_id": eid, "pin": pin, "consent": consent,
                                       "samples": samples or [amostra(), amostra(-23.55012, -46.63011, 8)]})

@pytest.fixture(scope="module")
def auth():
    if client.get("/auth/status").json()["setup_required"]:
        r = client.post("/auth/register", json={"email": "admin@flux.test", "password": "segredo-de-teste",
                                                "company_name": "Flux Ltda", "cnpj": "12345678000199"})
        assert r.status_code == 201
        token = r.json()["token"]
    else:
        r = client.post("/auth/login", json={"email": "admin@flux.test", "password": "segredo-de-teste"})
        assert r.status_code == 200
        token = r.json()["token"]
    return {"Authorization": "Bearer " + token}

def test_primeiro_cadastro_cria_empresa_e_login():
    assert client.get("/auth/status").json() == {"setup_required": True}
    response = client.post("/auth/register", json={"email": "admin@flux.test", "password": "segredo-de-teste",
                                                    "company_name": "Flux Ltda", "cnpj": "12345678000199"})
    assert response.status_code == 201 and response.json()["token"]
    headers = {"Authorization": "Bearer " + response.json()["token"]}
    assert client.get("/auth/status").json() == {"setup_required": False}
    assert client.get("/settings/company", headers=headers).json() == {"name": "Flux Ltda", "cnpj": "12345678000199"}
    assert client.post("/auth/register", json={"email": "intruso@flux.test", "password": "segredo-de-teste",
                                                "company_name": "Outra Ltda"}).status_code == 401

def test_criacao_de_acessos_limitada_a_empresa(auth):
    payload = {"email": "pessoa@flux.test", "password": "outra-senha-segura", "company_name": "Flux Ltda",
               "cnpj": "12345678000199"}
    assert client.post("/auth/register", json=payload).status_code == 401
    created = client.post("/auth/register", json=payload, headers=auth)
    assert created.status_code == 201 and created.json()["token"] is None
    assert client.post("/auth/register", json=payload, headers=auth).status_code == 409
    other_company = {**payload, "email": "outra@flux.test", "company_name": "Outra Ltda", "cnpj": ""}
    assert client.post("/auth/register", json=other_company, headers=auth).status_code == 409
    assert client.post("/auth/login", json={"email": payload["email"], "password": payload["password"]}).status_code == 200

def test_login_legado_com_user(auth):
    response = client.post("/auth/login", json={"user": "admin@flux.test", "password": "segredo-de-teste"})
    assert response.status_code == 200

@pytest.fixture(scope="module")
def ana(auth):
    client.put("/settings/workplace", json=SEDE, headers=auth)
    client.put("/settings/company", json={"name": "Flux Ltda", "cnpj": "12345678000199"}, headers=auth)
    return client.post("/employees", json={"name": "Ana", "cpf": "12345678901", "salary": 5000,
                                           "benefits": 500, "pin": "1234"}, headers=auth).json()["id"]

def test_rotas_exigem_login():
    assert client.get("/dashboard").status_code == 401
    assert client.get("/timesheet").status_code == 401
    assert client.post("/auth/login", json={"user": "admin", "password": "errada"}).status_code == 401

def test_front_end_e_servido():
    page = client.get("/").text
    assert "SIGI" in page and 'class="boot-screen"' in page
    assert "accountShortcut" in page and 'data-t="conta"' not in page
    assert "const formG=" in page and "Criar primeira conta da empresa" in page
    assert "Excluir remove o acesso e arquiva o cadastro" in page
    assert "Custeio mensal por setor" in page
    assert "/ai/fill-mask" not in page
    assert "Resumir com IA" not in page and "finance/department-summary" not in page

def test_ponto_tempo_real_e_comprovante(auth, ana):
    live = client.post("/clock/live", json={"employee_id": ana, "pin": "1234", "lat": -23.5501, "lng": -46.6301, "accuracy_m": 10}).json()
    assert live["inside"] and live["distance_m"] < 100
    far = client.post("/clock/live", json={"employee_id": ana, "pin": "1234", "lat": -23.60, "lng": -46.63, "accuracy_m": 10}).json()
    assert not far["inside"]
    r1 = bater(ana).json()
    assert r1["kind"] == "entrada" and r1["nsr"] == 1
    assert r1["comprovante"]["empresa"]["cnpj"] == "12345678000199" and len(r1["hash"]) == 64
    assert bater(ana).json()["kind"] == "saida"

def test_ponto_exige_consentimento(ana):
    assert bater(ana, consent=False).status_code == 400

def test_recusas_de_seguranca(ana):
    assert bater(ana, samples=[amostra(-23.60, -46.63)]).status_code == 403          # fora do raio
    assert bater(ana, samples=[amostra(acc=200)]).status_code == 403                  # GPS fraco

def test_instabilidade_real(ana):
    pontos = [amostra(-23.5500, -46.6300), amostra(-23.5512, -46.6312)]   # ~170 m entre amostras
    r = bater(ana, samples=pontos)
    assert r.status_code == 403 and "instável" in r.json()["detail"]

def test_localizacao_antiga_e_recusada(ana):
    velha = {**amostra(), "ts_ms": 1_000_000}
    assert bater(ana, samples=[velha]).status_code == 403

def test_pin_errado_bloqueia(auth):
    eid = client.post("/employees", json={"name": "Beto", "salary": 3000, "pin": "4321"}, headers=auth).json()["id"]
    codes = [bater(eid, pin="0000").status_code for _ in range(6)]
    assert codes[:5] == [401] * 5 and codes[5] == 423

def test_integridade_detecta_adulteracao(auth, ana):
    assert client.get("/time-entries/verify", headers=auth).json()["ok"] is True
    con = sqlite3.connect(main.DB)
    con.execute("UPDATE time_entries SET at='2020-01-01T00:00:00+00:00' WHERE nsr=1"); con.commit()
    res = client.get("/time-entries/verify", headers=auth).json()
    assert res["ok"] is False and res["broken_nsr"] == 1

def test_exportacao_csv(auth, ana):
    r = client.get("/time-entries/export.csv", headers=auth)
    assert r.status_code == 200 and r.text.startswith("NSR;CPF;Nome")

def test_exportacao_funcionarios_csv(auth, ana):
    response = client.get("/employees/export.csv", headers=auth)
    assert response.status_code == 200
    assert 'filename="funcionarios.csv"' in response.headers["content-disposition"]
    assert response.text.lstrip("\ufeff").startswith("ID;Nome;CPF;Cargo;Salário")
    assert "Ana" in response.text
    assert "pin_hash" not in response.text and "pin_salt" not in response.text
    assert client.get("/employees/export.csv").status_code == 401

def test_contas_de_funcionario_e_mensagens_isoladas(auth, ana):
    bob = client.post("/employees", json={"name": "Beto", "salary": 3000, "pin": "4321"}, headers=auth).json()["id"]
    assert client.post(f"/employees/{ana}/account", json={"email": "ana@flux.test", "password": "senha-segura-ana"}, headers=auth).status_code == 201
    assert client.post(f"/employees/{bob}/account", json={"email": "beto@flux.test", "password": "senha-segura-beto"}, headers=auth).status_code == 201
    assert next(e for e in client.get("/employees", headers=auth).json() if e["id"] == ana)["account_email"] == "ana@flux.test"
    assert client.get("/employees").status_code == 401
    assert client.post("/auth/employee/login", json={"email": "ana@flux.test", "password": "errada"}).status_code == 401
    ana_login = client.post("/auth/employee/login", json={"email": "ana@flux.test", "password": "senha-segura-ana"})
    bob_login = client.post("/auth/employee/login", json={"email": "beto@flux.test", "password": "senha-segura-beto"})
    assert ana_login.status_code == bob_login.status_code == 200
    ana_auth = {"Authorization": "Bearer " + ana_login.json()["token"]}
    bob_auth = {"Authorization": "Bearer " + bob_login.json()["token"]}

    assert client.get("/dashboard", headers=ana_auth).status_code == 401
    assert client.post("/auth/register", json={"email": "intruso@flux.test", "password": "senha-segura",
                                                "company_name": "Flux Ltda"}, headers=ana_auth).status_code == 401
    assert client.post("/messages", json={"scope": "team", "body": "Aviso para toda a equipe"}, headers=auth).status_code == 201
    assert client.post("/messages", json={"scope": "private", "employee_id": ana, "body": "Oi, Ana"}, headers=auth).status_code == 201

    ana_messages = client.get("/messages", headers=ana_auth).json()
    assert [m["body"] for m in ana_messages] == ["Aviso para toda a equipe", "Oi, Ana"]
    assert client.post("/messages", json={"scope": "private", "body": "Preciso conversar"}, headers=ana_auth).status_code == 201
    assert any(m["body"] == "Preciso conversar" for m in client.get(f"/messages?employee_id={ana}", headers=auth).json())

    bob_messages = client.get(f"/messages?employee_id={ana}", headers=bob_auth).json()
    assert [m["body"] for m in bob_messages] == ["Aviso para toda a equipe"]
    assert client.post("/messages", json={"scope": "private", "employee_id": ana, "body": "Conversa do Beto"}, headers=bob_auth).status_code == 201
    assert all(m.get("employee_id") != ana or m["scope"] != "private" for m in client.get(f"/messages?employee_id={ana}", headers=bob_auth).json())
    assert client.post("/messages", json={"scope": "private", "body": "sem destinatário"}, headers=auth).status_code == 400

def test_exportacao_financeira_csv(auth):
    transaction_id = client.post("/finance", json={"kind": "receber", "description": "=SUM(1,1)",
                                                   "amount": 123.45, "due": "2026-09-10"},
                                 headers=auth).json()["id"]
    try:
        response = client.get("/finance/export.csv", headers=auth)
        assert response.status_code == 200
        assert 'filename="financeiro.csv"' in response.headers["content-disposition"]
        assert response.text.lstrip("\ufeff").startswith("Tipo;Descrição")
        assert "A receber;'=SUM(1,1)" in response.text
        assert client.get("/finance/export.csv").status_code == 401
    finally:
        client.delete(f"/finance/{transaction_id}", headers=auth)

def test_horas_extras_entram_na_folha(auth, ana):
    con = sqlite3.connect(main.DB)   # 08:00 e 18:00 em Brasília = 11:00 e 21:00 UTC => 10 h, 2 h extras
    for kind, hora in (("entrada", "11:00:00"), ("saida", "21:00:00")):
        con.execute("INSERT INTO time_entries(employee_id,kind,at,lat,lng,distance_m,accepted) VALUES(?,?,?,?,?,?,1)",
                    (ana, kind, f"2026-09-10T{hora}+00:00", -23.55, -46.63, 0))
    con.commit()
    t = [e for e in client.get("/timesheet?month=2026-09", headers=auth).json()["employees"] if e["name"] == "Ana"][0]
    assert t["total_hours"] == 10 and t["overtime_hours"] == 2
    p = [e for e in client.get("/payroll?month=2026-09", headers=auth).json()["employees"] if e["name"] == "Ana"][0]
    assert p["overtime_pay"] == 68.18

def test_custeio_e_folha(auth, ana):
    client.post("/products", json={"name": "Mesa", "material": 340, "labor": 90, "overhead": 45, "price": 790}, headers=auth)
    mesa = [p for p in client.get("/products", headers=auth).json() if p["name"] == "Mesa"][0]
    assert mesa["cost"] == 475 and mesa["margin_pct"] == 39.9
    p = [e for e in client.get("/payroll?month=2026-08", headers=auth).json()["employees"] if e["name"] == "Ana"][0]
    assert p["inss"] == 550 and p["net"] == 4950 and p["company_cost"] == 6900

def test_financeiro_e_fluxo_de_caixa(auth, ana):
    client.post("/finance", json={"kind": "receber", "description": "Cliente A", "amount": 10000, "due": "2020-01-10"}, headers=auth)
    tid = client.post("/finance", json={"kind": "pagar", "description": "Aluguel", "amount": 2000, "due": "2030-01-10"}, headers=auth).json()["id"]
    s = client.get("/finance/summary", headers=auth).json()
    assert s["receber_aberto"] == 10000 and s["receber_vencido"] == 10000 and s["pagar_aberto"] == 2000
    assert s["fluxo"][0]["receber"] == 10000 and len(s["fluxo"]) == 6      # vencido cai no mês atual
    client.patch(f"/finance/{tid}/paid", headers=auth)
    assert client.get("/finance/summary", headers=auth).json()["pagar_aberto"] == 0
    assert client.post("/finance", json={"kind": "pagar", "description": "x", "amount": 1, "due": "2026-13-45"}, headers=auth).status_code == 400

def test_custos_por_setor_e_rota_ia_removida(auth):
    department_id = client.post("/departments", json={"name": "Z Operações"}, headers=auth).json()["id"]
    employee_id = client.post("/employees", json={"name": "Joana", "salary": 3000, "pin": "2468",
                                                  "department_id": department_id}, headers=auth).json()["id"]
    client.post("/finance", json={"kind": "pagar", "description": "Energia", "amount": 1200,
                                  "due": "2026-10-12", "department_id": department_id}, headers=auth)
    client.post("/finance", json={"kind": "pagar", "description": "Licença", "amount": 300,
                                  "due": "2026-10-15"}, headers=auth)
    response = client.get("/finance/department-costs?month=2026-10", headers=auth)
    assert response.status_code == 200
    data = response.json()
    operations = next(item for item in data["departments"] if item["department_id"] == department_id)
    unassigned = next(item for item in data["departments"] if item["name"] == "Sem setor")
    assert operations["paid"] == 0 and operations["open"] == 1200
    assert operations["payroll"] == 3840 and operations["total"] == 5040
    assert unassigned["open"] == 300 and data["totals"]["total"] == sum(item["total"] for item in data["departments"])
    assert client.post("/finance/department-summary", json={"month": "2026-10"}, headers=auth).status_code == 405
    assert client.post("/finance", json={"kind": "receber", "description": "Venda", "amount": 200,
                                         "due": "2026-10-12", "department_id": department_id}, headers=auth).status_code == 400
    assert client.post("/finance", json={"kind": "pagar", "description": "x", "amount": 1,
                                         "due": "2026-10-12", "department_id": 9999}, headers=auth).status_code == 404

def test_exclusao_de_funcionario_revoga_acesso_e_preserva_historico(auth):
    employee_id = client.post("/employees", json={"name": "Excluir", "salary": 2500, "pin": "2468"},
                              headers=auth).json()["id"]
    assert client.post(f"/employees/{employee_id}/account",
                       json={"email": "excluir@flux.test", "password": "senha-segura"}, headers=auth).status_code == 201
    login = client.post("/auth/employee/login",
                        json={"email": "excluir@flux.test", "password": "senha-segura"}).json()
    employee_auth = {"Authorization": "Bearer " + login["token"]}
    with main.db() as c:
        c.execute("INSERT INTO time_entries(employee_id,kind,at,accepted) VALUES(?,?,?,1)",
                  (employee_id, "entrada", "2026-10-03T09:00:00+00:00"))

    assert client.delete(f"/employees/{employee_id}").status_code == 401
    deleted = client.delete(f"/employees/{employee_id}", headers=auth)
    assert deleted.status_code == 200 and deleted.json() == {"ok": True, "archived": True}
    assert client.get("/messages", headers=employee_auth).status_code == 401
    assert client.post("/auth/employee/login",
                       json={"email": "excluir@flux.test", "password": "senha-segura"}).status_code == 401
    employee = next(e for e in client.get("/employees", headers=auth).json() if e["id"] == employee_id)
    assert employee["active"] == 0 and employee["account_email"] == ""
    with main.db() as c:
        archived = c.execute("SELECT active,pin_salt,pin_hash,account_salt,account_hash FROM employees WHERE id=?",
                             (employee_id,)).fetchone()
        history = c.execute("SELECT COUNT(*) FROM time_entries WHERE employee_id=?", (employee_id,)).fetchone()[0]
        sessions = c.execute("SELECT COUNT(*) FROM sessions WHERE role='employee' AND subject_id=?",
                             (employee_id,)).fetchone()[0]
    assert tuple(archived) == (0, None, None, None, None)
    assert history == 1 and sessions == 0
    assert client.delete(f"/employees/{employee_id}", headers=auth).status_code == 200
    assert client.delete("/employees/999999", headers=auth).status_code == 404

def test_logout_invalida_sessao(auth):
    tk = client.post("/auth/login", json={"email": "admin@flux.test", "password": "segredo-de-teste"}).json()["token"]
    h = {"Authorization": "Bearer " + tk}
    assert client.get("/dashboard", headers=h).status_code == 200
    client.post("/auth/logout", headers=h)
    assert client.get("/dashboard", headers=h).status_code == 401


def test_materia_departamentos_espacos_e_dre(auth):
    mid = client.post("/materials", json={"name": "Aço", "unit": "kg", "stock": 10, "unit_cost": 12.5}, headers=auth).json()["id"]
    mats = client.get("/materials", headers=auth).json()
    assert mats[0]["value"] == 125
    did = client.post("/departments", json={"name": "Produção", "lead": "Ana"}, headers=auth).json()["id"]
    assert client.get("/departments", headers=auth).json()[0]["name"] == "Produção"
    sid = client.post("/spaces", json={"name": "People Core", "kind": "pessoas", "admin_email": "gestor@empresa.com"}, headers=auth).json()["id"]
    assert client.get("/spaces", headers=auth).json()[0]["admin_email"] == "gestor@empresa.com"
    client.post("/cost-analyses", json={"title": "LOTE 042", "revenue": 42000, "material": 32000, "opex": 15000, "tax": 3200}, headers=auth)
    dre = client.get("/dashboard", headers=auth).json()["dre"]
    assert dre["receita"] == 42000 and dre["lucro"] == -8200
    tid = client.post("/tasks", json={"title": "LOTE 042- AGOSTO 2026", "assignee": "Admin", "due": "2026-09-30",
                                     "severity": "critico", "priority": "alta", "approval": "pendente", "progress": 0}, headers=auth).json()["id"]
    t = [x for x in client.get("/tasks", headers=auth).json() if x["id"] == tid][0]
    assert t["severity"] == "critico" and t["priority"] == "alta"
    client.patch(f"/tasks/{tid}", json={"progress": 40, "approval": "aprovado"}, headers=auth)
    iid = client.post("/integrations", json={"name": "ERP", "kind": "erp", "endpoint": "https://erp.local", "active": True}, headers=auth).json()["id"]
    assert client.get("/integrations", headers=auth).json()[0]["kind"] == "erp"
    client.delete(f"/materials/{mid}", headers=auth)
    client.delete(f"/departments/{did}", headers=auth)
    client.delete(f"/spaces/{sid}", headers=auth)
    client.delete(f"/integrations/{iid}", headers=auth)
