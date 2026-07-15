from app.db.base import Base, engine


ITEM_MIGRATIONS = {
    "effective_at": "ALTER TABLE items ADD COLUMN effective_at DATETIME",
    "expires_at": "ALTER TABLE items ADD COLUMN expires_at DATETIME",
    "status": "ALTER TABLE items ADD COLUMN status VARCHAR(32) DEFAULT 'unknown'",
    "replaced_by": "ALTER TABLE items ADD COLUMN replaced_by TEXT DEFAULT ''",
    "last_checked_at": "ALTER TABLE items ADD COLUMN last_checked_at DATETIME",
    "status_reason": "ALTER TABLE items ADD COLUMN status_reason TEXT DEFAULT ''",
    "override_enabled": "ALTER TABLE items ADD COLUMN override_enabled BOOLEAN DEFAULT 0",
    "override_status": "ALTER TABLE items ADD COLUMN override_status VARCHAR(32) DEFAULT ''",
    "override_effective_at": "ALTER TABLE items ADD COLUMN override_effective_at DATETIME",
    "override_expires_at": "ALTER TABLE items ADD COLUMN override_expires_at DATETIME",
    "override_replaced_by": "ALTER TABLE items ADD COLUMN override_replaced_by TEXT DEFAULT ''",
    "override_reason": "ALTER TABLE items ADD COLUMN override_reason TEXT DEFAULT ''",
    "override_updated_at": "ALTER TABLE items ADD COLUMN override_updated_at DATETIME",
}


def _migrate_sqlite_items() -> None:
    if engine.dialect.name != "sqlite":
        return

    with engine.begin() as connection:
        rows = connection.exec_driver_sql("PRAGMA table_info(items)").fetchall()
        columns = {row[1] for row in rows}
        for column_name, ddl in ITEM_MIGRATIONS.items():
            if column_name not in columns:
                connection.exec_driver_sql(ddl)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    _migrate_sqlite_items()
