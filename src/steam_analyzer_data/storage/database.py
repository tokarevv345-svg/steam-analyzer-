from __future__ import annotations

import os

from dotenv import load_dotenv
from sqlalchemy import Engine, create_engine, text

load_dotenv()


def get_database_url() -> str:
    user = os.environ["POSTGRES_USER"]
    password = os.environ["POSTGRES_PASSWORD"]
    host = os.environ["POSTGRES_HOST"]
    port = os.environ["POSTGRES_PORT"]
    db = os.environ["POSTGRES_DB"]
    return f"postgresql+psycopg://{user}:{password}@{host}:{port}/{db}"


def create_db_engine() -> Engine:
    return create_engine(get_database_url())


def check_connection() -> bool:
    engine = create_db_engine()
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    return True
