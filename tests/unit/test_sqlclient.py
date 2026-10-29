from __future__ import annotations

import struct
import sys

import pytest

from scripts.lib.sqlclient import SqlSession, UnsafeSqlError, assert_read_only, connect, encode_access_token, quote_identifier


def test_token_struct_is_length_prefixed_utf16le():
    packed = encode_access_token("abc")
    assert struct.unpack("<I", packed[:4])[0] == 6
    assert packed[4:] == "abc".encode("utf-16-le")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        "select * from dbo.t where a = ? ;",
        "WITH x AS (SELECT 1 AS v) SELECT v FROM x",
        "SELECT 'delete me' AS s",  # forbidden word only inside a string literal
        "SELECT 1 -- ; DROP TABLE x",  # inside a comment
        "SELECT /* insert */ 1",
    ],
)
def test_read_only_accepts_safe_selects(sql):
    assert_read_only(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "",
        "DROP TABLE dbo.t",
        "SELECT 1; DROP TABLE dbo.t",
        "SELECT * INTO #tmp FROM dbo.t",
        "WITH x AS (SELECT 1) DELETE FROM dbo.t",
        "EXEC sp_who",
        "UPDATE dbo.t SET a = 1",
        "SELECT * FROM OPENROWSET('x')",
        "INSERT INTO dbo.t VALUES (1)",
    ],
)
def test_read_only_rejects_everything_else(sql):
    with pytest.raises(UnsafeSqlError):
        assert_read_only(sql)


def test_quote_identifier_validates():
    assert quote_identifier("Plan_Revenue") == "[Plan_Revenue]"
    for bad in ("a b", "a]; DROP", "1abc", ""):
        with pytest.raises(ValueError):
            quote_identifier(bad)


class FakeCursor:
    def __init__(self, store):
        self.store = store
        self.description = [("a",), ("b",)]
        self.rowcount = 3

    def execute(self, sql, params=()):
        self.store.append((sql, params))

    def fetchall(self):
        return [(1, "x"), (2, "y")]

    def close(self):
        pass


class FakeConnection:
    def __init__(self):
        self.executed = []
        self.closed = False

    def cursor(self):
        return FakeCursor(self.executed)

    def close(self):
        self.closed = True


def test_query_returns_dicts_and_guards():
    connection = FakeConnection()
    session = SqlSession(connection)
    assert session.query("SELECT a, b FROM dbo.t WHERE a = ?", (1,)) == [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}]
    assert connection.executed[0][1] == (1,)
    with pytest.raises(UnsafeSqlError):
        session.query("DELETE FROM dbo.t")
    assert session.scalar("SELECT a FROM dbo.t") == 1


def test_delete_rows_requires_where_and_valid_identifiers():
    connection = FakeConnection()
    session = SqlSession(connection)
    assert session.delete_rows("dbo", "Plan_Revenue", "[Scenario] = ?", ("Upside",)) == 3
    assert connection.executed[0][0] == "DELETE FROM [dbo].[Plan_Revenue] WHERE [Scenario] = ?"
    with pytest.raises(ValueError):
        session.delete_rows("dbo", "Plan_Revenue", "  ")
    with pytest.raises(ValueError):
        session.delete_rows("dbo", "x]; DROP", "1 = 1")
    with pytest.raises(UnsafeSqlError):
        session.delete_rows("dbo", "t", "1 = 1; DROP TABLE y")


def test_context_manager_closes():
    connection = FakeConnection()
    with SqlSession(connection):
        pass
    assert connection.closed


class FakePyodbc:
    class OperationalError(Exception):
        pass

    def __init__(self, failures):
        self.failures = list(failures)
        self.calls = 0
        self.last_call = {}

    def connect(self, connection_string, attrs_before=None, autocommit=True, timeout=0):
        self.calls += 1
        self.last_call = {"connection_string": connection_string, "timeout": timeout}
        if self.failures:
            raise self.failures.pop(0)
        return "connection"


def test_connect_passes_the_login_timeout_as_a_connection_attribute(monkeypatch):
    fake = FakePyodbc([])
    monkeypatch.setitem(sys.modules, "pyodbc", fake)
    assert connect("srv", "db", "token", timeout=120) == "connection"
    assert fake.last_call["timeout"] == 120
    # the ODBC driver silently ignores a connection-string "Connection Timeout" and would use its 15 s default
    assert "Timeout" not in fake.last_call["connection_string"]
    assert "Encrypt=yes;TrustServerCertificate=no" in fake.last_call["connection_string"]


def test_connect_retries_transient_login_timeouts(monkeypatch):
    fake = FakePyodbc([])
    fake.failures = [fake.OperationalError("08001", "Login timeout expired"), fake.OperationalError("08001", "again")]
    monkeypatch.setitem(sys.modules, "pyodbc", fake)
    sleeps: list[float] = []
    assert connect("srv", "db", "token", sleep=sleeps.append, retry_delay=5) == "connection"
    assert fake.calls == 3 and sleeps == [5, 5]


def test_connect_gives_up_after_the_last_attempt_and_never_retries_other_errors(monkeypatch):
    fake = FakePyodbc([])
    fake.failures = [fake.OperationalError("08001", "t")] * 3
    monkeypatch.setitem(sys.modules, "pyodbc", fake)
    with pytest.raises(fake.OperationalError):
        connect("srv", "db", "token", sleep=lambda s: None, attempts=3)
    assert fake.calls == 3

    other = FakePyodbc([])
    other.failures = [other.OperationalError("28000", "Login failed for user")]
    monkeypatch.setitem(sys.modules, "pyodbc", other)
    with pytest.raises(other.OperationalError):
        connect("srv", "db", "token", sleep=lambda s: pytest.fail("must not retry an authentication failure"))
    assert other.calls == 1
