"""Additive, portable schema for HR, financial history and event staffing."""
SCHEMA = """
CREATE TABLE IF NOT EXISTS employee_payroll_profiles(
 employee_id INTEGER PRIMARY KEY REFERENCES employees(id), hired_on TEXT NOT NULL DEFAULT '',
 terminated_on TEXT NOT NULL DEFAULT '', dependents INTEGER NOT NULL DEFAULT 0,
 alimony REAL NOT NULL DEFAULT 0, other_deductions REAL NOT NULL DEFAULT 0,
 vt_amount REAL NOT NULL DEFAULT 0, vt_rate REAL NOT NULL DEFAULT 0.06,
 va_amount REAL NOT NULL DEFAULT 0, va_discount REAL NOT NULL DEFAULT 0,
 fgts_rate REAL NOT NULL DEFAULT 0.08, employer_rate REAL NOT NULL DEFAULT 0.20,
 vacation_days INTEGER NOT NULL DEFAULT 30, variable_average REAL NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS payroll_runs(
 id INTEGER PRIMARY KEY, month TEXT NOT NULL UNIQUE, closed_at TEXT NOT NULL,
 closed_by TEXT NOT NULL, rules_version TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS payroll_items(
 id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES payroll_runs(id),
 employee_id INTEGER NOT NULL REFERENCES employees(id), snapshot TEXT NOT NULL,
 UNIQUE(run_id,employee_id));
CREATE TABLE IF NOT EXISTS vacation_records(
 id INTEGER PRIMARY KEY, employee_id INTEGER NOT NULL REFERENCES employees(id),
 acquisition_start TEXT NOT NULL, days INTEGER NOT NULL CHECK(days>0),
 starts_on TEXT NOT NULL, created_at TEXT NOT NULL, created_by TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS payment_history(
 id INTEGER PRIMARY KEY, transaction_id INTEGER NOT NULL UNIQUE REFERENCES transactions(id),
 kind TEXT NOT NULL, description TEXT NOT NULL, amount REAL NOT NULL CHECK(amount>0),
 category TEXT NOT NULL, competence TEXT NOT NULL, paid_at TEXT NOT NULL,
 actor TEXT NOT NULL, department_id INTEGER, source_key TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS payment_attachments(
 id INTEGER PRIMARY KEY, payment_id INTEGER NOT NULL REFERENCES payment_history(id),
 name TEXT NOT NULL, mime TEXT NOT NULL, size INTEGER NOT NULL,
 content TEXT NOT NULL, sha256 TEXT NOT NULL, uploaded_at TEXT NOT NULL, uploaded_by TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS space_staff_rules(
 id INTEGER PRIMARY KEY, space_id INTEGER NOT NULL REFERENCES spaces(id) ON DELETE CASCADE,
 employee_id INTEGER NOT NULL REFERENCES employees(id), duty TEXT NOT NULL,
 before_minutes INTEGER NOT NULL DEFAULT 0, after_minutes INTEGER NOT NULL DEFAULT 0,
 UNIQUE(space_id,employee_id,duty));
CREATE TABLE IF NOT EXISTS event_staff_assignments(
 id INTEGER PRIMARY KEY, booking_id INTEGER NOT NULL REFERENCES space_bookings(id) ON DELETE CASCADE,
 employee_id INTEGER NOT NULL REFERENCES employees(id), duty TEXT NOT NULL,
 starts_at TEXT NOT NULL, ends_at TEXT NOT NULL,
 UNIQUE(booking_id,employee_id,duty));
CREATE INDEX IF NOT EXISTS ix_event_staff_time ON event_staff_assignments(employee_id,starts_at,ends_at);
CREATE INDEX IF NOT EXISTS ix_payment_history_time ON payment_history(paid_at);
"""
MIGRATIONS = [
 "ALTER TABLE transactions ADD COLUMN category TEXT NOT NULL DEFAULT 'operating'",
 "ALTER TABLE transactions ADD COLUMN competence TEXT NOT NULL DEFAULT ''",
 "ALTER TABLE transactions ADD COLUMN source_key TEXT NOT NULL DEFAULT ''",
 "ALTER TABLE spaces ADD COLUMN capacity INTEGER NOT NULL DEFAULT 1",
 "ALTER TABLE spaces ADD COLUMN allow_shared INTEGER NOT NULL DEFAULT 0",
 "ALTER TABLE space_bookings ADD COLUMN status TEXT NOT NULL DEFAULT 'confirmed'",
 "ALTER TABLE space_bookings ADD COLUMN attendees INTEGER NOT NULL DEFAULT 1",
 "ALTER TABLE space_bookings ADD COLUMN exclusive INTEGER NOT NULL DEFAULT 1",
 "ALTER TABLE space_bookings ADD COLUMN compatibility TEXT NOT NULL DEFAULT ''",
 "ALTER TABLE space_bookings ADD COLUMN details TEXT NOT NULL DEFAULT ''",
]
