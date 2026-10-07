import base64
import json
import sqlite3
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import main, payroll as hr


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'DB', str(tmp_path / 'business.db'))
    monkeypatch.setattr(main, 'SUPABASE_DATABASE_URL', '')
    with main.db() as c:
        c.executescript(main.SCHEMA)
        for migration in main.MIGRATIONS:
            try:
                c.execute(migration)
            except sqlite3.OperationalError:
                pass
        c.execute("CREATE UNIQUE INDEX ux_transaction_source ON transactions(source_key) WHERE source_key<>''")
    client = TestClient(main.app)
    token = client.post('/auth/register', json={'email':'business@test.com','password':'business-test-password'}).json()['token']
    return client, {'Authorization':'Bearer '+token}


def employee(service, salary=6000, hired='2026-01-01'):
    client, auth = service
    eid = client.post('/employees', json={'name':'Colaborador','salary':salary,'pin':'1234'}, headers=auth).json()['id']
    assert client.put(f'/employees/{eid}/payroll-profile', json={'hired_on':hired}, headers=auth).status_code == 200
    return eid


def test_progressive_inss_irrf_and_benefits():
    assert hr.inss(1621) == 121.58
    assert hr.inss(5000) == 501.51
    assert hr.inss(100000) == hr.inss(8475.55)
    p = {**hr.PROFILE_DEFAULTS, 'hired_on':'2026-01-01','vt_amount':150,'va_amount':500,'va_discount':100}
    e = {'id':1,'name':'A','role':'','department_id':None,'salary':5000,'benefits':0}
    result = hr.calculate(e,p,'2026-08')
    assert result['irrf'] == 0 and result['vt_discount'] == 150 and result['va_discount'] == 100
    assert result['fgts'] == 400 and result['net'] == 4248.49
    taxes = hr.irrf(6000, hr.inss(6000), p, '2026-08')
    assert taxes['irrf'] == 385.10 and taxes['irrf_method'] == 'legal'
    assert hr.irrf(7350.01, hr.inss(7350.01), p, '2026-08')['irrf_reduction'] == 0
    legal = hr.irrf(6000, hr.inss(6000), {**p,'dependents':3,'alimony':500}, '2026-08')
    assert legal['irrf'] < taxes['irrf']
    with pytest.raises(ValueError): hr.tax_rules('2027-01')


def test_proportional_months_vacations_and_annual_cost():
    p = {**hr.PROFILE_DEFAULTS,'hired_on':'2026-01-17'}
    e = {'id':1,'name':'A','role':'','department_id':None,'salary':3000,'benefits':0}
    assert hr.calculate(e,p,'2026-01')['thirteenth_months'] == 1
    p['hired_on'] = '2026-01-18'
    assert hr.calculate(e,p,'2026-01')['thirteenth_months'] == 0
    p['hired_on'] = '2024-01-01'
    result = hr.calculate(e,p,'2026-01')
    assert result['vacation_cycles'][0]['overdue'] and result['vacation_cycles'][0]['amount'] == 8000
    assert result['vacation_cycles'][1]['amount'] == 4000
    annual = sum(hr.calculate(e,{**p,'hired_on':'2026-01-01'},f'2026-{month:02d}')['accrual_cost'] for month in range(1,13))
    assert annual == pytest.approx(3000*(13+1/3)*1.28, abs=.2)


def test_close_payroll_freezes_history_and_dre_does_not_double_count(service):
    client, auth = service
    eid = employee(service)
    before = client.get('/payroll?month=2026-02',headers=auth).json()['employees'][0]
    response = client.post('/payroll/close',json={'month':'2026-02'},headers=auth)
    assert response.status_code == 200
    assert client.post('/payroll/close',json={'month':'2026-02'},headers=auth).json()['already_closed']
    tx = client.get('/finance?status=open',headers=auth).json()
    assert len(tx) == 1 and tx[0]['source_key'].startswith('payroll:')
    assert client.delete(f'/finance/{tx[0]["id"]}',headers=auth).status_code == 409
    assert client.patch(f'/finance/{tx[0]["id"]}/paid',headers=auth).status_code == 200
    assert client.patch(f'/finance/{tx[0]["id"]}/paid',headers=auth).json()['already_paid']
    assert len(client.get('/finance/payments',headers=auth).json()) == 1
    client.patch(f'/employees/{eid}',json={'salary':9000},headers=auth)
    frozen = client.get('/payroll?month=2026-02',headers=auth).json()
    assert frozen['closed'] and frozen['employees'][0]['gross'] == before['gross']
    assert frozen['employees'][0]['fgts_accumulated'] == before['fgts']
    revenue = client.post('/finance',json={'kind':'receber','description':'Venda','amount':20000,'due':'2026-02-10'},headers=auth)
    assert revenue.status_code == 201
    report = client.get('/finance/dre?start=2026-02&end=2026-02',headers=auth).json()
    assert report['personnel'] == before['accrual_cost'] and report['revenue'] == 20000
    assert report['net_profit'] == round(20000-before['accrual_cost'],2)
    annual = client.get('/payroll/annual-cost?year=2026',headers=auth).json()
    assert annual['employees'][0]['actual_closed'] == before['accrual_cost']
    assert client.get('/payroll?month=2027-01',headers=auth).status_code == 422
    assert client.get('/payroll?month=invalid',headers=auth).status_code == 400
    assert client.get('/payroll/annual-cost').status_code == 401


def test_payment_files_validation_authorization_and_immutable_history(service):
    client, auth = service
    tid = client.post('/finance',json={'kind':'pagar','description':'Espaço','amount':200,'due':'2026-10-03'},headers=auth).json()['id']
    client.patch(f'/finance/{tid}/paid',headers=auth)
    payment = client.get('/finance/payments',headers=auth).json()[0]
    content = b'%PDF-1.7\nreceipt'
    payload = {'name':'recibo.pdf','mime':'application/pdf','content_base64':base64.b64encode(content).decode()}
    result = client.post(f'/finance/payments/{payment["id"]}/attachments',json=payload,headers=auth)
    assert result.status_code == 201
    path = f'/finance/payments/{payment["id"]}/attachments/{result.json()["id"]}'
    assert client.get(path).status_code == 401
    assert client.get(path,headers=auth).content == content
    assert client.get(path,headers=auth).headers['cache-control'] == 'private, no-store'
    assert client.post(f'/finance/payments/{payment["id"]}/attachments',json={**payload,'mime':'image/png'},headers=auth).status_code == 400
    assert client.post(f'/finance/payments/{payment["id"]}/attachments',json={**payload,'content_base64':'invalid!'},headers=auth).status_code == 400
    assert client.delete(f'/finance/{tid}',headers=auth).status_code == 409


def space(service, capacity=10):
    client, auth = service
    return client.post('/spaces',json={'name':'Sala','capacity':capacity,'allow_shared':True},headers=auth).json()['id']


def booking(sid, start='10:00', end='11:00', attendees=6):
    return {'space_id':sid,'title':'Evento','starts_at':f'2026-10-09T{start}:00Z','ends_at':f'2026-10-09T{end}:00Z','attendees':attendees,'exclusive':False,'compatibility':'workshop'}


def test_multi_events_peak_capacity_compatibility_and_edit(service):
    client, auth = service
    sid = space(service)
    a = client.post('/space-bookings',json=booking(sid),headers=auth)
    assert a.status_code == 201
    assert client.post('/space-bookings',json=booking(sid,'11:00','12:00'),headers=auth).status_code == 201
    assert client.post('/space-bookings',json=booking(sid,'10:00','12:00',4),headers=auth).status_code == 201
    assert client.post('/space-bookings',json=booking(sid,attendees=1),headers=auth).status_code == 409
    assert client.post('/space-bookings',json={**booking(sid,'12:00','13:00'),'compatibility':'other'},headers=auth).status_code == 201
    assert client.post('/space-bookings',json=booking(sid,'12:00','13:00',1),headers=auth).status_code == 409
    assert client.patch(f'/space-bookings/{a.json()["id"]}',json={**booking(sid),'status':'cancelled'},headers=auth).status_code == 200
    assert client.post('/space-bookings',json=booking(sid,attendees=6),headers=auth).status_code == 201
    assert client.put(f'/spaces/{sid}',json={'name':'Sala','capacity':2,'allow_shared':True},headers=auth).status_code == 409
    assert next(s for s in client.get('/spaces',headers=auth).json() if s['id']==sid)['capacity'] == 10


def test_staff_automation_conflicts_and_atomic_rescheduling(service):
    client, auth = service
    eid = employee(service)
    s1,s2 = space(service),space(service)
    for sid in (s1,s2):
        assert client.post(f'/spaces/{sid}/staff-rules',json={'employee_id':eid,'duty':'preparation','before_minutes':30},headers=auth).status_code == 201
    first = client.post('/space-bookings',json=booking(s1),headers=auth).json()['id']
    assigned = client.get(f'/space-bookings/{first}/staff',headers=auth).json()
    assert assigned[0]['starts_at'] == '2026-10-09T09:30+00:00'
    assert client.post('/space-bookings',json=booking(s2,'11:00','12:00'),headers=auth).status_code == 409
    second = client.post('/space-bookings',json=booking(s2,'11:30','12:30'),headers=auth).json()['id']
    assert client.patch(f'/space-bookings/{first}',json=booking(s1,'11:45','12:00'),headers=auth).status_code == 409
    assert client.get(f'/space-bookings/{first}/staff',headers=auth).json() == assigned
    assert client.delete(f'/space-bookings/{first}',headers=auth).status_code == 200
    assert client.get(f'/space-bookings/{first}/staff',headers=auth).json() == []
    assert client.get('/space-bookings',headers=auth).json()[0]['status'] == 'cancelled'


def test_vacation_balance_quote_and_consumption(service):
    client, auth = service
    eid = employee(service,salary=3000,hired='2024-01-01')
    payload = {'acquisition_start':'2024-01-01','starts_on':'2026-02-10','days':30}
    quote = client.post(f'/employees/{eid}/vacations/quote',json=payload,headers=auth)
    assert quote.status_code == 200 and quote.json()['gross'] == 8000 and quote.json()['payment_due'] == '2026-02-08'
    assert client.post(f'/employees/{eid}/vacations',json=payload,headers=auth).status_code == 201
    assert client.post(f'/employees/{eid}/vacations',json=payload,headers=auth).status_code == 409
    p = client.get('/payroll?month=2026-03',headers=auth).json()['employees'][0]
    assert p['vacation_cycles'][0]['remaining_days'] == 0
    assert client.put(f'/employees/{eid}/payroll-profile',json={'hired_on':'2026-10-40'},headers=auth).status_code == 422
