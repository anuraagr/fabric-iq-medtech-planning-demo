"""T-SQL access to Fabric SQL databases using a Microsoft Entra access token (ODBC Driver 18).

Verification queries go through `SqlSession.query`, which refuses anything but a single read-only
SELECT/WITH statement. The only write path is `delete_rows`, used by reset, which requires a WHERE clause.
"""

from __future__ import annotations

import re
import struct
import time
from typing import Any, Callable, Sequence

SQL_COPT_SS_ACCESS_TOKEN = 1256
IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")

_COMMENT_RE = re.compile(r"--[^\n]*|/\*.*?\*/", re.S)
_STRING_RE = re.compile(r"'(?:[^']|'')*'")
_FORBIDDEN_RE = re.compile(
    r"\b(insert|update|delete|merge|drop|alter|create|truncate|exec|execute|grant|revoke|deny|backup|restore|"
    r"shutdown|openrowset|openquery|bulk|into|waitfor)\b",
    re.I,
)


class UnsafeSqlError(ValueError):
    """Raised when a statement is not a single read-only SELECT/WITH."""


def encode_access_token(token: str) -> bytes:
    """Pack a token for the SQL_COPT_SS_ACCESS_TOKEN connection attribute (length-prefixed UTF-16-LE)."""
    raw = token.encode("utf-16-le")
    return struct.pack("<I", len(raw)) + raw


def assert_read_only(sql: str) -> None:
    cleaned = _STRING_RE.sub("''", _COMMENT_RE.sub(" ", sql)).strip()
    if not cleaned:
        raise UnsafeSqlError("Empty statement")
    if ";" in cleaned.rstrip(";"):
        raise UnsafeSqlError("Multiple statements are not allowed")
    if not re.match(r"^(select|with)\b", cleaned, re.I):
        raise UnsafeSqlError("Only SELECT/WITH statements are allowed")
    match = _FORBIDDEN_RE.search(cleaned)
    if match:
        raise UnsafeSqlError(f"Statement contains a forbidden keyword: {match.group(0)}")


def quote_identifier(name: str) -> str:
    if not IDENTIFIER_RE.match(name):
        raise ValueError(f"Unsafe SQL identifier: {name!r}")
    return f"[{name}]"


TRANSIENT_LOGIN_STATE = "08001"  # TCP/login timeout: a Fabric SQL database that was idle can take a while to answer its first connection


def connect(server: str, database: str, token: str, *, driver: str = "ODBC Driver 18 for SQL Server", timeout: int = 90, autocommit: bool = True,
            attempts: int = 3, retry_delay: float = 15.0, sleep: Callable[[float], None] = time.sleep):
    import pyodbc  # imported lazily so unit tests do not need ODBC

    # The Microsoft ODBC driver ignores a "Connection Timeout" keyword and falls back to a 15 s login timeout,
    # which is shorter than the ~20 s an idle Fabric SQL database needs for its first login. The login timeout
    # has to be passed as the connection attribute (pyodbc's timeout argument).
    connection_string = f"Driver={{{driver}}};Server=tcp:{server},1433;Database={database};Encrypt=yes;TrustServerCertificate=no;"
    attrs = {SQL_COPT_SS_ACCESS_TOKEN: encode_access_token(token)}
    for attempt in range(1, attempts + 1):
        try:
            return pyodbc.connect(connection_string, attrs_before=attrs, autocommit=autocommit, timeout=timeout)
        except pyodbc.OperationalError as exc:
            if attempt == attempts or not (exc.args and exc.args[0] == TRANSIENT_LOGIN_STATE):
                raise
            sleep(retry_delay)


class SqlSession:
    def __init__(self, connection: Any) -> None:
        self._connection = connection

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "SqlSession":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        assert_read_only(sql)
        cursor = self._connection.cursor()
        try:
            cursor.execute(sql, tuple(params))
            columns = [column[0] for column in cursor.description or []]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]
        finally:
            cursor.close()

    def scalar(self, sql: str, params: Sequence[Any] = ()) -> Any:
        rows = self.query(sql, params)
        if not rows:
            return None
        return next(iter(rows[0].values()))

    def table_columns(self, schema: str, table: str) -> list[dict[str, Any]]:
        return self.query(
            "SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, NUMERIC_PRECISION, NUMERIC_SCALE, IS_NULLABLE "
            "FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ? ORDER BY ORDINAL_POSITION",
            (schema, table),
        )

    def tables(self) -> list[dict[str, Any]]:
        return self.query("SELECT TABLE_SCHEMA, TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_TYPE = 'BASE TABLE' ORDER BY 1, 2")

    def delete_rows(self, schema: str, table: str, where_sql: str, params: Sequence[Any] = ()) -> int:
        """Bounded DELETE used only by reset. A WHERE clause is mandatory; identifiers are validated."""
        if not where_sql or not where_sql.strip():
            raise ValueError("delete_rows requires a WHERE clause; use cleanup to remove whole objects")
        assert_read_only("SELECT 1 WHERE " + where_sql)
        cursor = self._connection.cursor()
        try:
            cursor.execute(f"DELETE FROM {quote_identifier(schema)}.{quote_identifier(table)} WHERE {where_sql}", tuple(params))
            return int(cursor.rowcount)
        finally:
            cursor.close()
