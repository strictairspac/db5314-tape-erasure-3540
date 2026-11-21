from sqlalchemy import (JSON, Column, ForeignKey, ForeignKeyConstraint,
                        Integer, MetaData, PrimaryKeyConstraint, String,
                        Table, Text)

schema = MetaData()
users = Table('accounts', schema,
    Column('id', Integer, primary_key=True),
    Column('username', String(40), nullable=False, unique=True),
    Column('password_hash', String(200), nullable=False),
    Column('role', String(20), nullable=False))
cases = Table('business_cases', schema,
    Column('id', Integer, primary_key=True),
    Column('slug', String(64), nullable=False, unique=True),
    Column('title', String(120), nullable=False),
    Column('payload', JSON, nullable=False),
    Column('author', String(40), nullable=False),
    Column('created_at', String(32), nullable=False))
runs = Table('business_runs', schema,
    Column('id', Integer, primary_key=True),
    Column('case_id', ForeignKey('business_cases.id'), nullable=False, index=True),
    Column('status', String(20), nullable=False, index=True),
    Column('owner', String(40), nullable=False),
    Column('cutoff', String(10)),
    Column('attempt_token', String(36)),
    Column('started_at', String(32)),
    Column('finished_at', String(32)),
    Column('created_at', String(32), nullable=False),
    Column('error', Text))
evidence = Table('business_evidence', schema,
    Column('id', Integer, primary_key=True),
    Column('run_id', ForeignKey('business_runs.id'), nullable=False, unique=True),
    Column('report', JSON, nullable=False),
    Column('digest', String(64), nullable=False),
    Column('reviewer', String(40)),
    Column('review_note', String(500)),
    Column('reviewed_at', String(32)))
audit = Table('audit_events', schema,
    Column('id', Integer, primary_key=True),
    Column('actor', String(40), nullable=False),
    Column('action', String(32), nullable=False),
    Column('reference', String(50), nullable=False),
    Column('created_at', String(32), nullable=False))

# 档案隔离副本：首次执行或补录合同时从原始档案播种，之后跨检查日沿用，
# 已擦除的资产不会从原始档案恢复。副本变更与擦除证据在同一事务落库。
replicas = Table('case_replicas', schema,
    Column('case_id', ForeignKey('business_cases.id'), primary_key=True),
    Column('seeded_at', String(32), nullable=False))
replica_assets = Table('replica_assets', schema,
    Column('case_id', ForeignKey('business_cases.id'), nullable=False),
    Column('asset_id', String(64), nullable=False),
    Column('kind', String(20), nullable=False),
    Column('object_key', String(500), nullable=False),
    Column('expires_on', String(10)),
    Column('track', String(20)),
    Column('duration_seconds', Integer),
    Column('performer_id', String(64)),
    PrimaryKeyConstraint('case_id', 'asset_id'))
replica_references = Table('replica_references', schema,
    Column('case_id', ForeignKey('business_cases.id'), nullable=False),
    Column('referrer', String(64), nullable=False),
    Column('dependency', String(64), nullable=False),
    PrimaryKeyConstraint('case_id', 'referrer', 'dependency'),
    ForeignKeyConstraint(['case_id', 'referrer'],
                         ['replica_assets.case_id', 'replica_assets.asset_id'], ondelete='RESTRICT'),
    ForeignKeyConstraint(['case_id', 'dependency'],
                         ['replica_assets.case_id', 'replica_assets.asset_id'], ondelete='RESTRICT'))
replica_holds = Table('replica_holds', schema,
    Column('case_id', ForeignKey('business_cases.id'), nullable=False),
    Column('contract_id', String(64), nullable=False),
    Column('asset_id', String(64), nullable=False),
    Column('hold_until', String(10)),
    PrimaryKeyConstraint('case_id', 'asset_id', 'contract_id'),
    ForeignKeyConstraint(['case_id', 'asset_id'],
                         ['replica_assets.case_id', 'replica_assets.asset_id'], ondelete='RESTRICT'))
replica_archive = Table('replica_archive', schema,
    Column('case_id', ForeignKey('business_cases.id'), nullable=False),
    Column('run_id', ForeignKey('business_runs.id'), nullable=False),
    Column('track', String(20), nullable=False),
    Column('duration_band', Integer, nullable=False),
    Column('tape_count', Integer, nullable=False),
    Column('performer_count', Integer, nullable=False),
    PrimaryKeyConstraint('case_id', 'run_id', 'track', 'duration_band'))
