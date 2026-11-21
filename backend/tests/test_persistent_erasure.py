"""持久隔离副本：可选检查日、补录合同、串行重算、重试幂等与证据落库。"""

import hashlib
import unittest

import support
from fastapi import HTTPException
from sqlalchemy import select

from src.models.tables import cases, evidence, replica_assets, runs
from src.services import replica, workflow
from src.services.domain import SPEC
from src.utils.encoding import canonical
from src.utils.time import utc_time


class PersistentErasureTest(support.WorkflowTestCase):
    def make_case(self):
        return workflow.make_case('雨夜留存核对', SPEC['sample'], 'operator')

    def run_next(self):
        job = workflow.claim()
        self.assertIsNotNone(job, '没有可领取的执行')
        run_id, token = job
        workflow.execute_run(run_id, token)
        return run_id

    def reports(self):
        return {row['run_id']: row['report'] for row in workflow.recent(evidence)}

    def replica_asset_ids(self, case_id):
        with self.engine.connect() as conn:
            return {row.asset_id for row in conn.execute(
                select(replica_assets).where(replica_assets.c.case_id == case_id))}

    def test_replica_kept_for_next_check_day_with_selectable_cutoff(self):
        case = self.make_case()
        run1 = workflow.enqueue(case['id'], None, 'operator')
        self.assertEqual(run1['cutoff'], '2026-11-21')
        self.assertEqual(self.run_next(), run1['id'])
        state = workflow.replica_state(case['id'])
        self.assertTrue(state['seeded'])
        self.assertEqual(state['assets'], 7)
        self.assertEqual(len(state['archive']), 1)
        self.assertEqual(self.reports()[run1['id']]['metrics'], SPEC['expect'])
        self.assertEqual(self.reports()[run1['id']]['cutoff'], '2026-11-21')

        # 下个检查日沿用同一副本，已擦除的资产不会从原始档案恢复
        run2 = workflow.enqueue(case['id'], '2026-11-22', 'operator')
        self.assertEqual(self.run_next(), run2['id'])
        self.assertEqual(self.replica_asset_ids(case['id']),
                         {'T-06', 'M-SHARED', 'A-SHARED', 'F-01'})
        report2 = self.reports()[run2['id']]
        self.assertEqual(report2['cutoff'], '2026-11-22')
        self.assertEqual(report2['metrics']['擦除样带'], 1)
        self.assertEqual(report2['metrics']['擦除资产'], 3)
        self.assertEqual(report2['metrics']['留存资产'], 4)
        # 原始档案保持 15 项不变
        self.assertEqual(len(workflow.recent(cases)[0]['payload']['assets']), 15)

    def test_holds_appended_after_queueing_protect_assets(self):
        case = self.make_case()
        run = workflow.enqueue(case['id'], None, 'operator')
        result = workflow.append_holds(case['id'], [
            {'contract_id': 'CT-217', 'asset_id': 'T-03', 'hold_until': '2026-11-30'}], 'operator')
        self.assertEqual(result['added'], 1)
        self.assertEqual(self.run_next(), run['id'])
        # 先入库的 CT-217 保护 T-03 及其依赖 U-SHARED
        remaining = self.replica_asset_ids(case['id'])
        self.assertIn('T-03', remaining)
        self.assertIn('U-SHARED', remaining)
        self.assertEqual(len(remaining), 9)
        report = self.reports()[run['id']]
        self.assertEqual(report['metrics']['擦除资产'], 6)
        self.assertEqual(report['metrics']['匿名归档组'], 0)
        # 已擦除的资产不能再补录合同
        with self.assertRaises(HTTPException) as ctx:
            workflow.append_holds(case['id'], [
                {'contract_id': 'CT-218', 'asset_id': 'T-01', 'hold_until': None}], 'operator')
        self.assertEqual(ctx.exception.status_code, 422)
        # 重复的合同关联被拒绝
        with self.assertRaises(HTTPException):
            workflow.append_holds(case['id'], [
                {'contract_id': 'CT-217', 'asset_id': 'T-03', 'hold_until': '2026-12-01'}], 'operator')
        # 执行间隙补录的合同保护下一轮尚未擦除的资产及其依赖；
        # CT-217 于 11-30 到期，12-10 检查日不再保护 T-03 与 U-SHARED
        workflow.append_holds(case['id'], [
            {'contract_id': 'CT-219', 'asset_id': 'T-06', 'hold_until': '2026-12-15'}], 'operator')
        run2 = workflow.enqueue(case['id'], '2026-12-10', 'operator')
        self.assertEqual(self.run_next(), run2['id'])
        self.assertEqual(self.replica_asset_ids(case['id']),
                         {'T-06', 'M-SHARED', 'A-SHARED', 'F-01'})
        self.assertEqual(self.reports()[run2['id']]['metrics']['擦除资产'], 5)

    def test_stale_run_reclaimed_with_new_token(self):
        case = self.make_case()
        workflow.enqueue(case['id'], None, 'operator')
        run_id, token1 = workflow.claim()
        # 执行中断后运行卡在处理中，超时由新的尝试令牌重新领取
        with self.engine.begin() as conn:
            conn.execute(runs.update().where(runs.c.id == run_id)
                         .values(started_at=utc_time(-601)))
        reclaimed, token2 = workflow.claim()
        self.assertEqual(reclaimed, run_id)
        self.assertNotEqual(token1, token2)
        # 旧令牌的中断尝试不能提交结果，新令牌重试只计入一次
        workflow.execute_run(run_id, token1)
        self.assertEqual(workflow.recent(evidence), [])
        workflow.execute_run(run_id, token2)
        self.assertEqual(len(workflow.recent(evidence)), 1)
        state = workflow.replica_state(case['id'])
        self.assertEqual(state['assets'], 7)
        self.assertEqual(len(state['archive']), 1)

    def test_later_submission_recomputes_on_changed_directory(self):
        case = self.make_case()
        run1 = workflow.enqueue(case['id'], None, 'operator')
        run2 = workflow.enqueue(case['id'], None, 'operator')
        self.assertLess(run1['id'], run2['id'])
        self.assertEqual(self.run_next(), run1['id'])
        self.assertEqual(self.run_next(), run2['id'])
        reports = self.reports()
        self.assertEqual(reports[run1['id']]['metrics']['擦除资产'], 8)
        # 较晚提交的执行按变化后的目录重算，不重复擦除
        self.assertEqual(reports[run2['id']]['metrics']['擦除资产'], 0)
        self.assertEqual(reports[run2['id']]['metrics']['留存资产'], 7)
        self.assertEqual(workflow.replica_state(case['id'])['assets'], 7)

    def test_retry_after_interruption_does_not_double_count(self):
        case = self.make_case()
        workflow.enqueue(case['id'], None, 'operator')
        run_id, token = workflow.claim()
        original = replica.execute

        def crash(conn, case_row, run_row):
            original(conn, case_row, run_row)
            raise RuntimeError('模拟中断')

        replica.execute = crash
        try:
            with self.assertRaises(RuntimeError):
                workflow.execute_run(run_id, token)
        finally:
            replica.execute = original
        # 中断的事务整体回滚：副本未落库、归档未计入、证据未发布
        state = workflow.replica_state(case['id'])
        self.assertFalse(state['seeded'])
        self.assertEqual(state['archive'], [])
        self.assertEqual(workflow.recent(evidence), [])
        # 重试从执行前状态重新开始，匿名归档只计入一次
        workflow.execute_run(run_id, token)
        state = workflow.replica_state(case['id'])
        self.assertEqual(state['assets'], 7)
        self.assertEqual(len(state['archive']), 1)
        self.assertEqual(state['archive'][0]['tape_count'], 3)
        self.assertEqual(state['archive'][0]['performer_count'], 3)
        self.assertEqual(len(workflow.recent(evidence)), 1)

    def test_evidence_published_only_with_directory_changes(self):
        case = self.make_case()
        workflow.enqueue(case['id'], None, 'operator')
        run_id, token = workflow.claim()
        original = replica.execute

        def broken(conn, case_row, run_row):
            raise ValueError('目录校验失败')

        replica.execute = broken
        try:
            with self.assertRaises(ValueError):
                workflow.execute_run(run_id, token)
        finally:
            replica.execute = original
        workflow.fail_run(run_id, token, '目录校验失败')
        # 失败的执行不发布证据，目录变更也不落库
        self.assertEqual(workflow.recent(evidence), [])
        self.assertFalse(workflow.replica_state(case['id'])['seeded'])
        # 成功的执行把目录变更与证据一起落库
        run2 = workflow.enqueue(case['id'], None, 'operator')
        self.assertEqual(self.run_next(), run2['id'])
        rows = workflow.recent(evidence)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['run_id'], run2['id'])
        self.assertEqual(rows[0]['digest'],
                         hashlib.sha256(canonical(rows[0]['report'])).hexdigest())
        self.assertEqual(workflow.replica_state(case['id'])['assets'], 7)


if __name__ == '__main__':
    unittest.main()
