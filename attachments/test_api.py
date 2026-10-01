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
    r = client.post("/auth/login", json={"user": "admin", "password": "segredo-de-teste"})
    assert r.status_code == 200
    return {"Authorization": "Bearer " + r.json()["token"]}

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
    assert "Flux" in client.get("/").text

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

def test_logout_invalida_sessao():
    tk = client.post("/auth/login", json={"user": "admin", "password": "segredo-de-teste"}).json()["token"]
    h = {"Authorization": "Bearer " + tk}
    assert client.get("/dashboard", headers=h).status_code == 200
    client.post("/auth/logout", headers=h)
    assert client.get("/dashboard", headers=h).status_code == 401
