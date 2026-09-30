from __future__ import annotations

from fastapi import Request

from app.persistence.database import Database


def get_db(request: Request) -> Database:
    return request.app.state.db
