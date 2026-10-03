from pathlib import Path

from app.database import CompatRow, PostgresConnection


class FakeConnection:
    def __init__(self):
        self.calls = []

    def cursor(self, cursor_factory=None):
        return FakeCursor(self)


class FakeCursor:
    rowcount = 1

    def __init__(self, connection):
        self.connection = connection
        self.rows = []

    def execute(self, query, params):
        self.connection.calls.append((query, params))

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None

    def fetchall(self):
        rows, self.rows = self.rows, []
        return rows


def test_postgres_adapter_translates_placeholders_and_registration_lock():
    raw = FakeConnection()
    connection = PostgresConnection(raw)

    connection.execute("SELECT id FROM employees WHERE id=?", (23,))
    connection.execute("BEGIN IMMEDIATE")

    assert raw.calls == [
        ("SELECT id FROM employees WHERE id=%s", (23,)),
        ("SELECT pg_advisory_xact_lock(624318209)", ()),
    ]


def test_postgres_rows_support_mapping_and_sqlite_style_index_access():
    row = CompatRow({"id": 23, "name": "Ana"})

    assert row["id"] == row[0] == 23
    assert dict(row) == {"id": 23, "name": "Ana"}


def test_supabase_schema_has_all_tables_and_blocks_public_api_access():
    schema = (Path(__file__).resolve().parent.parent / "supabase" / "schema.sql").read_text()
    expected = {
        "admins", "sessions", "settings", "departments", "employees", "products", "tasks",
        "time_entries", "transactions", "materials", "spaces", "cost_analyses", "integrations",
        "messages",
    }

    assert all(f"CREATE TABLE IF NOT EXISTS {table}" in schema for table in expected)
    assert "ENABLE ROW LEVEL SECURITY" in schema
    assert "FROM anon, authenticated" in schema
