import os
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "optionsStrat.db")
DATABASE_URL = f"sqlite:///{_DB_PATH}"

DatabaseURL = DATABASE_URL  # re-export for convenience

engine = create_engine(DATABASE_URL, echo=False, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    """Create all tables if they don't exist."""
    from models import singles, strangles, calendars, trade_state, trade_ledger, scan_results  # noqa: F401
    Base.metadata.create_all(bind=engine)


def get_session():
    """Context-manager-friendly session factory."""
    return SessionLocal()
