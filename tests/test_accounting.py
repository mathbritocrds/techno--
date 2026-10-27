import csv
import io
import sqlite3
from datetime import datetime, timezone

import pytest
from app import main
from tests.test_business import service, employee


def journal(client,auth,reference,lines,department=None,day='2026-10-10'):
    return client.post('/accounting/journals',headers=auth,json={'booked_on':day,'description':reference,
            'reference':reference,'department_id':department,'lines':lines})


def pair(debit,credit,amount):
    return [{'account_code':debit,'debit':str(amount)},{'account_code':credit,'credit':str(amount)}]


def test_exact_cent_ledger_subtotals_balance_and_trial(service):
    client,auth=service
    assert journal(client,auth,'capital-inicial',pair('1.1.01','3.1.01','50000')).status_code==201
    for i,(account,amount) in enumerate([('4.1.01',20000),('4.2.01',500),('4.3.01',700),('4.4.01',50)]):
        assert journal(client,auth,'receita-ref-'+str(i),pair('1.1.01',account,amount)).status_code==201
    for i,(account,amount) in enumerate([('5.1.01',1000),('5.2.01',6000),('5.3.01',3000),('5.4.01',100),('5.5.01',200),('5.6.01',400),('5.7.01',300),('5.8.01',75)]):
        assert journal(client,auth,'despesa-ref-'+str(i),pair(account,'1.1.01',amount)).status_code==201
    data=client.get('/accounting/reports?start=2026-10-01&end=2026-10-31',headers=auth).json()
    rows={r['key']:r for r in data['dre']}
    assert len(rows)==15 and sum(r['required'] for r in rows.values())==3
    assert rows['net_revenue']['amount']==19000 and rows['gross_profit']['amount']==13000
    assert rows['operating_profit']['amount']==10400 and rows['laft']['amount']==10900
    assert rows['before_tax']['amount']==10550 and rows['net_profit']['amount']==10175
    assert data['balance']['assets_cents']==data['balance']['liabilities_equity_cents']==6017500
    assert data['balance']['difference_cents']==data['trial']['difference_cents']==0
    assert data['trial']['balanced'] and sum(r['closing'] for r in data['trial']['items'])==0
    for kind in ('dre','balance','trial'):
        response=client.get(f'/accounting/export.csv?start=2026-10-01&end=2026-10-31&kind={kind}',headers=auth)
        assert response.status_code==200 and 'private, no-store'==response.headers['cache-control']
        rows=list(csv.reader(io.StringIO(response.text.lstrip('\ufeff')),delimiter=';'))
        if kind=='dre':assert len(rows)==17 and rows[-1][1]=='10175,00'


def test_unbalanced_fractional_unknown_and_duplicate_postings(service):
    client,auth=service
    lines=pair('1.1.01','3.1.01','.10')
    assert journal(client,auth,'precisao-dez-centavos',lines).status_code==201
    assert not journal(client,auth,'precisao-dez-centavos',lines).json()['created']
    assert journal(client,auth,'precisao-dez-centavos',pair('1.1.01','3.1.01','.20')).status_code==409
    assert journal(client,auth,'sem-arredondar-ilegal',pair('1.1.01','3.1.01','.001')).status_code==422
    assert journal(client,auth,'conta-inexistente',pair('9.9','3.1.01','100')).status_code==400
    bad=[{'account_code':'1.1.01','debit':'100'},{'account_code':'3.1.01','credit':'99.99'}]
    assert journal(client,auth,'nao-fecha-centavo',bad).status_code==400
    assert client.get('/accounting/journals?start=2026-10-01&end=2026-10-31',headers=auth).json()['total']==1
    with main.db() as c:
        with pytest.raises(sqlite3.IntegrityError):c.execute('UPDATE ledger_lines SET debit_cents=11 WHERE debit_cents=10')
    with main.db() as c:
        with pytest.raises(sqlite3.IntegrityError):c.execute("UPDATE ledger_journals SET description='Alterado' WHERE id=1")
    with main.db() as c:
        with pytest.raises(sqlite3.IntegrityError):c.execute("INSERT INTO ledger_journals(booked_on,description,status,source_key,payload_hash,created_at,created_by) VALUES('2026-10-01','Fraude','posted','x','x','x','x')")


def test_reversal_keeps_history_and_opening_balances(service):
    client,auth=service
    jid=journal(client,auth,'capital-setembro',pair('1.1.01','3.1.01','100'),day='2026-09-01').json()['id']
    trial=client.get('/accounting/reports?start=2026-10-01&end=2026-10-31',headers=auth).json()['trial']
    assert next(r for r in trial['items'] if r['code']=='1.1.01')['opening']==10000
    reversed=client.post(f'/accounting/journals/{jid}/reverse',json={'booked_on':'2026-10-10','reason':'Capital informado incorretamente'},headers=auth)
    assert reversed.status_code==200
    assert client.post(f'/accounting/journals/{jid}/reverse',json={'booked_on':'2026-10-10','reason':'Tentar novamente'},headers=auth).status_code==409
    report=client.get('/accounting/reports?start=2026-10-01&end=2026-10-31',headers=auth).json()
    assert report['balance']['assets_cents']==0 and report['balance']['balanced']
    assert client.get('/accounting/journals?start=2026-09-01&end=2026-10-31&page_size=1',headers=auth).json()['total']==2


def test_operational_sync_is_idempotent_and_deleted_ids_do_not_collide(service):
    client,auth=service
    def sale():return client.post('/finance',headers=auth,json={'kind':'receber','description':'Venda','amount':100,'due':'2026-10-10'}).json()['id']
    first=sale();assert client.delete('/finance/'+str(first),headers=auth).status_code==200
    sale()
    sync=client.post('/accounting/integrate',headers=auth,json={'through':'2026-10-31'})
    assert sync.status_code==200 and sync.json()['created']==0
    data=client.get('/accounting/reports?start=2026-10-01&end=2026-10-31',headers=auth).json()
    assert data['dre'][0]['amount']==100 and data['balance']['balanced']
    exported=client.get('/finance/dre/statement.csv?start=2026-10&end=2026-10',headers=auth)
    assert exported.status_code==200 and len(list(csv.reader(io.StringIO(exported.text),delimiter=';')))==17


def test_legacy_paid_source_integrates_once_and_keeps_cash(service):
    client,auth=service
    with main.db() as c:
        c.execute("INSERT INTO transactions(kind,description,amount,due,paid,paid_at,category,competence) VALUES('receber','Venda legada',100,'2026-10-10',1,'2026-10-10T12:00:00+00:00','revenue','2026-10')")
    first=client.post('/accounting/integrate',headers=auth,json={'through':'2026-10-31'})
    assert first.status_code==200 and first.json()['created']==2
    assert client.post('/accounting/integrate',headers=auth,json={'through':'2026-10-31'}).json()['created']==0
    data=client.get('/accounting/reports?start=2026-10-01&end=2026-10-31',headers=auth).json()
    assert data['dre'][0]['amount']==100 and data['balance']['assets_cents']==10000
    assert next(r for r in data['trial']['items'] if r['code']=='1.1.02')['closing']==0


def test_material_audit_time_adjustment_preserves_hash_and_changes_open_timesheet(service,monkeypatch):
    client,auth=service;eid=employee(service)
    material=client.post('/materials',headers=auth,json={'name':'Aço','unit_cost':10}).json()['id']
    assert client.put(f'/materials/{material}',headers=auth,json={'name':'Aço','unit_cost':12}).status_code==200
    now=datetime(2026,10,20,18,tzinfo=timezone.utc);monkeypatch.setattr(main,'utcnow',lambda:now)
    client.put('/settings/workplace',headers=auth,json={'lat':0,'lng':0,'radius_m':100})
    for stamp in ['2026-10-20T12:00:00+00:00','2026-10-20T16:00:00+00:00']:
        monkeypatch.setattr(main,'utcnow',lambda value=stamp:datetime.fromisoformat(value))
        assert client.post('/clock',json={'employee_id':eid,'pin':'1234','consent':True,'samples':[{'lat':0,'lng':0,'accuracy_m':10}]}).status_code==200
    monkeypatch.setattr(main,'utcnow',lambda:now)
    with main.db() as c:
        original_row=c.execute("SELECT id,hash FROM time_entries WHERE kind='saida'").fetchone()
        tid=original_row['id'];original_hash=original_row['hash']
    correction=client.post(f'/time-entries/{tid}/adjustments',headers=auth,json={'corrected_at':'2026-10-20T17:00:00+00:00','reason':'Saída registrada incorretamente'})
    assert correction.status_code==201
    assert client.get('/timesheet?month=2026-10',headers=auth).json()['employees'][0]['total_hours']==5
    with main.db() as c:
        original=c.execute('SELECT at,hash FROM time_entries WHERE id=?',(tid,)).fetchone()
        assert original['at']=='2026-10-20T16:00:00+00:00' and original['hash']==original_hash
        assert c.execute('SELECT COUNT(*) FROM audit_events').fetchone()[0]==2
        with pytest.raises(sqlite3.IntegrityError):c.execute("DELETE FROM time_entry_adjustments")
    assert client.get('/time-entries/verify',headers=auth).json()=={'ok':True,'checked':2}
    audit=client.get('/accounting/audit?page_size=1&page=2',headers=auth).json()
    assert audit['total']==2 and len(audit['items'])==1
    assert '10.0' in audit['items'][0]['before_json'] and '12.0' in audit['items'][0]['after_json']
    logs=client.get('/time-entries?paginated=true&month=2026-10&page_size=1&page=2',headers=auth).json()
    assert logs['total']==2 and len(logs['items'])==1


def test_manager_department_and_operator_boundaries(service):
    client,auth=service;eid=employee(service)
    own=client.post('/departments',headers=auth,json={'name':'Meu setor','lead_employee_id':eid}).json()['id']
    other=client.post('/departments',headers=auth,json={'name':'Outro setor'}).json()['id']
    client.post('/auth/register',headers=auth,json={'email':'manager-ledger@test.com','password':'ledger-password','role':'manager','employee_id':eid,'department_id':own})
    token=client.post('/auth/login',json={'email':'manager-ledger@test.com','password':'ledger-password'}).json()['token'];manager={'Authorization':'Bearer '+token}
    assert journal(client,manager,'gestor-setor-proprio',pair('1.1.01','3.1.01','10')).status_code==201
    assert journal(client,manager,'gestor-outro-setor',pair('1.1.01','3.1.01','10'),other).status_code==403
    journal(client,auth,'outro-setor-admin',pair('1.1.01','3.1.01','900'),other)
    report=client.get('/accounting/reports?start=2026-10-01&end=2026-10-31',headers=manager).json()
    assert report['balance']['assets_cents']==1000
    assert client.get('/accounting/reports?start=2026-10-01&end=2026-10-31&department_id='+str(other),headers=manager).status_code==403
    with main.db() as c:c.execute("UPDATE admins SET role='operator' WHERE lower(\"user\")='manager-ledger@test.com'")
    operator=client.post('/auth/login',json={'email':'manager-ledger@test.com','password':'ledger-password'}).json()['token']
    assert client.get('/accounting/accounts',headers={'Authorization':'Bearer '+operator}).status_code==403
