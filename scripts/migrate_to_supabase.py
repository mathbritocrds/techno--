#!/usr/bin/env python3
"""Copy an existing SIGI SQLite database into an empty Supabase database."""
import os
import sqlite3
from pathlib import Path

import psycopg2

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.getenv("DB_PATH") or ROOT / "data" / "flux.db")
DATABASE_URL = os.getenv("SUPABASE_DATABASE_URL", "").strip()
TABLES = (
    "admins",
    "settings",
    "departments",
    "employees",
    "sessions",
    "products",
    "tasks",
    "task_checklist",
    "task_tags",
    "task_attachments",
    "task_history",
    "time_entries",
    "transactions",
    "materials",
    "spaces",
    "space_bookings",
    "automation_rules",
    "automation_runs",
    "task_materials",
    "cost_analyses",
    "integrations",
    "messages",
    "audit_log",
    "notification_channels",
)


def quoted(identifier):
    return '"' + identifier.replace('"', '""') + '"'


def main():
    if not DATABASE_URL:
        raise SystemExit("Configure SUPABASE_DATABASE_URL como variável de ambiente.")
    if not DB_PATH.is_file():
        raise SystemExit(f"Banco SQLite não encontrado: {DB_PATH}")

    schema = (ROOT / "supabase" / "schema.sql").read_text()
    counts = {}
    with sqlite3.connect(DB_PATH) as source, psycopg2.connect(DATABASE_URL, sslmode="require") as target:
        source.row_factory = sqlite3.Row
        source_tables = {row[0] for row in source.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        with target.cursor() as cursor:
            for statement in schema.split(";"):
                if statement.strip():
                    cursor.execute(statement)

            nonempty = []
            for table in TABLES:
                cursor.execute(f"SELECT EXISTS (SELECT 1 FROM {quoted(table)} LIMIT 1)")
                if cursor.fetchone()[0]:
                    nonempty.append(table)
            if nonempty:
                raise SystemExit(
                    "Migração cancelada: as tabelas de destino já contêm dados: "
                    + ", ".join(nonempty)
                    + ". A importação não sobrescreve dados."
                )

            for table in TABLES:
                if table not in source_tables:
                    counts[table] = 0
                    continue
                records = source.execute(f"SELECT * FROM {quoted(table)}").fetchall()
                columns = [column for column in records[0].keys()] if records else [
                    column[1] for column in source.execute(f"PRAGMA table_info({quoted(table)})")
                ]
                if records:
                    column_sql = ", ".join(quoted(column) for column in columns)
                    values_sql = ", ".join(["%s"] * len(columns))
                    cursor.executemany(
                        f"INSERT INTO {quoted(table)} ({column_sql}) VALUES ({values_sql})",
                        [tuple(record[column] for column in columns) for record in records],
                    )
                counts[table] = len(records)
                if columns and "id" in columns:
                    cursor.execute(
                        f"SELECT setval(pg_get_serial_sequence(%s, 'id'), "
                        f"COALESCE(MAX(id), 1), COUNT(*) > 0) FROM {quoted(table)}",
                        (f"public.{table}",),
                    )

    print("Migração concluída. Registros copiados:")
    for table, count in counts.items():
        print(f"  {table}: {count}")


if __name__ == "__main__":
    main()
