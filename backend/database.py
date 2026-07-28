import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

DATABASE_URL = os.environ['DATABASE_URL']

# Production DATABASE_URL points at Supabase's pooler (Session or Transaction
# mode), not straight at Postgres. asyncpg prepares every statement under a
# deterministic auto-generated name and caches it client-side; a pooled
# connection can be handed to a different logical client than the one that
# prepared a given name, so the cached name can collide with one the pooler's
# backend already has, raising DuplicatePreparedStatementError.
#
# statement_cache_size=0 is the actual, complete fix for that: per asyncpg's
# own connection.py (`_get_statement`), setting it to 0 makes every statement
# anonymous and marks it unprepared after use, specifically "assuming people
# are running PgBouncer" (that's asyncpg's own comment). It does not depend
# on how SQLAlchemy pools connections on top.
#
# An earlier version of this also forced poolclass=NullPool, which made
# SQLAlchemy open a brand-new physical connection (full TCP+TLS handshake to
# Supabase) for every single query instead of reusing one - a real, measured
# source of added latency on every request, for no correctness benefit once
# statement_cache_size=0 is in place. Reverted to a real (async-adapted
# queue) pool, sized well under Supabase's pooler connection limit for a
# single backend container, with a recycle so a connection the pooler has
# silently dropped gets replaced instead of erroring.
engine = create_async_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=5,
    pool_recycle=300,
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
