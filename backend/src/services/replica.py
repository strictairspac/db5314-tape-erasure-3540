"""档案隔离副本的播种、补录与持久擦除执行。

副本在首次执行或补录合同时从原始档案播种，之后跨检查日沿用：已擦除的资产
不会从原始档案恢复，补录的合同即时保护尚未擦除的资产及其依赖。目录变更、
匿名归档与擦除证据由调用方放在同一事务落库。
"""

from sqlalchemy import func, select

from src.models.tables import (replica_archive, replica_assets, replica_holds,
                               replica_references, replicas)
from src.services.domain import (build_report, parse_day, parse_hold,
                                 parse_payload)
from src.services.tape_erasure import (Asset, Catalog, Hold, archive_cohorts,
                                       plan_erasure, verify_applied_state)
from src.utils.time import utc_time


def ensure_replica(conn, case_id, payload):
    """首次接触时把原始档案播种为隔离副本；已播种过的档案绝不重新播种。"""
    if conn.execute(select(replicas.c.case_id).where(replicas.c.case_id == case_id)).first():
        return False
    _, catalog = parse_payload(payload)
    conn.execute(replicas.insert().values(case_id=case_id, seeded_at=utc_time()))
    if catalog.assets:
        conn.execute(replica_assets.insert(), [
            {'case_id': case_id, **asset._asdict()} for asset in catalog.assets.values()])
    if catalog.references:
        conn.execute(replica_references.insert(), [
            {'case_id': case_id, 'referrer': referrer, 'dependency': dependency}
            for referrer, dependency in catalog.references])
    if catalog.holds:
        conn.execute(replica_holds.insert(), [
            {'case_id': case_id, **hold._asdict()} for hold in catalog.holds])
    return True


def read_replica(conn, case_id):
    rows = conn.execute(select(replica_assets).where(replica_assets.c.case_id == case_id)).mappings()
    assets = {row['asset_id']: Asset(row['asset_id'], row['kind'], row['object_key'], row['expires_on'],
                                     row['track'], row['duration_seconds'], row['performer_id'])
              for row in rows}
    references = {(row['referrer'], row['dependency']) for row in conn.execute(
        select(replica_references.c.referrer, replica_references.c.dependency)
        .where(replica_references.c.case_id == case_id)).mappings()}
    holds = {Hold(row['contract_id'], row['asset_id'], row['hold_until']) for row in conn.execute(
        select(replica_holds).where(replica_holds.c.case_id == case_id)).mappings()}
    return Catalog(assets, references, holds)


def list_holds(conn, case_id):
    return [dict(row) for row in conn.execute(
        select(replica_holds.c.contract_id, replica_holds.c.asset_id, replica_holds.c.hold_until)
        .where(replica_holds.c.case_id == case_id)
        .order_by(replica_holds.c.contract_id, replica_holds.c.asset_id)).mappings()]


def add_holds(conn, case_id, rows):
    """补录合同关联：先入库的合同保护副本中尚未擦除的资产及其依赖。"""
    if not rows:
        raise ValueError('须至少提供一条合同留存关联')
    catalog = read_replica(conn, case_id)
    known = {(hold.contract_id, hold.asset_id) for hold in catalog.holds}
    added = []
    for row in rows:
        hold = parse_hold(row)
        if hold.asset_id not in catalog.assets:
            raise ValueError(f'合同 {hold.contract_id} 指向已擦除或不存在的资产')
        if (hold.contract_id, hold.asset_id) in known:
            raise ValueError('合同留存关联重复')
        known.add((hold.contract_id, hold.asset_id))
        added.append(hold)
    conn.execute(replica_holds.insert(), [{'case_id': case_id, **hold._asdict()} for hold in added])
    return added


def state(conn, case_id):
    seeded = conn.execute(select(replicas).where(replicas.c.case_id == case_id)).mappings().first()
    assets = conn.execute(select(func.count()).select_from(replica_assets)
                          .where(replica_assets.c.case_id == case_id)).scalar_one()
    archive = [dict(row) for row in conn.execute(
        select(replica_archive.c.run_id, replica_archive.c.track, replica_archive.c.duration_band,
               replica_archive.c.tape_count, replica_archive.c.performer_count)
        .where(replica_archive.c.case_id == case_id)
        .order_by(replica_archive.c.run_id, replica_archive.c.track, replica_archive.c.duration_band)
    ).mappings()]
    return {'seeded': seeded is not None, 'seeded_at': seeded['seeded_at'] if seeded else None,
            'assets': assets, 'holds': list_holds(conn, case_id), 'archive': archive}


def execute(conn, case, run):
    """在隔离副本上按检查日执行擦除，目录变更与匿名归档随调用方事务一并落库。"""
    case_id = case['id']
    ensure_replica(conn, case_id, case['payload'])
    cutoff = parse_day(run['cutoff'] or parse_payload(case['payload'])[0])
    before = read_replica(conn, case_id)
    plan = plan_erasure(before, cutoff)
    for track, band, tapes, performers in archive_cohorts(before, plan):
        conn.execute(replica_archive.insert().values(
            case_id=case_id, run_id=run['id'], track=track, duration_band=band,
            tape_count=tapes, performer_count=performers))
    for asset_id in plan.deletion_order:
        conn.execute(replica_references.delete()
                     .where(replica_references.c.case_id == case_id,
                            replica_references.c.referrer == asset_id))
        conn.execute(replica_holds.delete()
                     .where(replica_holds.c.case_id == case_id,
                            replica_holds.c.asset_id == asset_id,
                            replica_holds.c.hold_until < cutoff))
        conn.execute(replica_assets.delete()
                     .where(replica_assets.c.case_id == case_id,
                            replica_assets.c.asset_id == asset_id))
    after = read_replica(conn, case_id)
    archive = [tuple(row) for row in conn.execute(
        select(replica_archive.c.track, replica_archive.c.duration_band,
               replica_archive.c.tape_count, replica_archive.c.performer_count)
        .where(replica_archive.c.case_id == case_id, replica_archive.c.run_id == run['id'])
        .order_by(replica_archive.c.track, replica_archive.c.duration_band))]
    verify_applied_state(before, after, plan, cutoff, archive)
    return build_report(before, after, plan, archive, cutoff)
