"""Brazilian monthly payroll calculations. Monetary operations use Decimal."""
import calendar
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

D = lambda value: Decimal(str(value))
ZERO = D(0)
RULES_VERSION = 'BR-2026.2'
PROFILE_DEFAULTS = dict(hired_on='', terminated_on='', dependents=0, alimony=0,
    other_deductions=0, vt_amount=0, vt_rate=.06, va_amount=0, va_discount=0,
    fgts_rate=.08, employer_rate=.20, vacation_days=30, variable_average=0)


def money(value):
    return float(D(value).quantize(D('.01'), rounding=ROUND_HALF_UP))


def validate_month(month):
    if len(month) != 7 or month[4] != '-':
        raise ValueError('Competência inválida. Use AAAA-MM.')
    start = date.fromisoformat(month + '-01')
    return start, date(start.year, start.month, calendar.monthrange(start.year, start.month)[1])


def tax_rules(month):
    start, _ = validate_month(month)
    if start.year != 2026:
        raise ValueError('Tabela fiscal não cadastrada para esta competência. Disponível: 2026.')
    return {'version': RULES_VERSION,
        'inss': [(1621, .075), (2902.84, .09), (4354.27, .12), (8475.55, .14)],
        'inss_offsets': [0, 24.32, 111.40, 198.49], 'inss_max': 988.09,
        'inss_method': 'progressive',
        'irrf': [(2428.80, 0, 0), (2826.65, .075, 182.16),
                 (3751.05, .15, 394.16), (4664.68, .225, 675.49), (None, .275, 908.73)],
        'dependent_deduction': 189.59, 'simplified_deduction': 607.20}


def inss(gross, month='2026-01'):
    total, previous = ZERO, ZERO
    for ceiling, rate in tax_rules(month)['inss']:
        taxable = max(ZERO, min(D(gross), D(ceiling)) - previous)
        total += taxable * D(rate)
        previous = D(ceiling)
    return min(money(total), tax_rules(month)['inss_max'])


def irrf(gross, contribution, profile, month):
    rules = tax_rules(month)
    legal = D(contribution) + D(profile['dependents']) * D(rules['dependent_deduction']) + D(profile['alimony']) + D(profile['other_deductions'])
    deduction = max(legal, D(rules['simplified_deduction']))
    base = max(ZERO, D(gross) - deduction)
    for ceiling, rate, offset in rules['irrf']:
        if ceiling is None or base <= D(ceiling):
            tax = max(ZERO, base * D(rate) - D(offset))
            break
    reduction = min(tax, D('312.89')) if D(gross) <= 5000 else min(tax, max(ZERO, D('978.62') - D('.133145') * D(gross))) if D(gross) <= 7350 else ZERO
    return {'irrf': money(tax - reduction), 'irrf_base': money(base),
            'irrf_deduction': money(deduction), 'irrf_reduction': money(reduction),
            'irrf_method': 'legal' if legal >= D(rules['simplified_deduction']) else 'simplified'}


def anniversary(start, years):
    return start.replace(year=start.year + years, day=min(start.day, calendar.monthrange(start.year + years, start.month)[1]))


def entitlement(profile, as_of, records):
    """Vacation cycles use anniversaries, rather than calendar-year months."""
    hired = date.fromisoformat(profile['hired_on']) if profile['hired_on'] else None
    if not hired or hired > as_of:
        return {'vacation_cycles': [], 'vacation_proportional': 0, 'vacation_total': 0, 'vacation_months': 0}
    cycles, year = [], 0
    remuneration = D(profile.get('remuneration', 0))
    while anniversary(hired, year + 1) <= as_of:
        start, end = anniversary(hired, year), anniversary(hired, year + 1)
        taken = sum(r['days'] for r in records if r['acquisition_start'] == start.isoformat())
        days = max(0, profile['vacation_days'] - taken)
        overdue = as_of >= anniversary(hired, year + 2)
        amount = money(remuneration * D(days) / 30 * D(4) / 3 * (2 if overdue else 1))
        cycles.append({'start': start.isoformat(), 'end': (end-timedelta(days=1)).isoformat(),
                       'remaining_days': days, 'overdue': overdue, 'amount': amount})
        year += 1
    cycle_start = anniversary(hired, year)
    months = 0
    for m in range(12):
        ordinal = cycle_start.year * 12 + cycle_start.month - 1 + m
        y, mo = divmod(ordinal, 12)
        part_start = date(y, mo + 1, min(cycle_start.day, calendar.monthrange(y, mo+1)[1]))
        ordinal += 1
        y, mo = divmod(ordinal, 12)
        part_end = date(y, mo+1, min(cycle_start.day, calendar.monthrange(y, mo+1)[1])) - timedelta(days=1)
        if (min(part_end, as_of) - part_start).days + 1 >= 15:
            months += 1
    proportional = money(remuneration * D(profile['vacation_days']) / 30 * D(months) / 12 * D(4) / 3)
    return {'vacation_cycles': cycles, 'vacation_proportional': proportional,
            'vacation_total': money(D(proportional) + sum((D(c['amount']) for c in cycles), ZERO)), 'vacation_months': months}


def calculate(employee, profile, month, overtime_hours=0, records=()):
    start, end = validate_month(month)
    tax_rules(month)
    p = {**PROFILE_DEFAULTS, **profile}
    hired = date.fromisoformat(p['hired_on']) if p['hired_on'] else start.replace(month=1)
    terminated = date.fromisoformat(p['terminated_on']) if p['terminated_on'] else end
    days = max(0, (min(end, terminated)-max(start, hired)).days+1)
    fraction = D(1) if days == (end-start).days+1 else min(D(1), D(days)/30)
    salary = D(employee['salary'])
    ot = D(money(D(overtime_hours) * salary / 220 * D('1.5'))) if days else ZERO
    gross = D(money(salary * fraction + ot))
    contribution = inss(gross, month)
    taxes = irrf(gross, contribution, p, month)
    vt = D(money(D(p['vt_amount']) * fraction))
    va = D(money(D(p['va_amount']) * fraction))
    vt_discount = D(money(min(vt, salary * fraction * D(p['vt_rate']))))
    va_discount = D(money(min(va, D(p['va_discount']) * fraction)))
    benefit_cash = D(money(D(employee['benefits']) * fraction))
    fgts = D(money(gross * D(p['fgts_rate'])))
    employer = D(money(gross * D(p['employer_rate'])))
    thirteenth_months = sum((min(date(start.year, m, calendar.monthrange(start.year,m)[1]), end, terminated) - max(date(start.year,m,1), hired)).days+1 >= 15 for m in range(1, start.month+1))
    remuneration = salary + D(p['variable_average'])
    eligible = days >= 15
    thirteenth_provision = D(money(remuneration / 12)) if eligible else ZERO
    vacation_provision = D(money(remuneration * D(p['vacation_days']) / 30 / 12 * D(4) / 3)) if eligible else ZERO
    provision_charges = D(money((thirteenth_provision+vacation_provision) * (D(p['fgts_rate'])+D(p['employer_rate']))))
    vacation_salary_offset = D(money(remuneration * D(p['vacation_days']) / 30 / 12)) if eligible else ZERO
    vacation_charge_offset = D(money(vacation_salary_offset * (D(p['fgts_rate'])+D(p['employer_rate']))))
    cash_cost = gross + benefit_cash + vt-vt_discount + va-va_discount + fgts + employer
    result = {'id': employee['id'], 'name': employee['name'], 'role': employee['role'], 'department_id': employee['department_id'],
        'base_salary': float(salary), 'gross': float(gross), 'overtime_hours': overtime_hours,
        'overtime_pay': float(ot), 'inss': contribution, **taxes, 'benefits': float(benefit_cash),
        'vt_amount': float(vt), 'vt_discount': float(vt_discount), 'va_amount': float(va), 'va_discount': float(va_discount),
        'fgts': float(fgts), 'employer_charges': float(employer),
        'net': money(gross-D(contribution)-D(taxes['irrf'])-D(p['alimony'])-vt_discount-va_discount+benefit_cash),
        'company_cost': money(cash_cost), 'accrual_cost': money(cash_cost+thirteenth_provision+vacation_provision+provision_charges-vacation_salary_offset-vacation_charge_offset),
        'thirteenth_months': thirteenth_months, 'thirteenth_proportional': money(remuneration*D(thirteenth_months)/12),
        'thirteenth_provision': float(thirteenth_provision), 'vacation_provision': float(vacation_provision),
        'vacation_salary_offset': float(vacation_salary_offset), 'vacation_charge_offset': float(vacation_charge_offset), 'provision_charges': float(provision_charges), 'rules_version': RULES_VERSION,
        'profile_complete': bool(p['hired_on']), 'calculation_profile': {k: p[k] for k in PROFILE_DEFAULTS}}
    result.update(entitlement({**p, 'remuneration': float(remuneration)}, min(end, terminated), records))
    return result
