from datetime import datetime, timezone
from uuid import UUID


class DomainError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def uuid_text(value):
    try:
        if not isinstance(value, str):
            raise ValueError
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise DomainError(422, 'invalid_uuid', 'Ожидается UUID')


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def pagination(limit=20, offset=0):
    try:
        if isinstance(limit, bool) or isinstance(offset, bool):
            raise ValueError
        limit, offset = int(str(limit)), int(str(offset))
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError
        return limit, offset
    except (ValueError, TypeError):
        raise DomainError(422, 'invalid_pagination', 'limit: 1-100; offset: 0 или больше')
