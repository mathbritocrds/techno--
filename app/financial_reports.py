"""DRE by competence with scoped reports and audited, idempotent CSV imports."""
import csv
import hashlib
import io
import json
from collections import defaultdict
from decimal import Decimal, InvalidOperation

from fastapi import Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from app import payroll as hr

CATEGORIES = {'revenue':'Receita bruta','tax':'Impostos / deduções','cost':'Custos operacionais',
              'operating':'Despesas operacionais','personnel':'Pessoal','financial':'Despesas financeiras'}
HEADERS = ['origem','referencia','ano','mes','setor_id','setor','categoria','descricao','valor','percentual_receita','percentual_gastos']

class ImportCSV(BaseModel):
    content: str = Field(min_length=1, max_length=1_000_000)
    preview: bool = True


def scope(who, department_id=None):
    if who['role'] == 'manager':
        if who['department_id'] is None:
            raise HTTPException(403, 'Vincule o gestor a um departamento para consultar a DRE.')
        if department_id is not None and department_id != who['department_id']:
            raise HTTPException(403, 'A DRE do gestor é restrita ao seu departamento.')
        return who['department_id']
    return department_id


def report(m, start, end, who, department_id=None, category=None):
    from app.business import month_or_error
    month_or_error(start); month_or_error(end)
    distance = int(end[:4])*12+int(end[5:])-int(start[:4])*12-int(start[5:])
    if distance < 0 or distance > 119:
        raise HTTPException(400, 'Intervalo inválido ou superior a dez anos.')
    if category and category not in CATEGORIES:
        raise HTTPException(400, 'Categoria inválida.')
    department_id = scope(who, department_id)
    entries = []
    with m.db() as c:
        departments = m.rows(c.execute('SELECT id,name FROM departments ORDER BY name'))
        names = {d['id']:d['name'] for d in departments}
        if department_id is not None and department_id not in names:
            raise HTTPException(404, 'Setor não encontrado.')
        tx = m.rows(c.execute("SELECT * FROM transactions WHERE (CASE WHEN competence='' THEN substr(due,1,7) ELSE competence END)>=? AND (CASE WHEN competence='' THEN substr(due,1,7) ELSE competence END)<=?", (start,end)))
        for t in tx:
            if t['source_key'].startswith('payroll:'): continue
            entries.append(dict(origin='transaction',reference=str(t['id']),month=t['competence'] or t['due'][:7],department_id=t['department_id'],category='revenue' if t['kind']=='receber' else t['category'],description=t['description'],amount=t['amount']))
        runs = m.rows(c.execute('SELECT id,month FROM payroll_runs WHERE month>=? AND month<=? ORDER BY month', (start,end)))
        for run in runs:
            for item in c.execute('SELECT employee_id,snapshot FROM payroll_items WHERE run_id=?', (run['id'],)):
                p=json.loads(item['snapshot'])
                entries.append(dict(origin='payroll',reference=f"{run['id']}:{item['employee_id']}",month=run['month'],department_id=p['department_id'],category='personnel',description='Folha fechada · '+p['name'],amount=p['accrual_cost']))
        for r in c.execute('SELECT * FROM dre_import_entries WHERE month>=? AND month<=?',(start,end)):
            entries.append(dict(origin='import',reference=r['reference'],month=r['month'],department_id=r['department_id'],category=r['category'],description=r['description'],amount=r['amount']))
    entries = [e for e in entries if department_id is None or e['department_id']==department_id]
    # Revenue remains the denominator even when a category filter is applied.
    revenue_base = sum(hr.D(e['amount']) for e in entries if e['category']=='revenue')
    totals = {k:Decimal(0) for k in CATEGORIES}
    grouped = defaultdict(lambda:Decimal(0))
    for e in entries:
        totals[e['category']] += hr.D(e['amount'])
        if not category or e['category']==category:
            grouped[(e['month'],e['department_id'],e['category'])] += hr.D(e['amount'])
    expenses = sum(v for k,v in totals.items() if k!='revenue')
    pct = lambda value,base: round(float(value/base*100),2) if base else None
    breakdown = [dict(month=ym,year=int(ym[:4]),month_number=int(ym[5:]),department_id=did,department=names.get(did,'Sem setor'),category=key,amount=hr.money(value),revenue_pct=pct(value,revenue_base),expense_pct=pct(value,expenses) if key!='revenue' else None) for (ym,did,key),value in sorted(grouped.items(),key=lambda x:(x[0][0],names.get(x[0][1],''),x[0][2]))]
    if category: entries=[e for e in entries if e['category']==category]
    for e in entries:
        e.update(department=names.get(e['department_id'],'Sem setor'),revenue_pct=pct(hr.D(e['amount']),revenue_base),expense_pct=pct(hr.D(e['amount']),expenses) if e['category']!='revenue' else None)
    gross=totals['revenue']-totals['tax']-totals['cost']; net=totals['revenue']-expenses
    return dict(start=start,end=end,**{k:hr.money(v) for k,v in totals.items()},gross_profit=hr.money(gross),net_profit=hr.money(net),net_margin_pct=pct(net,revenue_base),percentages={k:pct(v,revenue_base) for k,v in totals.items()},breakdown=breakdown,entries=sorted(entries,key=lambda e:(e['month'],e['department'],e['category'],e['reference'])),departments=[d for d in departments if who['role']=='admin' or d['id']==department_id],closed_payroll_months=[r['month'] for r in runs],basis='competence',note='Pessoal inclui folhas fechadas e despesas avulsas. Importações são lançamentos contábeis, sem gerar pagamentos. Percentuais usam a receita do período; sem receita, ficam indisponíveis.')


def parse_csv(m, payload, who, c):
    content=payload.content.lstrip('\ufeff')
    first=content.splitlines()[0] if content.splitlines() else ''
    delimiter=';' if first.count(';')>=first.count(',') else ','
    reader=csv.DictReader(io.StringIO(content),delimiter=delimiter)
    required={'ano','mes','categoria','descricao','valor'}
    if not reader.fieldnames or len(set(reader.fieldnames))!=len(reader.fieldnames) or not required.issubset(reader.fieldnames):
        raise HTTPException(400, 'Cabeçalho obrigatório: ano;mes;categoria;descricao;valor. Baixe o modelo CSV.')
    names={r['id']:r['name'] for r in c.execute('SELECT id,name FROM departments')}
    errors=[]; items=[]; existing=0; seen={}
    for line,row in enumerate(reader,2):
        if line>2001: raise HTTPException(413,'Limite de 2.000 linhas por importação.')
        if not any(v for v in row.values()): continue
        try:
            if None in row: raise ValueError('Quantidade de colunas inválida.')
            origin=(row.get('origem') or 'import').strip()
            if origin in ('transaction','payroll'):
                existing+=1; continue  # Internal sources in an exported file never become new postings.
            if origin!='import': raise ValueError('Origem deve ser import.')
            ym=f"{int(row['ano']):04d}-{int(row['mes']):02d}"
            from app.business import month_or_error
            month_or_error(ym)
            category=row['categoria'].strip()
            if category not in CATEGORIES: raise ValueError('Categoria inválida: '+category)
            description=row['descricao'].strip()
            if len(description)>1 and description[0]=="'" and description[1] in '=+-@': description=description[1:]
            if not description or len(description)>300: raise ValueError('Descrição deve ter de 1 a 300 caracteres.')
            value=row['valor'].strip()
            if ',' in value: value=value.replace('.','').replace(',','.')
            amount=Decimal(value)
            if not amount.is_finite() or amount<=0 or amount>Decimal('999999999999.99') or amount!=amount.quantize(Decimal('.01')):
                raise ValueError('Valor deve ser positivo e ter no máximo duas casas decimais.')
            raw=(row.get('setor_id') or '').strip()
            did=int(raw) if raw else None
            sector=(row.get('setor') or '').strip().lstrip("'")
            if did is None and sector and sector!='Sem setor':
                matches=[k for k,v in names.items() if v.casefold()==sector.casefold()]
                if len(matches)!=1: raise ValueError('Setor inexistente ou ambíguo.')
                did=matches[0]
            if did is not None and did not in names: raise ValueError('Setor não encontrado.')
            did=scope(who,did)
            ref=(row.get('referencia') or '').strip()
            if not ref: ref=hashlib.sha256(json.dumps([ym,did,category,description,str(amount)],ensure_ascii=False).encode()).hexdigest()
            if len(ref)>160: raise ValueError('Referência muito longa.')
            key=f"{did or 0}:{ref}"
            prior=c.execute('SELECT * FROM dre_import_entries WHERE reference=?',(key,)).fetchone()
            if prior and (prior['month']!=ym or prior['category']!=category or prior['description']!=description or hr.D(prior['amount'])!=amount):
                raise ValueError('Referência já importada com valores diferentes. Use uma nova referência para outro lançamento.')
            signature=(ym,category,description,amount)
            if key in seen and seen[key]!=signature:
                raise ValueError('Referência repetida com conteúdos diferentes no arquivo.')
            if key in seen or prior:
                existing+=1; continue
            seen[key]=signature
            items.append(dict(reference=key,month=ym,department_id=did,category=category,description=description,amount=float(amount)))
        except (ValueError,InvalidOperation,HTTPException) as exc:
            errors.append({'line':line,'message':exc.detail if isinstance(exc,HTTPException) else str(exc)})
    return items,errors,existing


def register_routes(m):
    @m.app.get('/finance/dre', dependencies=[Depends(m.manager_or_admin)])
    def dre(start:str,end:str,department_id:int|None=None,category:str|None=None,who=Depends(m.principal)):
        return report(m,start,end,who,department_id,category)

    @m.app.get('/finance/dre/export.csv', dependencies=[Depends(m.manager_or_admin)])
    def export(start:str,end:str,department_id:int|None=None,category:str|None=None,who=Depends(m.principal)):
        result=report(m,start,end,who,department_id,category)
        out=io.StringIO();out.write('\ufeff');writer=csv.writer(out,delimiter=';');writer.writerow(HEADERS)
        for e in result['entries']:
            # References from imported rows are already scoped. Strip exactly one scope for round trips.
            ref=e['reference'].split(':',1)[1] if e['origin']=='import' else e['reference']
            writer.writerow([m.csv_safe(v) for v in [e['origin'],ref,e['month'][:4],e['month'][5:],e['department_id'] or '',e['department'],e['category'],e['description'],f"{e['amount']:.2f}".replace('.',','),e['revenue_pct'] if e['revenue_pct'] is not None else '',e['expense_pct'] if e['expense_pct'] is not None else '']])
        return Response(out.getvalue(),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':f'attachment; filename="dre-{start}-{end}.csv"','Cache-Control':'private, no-store'})

    @m.app.get('/finance/dre/template.csv', dependencies=[Depends(m.manager_or_admin)])
    def template():
        return Response('\ufeff'+';'.join(HEADERS)+'\r\n',media_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename="modelo-dre.csv"'})

    @m.app.post('/finance/dre/import', dependencies=[Depends(m.manager_or_admin)])
    def import_dre(payload:ImportCSV,who=Depends(m.principal)):
        scope(who)
        with m.db() as c:
            c.execute('BEGIN IMMEDIATE')
            items,errors,existing=parse_csv(m,payload,who,c)
            summary={'preview':payload.preview,'accepted':len(items),'existing':existing,'errors':errors,'total':hr.money(sum(hr.D(r['amount']) for r in items)),'rows':items[:20]}
            if payload.preview: return summary
            if errors: raise HTTPException(400,{'message':'Corrija as linhas inválidas antes de importar.','errors':errors})
            if not items: return {**summary,'imported':0}
            digest=hashlib.sha256(payload.content.encode()).hexdigest()
            batch=c.execute('INSERT INTO dre_import_batches(sha256,actor,created_at,row_count) VALUES(?,?,?,?) RETURNING id',(digest,who['user'],m.iso(m.utcnow()),len(items))).fetchone()['id']
            for r in items:
                c.execute('INSERT INTO dre_import_entries(batch_id,reference,month,department_id,category,description,amount) VALUES(?,?,?,?,?,?,?)',(batch,r['reference'],r['month'],r['department_id'],r['category'],r['description'],r['amount']))
            return {**summary,'imported':len(items),'batch_id':batch}
