"""演示库的启动结构检查：建表并为已有演示卷补充新增列。"""

from sqlalchemy import inspect, text

from src.models.tables import schema

# 早期演示卷的 business_runs 没有 cutoff 列；create_all 不会改已有表，这里做增量补列。
ADDITIVE_COLUMNS = {'business_runs': {'cutoff': 'VARCHAR(10)'}}


def migrate(engine):
    schema.create_all(engine)
    existing = {table: {column['name'] for column in inspect(engine).get_columns(table)}
                for table in ADDITIVE_COLUMNS if table in inspect(engine).get_table_names()}
    with engine.begin() as conn:
        for table, columns in ADDITIVE_COLUMNS.items():
            for name, definition in columns.items():
                if name not in existing.get(table, set()):
                    conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {definition}'))
