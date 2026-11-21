# 初始结构

`src/models/tables.py` 定义初始关系结构，包含持久隔离副本（`case_replicas`、`replica_assets`、`replica_references`、`replica_holds`、`replica_archive`）与 `business_runs.cutoff`。API 启动时经 `src/models/migrate.py` 显式建表，并为已有演示卷增量补充新增列；破坏性结构变更仍应单独引入版本化迁移。
