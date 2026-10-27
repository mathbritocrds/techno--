"""Append-only, exact-cent double-entry accounting and scoped reports."""
import calendar
import hashlib
import json
import uuid
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator


class Line(BaseModel):
    account_code: str = Field(min_length=1, max_length=20)
    debit: Decimal = Field(default=Decimal(0), ge=0, max_digits=14, decimal_places=2, allow_inf_nan=False)
    credit: Decimal = Field(default=Decimal(0), ge=0, max_digits=14, decimal_places=2, allow_inf_nan=False)

    @model_validator(mode='after')
    def one_side(self):
        if bool(self.debit) == bool(self.credit):
            raise ValueError('Informe somente débito ou crédito, maior que zero, em cada linha.')
        return self


class Journal(BaseModel):
    booked_on: date
    description: str = Field(min_length=3, max_length=300)
    reference: str = Field(min_length=8, max_length=120)
    department_id: int | None = None
    lines: list[Line] = Field(min_length=2, max_length=100)

    @model_validator(mode='after')
    def description_required(self):
        self.description=self.description.strip()
        if len(self.description)<3:raise ValueError('Descreva o lançamento.')
        return self


class Reversal(BaseModel):
    booked_on: date
    reason: str = Field(min_length=5, max_length=500)


class Adjustment(BaseModel):
    corrected_at: datetime
    reason: str = Field(min_length=5, max_length=500)


class Integration(BaseModel):
    through: date
    imported_revenue_counterpart: str | None = None
    imported_expense_counterpart: str | None = None


def cents(value):
    return int(Decimal(str(value)).quantize(Decimal('.01')) * 100)


def json_exact(value):
    """Keep cent integers exact when a browser's Number cannot represent them."""
    if isinstance(value, bool):return value
    if isinstance(value,int):return str(value) if abs(value)>9007199254740991 else value
    if isinstance(value,list):return [json_exact(item) for item in value]
    if isinstance(value,dict):return {key:json_exact(item) for key,item in value.items()}
    return value


def event(m, c, who, module, resource, before, after, reason, department_id=None):
    c.execute('INSERT INTO audit_events(at,actor,role,module,resource_id,department_id,before_json,after_json,reason) VALUES(?,?,?,?,?,?,?,?,?)',
              (m.iso(m.utcnow()), who['user'], who['role'], module, str(resource), department_id,
               json.dumps(before, ensure_ascii=False, default=str), json.dumps(after, ensure_ascii=False, default=str), reason))


def scoped(who, department_id=None):
    if who['role'] not in ('admin', 'manager'):
        raise HTTPException(403, 'Sua função não pode acessar a escrituração contábil.')
    if who['role'] == 'manager':
        own = who.get('department_id')
        if own is None or (department_id is not None and department_id != own):
            raise HTTPException(403, 'Consulta restrita ao departamento do gestor.')
        return own
    return department_id


@contextmanager
def connection(m, who):
    scoped(who)
    with m.db() as c:
        if m.SUPABASE_DATABASE_URL:
            # This NOLOGIN/NOBYPASSRLS role is deliberately not the connection owner.
            c.execute('SET LOCAL ROLE sigi_accounting_api')
            c.execute("SELECT set_config('sigi.role',?,true)", (who['role'],))
            c.execute("SELECT set_config('sigi.department_id',?,true)", (str(who.get('department_id') or ''),))
        yield c


def post(m, c, booked_on, description, department_id, source_key, lines, actor, reverses_id=None):
    if len(lines) < 2 or any(d < 0 or cr < 0 or max(d,cr)>9223372036854775807 or bool(d) == bool(cr) for _, d, cr in lines):
        raise HTTPException(400, 'Partidas inválidas.')
    if sum(d for _, d, _ in lines) != sum(cr for _, _, cr in lines):
        raise HTTPException(400, 'Total de débitos deve ser exatamente igual ao total de créditos.')
    payload = json.dumps([str(booked_on), description, department_id, lines, reverses_id], sort_keys=True)
    digest = hashlib.sha256(payload.encode()).hexdigest()
    existing = c.execute('SELECT id,payload_hash FROM ledger_journals WHERE source_key=?', (source_key,)).fetchone()
    if existing:
        if existing['payload_hash'] != digest:
            raise HTTPException(409, 'Referência já utilizada com outro conteúdo.')
        return existing['id'], False
    codes = {r['code'] for r in c.execute('SELECT code FROM ledger_accounts')}
    if any(code not in codes for code, _, _ in lines):
        raise HTTPException(400, 'Conta contábil inexistente.')
    jid = c.execute('INSERT INTO ledger_journals(booked_on,description,department_id,source_key,payload_hash,reverses_id,created_at,created_by) VALUES(?,?,?,?,?,?,?,?) RETURNING id',
                    (str(booked_on), description, department_id, source_key, digest, reverses_id, m.iso(m.utcnow()), actor)).fetchone()['id']
    for code, debit, credit in lines:
        c.execute('INSERT INTO ledger_lines(journal_id,account_code,debit_cents,credit_cents) VALUES(?,?,?,?)', (jid, code, debit, credit))
    c.execute("UPDATE ledger_journals SET status='posted' WHERE id=?", (jid,))
    return jid, True


CATEGORY_ACCOUNT = {'cost':'5.2.01','operating':'5.3.01','personnel':'5.3.02','tax':'5.1.01','financial':'5.6.01'}


def sync_transaction(m, c, t, actor='system'):
    if t['source_key'].startswith('payroll:'):
        return False
    t=dict(t)
    if not t.get('ledger_ref'):
        fresh=c.execute('SELECT ledger_ref FROM transactions WHERE id=?',(t['id'],)).fetchone()
        t['ledger_ref']=fresh['ledger_ref'] if fresh and fresh['ledger_ref'] else uuid.uuid4().hex
        c.execute('UPDATE transactions SET ledger_ref=? WHERE id=?',(t['ledger_ref'],t['id']))
    amount = cents(t['amount'])
    account = '4.1.01' if t['kind'] == 'receber' else CATEGORY_ACCOUNT[t['category']]
    counterpart = '1.1.02' if t['kind'] == 'receber' else '2.1.01'
    lines = [(counterpart, amount, 0), (account, 0, amount)] if t['kind']=='receber' else [(account, amount, 0), (counterpart, 0, amount)]
    _, created = post(m,c,(t['competence'] or t['due'][:7])+'-01',t['description'],t['department_id'],f"transaction:{t['ledger_ref']}",lines,actor)
    return created


def sync_payment(m, c, t, actor='system'):
    sync_transaction(m,c,t,actor)
    amount = cents(t['amount'])
    counterpart = '2.1.03' if t['source_key'].startswith('payroll:') else ('1.1.02' if t['kind']=='receber' else '2.1.01')
    lines = [('1.1.01',amount,0),(counterpart,0,amount)] if t['kind']=='receber' else [(counterpart,amount,0),('1.1.01',0,amount)]
    _,created=post(m,c,m.local(datetime.fromisoformat(t['paid_at'])).date(), 'Baixa · '+t['description'],t['department_id'],f"payment:{t['id']}",lines,actor)
    return created


def sync_payroll(m,c,run_id,month,actor):
    created=0
    for row in c.execute('SELECT employee_id,snapshot FROM payroll_items WHERE run_id=?',(run_id,)).fetchall():
        p=json.loads(row['snapshot']); total=cents(p['accrual_cost']); cash=cents(p['company_cost'])
        if not total: continue
        lines=[('5.3.02',total,0),('2.1.03',0,cash)]
        reserve=total-cash
        if reserve>0: lines.append(('2.1.04',0,reserve))
        elif reserve<0: lines.append(('2.1.04',-reserve,0))
        lines=[line for line in lines if line[1] or line[2]]
        _,new=post(m,c,month+'-01','Folha · '+p['name'],p['department_id'],f"payroll:{run_id}:{row['employee_id']}",lines,actor)
        created+=int(new)
    return created


def reverse(m,c,journal,booked_on,reason,actor):
    if journal['reverses_id'] or c.execute('SELECT 1 FROM ledger_journals WHERE reverses_id=?',(journal['id'],)).fetchone():
        raise HTTPException(409,'Lançamento já estornado ou de estorno.')
    lines=[(r['account_code'],r['credit_cents'],r['debit_cents']) for r in c.execute('SELECT * FROM ledger_lines WHERE journal_id=? ORDER BY id',(journal['id'],))]
    return post(m,c,booked_on,('Estorno · '+reason)[:300],journal['department_id'],f"reversal:{journal['id']}",lines,actor,journal['id'])[0]


def cancel_transaction(m,c,tid,actor):
    transaction=c.execute('SELECT ledger_ref FROM transactions WHERE id=?',(tid,)).fetchone()
    journal=c.execute('SELECT * FROM ledger_journals WHERE source_key=?',(f"transaction:{transaction['ledger_ref']}",)).fetchone()
    if journal:
        reverse(m,c,journal,journal['booked_on'],'Cancelamento da conta '+str(tid),actor)


def period(start,end):
    try:
        a,b=date.fromisoformat(start),date.fromisoformat(end)
    except ValueError:
        raise HTTPException(400,'Use datas AAAA-MM-DD.')
    if a>b or (b-a).days>3660: raise HTTPException(400,'Intervalo inválido ou superior a dez anos.')
    return str(a),str(b)


def balances(m,c,start,end,department_id):
    where,params='j.status=\'posted\' AND j.booked_on<=?', [end]
    if department_id is not None: where+=' AND j.department_id=?';params.append(department_id)
    # Aggregation is done in SQL; raw postings are never downloaded for reports.
    data={r['account_code']:dict(r) for r in c.execute(f'''SELECT l.account_code,
      SUM(CASE WHEN j.booked_on<? THEN l.debit_cents-l.credit_cents ELSE 0 END) AS opening,
      SUM(CASE WHEN j.booked_on>=? THEN l.debit_cents ELSE 0 END) AS debit,
      SUM(CASE WHEN j.booked_on>=? THEN l.credit_cents ELSE 0 END) AS credit,
      SUM(l.debit_cents-l.credit_cents) AS closing FROM ledger_lines l JOIN ledger_journals j ON j.id=l.journal_id
      WHERE {where} GROUP BY l.account_code''',(start,start,start,*params))}
    items=[]
    for account in c.execute('SELECT * FROM ledger_accounts ORDER BY code'):
        item={**dict(account),**data.get(account['code'],{})}
        for key in ('opening','debit','credit','closing'): item[key]=int(item.get(key) or 0)
        item['normal']='D' if item['kind'] in ('asset','expense') else 'C'
        item['side']='D' if item['closing']>0 else 'C' if item['closing']<0 else '—'
        items.append(item)
    return items


def statement(totals):
    t=lambda key:int(totals.get(key,0))
    net_revenue=t('revenue')+t('deductions');gross=net_revenue+t('cost')
    operating=gross+t('operating')+t('other_operating');laft=operating+t('investment')
    before_tax=laft+t('financing');net=before_tax+t('income_tax')+t('discontinued')
    rows=[('revenue','(+) Receita Operacional Bruta',t('revenue'),False),('deductions','(-) Deduções e Impostos',t('deductions'),False),
      ('net_revenue','(=) Receita Operacional Líquida',net_revenue,False),('cost','(-) Custo dos Bens e Serviços Vendidos',t('cost'),False),
      ('gross_profit','(=) Lucro Bruto',gross,False),('operating','(-) Despesas Operacionais',t('operating'),False),
      ('other_operating','(+/-) Outras Receitas e Despesas Operacionais',t('other_operating'),False),
      ('operating_profit','(=) Lucro ou Prejuízo Operacional',operating,True),('investment','(+/-) Resultado de Investimentos',t('investment'),False),
      ('laft','(=) Lucro Antes de Financiamento e Tributos (LAFT)',laft,True),('financing','(+/-) Receitas e Despesas Financeiras',t('financing'),False),
      ('before_tax','(=) Resultado Antes dos Tributos sobre o Lucro',before_tax,False),('income_tax','(-) Provisão para IRPJ e CSLL',t('income_tax'),False),
      ('discontinued','(+/-) Resultado de Operações Descontinuadas',t('discontinued'),False),('net_profit','(=) Lucro ou Prejuízo Líquido do Período',net,True)]
    return [{'key':key,'label':label,'amount_cents':amount,'amount':amount/100,'required':required,
             'subtotal':label.startswith('(=)'), 'revenue_pct':round(amount/t('revenue')*100,2) if t('revenue') else None} for key,label,amount,required in rows]


def report(m,c,start,end,department_id):
    items=balances(m,c,start,end,department_id);totals={}
    for item in items:
        if item['dre_category']:
            key=item['dre_category'];totals[key]=totals.get(key,0)+item['credit']-item['debit']
    dre=statement(totals)
    sections={key:[] for key in ('current_assets','noncurrent_assets','current_liabilities','noncurrent_liabilities','equity')}
    result=0
    for item in items:
        if item['section']=='result': result-=item['closing'];continue
        amount=item['closing']*(1 if item['kind']=='asset' else -1)
        sections[item['section']].append({'code':item['code'],'name':item['name'],'amount_cents':amount})
    sections['equity'].append({'code':'3.RESULT','name':'Resultado acumulado ainda não destinado','amount_cents':result})
    sums={key:sum(r['amount_cents'] for r in values) for key,values in sections.items()}
    asset=sums['current_assets']+sums['noncurrent_assets'];other=sums['current_liabilities']+sums['noncurrent_liabilities']+sums['equity']
    debit=sum(r['debit'] for r in items);credit=sum(r['credit'] for r in items)
    return {'start':start,'end':end,'department_id':department_id,'dre':dre,'balance':{'sections':sections,'totals':sums,
            'assets_cents':asset,'liabilities_equity_cents':other,'difference_cents':asset-other,'balanced':asset==other},
            'trial':{'items':items,'debit_cents':debit,'credit_cents':credit,'difference_cents':debit-credit,
                     'balanced':debit==credit and sum(r['closing'] for r in items)==0}}


def register_routes(m):
    app=m.app
    @app.get('/accounting/accounts')
    def accounts(who=Depends(m.manager_or_admin)):
        with connection(m,who) as c: return m.rows(c.execute('SELECT * FROM ledger_accounts ORDER BY code'))

    @app.post('/accounting/journals',status_code=201)
    def create(payload:Journal,who=Depends(m.manager_or_admin)):
        department=scoped(who,payload.department_id)
        if department is not None:
            with m.db() as c:
                if not c.execute('SELECT 1 FROM departments WHERE id=?',(department,)).fetchone():raise HTTPException(404,'Departamento não encontrado.')
        with connection(m,who) as c:
            c.execute('BEGIN IMMEDIATE')
            jid,new=post(m,c,payload.booked_on,payload.description.strip(),department,f'manual:{department or 0}:'+payload.reference,
                         [(r.account_code,int(r.debit*100),int(r.credit*100)) for r in payload.lines],who['user'])
            if new: event(m,c,who,'journal',jid,{},payload.model_dump(),'Escrituração manual',department)
        return {'id':jid,'created':new}

    @app.get('/accounting/reports')
    def reports(start:str,end:str,department_id:int|None=None,who=Depends(m.manager_or_admin)):
        start,end=period(start,end);department=scoped(who,department_id)
        with connection(m,who) as c: return json_exact(report(m,c,start,end,department))

    @app.get('/accounting/export.csv')
    def export(start:str,end:str,kind:Literal['dre','balance','trial']='dre',department_id:int|None=None,who=Depends(m.manager_or_admin)):
        import csv,io
        from fastapi.responses import Response
        start,end=period(start,end);department=scoped(who,department_id)
        with connection(m,who) as c: result=report(m,c,start,end,department)
        out=io.StringIO();out.write('\ufeff');w=csv.writer(out,delimiter=';')
        def money(n):return format(Decimal(n)/100,'.2f').replace('.',',')
        if kind=='dre':
            w.writerow(['DRE CPC 51 / IFRS 18 — modelo industrial',start,end]);w.writerow(['Rubrica','Valor','% receita','Obrigatório'])
            for r in result['dre']:w.writerow([m.csv_safe(r['label']),money(r['amount_cents']),(f"{r['revenue_pct']:.2f}".replace('.',',')+'%') if r['revenue_pct'] is not None else '', 'Sim' if r['required'] else ''])
        elif kind=='balance':
            w.writerow(['Balanço Patrimonial',end]);w.writerow(['Grupo','Código','Conta','Valor'])
            for group,rows in result['balance']['sections'].items():
                for r in rows:w.writerow([group,r['code'],m.csv_safe(r['name']),money(r['amount_cents'])])
            w.writerow(['Total Ativo','','',money(result['balance']['assets_cents'])]);w.writerow(['Total Passivo + PL','','',money(result['balance']['liabilities_equity_cents'])])
        else:
            w.writerow(['Balancete',start,end]);w.writerow(['Código','Conta','Saldo anterior (D-C)','Débitos','Créditos','Saldo atual','D/C'])
            for r in result['trial']['items']:w.writerow([r['code'],m.csv_safe(r['name']),money(r['opening']),money(r['debit']),money(r['credit']),money(abs(r['closing'])),r['side']])
            w.writerow(['Totais','','',money(result['trial']['debit_cents']),money(result['trial']['credit_cents'])])
        return Response(out.getvalue(),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':f'attachment; filename="{kind}-{start}-{end}.csv"','Cache-Control':'private, no-store'})

    @app.get('/accounting/journals')
    def journals(start:str,end:str,page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100),department_id:int|None=None,who=Depends(m.manager_or_admin)):
        start,end=period(start,end);department=scoped(who,department_id)
        where,params="j.status='posted' AND j.booked_on>=? AND j.booked_on<=?",[start,end]
        if department is not None:where+=' AND j.department_id=?';params.append(department)
        with connection(m,who) as c:
            total=c.execute('SELECT COUNT(*) FROM ledger_journals j WHERE '+where,params).fetchone()[0]
            items=m.rows(c.execute('SELECT j.*,EXISTS(SELECT 1 FROM ledger_journals r WHERE r.reverses_id=j.id) AS reversed FROM ledger_journals j WHERE '+where+' ORDER BY j.booked_on DESC,j.id DESC LIMIT ? OFFSET ?',(*params,page_size,(page-1)*page_size)))
            for item in items:item['lines']=m.rows(c.execute('SELECT l.*,a.name FROM ledger_lines l JOIN ledger_accounts a ON a.code=l.account_code WHERE journal_id=? ORDER BY l.id',(item['id'],)))
        return {'items':items,'total':total,'page':page,'page_size':page_size}

    @app.post('/accounting/journals/{jid}/reverse')
    def reversal(jid:int,payload:Reversal,who=Depends(m.manager_or_admin)):
        payload.reason=payload.reason.strip()
        if len(payload.reason)<5:raise HTTPException(400,'Informe o motivo do estorno.')
        department=scoped(who)
        with connection(m,who) as c:
            c.execute('BEGIN IMMEDIATE');j=c.execute('SELECT * FROM ledger_journals WHERE id=?',(jid,)).fetchone()
            if not j or (department is not None and j['department_id']!=department):raise HTTPException(404,'Lançamento não encontrado.')
            if not j['source_key'].startswith('manual:'):raise HTTPException(409,'Corrija a origem operacional para manter a integração consistente.')
            if payload.booked_on<date.fromisoformat(j['booked_on']):raise HTTPException(400,'Estorno não pode anteceder o lançamento original.')
            rid=reverse(m,c,j,payload.booked_on,payload.reason,who['user'])
            event(m,c,who,'reversal',jid,dict(j),{'reversal_id':rid},payload.reason,j['department_id'])
        return {'id':rid}

    @app.get('/accounting/audit')
    def audit(page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100),department_id:int|None=None,who=Depends(m.manager_or_admin)):
        department=scoped(who,department_id);where=' WHERE department_id=?' if department is not None else '';params=[department] if department is not None else []
        with connection(m,who) as c:
            total=c.execute('SELECT COUNT(*) FROM audit_events'+where,params).fetchone()[0]
            items=m.rows(c.execute('SELECT * FROM audit_events'+where+' ORDER BY id DESC LIMIT ? OFFSET ?',(*params,page_size,(page-1)*page_size)))
        return {'items':items,'total':total,'page':page,'page_size':page_size}

    @app.post('/time-entries/{eid}/adjustments',status_code=201)
    def adjust(eid:int,payload:Adjustment,who=Depends(m.manager_or_admin)):
        payload.reason=payload.reason.strip()
        if len(payload.reason)<5:raise HTTPException(400,'Informe o motivo da correção.')
        department=scoped(who)
        if payload.corrected_at.tzinfo is None:raise HTTPException(400,'Informe o fuso do horário corrigido.')
        corrected=m.iso(payload.corrected_at)
        if payload.corrected_at>m.utcnow():raise HTTPException(400,'Horário corrigido não pode estar no futuro.')
        with m.db() as c:
            c.execute('BEGIN IMMEDIATE')
            old=c.execute('SELECT t.*,e.department_id FROM effective_time_entries t JOIN employees e ON e.id=t.employee_id WHERE t.id=?',(eid,)).fetchone()
            if not old or not old['accepted'] or (department is not None and old['department_id']!=department):raise HTTPException(404,'Marcação válida não encontrada.')
            if m.local(payload.corrected_at).date()!=m.local(datetime.fromisoformat(old['at'])).date():raise HTTPException(400,'A correção deve permanecer no dia da marcação original.')
            neighbors=m.rows(c.execute('SELECT id,kind,effective_at FROM effective_time_entries WHERE employee_id=? AND accepted=1 AND id<>? ORDER BY effective_at,id',(old['employee_id'],eid)))
            all_rows=sorted(neighbors+[{'id':eid,'kind':old['kind'],'effective_at':corrected}],key=lambda r:(r['effective_at'],r['id']))
            day=m.local(payload.corrected_at).date();day_rows=[r for r in all_rows if m.local(datetime.fromisoformat(r['effective_at'])).date()==day]
            if any(r['kind']!=('entrada' if i%2==0 else 'saida') for i,r in enumerate(day_rows)):raise HTTPException(400,'A correção inverte a ordem de entrada/saída.')
            aid=c.execute('INSERT INTO time_entry_adjustments(entry_id,corrected_at,reason,created_at,created_by,department_id) VALUES(?,?,?,?,?,?) RETURNING id',(eid,corrected,payload.reason,m.iso(m.utcnow()),who['user'],old['department_id'])).fetchone()['id']
            event(m,c,who,'time_entry',eid,{'at':old['effective_at'],'original_at':old['at']},{'at':corrected,'adjustment_id':aid},payload.reason,old['department_id'])
        return {'id':aid,'original_at':old['at'],'effective_at':corrected}

    @app.post('/accounting/integrate')
    def integrate(payload:Integration,who=Depends(m.admin_context)):
        if who['role']!='admin':raise HTTPException(403,'Somente administrador pode integrar origens operacionais.')
        count=0;pending=0;through=str(payload.through)
        with m.db() as c:
            c.execute('BEGIN IMMEDIATE')
            for run in c.execute('SELECT id,month FROM payroll_runs WHERE month<=?',(through[:7],)).fetchall():count+=sync_payroll(m,c,run['id'],run['month'],who['user'])
            for t in c.execute("SELECT * FROM transactions WHERE (CASE WHEN competence='' THEN substr(due,1,7) ELSE competence END)<=?",(through[:7],)).fetchall():
                count+=int(sync_transaction(m,c,t,who['user']))
            for t in c.execute("SELECT * FROM transactions WHERE paid=1 AND paid_at IS NOT NULL").fetchall():
                if t['paid'] and t['paid_at'] and m.local(datetime.fromisoformat(t['paid_at'])).date()<=payload.through:count+=int(sync_payment(m,c,t,who['user']))
            for r in c.execute('SELECT * FROM dre_import_entries WHERE month<=?',(through[:7],)).fetchall():
                counterpart=payload.imported_revenue_counterpart if r['category']=='revenue' else payload.imported_expense_counterpart
                if not counterpart:pending+=1;continue
                a=c.execute('SELECT kind FROM ledger_accounts WHERE code=?',(counterpart,)).fetchone()
                if not a or a['kind'] not in ('asset','liability','equity'):raise HTTPException(400,'Contrapartida de importação deve ser uma conta patrimonial.')
                amount=cents(r['amount']);account='4.1.01' if r['category']=='revenue' else CATEGORY_ACCOUNT[r['category']]
                lines=[(counterpart,amount,0),(account,0,amount)] if r['category']=='revenue' else [(account,amount,0),(counterpart,0,amount)]
                _,new=post(m,c,r['month']+'-01',r['description'],r['department_id'],f"dre-import:{r['id']}",lines,who['user']);count+=int(new)
            event(m,c,who,'integration','sources',{}, {'created':count,'pending_imports':pending},'Integração explícita de fontes operacionais')
        return {'created':count,'pending_imports':pending}
