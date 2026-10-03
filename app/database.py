from collections.abc import Mapping

import psycopg
from psycopg.rows import dict_row


class CompatRow(dict):
    def __getitem__(self, key):
        if isinstance(key, int):
            return tuple(self.values())[key]
        return super().__getitem__(key)


class PostgresCursor:
    def __init__(self, cursor):
        self.cursor = cursor

    @property
    def rowcount(self):
        return self.cursor.rowcount

    def _row(self, row):
        return CompatRow(row) if isinstance(row, Mapping) else row

    def fetchone(self):
        row = self.cursor.fetchone()
        return self._row(row) if row is not None else None

    def fetchall(self):
        return [self._row(row) for row in self.cursor.fetchall()]

    def __iter__(self):
        for row in self.cursor:
            yield self._row(row)


class PostgresConnection:
    def __init__(self, connection):
        self.connection = connection

    def execute(self, query, params=()):
        query = query.replace("?", "%s")
        if query.strip().upper() == "BEGIN IMMEDIATE":
            query = "SELECT pg_advisory_xact_lock(624318209)"
        return PostgresCursor(self.connection.execute(query, params))

    def commit(self):
        self.connection.commit()

    def close(self):
        self.connection.close()


def connect_postgres(url):
    return PostgresConnection(psycopg.connect(url, sslmode="require", row_factory=dict_row))
