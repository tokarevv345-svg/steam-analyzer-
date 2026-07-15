from __future__ import annotations

import os

from dotenv import load_dotenv
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

load_dotenv()


def get_database_url() -> str:
    user = os.environ["POSTGRES_USER"]
    password = os.environ["POSTGRES_PASSWORD"]
    host = os.environ["POSTGRES_HOST"]
    port = os.environ["POSTGRES_PORT"]
    db = os.environ["POSTGRES_DB"]
    return f"postgresql+psycopg://{user}:{password}@{host}:{port}/{db}"


_engine: Engine | None = None


def create_db_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_engine(get_database_url())
    return _engine


def check_connection() -> bool:
    engine = create_db_engine()
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    return True


def create_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=create_db_engine())
