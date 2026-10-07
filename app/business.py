"""Business services and authenticated routes attached to the existing FastAPI app."""
import base64
import binascii
import hashlib
import json
from datetime import date, datetime, timedelta
from typing import Literal

from fastapi import Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field, model_validator

from app import payroll as hr


class PayrollProfile(BaseModel):
    hired_on: str = ''
    terminated_on: str = ''
    dependents: int = Field(default=0, ge=0, le=50)
    alimony: float = Field(default=0, ge=0, allow_inf_nan=False)
    other_deductions: float = Field(default=0, ge=0, allow_inf_nan=False)
    vt_amount: float = Field(default=0, ge=0, allow_inf_nan=False)
    vt_rate: float = Field(default=.06, ge=0, le=.06)
    va_amount: float = Field(default=0, ge=0, allow_inf_nan=False)
    va_discount: float = Field(default=0, ge=0, allow_inf_nan=False)
    fgts_rate: Literal[.08, .02] = .08
    employer_rate: float = Field(default=.20, ge=0, le=1, allow_inf_nan=False)
    vacation_days: Literal[12, 18, 24, 30] = 30
    variable_average: float = Field(default=0, ge=0, allow_inf_nan=False)

    @model_validator(mode='after')
    def dates(self):
        try:
            for value in (self.hired_on, self.terminated_on):
                if value:
                    date.fromisoformat(value)
        except ValueError:
            raise ValueError('Data de contrato inválida.')
        if self.terminated_on and (not self.hired_on or self.terminated_on < self.hired_on):
            raise ValueError('Desligamento deve ser posterior à admissão.')
        if self.va_discount > self.va_amount:
            raise ValueError('Desconto VA não pode exceder a concessão.')
        return self


class ClosePayroll(BaseModel):
    month: str


class PaymentFile(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    mime: Literal['application/pdf', 'image/png', 'image/jpeg']
    content_base64: str = Field(max_length=8 * 1024 * 1024)


class StaffRule(BaseModel):
    employee_id: int
    duty: Literal['preparation', 'service', 'maintenance']
    before_minutes: int = Field(default=0, ge=0, le=1440)
    after_minutes: int = Field(default=0, ge=0, le=1440)


class VacationIn(BaseModel):
    acquisition_start: str
    days: int = Field(ge=1, le=30)
    starts_on: str


def month_or_error(month):
    try:
        hr.validate_month(month)
    except (ValueError, TypeError):
        raise HTTPException(400, 'Competência inválida. Use AAAA-MM.')
    return month


def profile_for(c, employee_id):
    row = c.execute('SELECT * FROM employee_payroll_profiles WHERE employee_id=?', (employee_id,)).fetchone()
    return {**hr.PROFILE_DEFAULTS, **(dict(row) if row else {})}


def payroll_data(m, month, c=None):
    month_or_error(month)
    if c is None:
        with m.db() as connection:
            return payroll_data(m, month, connection)
    run = c.execute('SELECT * FROM payroll_runs WHERE month=?', (month,)).fetchone()
    if run:
        employees = [json.loads(row['snapshot']) for row in c.execute('SELECT snapshot FROM payroll_items WHERE run_id=? ORDER BY employee_id', (run['id'],))]
    else:
        try:
            hr.tax_rules(month)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        employees = []
        timesheet = m.timesheet_data(c, month)
        for row in c.execute('SELECT * FROM employees ORDER BY name').fetchall():
            employee = dict(row)
            p = profile_for(c, employee['id'])
            if not employee['active'] and not p['terminated_on']:
                continue
            if p['hired_on'] and p['hired_on'] > month + '-31':
                continue
            if p['terminated_on'] and p['terminated_on'][:7] < month:
                continue
            records = m.rows(c.execute('SELECT * FROM vacation_records WHERE employee_id=?', (employee['id'],)))
            overtime = round(timesheet.get(employee['id'], {}).get('overtime', 0), 2)
            employees.append(hr.calculate(employee, p, month, overtime, records))
    for employee in employees:
        fgts = c.execute('SELECT i.snapshot FROM payroll_items i JOIN payroll_runs r ON r.id=i.run_id WHERE i.employee_id=? AND r.month<=?', (employee['id'], month)).fetchall()
        employee['fgts_accumulated'] = hr.money(sum(hr.D(json.loads(row['snapshot'])['fgts']) for row in fgts))
    return {'month': month, 'closed': bool(run), 'rules_version': run['rules_version'] if run else hr.RULES_VERSION,
            'employees': employees, 'total_net': hr.money(sum(hr.D(e['net']) for e in employees)),
            'total_company_cost': hr.money(sum(hr.D(e['company_cost']) for e in employees)),
            'total_accrual_cost': hr.money(sum(hr.D(e['accrual_cost']) for e in employees))}


def record_payment(m, c, transaction, actor):
    c.execute('INSERT INTO payment_history(transaction_id,kind,description,amount,category,competence,paid_at,actor,department_id,source_key) VALUES(?,?,?,?,?,?,?,?,?,?)',
        (transaction['id'], transaction['kind'], transaction['description'], transaction['amount'],
         transaction['category'], transaction['competence'] or transaction['due'][:7], transaction['paid_at'],
         actor, transaction['department_id'], transaction['source_key']))


def check_booking(m, c, booking, start, end, exclude_id=0):
    space = c.execute('SELECT * FROM spaces WHERE id=?', (booking.space_id,)).fetchone()
    if not space:
        raise HTTPException(404, 'Espaço não encontrado.')
    if booking.status in ('cancelled', 'completed'):
        return
    if booking.attendees > space['capacity']:
        raise HTTPException(409, 'Número de participantes excede a capacidade do espaço.')
    others = m.rows(c.execute("SELECT * FROM space_bookings WHERE space_id=? AND id<>? AND status NOT IN ('cancelled','completed') AND starts_at<? AND ends_at>?", (booking.space_id, exclude_id, end, start)))
    if others and (not space['allow_shared'] or booking.exclusive or any(b['exclusive'] or not booking.compatibility or b['compatibility'] != booking.compatibility for b in others)):
        raise HTTPException(409, 'Conflito de agenda: espaço exclusivo ou eventos incompatíveis.')
    points = [(start, booking.attendees), (end, -booking.attendees)]
    for b in others:
        points.extend([(max(start, b['starts_at']), b['attendees']), (min(end, b['ends_at']), -b['attendees'])])
    occupancy = 0
    for _, change in sorted(points, key=lambda point: (point[0], point[1])):
        occupancy += change
        if occupancy > space['capacity']:
            raise HTTPException(409, 'Conflito de agenda: capacidade simultânea excedida.')


def sync_staff(m, c, booking_id):
    booking = c.execute('SELECT * FROM space_bookings WHERE id=?', (booking_id,)).fetchone()
    c.execute('DELETE FROM event_staff_assignments WHERE booking_id=?', (booking_id,))
    if booking['status'] in ('cancelled', 'completed'):
        return
    rules = m.rows(c.execute('SELECT r.*,e.active FROM space_staff_rules r JOIN employees e ON e.id=r.employee_id WHERE r.space_id=?', (booking['space_id'],)))
    for rule in rules:
        if not rule['active']:
            raise HTTPException(409, 'Escala contém funcionário inativo. Atualize a equipe do espaço.')
        start = m.booking_time((datetime.fromisoformat(booking['starts_at'])-timedelta(minutes=rule['before_minutes'])).isoformat())
        end = m.booking_time((datetime.fromisoformat(booking['ends_at'])+timedelta(minutes=rule['after_minutes'])).isoformat())
        clash = c.execute('SELECT 1 FROM event_staff_assignments WHERE employee_id=? AND booking_id<>? AND starts_at<? AND ends_at>?', (rule['employee_id'], booking_id, end, start)).fetchone()
        if clash:
            raise HTTPException(409, 'Conflito na escala: funcionário já designado para outro evento nesse horário.')
        c.execute('INSERT INTO event_staff_assignments(booking_id,employee_id,duty,starts_at,ends_at) VALUES(?,?,?,?,?)', (booking_id, rule['employee_id'], rule['duty'], start, end))


def register_routes(m):
    app = m.app

    @app.get('/payroll/rules', dependencies=[Depends(m.admin)])
    def rules(month: str = '2026-01'):
        try:
            return hr.tax_rules(month)
        except ValueError as exc:
            raise HTTPException(422, str(exc))

    @app.get('/employees/{eid}/payroll-profile', dependencies=[Depends(m.admin)])
    def get_profile(eid: int):
        with m.db() as c:
            if not c.execute('SELECT 1 FROM employees WHERE id=?', (eid,)).fetchone():
                raise HTTPException(404, 'Funcionário não encontrado.')
            return profile_for(c, eid)

    @app.put('/employees/{eid}/payroll-profile', dependencies=[Depends(m.admin)])
    def set_profile(eid: int, profile: PayrollProfile):
        with m.db() as c:
            c.execute('BEGIN IMMEDIATE')
            if not c.execute('SELECT 1 FROM employees WHERE id=?', (eid,)).fetchone():
                raise HTTPException(404, 'Funcionário não encontrado.')
            data = profile.model_dump()
            keys = ','.join(data)
            updates = ','.join(k+'=excluded.'+k for k in data)
            c.execute(f'INSERT INTO employee_payroll_profiles(employee_id,{keys}) VALUES({",".join("?" for _ in range(len(data)+1))}) ON CONFLICT(employee_id) DO UPDATE SET {updates}', (eid, *data.values()))
        return {'ok': True}

    @app.post('/payroll/close', dependencies=[Depends(m.admin)])
    def close(payload: ClosePayroll, who=Depends(m.admin_context)):
        month = month_or_error(payload.month)
        if month > m.local(m.utcnow()).strftime('%Y-%m'):
            raise HTTPException(400, 'Não é possível fechar competência futura.')
        with m.db() as c:
            c.execute('BEGIN IMMEDIATE')
            existing = c.execute('SELECT id FROM payroll_runs WHERE month=?', (month,)).fetchone()
            if existing:
                return {'id': existing['id'], 'already_closed': True}
            data = payroll_data(m, month, c)
            if any(not e['profile_complete'] for e in data['employees']):
                raise HTTPException(409, 'Informe a data de admissão de todos os funcionários antes de fechar.')
            if any(e['net'] < 0 for e in data['employees']):
                raise HTTPException(409, 'Folha com líquido negativo. Revise as deduções antes de fechar.')
            run_id = c.execute('INSERT INTO payroll_runs(month,closed_at,closed_by,rules_version) VALUES(?,?,?,?) RETURNING id', (month, m.iso(m.utcnow()), who['user'], hr.RULES_VERSION)).fetchone()['id']
            due = hr.validate_month(month)[1].isoformat()
            for employee in data['employees']:
                c.execute('INSERT INTO payroll_items(run_id,employee_id,snapshot) VALUES(?,?,?)', (run_id, employee['id'], json.dumps(employee, ensure_ascii=False)))
                if employee['net'] > 0:
                    c.execute("INSERT INTO transactions(kind,description,amount,due,department_id,category,competence,source_key) VALUES('pagar',?,?,?,?,?,?,?)", (f"Salário {month} · {employee['name']}", employee['net'], due, employee['department_id'], 'personnel', month, f'payroll:{run_id}:{employee["id"]}'))
        return {'id': run_id, 'already_closed': False}

    @app.get('/payroll/annual-cost', dependencies=[Depends(m.admin)])
    def annual(year: int = 2026):
        if year != 2026:
            raise HTTPException(422, 'Tabela fiscal não cadastrada para o ano solicitado.')
        consolidated = {}
        closed_months = []
        for month_number in range(1, 13):
            month = f'{year}-{month_number:02d}'
            data = payroll_data(m, month)
            if data['closed']:
                closed_months.append(month)
            for e in data['employees']:
                item = consolidated.setdefault(e['id'], {'id': e['id'], 'name': e['name'], 'actual_closed': 0, 'projected_open': 0, 'fgts': 0, 'benefits': 0, 'provisions': 0, 'employer_charges': 0, 'salary': 0})
                key = 'actual_closed' if data['closed'] else 'projected_open'
                item[key] += e['accrual_cost']
                for k, value in [('fgts', e['fgts']), ('benefits', e['benefits']+e['vt_amount']-e['vt_discount']+e['va_amount']-e['va_discount']), ('provisions', e['thirteenth_provision']+e['vacation_provision']+e['provision_charges']-e['vacation_salary_offset']-e['vacation_charge_offset']), ('employer_charges', e['employer_charges']), ('salary', e['gross'])]:
                    item[k] += value
        for item in consolidated.values():
            item['total'] = item['actual_closed']+item['projected_open']
            for k in list(item):
                if k not in ('id', 'name'):
                    item[k] = hr.money(item[k])
        return {'year': year, 'closed_months': closed_months, 'employees': list(consolidated.values()), 'basis': 'Folhas fechadas + projeção das abertas; férias provisionadas separadamente do salário.'}

    @app.post('/employees/{eid}/vacations/quote', dependencies=[Depends(m.admin)])
    def vacation_quote(eid: int, payload: VacationIn):
        try:
            acquisition = date.fromisoformat(payload.acquisition_start)
            starts = date.fromisoformat(payload.starts_on)
        except ValueError:
            raise HTTPException(400, 'Data de férias inválida.')
        with m.db() as c:
            employee = c.execute('SELECT * FROM employees WHERE id=?', (eid,)).fetchone()
            if not employee:
                raise HTTPException(404, 'Funcionário não encontrado.')
            p = profile_for(c, eid)
            if not p['hired_on']:
                raise HTTPException(409, 'Informe a admissão antes de calcular férias.')
            records = m.rows(c.execute('SELECT * FROM vacation_records WHERE employee_id=?', (eid,)))
            balance = hr.entitlement({**p, 'remuneration': employee['salary']+p['variable_average']}, starts, records)
        cycle = next((r for r in balance['vacation_cycles'] if r['start'] == acquisition.isoformat()), None)
        if not cycle or payload.days > cycle['remaining_days']:
            raise HTTPException(409, 'Período aquisitivo inválido ou saldo de férias insuficiente.')
        base = hr.D(employee['salary']+p['variable_average']) * payload.days / 30
        multiplier = 2 if cycle['overdue'] else 1
        gross = hr.money(base * hr.D(4)/3 * multiplier)
        return {'base': hr.money(base*multiplier), 'constitutional_third': hr.money(base/3*multiplier), 'gross': gross, 'overdue': cycle['overdue'], 'payment_due': (starts-timedelta(days=2)).isoformat(), 'remaining_days': cycle['remaining_days'], 'note': 'Remuneração bruta de férias. Retenções devem ser apuradas separadamente na data do pagamento.'}

    @app.post('/employees/{eid}/vacations', dependencies=[Depends(m.admin)], status_code=201)
    def vacation_record(eid: int, payload: VacationIn, who=Depends(m.admin_context)):
        with m.db() as c:
            c.execute('BEGIN IMMEDIATE')
            quote = vacation_quote(eid, payload)
            start = date.fromisoformat(payload.starts_on)
            end = start + timedelta(days=payload.days)
            for row in c.execute('SELECT starts_on,days FROM vacation_records WHERE employee_id=?', (eid,)).fetchall():
                old_start = date.fromisoformat(row['starts_on'])
                if old_start < end and old_start+timedelta(days=row['days']) > start:
                    raise HTTPException(409, 'Período de férias já registrado.')
            record_id = c.execute('INSERT INTO vacation_records(employee_id,acquisition_start,days,starts_on,created_at,created_by) VALUES(?,?,?,?,?,?) RETURNING id', (eid, payload.acquisition_start, payload.days, payload.starts_on, m.iso(m.utcnow()), who['user'])).fetchone()['id']
        return {'id': record_id, **quote}

    @app.get('/finance/payments', dependencies=[Depends(m.admin)])
    def payments(month: str | None = None):
        query, params = 'SELECT * FROM payment_history', []
        if month:
            month_or_error(month)
            query += ' WHERE substr(paid_at,1,7)=?'
            params.append(month)
        query += ' ORDER BY paid_at DESC,id DESC'
        with m.db() as c:
            items = m.rows(c.execute(query, params))
            for item in items:
                item['attachments'] = m.rows(c.execute('SELECT id,name,mime,size,sha256,uploaded_at,uploaded_by FROM payment_attachments WHERE payment_id=? ORDER BY id', (item['id'],)))
        return items

    @app.post('/finance/payments/{payment_id}/attachments', dependencies=[Depends(m.admin)], status_code=201)
    def upload(payment_id: int, file: PaymentFile, who=Depends(m.admin_context)):
        try:
            content = base64.b64decode(file.content_base64, validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(400, 'Arquivo codificado inválido.')
        if not content or len(content) > 6*1024*1024:
            raise HTTPException(413, 'Comprovante deve ter até 6 MB.')
        signature = {'application/pdf': b'%PDF-', 'image/png': b'\x89PNG\r\n\x1a\n', 'image/jpeg': b'\xff\xd8\xff'}[file.mime]
        if not content.startswith(signature):
            raise HTTPException(400, 'Conteúdo não corresponde ao formato do arquivo.')
        name = file.name.replace('\\', '/').rsplit('/', 1)[-1].strip()
        if not name or name in ('.', '..') or any(ord(char) < 32 for char in name):
            raise HTTPException(400, 'Nome inválido.')
        digest = hashlib.sha256(content).hexdigest()
        with m.db() as c:
            if not c.execute('SELECT 1 FROM payment_history WHERE id=?', (payment_id,)).fetchone():
                raise HTTPException(404, 'Pagamento não encontrado.')
            file_id = c.execute('INSERT INTO payment_attachments(payment_id,name,mime,size,content,sha256,uploaded_at,uploaded_by) VALUES(?,?,?,?,?,?,?,?) RETURNING id', (payment_id, name, file.mime, len(content), base64.b64encode(content).decode(), digest, m.iso(m.utcnow()), who['user'])).fetchone()['id']
        return {'id': file_id, 'name': name, 'sha256': digest}

    @app.get('/finance/payments/{payment_id}/attachments/{file_id}', dependencies=[Depends(m.admin)])
    def download(payment_id: int, file_id: int):
        with m.db() as c:
            file = c.execute('SELECT * FROM payment_attachments WHERE id=? AND payment_id=?', (file_id, payment_id)).fetchone()
        if not file:
            raise HTTPException(404, 'Comprovante não encontrado.')
        return Response(base64.b64decode(file['content']), media_type=file['mime'], headers={'Content-Disposition': "attachment; filename*=UTF-8''"+m.quote(file['name']), 'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'})

    from app import financial_reports
    financial_reports.register_routes(m)

    @app.get('/spaces/{sid}/staff-rules', dependencies=[Depends(m.admin)])
    def staff_rules(sid: int):
        with m.db() as c:
            return m.rows(c.execute('SELECT r.*,e.name FROM space_staff_rules r JOIN employees e ON e.id=r.employee_id WHERE space_id=?', (sid,)))

    @app.post('/spaces/{sid}/staff-rules', dependencies=[Depends(m.admin)], status_code=201)
    def add_staff_rule(sid: int, rule: StaffRule):
        with m.db() as c:
            c.execute('BEGIN IMMEDIATE')
            if not c.execute('SELECT 1 FROM spaces WHERE id=?', (sid,)).fetchone() or not c.execute('SELECT 1 FROM employees WHERE id=? AND active=1', (rule.employee_id,)).fetchone():
                raise HTTPException(404, 'Espaço ou funcionário ativo não encontrado.')
            if c.execute('SELECT 1 FROM space_staff_rules WHERE space_id=? AND employee_id=? AND duty=?', (sid, rule.employee_id, rule.duty)).fetchone():
                raise HTTPException(409, 'Vínculo já cadastrado.')
            rule_id = c.execute('INSERT INTO space_staff_rules(space_id,employee_id,duty,before_minutes,after_minutes) VALUES(?,?,?,?,?) RETURNING id', (sid, rule.employee_id, rule.duty, rule.before_minutes, rule.after_minutes)).fetchone()['id']
            for b in c.execute("SELECT id FROM space_bookings WHERE space_id=? AND status NOT IN ('cancelled','completed') ORDER BY starts_at", (sid,)).fetchall():
                sync_staff(m, c, b['id'])
        return {'id': rule_id}

    @app.delete('/spaces/{sid}/staff-rules/{rule_id}', dependencies=[Depends(m.admin)])
    def delete_staff_rule(sid: int, rule_id: int):
        with m.db() as c:
            c.execute('BEGIN IMMEDIATE')
            c.execute('DELETE FROM space_staff_rules WHERE id=? AND space_id=?', (rule_id, sid))
            for b in c.execute('SELECT id FROM space_bookings WHERE space_id=?', (sid,)).fetchall():
                sync_staff(m, c, b['id'])
        return {'ok': True}

    @app.get('/space-bookings/{booking_id}/staff')
    def event_staff(booking_id: int, who=Depends(m.manager_or_admin)):
        with m.db() as c:
            return m.rows(c.execute('SELECT a.*,e.name FROM event_staff_assignments a JOIN employees e ON e.id=a.employee_id WHERE booking_id=? ORDER BY starts_at', (booking_id,)))

    @app.get('/employee/event-shifts')
    def my_shifts(who=Depends(m.employee_principal)):
        with m.db() as c:
            return m.rows(c.execute("SELECT a.*,b.title,s.name AS space_name FROM event_staff_assignments a JOIN space_bookings b ON b.id=a.booking_id JOIN spaces s ON s.id=b.space_id WHERE a.employee_id=? AND b.status NOT IN ('cancelled','completed') ORDER BY a.starts_at", (who['employee_id'],)))
