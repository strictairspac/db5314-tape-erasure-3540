import hashlib
import uuid

from fastapi import HTTPException
from sqlalchemy import and_, or_, select

from src.config.settings import cache, engine
from src.constants.workflow import Status
from src.models.tables import audit, cases, evidence, runs
from src.services import domain, replica
from src.utils.encoding import canonical
from src.utils.time import utc_time


def log(conn, actor, action, reference):
    conn.execute(audit.insert().values(actor=actor, action=action, reference=str(reference), created_at=utc_time()))


def recent(table):
    with engine.connect() as conn:
        return [dict(row) for row in conn.execute(select(table).order_by(table.c.id.desc()).limit(100)).mappings()]


def found(conn, table, identity):
    row = conn.execute(select(table).where(table.c.id == identity)).mappings().first()
    if row is None:
        raise HTTPException(404, '找不到该记录')
    return dict(row)


def make_case(title, payload, actor):
    title = title.strip()
    if not title:
        raise HTTPException(422, '档案名称不能为空')
    try:
        if len(canonical(payload)) > 512 * 1024:
            raise ValueError('业务输入超过 512 KiB')
        if callable(getattr(domain, 'validate', None)):
            domain.validate(payload)
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, str(exc)) from None
    with engine.begin() as conn:
        case_id = conn.execute(cases.insert().values(slug=uuid.uuid4().hex, title=title, payload=payload,
                               author=actor, created_at=utc_time())).inserted_primary_key[0]
        log(conn, actor, 'create_case', case_id)
        return found(conn, cases, case_id)


def enqueue(case_id, cutoff, actor):
    """提交执行：检查日可选，缺省按档案登记的检查日；同一档案的执行按提交顺序处理。"""
    if cutoff is not None:
        try:
            domain.parse_day(cutoff)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
    with engine.begin() as conn:
        case = found(conn, cases, case_id)
        run_id = conn.execute(runs.insert().values(case_id=case_id, status=Status.QUEUED,
                              owner=actor, cutoff=cutoff or case['payload'].get('cutoff'),
                              created_at=utc_time())).inserted_primary_key[0]
        log(conn, actor, 'enqueue', run_id)
        result = found(conn, runs, run_id)
    try:
        cache.lpush('worker:wake', 'queued')
        cache.ltrim('worker:wake', 0, 99)
    except Exception:
        # The durable database queue is polled even if Redis is temporarily unavailable.
        pass
    return result


def append_holds(case_id, rows, actor):
    """补录合同关联：擦除排队后仍可补录，先入库的合同保护尚未擦除的资产及其依赖。"""
    try:
        with engine.begin() as conn:
            case = conn.execute(select(cases).where(cases.c.id == case_id)
                                .with_for_update()).mappings().first()
            if case is None:
                raise HTTPException(404, '找不到该记录')
            replica.ensure_replica(conn, case_id, case['payload'])
            added = replica.add_holds(conn, case_id, rows)
            log(conn, actor, 'append_holds', case_id)
            return {'case_id': case_id, 'added': len(added), 'holds': replica.list_holds(conn, case_id)}
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


def replica_state(case_id):
    with engine.connect() as conn:
        found(conn, cases, case_id)
        return replica.state(conn, case_id)


def claim():
    ready = or_(runs.c.status == Status.QUEUED,
                and_(runs.c.status == Status.RUNNING, runs.c.started_at < utc_time(-600)))
    with engine.begin() as conn:
        run = conn.execute(select(runs.c.id).where(ready).order_by(runs.c.id)
                           .limit(1).with_for_update(skip_locked=True)).first()
        if not run:
            return None
        token = str(uuid.uuid4())
        conn.execute(runs.update().where(runs.c.id == run.id).values(
            started_at=utc_time(), status=Status.RUNNING, attempt_token=token))
        return run.id, token


def execute_run(run_id, token):
    """在单个事务中执行擦除：目录变更、匿名归档与擦除证据一起落库，失败则一并回滚。

    档案行锁保证同一档案的并发执行串行，较晚提交的执行按变化后的目录重算；
    中断的执行随连接回滚，重试从执行前的副本状态重新开始，不会重复计入匿名归档。
    """
    with engine.begin() as conn:
        run = conn.execute(select(runs).where(runs.c.id == run_id, runs.c.attempt_token == token,
                           runs.c.status == Status.RUNNING).with_for_update()).mappings().first()
        if not run:
            return
        case = conn.execute(select(cases).where(cases.c.id == run['case_id'])
                            .with_for_update()).mappings().first()
        report = replica.execute(conn, case, run)
        digest = hashlib.sha256(canonical(report)).hexdigest()
        conn.execute(evidence.insert().values(run_id=run_id, report=report, digest=digest))
        conn.execute(runs.update().where(runs.c.id == run_id)
                     .values(status=Status.COMPLETE, finished_at=utc_time()))
        log(conn, 'worker', Status.COMPLETE.value, run_id)


def fail_run(run_id, token, error):
    with engine.begin() as conn:
        count = conn.execute(runs.update().where(runs.c.id == run_id, runs.c.attempt_token == token,
                             runs.c.status == Status.RUNNING)
                             .values(status=Status.FAILED, finished_at=utc_time(),
                                     error=error[:500])).rowcount
        if count:
            log(conn, 'worker', Status.FAILED.value, run_id)


def review(identity, note, actor):
    note = note.strip()
    if not note:
        raise HTTPException(422, '复核意见不能为空')
    with engine.begin() as conn:
        found(conn, evidence, identity)
        count = conn.execute(evidence.update().where(evidence.c.id == identity, evidence.c.reviewed_at.is_(None))
                            .values(review_note=note, reviewer=actor, reviewed_at=utc_time())).rowcount
        if count != 1:
            raise HTTPException(409, '该证据已经复核')
        log(conn, actor, 'review', identity)
        return found(conn, evidence, identity)
