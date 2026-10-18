from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from app.models import Base
from app.config import APP_ENV, DATABASE_URL
from pathlib import Path
from sqlalchemy.engine import make_url
from sqlalchemy import event

engine = create_async_engine(DATABASE_URL, echo=False,
                             connect_args={"timeout": 30} if DATABASE_URL.startswith("sqlite") else {})
if DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine.sync_engine, "connect")
    def sqlite_pragmas(connection, _):
        cursor = connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA busy_timeout=15000")
        finally:
            cursor.close()
async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

async def init_db():
    if DATABASE_URL.startswith("sqlite"):
        database_path = make_url(DATABASE_URL).database
        if database_path and database_path != ":memory:":
            Path(database_path).parent.mkdir(parents=True, exist_ok=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Additive migration for SQLite databases created by earlier versions.
        if DATABASE_URL.startswith("sqlite"):
            result = await conn.exec_driver_sql("PRAGMA table_info(campaigns)")
            existing = {row[1] for row in result.fetchall()}
            additions = {
                "category": "VARCHAR NOT NULL DEFAULT 'general'",
                "budget": "FLOAT NOT NULL DEFAULT 0",
                "currency": "VARCHAR NOT NULL DEFAULT 'INR'",
                "landing_page": "TEXT NOT NULL DEFAULT ''",
                "language": "VARCHAR NOT NULL DEFAULT 'English'",
                "tone": "VARCHAR NOT NULL DEFAULT 'friendly'",
                "tags": "VARCHAR NOT NULL DEFAULT ''",
                "internal_notes": "TEXT NOT NULL DEFAULT ''",
                "telegram_user_id": "INTEGER",
                "audience": "TEXT NOT NULL DEFAULT ''",
                "audience_size": "INTEGER NOT NULL DEFAULT 0",
                "product_name": "VARCHAR NOT NULL DEFAULT ''",
                "value_proposition": "TEXT NOT NULL DEFAULT ''",
                "proof": "TEXT NOT NULL DEFAULT ''",
                "offer": "TEXT NOT NULL DEFAULT ''",
                "call_to_action": "VARCHAR NOT NULL DEFAULT ''",
                "primary_metric": "VARCHAR NOT NULL DEFAULT 'conversions'",
                "target_value": "INTEGER NOT NULL DEFAULT 0",
                "deadline": "VARCHAR NOT NULL DEFAULT ''",
                "consent_confirmed": "BOOLEAN NOT NULL DEFAULT 0",
            }
            for name, definition in additions.items():
                if name not in existing:
                    await conn.exec_driver_sql(f"ALTER TABLE campaigns ADD COLUMN {name} {definition}")
            await conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_campaigns_telegram_user_id ON campaigns (telegram_user_id)")
            if APP_ENV == "development":
                await conn.exec_driver_sql("UPDATE campaigns SET telegram_user_id = 0 WHERE telegram_user_id IS NULL")
            result = await conn.exec_driver_sql("PRAGMA table_info(metric_snapshots)")
            metric_columns = {row[1] for row in result.fetchall()}
            for name in ("replies", "signups"):
                if name not in metric_columns:
                    await conn.exec_driver_sql(f"ALTER TABLE metric_snapshots ADD COLUMN {name} INTEGER NOT NULL DEFAULT 0")

async def get_session():
    async with async_session() as session:
        yield session
