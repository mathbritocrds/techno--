"""Exact-cent ledger shared by SQLite and PostgreSQL migrations."""
ACCOUNTS = [
 ('1.1.01','Caixa e equivalentes','asset','current_assets',''),
 ('1.1.02','Contas a receber — clientes','asset','current_assets',''),
 ('1.1.03','Estoques','asset','current_assets',''),
 ('1.2.01','Realizável a longo prazo','asset','noncurrent_assets',''),
 ('1.2.02','Investimentos','asset','noncurrent_assets',''),
 ('1.2.03','Imobilizado — máquinas e imóveis','asset','noncurrent_assets',''),
 ('1.2.04','Intangível — marcas e patentes','asset','noncurrent_assets',''),
 ('2.1.01','Fornecedores','liability','current_liabilities',''),
 ('2.1.02','Empréstimos e financiamentos de curto prazo','liability','current_liabilities',''),
 ('2.1.03','Obrigações trabalhistas e fiscais','liability','current_liabilities',''),
 ('2.1.04','Provisões de 13º e férias','liability','current_liabilities',''),
 ('2.2.01','Financiamentos de longo prazo','liability','noncurrent_liabilities',''),
 ('3.1.01','Capital social','equity','equity',''),
 ('3.2.01','Lucros ou prejuízos acumulados','equity','equity',''),
 ('4.1.01','Receita operacional bruta','revenue','result','revenue'),
 ('4.2.01','Outras receitas operacionais','revenue','result','other_operating'),
 ('4.3.01','Receitas de investimentos','revenue','result','investment'),
 ('4.4.01','Receitas da categoria financiamento','revenue','result','financing'),
 ('4.5.01','Receitas de operações descontinuadas','revenue','result','discontinued'),
 ('5.1.01','Deduções e impostos sobre a receita','expense','result','deductions'),
 ('5.2.01','Custo dos bens e serviços vendidos','expense','result','cost'),
 ('5.3.01','Despesas operacionais','expense','result','operating'),
 ('5.3.02','Despesas com pessoal e provisões','expense','result','operating'),
 ('5.4.01','Outras despesas operacionais','expense','result','other_operating'),
 ('5.5.01','Despesas da categoria investimento','expense','result','investment'),
 ('5.6.01','Despesas financeiras — custo da dívida','expense','result','financing'),
 ('5.7.01','IRPJ e CSLL sobre o lucro','expense','result','income_tax'),
 ('5.8.01','Despesas de operações descontinuadas','expense','result','discontinued'),
]

TABLES = """
CREATE TABLE IF NOT EXISTS ledger_accounts(
 code TEXT PRIMARY KEY,name TEXT NOT NULL,kind TEXT NOT NULL CHECK(kind IN ('asset','liability','equity','revenue','expense')),
 section TEXT NOT NULL,dre_category TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS ledger_journals(
 id INTEGER PRIMARY KEY AUTOINCREMENT,booked_on TEXT NOT NULL,description TEXT NOT NULL,
 department_id INTEGER,status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','posted')),
 source_key TEXT NOT NULL UNIQUE,payload_hash TEXT NOT NULL,
 reverses_id INTEGER UNIQUE REFERENCES ledger_journals(id),created_at TEXT NOT NULL,created_by TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ledger_lines(
 id INTEGER PRIMARY KEY AUTOINCREMENT,journal_id INTEGER NOT NULL REFERENCES ledger_journals(id),
 account_code TEXT NOT NULL REFERENCES ledger_accounts(code),debit_cents INTEGER NOT NULL DEFAULT 0,
 credit_cents INTEGER NOT NULL DEFAULT 0,
 CHECK(debit_cents>=0 AND credit_cents>=0 AND ((debit_cents>0 AND credit_cents=0) OR (credit_cents>0 AND debit_cents=0))));
CREATE TABLE IF NOT EXISTS audit_events(
 id INTEGER PRIMARY KEY AUTOINCREMENT,at TEXT NOT NULL,actor TEXT NOT NULL,role TEXT NOT NULL,
 module TEXT NOT NULL,resource_id TEXT NOT NULL,department_id INTEGER,
 before_json TEXT NOT NULL,after_json TEXT NOT NULL,reason TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS time_entry_adjustments(
 id INTEGER PRIMARY KEY AUTOINCREMENT,entry_id INTEGER NOT NULL REFERENCES time_entries(id),
 corrected_at TEXT NOT NULL,reason TEXT NOT NULL,created_at TEXT NOT NULL,created_by TEXT NOT NULL,
 department_id INTEGER);
CREATE INDEX IF NOT EXISTS ix_ledger_date_scope ON ledger_journals(booked_on,department_id,status);
CREATE INDEX IF NOT EXISTS ix_ledger_lines_account ON ledger_lines(account_code,journal_id);
CREATE INDEX IF NOT EXISTS ix_adjustment_entry ON time_entry_adjustments(entry_id,id);
CREATE INDEX IF NOT EXISTS ix_audit_events_scope ON audit_events(department_id,module,id);
CREATE INDEX IF NOT EXISTS ix_time_entries_page ON time_entries(at,id);
CREATE VIEW IF NOT EXISTS effective_time_entries AS SELECT t.*,
 COALESCE((SELECT a.corrected_at FROM time_entry_adjustments a WHERE a.entry_id=t.id ORDER BY a.id DESC LIMIT 1),t.at) AS effective_at
 FROM time_entries t;
"""
SEEDS = '\n'.join("INSERT INTO ledger_accounts(code,name,kind,section,dre_category) VALUES("+
                  ','.join("'"+v.replace("'","''")+"'" for v in row)+") ON CONFLICT(code) DO NOTHING;" for row in ACCOUNTS)
SQLITE_TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS ledger_insert_draft BEFORE INSERT ON ledger_journals WHEN NEW.status<>'draft'
 BEGIN SELECT RAISE(ABORT,'Journal must start as draft'); END;
CREATE TRIGGER IF NOT EXISTS ledger_check_posting BEFORE UPDATE OF status ON ledger_journals WHEN NEW.status='posted'
 BEGIN SELECT CASE WHEN (SELECT COUNT(*) FROM ledger_lines WHERE journal_id=NEW.id)<2
 OR (SELECT COALESCE(SUM(debit_cents-credit_cents),0) FROM ledger_lines WHERE journal_id=NEW.id)<>0
 THEN RAISE(ABORT,'Unbalanced journal') END; END;
CREATE TRIGGER IF NOT EXISTS ledger_no_change BEFORE UPDATE ON ledger_journals WHEN OLD.status='posted'
 BEGIN SELECT RAISE(ABORT,'Posted journal is immutable'); END;
CREATE TRIGGER IF NOT EXISTS ledger_no_delete BEFORE DELETE ON ledger_journals WHEN OLD.status='posted'
 BEGIN SELECT RAISE(ABORT,'Posted journal is immutable'); END;
CREATE TRIGGER IF NOT EXISTS lines_no_insert BEFORE INSERT ON ledger_lines WHEN (SELECT status FROM ledger_journals WHERE id=NEW.journal_id)='posted'
 BEGIN SELECT RAISE(ABORT,'Posted lines are immutable'); END;
CREATE TRIGGER IF NOT EXISTS lines_no_update BEFORE UPDATE ON ledger_lines WHEN (SELECT status FROM ledger_journals WHERE id=OLD.journal_id)='posted' OR (SELECT status FROM ledger_journals WHERE id=NEW.journal_id)='posted'
 BEGIN SELECT RAISE(ABORT,'Posted lines are immutable'); END;
CREATE TRIGGER IF NOT EXISTS lines_no_delete BEFORE DELETE ON ledger_lines WHEN (SELECT status FROM ledger_journals WHERE id=OLD.journal_id)='posted'
 BEGIN SELECT RAISE(ABORT,'Posted lines are immutable'); END;
"""
for table in ('audit_events','time_entry_adjustments'):
    for operation in ('UPDATE','DELETE'):
        SQLITE_TRIGGERS += f"CREATE TRIGGER IF NOT EXISTS {table}_no_{operation.lower()} BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT,'Append-only history'); END;\n"
SQLITE_SCHEMA = TABLES + SEEDS + SQLITE_TRIGGERS

POSTGRES_SCHEMA = TABLES.replace('CREATE VIEW IF NOT EXISTS','CREATE OR REPLACE VIEW').replace('INTEGER PRIMARY KEY AUTOINCREMENT','BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY').replace(' INTEGER',' BIGINT') + SEEDS + """
CREATE OR REPLACE FUNCTION sigi_guard_journal() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='INSERT' THEN
  IF NEW.status<>'draft' THEN RAISE EXCEPTION 'Journal must start as draft'; END IF;
  RETURN NEW;
 END IF;
 IF OLD.status='posted' THEN RAISE EXCEPTION 'Posted journal is immutable'; END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF;
 IF NEW.status='posted' AND ((SELECT COUNT(*) FROM ledger_lines WHERE journal_id=NEW.id)<2 OR
 (SELECT COALESCE(SUM(debit_cents-credit_cents),0) FROM ledger_lines WHERE journal_id=NEW.id)<>0)
 THEN RAISE EXCEPTION 'Unbalanced journal'; END IF;
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS sigi_journal_guard ON ledger_journals;
CREATE TRIGGER sigi_journal_guard BEFORE INSERT OR UPDATE OR DELETE ON ledger_journals FOR EACH ROW EXECUTE FUNCTION sigi_guard_journal();
CREATE OR REPLACE FUNCTION sigi_guard_line() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP<>'INSERT' AND (SELECT status FROM ledger_journals WHERE id=OLD.journal_id)='posted' THEN RAISE EXCEPTION 'Posted lines are immutable'; END IF;
 IF TG_OP<>'DELETE' AND (SELECT status FROM ledger_journals WHERE id=NEW.journal_id)='posted' THEN RAISE EXCEPTION 'Posted lines are immutable'; END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF;
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS sigi_line_guard ON ledger_lines;
CREATE TRIGGER sigi_line_guard BEFORE INSERT OR UPDATE OR DELETE ON ledger_lines FOR EACH ROW EXECUTE FUNCTION sigi_guard_line();
CREATE OR REPLACE FUNCTION sigi_append_only() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Append-only history'; END $$;
DROP TRIGGER IF EXISTS sigi_audit_append ON audit_events;
CREATE TRIGGER sigi_audit_append BEFORE UPDATE OR DELETE ON audit_events FOR EACH ROW EXECUTE FUNCTION sigi_append_only();
DROP TRIGGER IF EXISTS sigi_adjustment_append ON time_entry_adjustments;
CREATE TRIGGER sigi_adjustment_append BEFORE UPDATE OR DELETE ON time_entry_adjustments FOR EACH ROW EXECUTE FUNCTION sigi_append_only();
DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='sigi_accounting_api') THEN CREATE ROLE sigi_accounting_api NOLOGIN NOBYPASSRLS; END IF; END $$;
GRANT sigi_accounting_api TO CURRENT_USER;
GRANT USAGE ON SCHEMA public TO sigi_accounting_api;
GRANT SELECT ON ledger_accounts TO sigi_accounting_api;
GRANT SELECT,INSERT,UPDATE ON ledger_journals TO sigi_accounting_api;
GRANT SELECT,INSERT ON ledger_lines,audit_events,time_entry_adjustments TO sigi_accounting_api;
GRANT USAGE,SELECT ON SEQUENCE ledger_journals_id_seq,ledger_lines_id_seq,audit_events_id_seq,time_entry_adjustments_id_seq TO sigi_accounting_api;
"""
for table in ('ledger_accounts','ledger_journals','ledger_lines','audit_events','time_entry_adjustments'):
    POSTGRES_SCHEMA += f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY; REVOKE ALL ON {table} FROM anon,authenticated;\n'
    if table == 'ledger_accounts':
        condition="current_setting('sigi.role',true) IN ('admin','manager')"
    elif table == 'ledger_lines':
        condition="EXISTS(SELECT 1 FROM ledger_journals j WHERE j.id=journal_id)"
    else:
        condition="(current_setting('sigi.role',true)='admin' OR (current_setting('sigi.role',true)='manager' AND department_id=NULLIF(current_setting('sigi.department_id',true),'')::bigint))"
    POSTGRES_SCHEMA += f'DROP POLICY IF EXISTS sigi_scope ON {table}; CREATE POLICY sigi_scope ON {table} TO sigi_accounting_api USING ({condition}) WITH CHECK ({condition});\n'

POSTGRES_SCHEMA += """
ALTER VIEW effective_time_entries SET (security_invoker=true);
REVOKE ALL ON effective_time_entries FROM anon,authenticated;
GRANT SELECT ON effective_time_entries TO sigi_accounting_api;
GRANT SELECT ON time_entries,materials TO sigi_accounting_api;
GRANT SELECT(id,department_id) ON employees TO sigi_accounting_api;
DROP POLICY IF EXISTS sigi_staff_lookup ON employees;
CREATE POLICY sigi_staff_lookup ON employees FOR SELECT TO sigi_accounting_api USING
(current_setting('sigi.role',true)='admin' OR (current_setting('sigi.role',true)='manager' AND department_id=NULLIF(current_setting('sigi.department_id',true),'')::bigint));
DROP POLICY IF EXISTS sigi_clock_read ON time_entries;
CREATE POLICY sigi_clock_read ON time_entries FOR SELECT TO sigi_accounting_api USING
(current_setting('sigi.role',true)='admin' OR (current_setting('sigi.role',true)='manager' AND EXISTS(SELECT 1 FROM employees e WHERE e.id=employee_id AND e.department_id=NULLIF(current_setting('sigi.department_id',true),'')::bigint)));
DROP POLICY IF EXISTS sigi_material_read ON materials;
CREATE POLICY sigi_material_read ON materials FOR SELECT TO sigi_accounting_api USING(current_setting('sigi.role',true) IN ('admin','manager'));
DROP POLICY IF EXISTS sigi_scope ON time_entry_adjustments;
CREATE POLICY sigi_scope ON time_entry_adjustments TO sigi_accounting_api USING
(current_setting('sigi.role',true)='admin' OR (current_setting('sigi.role',true)='manager' AND department_id=NULLIF(current_setting('sigi.department_id',true),'')::bigint))
WITH CHECK (current_setting('sigi.role',true)='admin' OR (current_setting('sigi.role',true)='manager' AND department_id=NULLIF(current_setting('sigi.department_id',true),'')::bigint AND EXISTS(SELECT 1 FROM time_entries t JOIN employees e ON e.id=t.employee_id WHERE t.id=entry_id AND e.department_id=NULLIF(current_setting('sigi.department_id',true),'')::bigint)));
"""
