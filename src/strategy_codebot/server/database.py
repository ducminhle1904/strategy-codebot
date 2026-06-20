from collections.abc import Callable

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from strategy_codebot.server.models import Base
from strategy_codebot.server.sql_repository import SQLAlchemyConversationRepository


SessionFactory = Callable[[], Session]


def create_engine_for_url(database_url: str) -> Engine:
    if database_url.startswith("sqlite"):
        options = {"connect_args": {"check_same_thread": False}}
        if database_url.endswith(":memory:"):
            options["poolclass"] = StaticPool
        return create_engine(database_url, **options)
    return create_engine(database_url)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def create_sqlalchemy_repository(database_url: str, *, create_schema: bool = False) -> SQLAlchemyConversationRepository:
    engine = create_engine_for_url(database_url)
    if create_schema:
        Base.metadata.create_all(engine)
    return SQLAlchemyConversationRepository(create_session_factory(engine))


def create_sqlite_repository(database_url: str = "sqlite+pysqlite:///:memory:") -> SQLAlchemyConversationRepository:
    return create_sqlalchemy_repository(database_url, create_schema=True)
