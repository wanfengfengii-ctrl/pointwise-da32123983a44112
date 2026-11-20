from contextlib import contextmanager
import sqlite3


class ArchiveError(Exception):
    """Failure reported by the archive API."""


class InvalidRequest(ArchiveError):
    """The input does not satisfy the operation's contract."""


class UnknownRecord(ArchiveError):
    """The requested business identifier is unknown."""


class Conflict(ArchiveError):
    """A business identifier has already been committed with different input."""


class Unavailable(ArchiveError):
    """The operation could not obtain or commit its complete evidence."""


def require_name(value, field):
    if not isinstance(value, str) or not value.strip() or '\0' in value:
        raise InvalidRequest(f'{field}须为有效的非空标识')
    try:
        value.encode('utf-8')
    except UnicodeEncodeError as error:
        raise InvalidRequest(f'{field}无法编码为 UTF-8') from error


@contextmanager
def transaction(db: sqlite3.Connection, *, write: bool = False):
    """Hold a consistent read view or acquire the write reservation up front."""
    try:
        db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
        try:
            yield
            db.commit()
        except BaseException:
            db.rollback()
            raise
    except sqlite3.DatabaseError as error:
        raise Unavailable(f'归档存储失败：{error}') from error
