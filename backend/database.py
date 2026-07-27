import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

DATABASE_URL = os.environ['DATABASE_URL']

# Production DATABASE_URL points at Supabase's pooler (Session or Transaction
# mode), not straight at Postgres. asyncpg prepares every statement under a
# deterministic auto-generated name and caches it client-side; a pooled
# connection can be handed to a different logical client than the one that
# prepared a given name, so the cached name can collide with one the pooler's
# backend already has, raising DuplicatePreparedStatementError. NullPool stops
# SQLAlchemy from layering its own pool on top of the pooler's (avoids
# double-pooling), and statement_cache_size=0 disables asyncpg's client-side
# statement cache so it never re-sends a name that could already be prepared
# on whatever backend connection the pooler hands back.
# (`prepared_statement_cache_size` / `prepared_statement_name_func` are not
# real asyncpg 0.31 connect() parameters - passing them raises TypeError on
# every connection attempt. statement_cache_size=0 alone is the complete fix.)
engine = create_async_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    poolclass=NullPool,
    connect_args={"statement_cache_size": 0},
)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
