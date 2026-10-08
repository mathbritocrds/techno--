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


def test_inss_requested_table_boundaries_and_cap():
    expected={0:0,1621:121.58,1621.01:121.58,2902.84:236.94,2902.85:236.94,4354.27:411.11,4354.28:411.11,8475.55:988.09,8475.56:988.09,100000:988.09}
    for gross,discount in expected.items(): assert hr.inss(gross)==discount
    rules=hr.tax_rules('2026-10')
    assert rules['inss_offsets']==[0,24.32,111.40,198.49] and rules['inss_max']==988.09


def test_fractional_gps_timestamp_and_duplicate_clock(service,monkeypatch):
    from datetime import timedelta
    client,auth=service;eid=employee(service)
    client.put('/settings/workplace',json={'lat':-23.55,'lng':-46.63,'radius_m':100},headers=auth)
    client.post(f'/employees/{eid}/account',json={'email':'clock@test.com','password':'clock-test-password'},headers=auth)
    token=client.post('/auth/employee/login',json={'email':'clock@test.com','password':'clock-test-password'}).json()['token']
    employee_auth={'Authorization':'Bearer '+token}
    sample={'lat':-23.55,'lng':-46.63,'accuracy_m':10,'ts_ms':main.utcnow().timestamp()*1000+.125}
    first=client.post('/employee/clock',json={'consent':True,'samples':[sample]},headers=employee_auth)
    assert first.status_code==200 and first.json()['kind']=='entrada'
    assert client.post('/employee/clock',json={'consent':True,'samples':[sample]},headers=employee_auth).status_code==409
    now=main.utcnow();monkeypatch.setattr(main,'utcnow',lambda:now+timedelta(seconds=31))
    last=client.post('/employee/clock',json={'consent':True,'samples':[sample]},headers=employee_auth)
    assert last.status_code==200 and last.json()['kind']=='saida'
    assert client.get('/time-entries/verify',headers=auth).json()['ok']


def test_concurrent_clock_serializes_nsr(service):
    from concurrent.futures import ThreadPoolExecutor
    client,auth=service;eid=employee(service)
    client.put('/settings/workplace',json={'lat':0,'lng':0,'radius_m':100},headers=auth)
    payload={'employee_id':eid,'pin':'1234','consent':True,'samples':[{'lat':0,'lng':0,'accuracy_m':10}]}
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses=list(pool.map(lambda _:client.post('/clock',json=payload),range(2)))
    assert sorted(r.status_code for r in responses)==[200,409]
    assert client.get('/time-entries/verify',headers=auth).json()['checked']==1


def test_dre_import_preview_atomicity_export_and_roundtrip(service):
    client,auth=service
    did=client.post('/departments',json={'name':'Produção'},headers=auth).json()['id']
    content=f'origem;referencia;ano;mes;setor_id;categoria;descricao;valor\nimport;rev1;2026;02;{did};revenue;Venda;1000,00\nimport;exp1;2026;02;{did};cost;Materiais;250,00\n'
    preview=client.post('/finance/dre/import',json={'content':content},headers=auth).json()
    assert preview['accepted']==2 and not preview['errors']
    assert client.get('/finance/dre?start=2026-02&end=2026-02',headers=auth).json()['revenue']==0
    invalid=content+'import;invalid;2026;13;;cost;Inválido;100\n'
    assert client.post('/finance/dre/import',json={'content':invalid,'preview':False},headers=auth).status_code==400
    imported=client.post('/finance/dre/import',json={'content':content,'preview':False},headers=auth).json()
    assert imported['imported']==2
    assert client.post('/finance/dre/import',json={'content':content,'preview':False},headers=auth).json()['imported']==0
    result=client.get('/finance/dre?start=2026-02&end=2026-02',headers=auth).json()
    assert result['net_profit']==750 and result['net_margin_pct']==75
    cost=next(r for r in result['breakdown'] if r['category']=='cost')
    assert cost['expense_pct']==100 and cost['revenue_pct']==25
    csv_file=client.get('/finance/dre/export.csv?start=2026-02&end=2026-02',headers=auth)
    assert csv_file.status_code==200 and 'percentual_gastos' in csv_file.text
    assert client.post('/finance/dre/import',json={'content':csv_file.text,'preview':False},headers=auth).json()['imported']==0
    filtered=client.get(f'/finance/dre?start=2026-02&end=2026-02&category=cost&department_id={did}',headers=auth).json()
    assert filtered['revenue']==1000 and len(filtered['breakdown'])==1
    assert client.post('/finance/dre/import',json={'content':content.replace('250,00','300,00'),'preview':False},headers=auth).status_code==400
    assert client.get('/finance',headers=auth).json()==[]  # Accounting import creates no payable.


def test_manager_employee_requirement_and_scoped_finance(service):
    client,auth=service
    did=client.post('/departments',json={'name':'Gestão'},headers=auth).json()['id']
    other=client.post('/departments',json={'name':'Outro'},headers=auth).json()['id']
    payload={'email':'manager@test.com','password':'manager-test-password','role':'manager','department_id':did}
    assert client.post('/auth/register',json=payload,headers=auth).status_code==400
    eid=employee(service)
    assert client.post('/auth/register',json={**payload,'employee_id':eid},headers=auth).status_code==400
    client.patch(f'/employees/{eid}',json={'department_id':did},headers=auth)
    assert client.post('/auth/register',json={**payload,'employee_id':eid},headers=auth).status_code==201
    token=client.post('/auth/login',json={'email':payload['email'],'password':payload['password']}).json()['token']
    manager={'Authorization':'Bearer '+token}
    assert client.get('/finance',headers=manager).status_code==403
    assert client.get('/finance/dre?start=2026-02&end=2026-02',headers=manager).status_code==200
    assert client.get(f'/finance/dre?start=2026-02&end=2026-02&department_id={other}',headers=manager).status_code==403
    content=f'ano;mes;setor_id;categoria;descricao;valor\n2026;02;{other};cost;Outro setor;200\n'
    assert client.post('/finance/dre/import',json={'content':content,'preview':False},headers=manager).status_code==400
    content='ano;mes;categoria;descricao;valor\n2026;02;cost;Meu setor;200\n'
    assert client.post('/finance/dre/import',json={'content':content,'preview':False},headers=manager).json()['imported']==1
    report=client.get('/finance/dre?start=2026-02&end=2026-02',headers=manager).json()
    assert report['cost']==200 and report['breakdown'][0]['department_id']==did
    assert report['net_margin_pct'] is None
    assert client.delete(f'/employees/{eid}',headers=auth).status_code==409
    assert client.get('/finance/dre/export.csv?start=2026-02&end=2026-02',headers=manager).status_code==200


def test_department_head_must_be_registered_and_active(service):
    client,auth=service;eid=employee(service)
    assert client.post('/departments',json={'name':'Setor','lead':'Pessoa não cadastrada'},headers=auth).status_code==400
    did=client.post('/departments',json={'name':'Setor','lead_employee_id':eid},headers=auth).json()['id']
    assert client.get('/departments',headers=auth).json()[0]['lead_employee_id']==eid
    assert client.delete(f'/employees/{eid}',headers=auth).status_code==409
    assert client.patch(f'/employees/{eid}',json={'active':False},headers=auth).status_code==409
    client.put(f'/departments/{did}',json={'name':'Setor','lead_employee_id':None},headers=auth)
    assert client.delete(f'/employees/{eid}',headers=auth).status_code==200
    assert client.put(f'/departments/{did}',json={'name':'Setor','lead_employee_id':eid},headers=auth).status_code==400


def test_onboarding_completion_is_durable_and_financial_page_endpoints(service):
    client,auth=service;employee(service)
    assert not client.get('/dashboard',headers=auth).json()['setup_completed']
    client.put('/settings/workplace',json={'lat':0,'lng':0},headers=auth)
    client.post('/products',json={'name':'Produto'},headers=auth)
    client.post('/materials',json={'name':'Material'},headers=auth)
    client.post('/tasks',json={'title':'Primeira tarefa'},headers=auth)
    sid=client.post('/spaces',json={'name':'Espaço'},headers=auth).json()['id']
    assert client.get('/dashboard',headers=auth).json()['setup_completed']
    client.delete(f'/spaces/{sid}',headers=auth)
    assert client.get('/dashboard',headers=auth).json()['setup_completed']
    for path in ['/finance/summary','/finance?status=open','/departments','/finance/department-costs?month=2026-10','/finance/dre?start=2026-10&end=2026-10','/finance/payments?month=2026-10']:
        assert client.get(path,headers=auth).status_code==200


def test_employee_counter_does_not_count_open_shift_twice(service,monkeypatch):
    from datetime import timedelta
    client,auth=service;eid=employee(service)
    client.post(f'/employees/{eid}/account',json={'email':'counter@test.com','password':'counter-password'},headers=auth)
    token=client.post('/auth/employee/login',json={'email':'counter@test.com','password':'counter-password'}).json()['token']
    now=main.utcnow()
    with main.db() as c:
        c.execute('INSERT INTO time_entries(employee_id,kind,at,lat,lng,distance_m,accepted) VALUES(?,?,?,?,?,?,1)',(eid,'entrada',main.iso(now-timedelta(minutes=10)),0,0,0))
    monkeypatch.setattr(main,'utcnow',lambda:now)
    summary=client.get('/employee/summary',headers={'Authorization':'Bearer '+token}).json()
    assert summary['worked_minutes']==10 and summary['completed_seconds']==0 and summary['on_clock']


def test_dre_rejects_conflicting_references_and_csv_formula_roundtrip(service):
    client,auth=service
    content='ano;mes;categoria;descricao;valor;referencia\n2026;10;cost;=SUM(1);100;external-1\n'
    conflict=content+'2026;10;cost;=SUM(1);200;external-1\n'
    assert client.post('/finance/dre/import',json={'content':conflict,'preview':False},headers=auth).status_code==400
    assert client.post('/finance/dre/import',json={'content':content,'preview':False},headers=auth).json()['imported']==1
    exported=client.get('/finance/dre/export.csv?start=2026-10&end=2026-10',headers=auth).text
    assert "'=SUM(1)" in exported
    assert client.post('/finance/dre/import',json={'content':exported,'preview':False},headers=auth).json()['imported']==0


def test_unified_sign_in_username_keeps_canonical_session(service):
    client, auth = service
    with main.db() as c:
        c.execute('UPDATE admins SET username=? WHERE "user"=?', ('Gestor Financeiro', 'business@test.com'))
    login = client.post('/auth/sign-in', json={'user':'GESTOR FINANCEIRO','password':'business-test-password'})
    assert login.status_code == 200 and login.json()['role'] == 'admin'
    session = {'Authorization':'Bearer '+login.json()['token']}
    assert client.get('/auth/account', headers=session).json()['email'] == 'business@test.com'
    for path in ['/finance', '/finance/summary', '/finance/dre?start=2026-10&end=2026-10', '/payroll?month=2026-10']:
        assert client.get(path, headers=session).status_code == 200
    assert client.get('/auth/branding').json() == {'name': ''}
    client.put('/settings/company', json={'name':'Equipe Verde','cnpj':''}, headers=auth)
    assert client.get('/auth/branding').json() == {'name':'Equipe Verde'}


def test_unified_employee_sign_in_restricts_finance_and_inactive_accounts(service):
    client, auth = service
    eid = employee(service)
    assert client.post(f'/employees/{eid}/account', json={'email':'worker@test.com','password':'worker-password'}, headers=auth).status_code == 201
    login = client.post('/auth/sign-in', json={'email':'WORKER@test.com','password':'worker-password'})
    assert login.json()['role'] == 'employee'
    worker_auth = {'Authorization':'Bearer '+login.json()['token']}
    assert client.get('/employee/payroll?month=2026-10', headers=worker_auth).status_code == 200
    assert client.post('/auth/register',json={'email':'worker@test.com','password':'another-password'},headers=auth).status_code == 409
    assert client.get('/finance/dre?start=2026-10&end=2026-10', headers=worker_auth).status_code == 403
    client.patch(f'/employees/{eid}', json={'active':False}, headers=auth)
    assert client.post('/auth/sign-in', json={'email':'worker@test.com','password':'worker-password'}).status_code == 401


def test_monthly_bonus_updates_payroll_annual_dre_and_freezes(service):
    client, auth = service
    eid = employee(service, salary=5000)
    assert client.get(f'/employees/{eid}/bonuses/2026-10', headers=auth).json()['amount'] == 0
    assert client.put(f'/employees/{eid}/bonuses/2026-10', json={'amount':1000,'note':'Meta alcançada'}, headers=auth).status_code == 200
    october = client.get('/payroll?month=2026-10', headers=auth).json()
    item = october['employees'][0]
    assert item['bonus_amount'] == 1000 and item['gross'] == 6000
    assert item['inss'] == hr.inss(6000) and item['fgts'] == 480 and item['employer_charges'] == 1200
    assert item['net'] == round(6000-item['inss']-item['irrf'], 2)
    assert client.get('/payroll?month=2026-09', headers=auth).json()['employees'][0]['bonus_amount'] == 0
    annual = client.get('/payroll/annual-cost?year=2026', headers=auth).json()
    assert annual['employees'][0]['bonuses'] == 1000
    baseline = hr.calculate({'id':eid,'name':'Colaborador','role':'','department_id':None,'salary':5000,'benefits':0}, {'hired_on':'2026-01-01'}, '2026-09')
    assert annual['total_net'] == pytest.approx(baseline['net']*11+item['net'], abs=.01)
    assert annual['total_cost'] == pytest.approx(baseline['accrual_cost']*11+item['accrual_cost'], abs=.01)
    assert client.get('/finance/dre?start=2026-10&end=2026-10',headers=auth).json()['personnel'] == 0
    assert client.put(f'/employees/{eid}/bonuses/2026-10', json={'amount':-1}, headers=auth).status_code == 422
    assert client.put(f'/employees/{eid}/bonuses/2027-01', json={'amount':1}, headers=auth).status_code == 422
    assert client.put(f'/employees/{eid}/bonuses/invalid', json={'amount':1}, headers=auth).status_code == 400
    assert client.get(f'/employees/{eid}/bonuses/2026-10').status_code == 401
    assert client.get('/employees/999999/bonuses/2026-10',headers=auth).status_code == 404
    assert client.post('/payroll/close', json={'month':'2026-10'}, headers=auth).status_code == 200
    assert client.get('/finance/dre?start=2026-10&end=2026-10',headers=auth).json()['personnel'] == item['accrual_cost']
    assert client.put(f'/employees/{eid}/bonuses/2026-10', json={'amount':2000}, headers=auth).status_code == 409
    assert client.get('/finance?status=open',headers=auth).json()[0]['amount'] == item['net']
    annual = client.get('/payroll/annual-cost?year=2026',headers=auth).json()
    assert annual['employees'][0]['net_closed'] == item['net']
    assert annual['employees'][0]['net_total'] == annual['total_net']
    assert client.get('/payroll?month=2026-10', headers=auth).json()['employees'][0]['bonus_amount'] == 1000


def test_unified_sign_in_does_not_bypass_totp_or_fallback_role(service):
    client, auth = service
    eid = employee(service)
    # Existing installations may have the same e-mail in separate legacy tables.
    with main.db() as c:
        salt = 'aabbccdd'
        c.execute('UPDATE admins SET totp_enabled=1,totp_secret=? WHERE "user"=?', ('JBSWY3DPEHPK3PXP','business@test.com'))
        c.execute('UPDATE employees SET account_email=?,account_salt=?,account_hash=? WHERE id=?', ('business@test.com',salt,main.hash_pin('worker-password',salt),eid))
    assert client.post('/auth/sign-in',json={'email':'business@test.com','password':'worker-password'}).status_code == 401
    step = client.post('/auth/sign-in',json={'email':'business@test.com','password':'business-test-password'}).json()
    assert step == {'requires_otp': True} and 'token' not in step
    assert client.post('/auth/sign-in',json={'email':'business@test.com','password':'business-test-password','totp_code':'bad'}).status_code == 401


def test_employee_creation_with_admission_and_access_is_atomic(service):
    client, auth = service
    payload = {'name':'Nova Pessoa','salary':2500,'pin':'4321','hired_on':'2026-10-01','account_email':'new@test.com','account_password':'new-password'}
    response = client.post('/employees',json=payload,headers=auth)
    assert response.status_code == 201
    eid = response.json()['id']
    assert client.get(f'/employees/{eid}/payroll-profile',headers=auth).json()['hired_on'] == '2026-10-01'
    assert client.post('/auth/sign-in',json={'email':'new@test.com','password':'new-password'}).json()['role'] == 'employee'
    before = len(client.get('/employees',headers=auth).json())
    assert client.post('/employees',json=payload,headers=auth).status_code == 409
    assert client.post('/employees',json={**payload,'account_email':'another@test.com','account_password':'short'},headers=auth).status_code == 422
    assert client.post('/employees',json={**payload,'hired_on':'invalid'},headers=auth).status_code == 422
    assert client.post('/employees',json={**payload,'department_id':99999},headers=auth).status_code == 404
    assert len(client.get('/employees',headers=auth).json()) == before


def test_employee_can_edit_own_reservation_and_cannot_edit_others(service):
    client, auth = service
    eid = employee(service)
    client.post(f'/employees/{eid}/account',json={'email':'booking@test.com','password':'booking-password'},headers=auth)
    token = client.post('/auth/sign-in',json={'email':'booking@test.com','password':'booking-password'}).json()['token']
    worker = {'Authorization':'Bearer '+token}
    assert client.put(f'/employees/{eid}/bonuses/2026-10',json={'amount':100},headers=worker).status_code == 403
    sid = client.post('/spaces',json={'name':'Sala A','capacity':10},headers=auth).json()['id']
    other_sid = client.post('/spaces',json={'name':'Sala B','capacity':10},headers=auth).json()['id']
    payload = {'space_id':sid,'title':'Reserva própria','starts_at':'2026-10-20T10:00:00Z','ends_at':'2026-10-20T11:00:00Z','attendees':5}
    own = client.post('/space-bookings',json=payload,headers=worker).json()['id']
    other = client.post('/space-bookings',json={**payload,'title':'Reserva do administrador','starts_at':'2026-10-20T12:00:00Z','ends_at':'2026-10-20T13:00:00Z'},headers=auth).json()['id']
    rows = client.get('/employee/spaces',headers=worker).json()['bookings']
    assert next(b for b in rows if b['id']==own)['can_edit']
    assert not next(b for b in rows if b['id']==other)['can_edit']
    assert client.patch(f'/space-bookings/{other}',json=payload,headers=worker).status_code == 403
    assert client.patch(f'/space-bookings/{own}',json={**payload,'space_id':other_sid,'title':'Reserva alterada','details':'Novo recurso'},headers=worker).status_code == 200
    updated = next(b for b in client.get('/employee/spaces',headers=worker).json()['bookings'] if b['id']==own)
    assert updated['space_id'] == other_sid and updated['details'] == 'Novo recurso'
