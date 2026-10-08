"""Exercise Psycopg's real parameter parser without requiring a database server.

The loopback socket supplies only startup metadata. SQL is formatted with
mogrify(), never executed against this handshake stub.
"""

import socket
import struct
import threading

import psycopg2
import pytest

from app.database import PostgresConnection


@pytest.fixture(scope="module")
def driver_cursor():
    def packet(kind, payload):
        return kind + struct.pack("!I", len(payload) + 4) + payload

    def read_exact(connection, size):
        data = b""
        while len(data) < size:
            chunk = connection.recv(size - len(data))
            if not chunk:
                raise EOFError("Incomplete driver startup")
            data += chunk
        return data

    def handshake():
        connection, _ = listener.accept()
        with connection:
            connection.settimeout(3)
            size = struct.unpack("!I", read_exact(connection, 4))[0]
            read_exact(connection, size - 4)
            response = packet(b"R", struct.pack("!I", 0))
            for name, value in (
                ("DateStyle", "ISO, MDY"),
                ("integer_datetimes", "on"),
                ("server_version", "17.0"),
                ("client_encoding", "UTF8"),
                ("standard_conforming_strings", "on"),
            ):
                response += packet(b"S", name.encode() + b"\0" + value.encode() + b"\0")
            response += packet(b"K", struct.pack("!II", 1, 1)) + packet(b"Z", b"I")
            connection.sendall(response)
            # Only the driver's Terminate packet is expected after mogrify().
            assert read_exact(connection, 1) == b"X"
            size = struct.unpack("!I", read_exact(connection, 4))[0]
            read_exact(connection, size - 4)

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    worker = threading.Thread(target=handshake, daemon=True)
    worker.start()
    raw = psycopg2.connect(
        host="127.0.0.1", port=listener.getsockname()[1], user="postgres",
        dbname="postgres", sslmode="disable", gssencmode="disable", connect_timeout=3,
    )
    try:
        with raw.cursor() as cursor:
            yield cursor
    finally:
        raw.close()
        listener.close()
        worker.join(timeout=3)
        assert not worker.is_alive()


@pytest.mark.parametrize("query, params, expected", [
    (
        "SELECT SUM(amount) FROM transactions WHERE source_key NOT LIKE 'payroll:%' "
        "AND due>=? AND due<?",
        ("2026-10-01", "2026-11-01"),
        b"SELECT SUM(amount) FROM transactions WHERE source_key NOT LIKE 'payroll:%' "
        b"AND due>='2026-10-01' AND due<'2026-11-01'",
    ),
    ("SELECT '100%'", (), b"SELECT '100%'"),
    (
        "SELECT description FROM transactions WHERE description=? AND id % 2 = 0",
        ("50%_O'Brien",),
        b"SELECT description FROM transactions WHERE description='50%_O''Brien' AND id % 2 = 0",
    ),
])
def test_adapter_preserves_percent_literals_and_bound_values(driver_cursor, query, params, expected):
    class FormattingCursor:
        def execute(self, sql, values):
            self.formatted = driver_cursor.mogrify(sql, values)

    class FormattingConnection:
        def cursor(self, cursor_factory=None):
            return FormattingCursor()

    result = PostgresConnection(FormattingConnection()).execute(query, params)
    assert result.cursor.formatted == expected
