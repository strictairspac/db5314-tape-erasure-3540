"""领域规则：目录校验、拓扑擦除计划与匿名归档门槛。"""

import unittest

from src.services import domain
from src.services.domain import SPEC
from src.services.tape_erasure import plan_erasure


class ParseTest(unittest.TestCase):
    def test_sample_payload_parses(self):
        cutoff, catalog = domain.parse_payload(SPEC['sample'])
        self.assertEqual(cutoff, '2026-11-21')
        self.assertEqual(len(catalog.assets), 15)
        self.assertEqual(len(catalog.references), 12)
        self.assertEqual(len(catalog.holds), 3)

    def test_invalid_cutoff_rejected(self):
        with self.assertRaises(ValueError):
            domain.validate({**SPEC['sample'], 'cutoff': '2026-11-31'})

    def test_cycle_rejected(self):
        payload = {
            'cutoff': '2026-11-21',
            'assets': [
                {'asset_id': 'A', 'kind': 'master', 'object_key': 'a.mxf'},
                {'asset_id': 'B', 'kind': 'master', 'object_key': 'b.mxf'},
            ],
            'references': [
                {'referrer': 'A', 'dependency': 'B'},
                {'referrer': 'B', 'dependency': 'A'},
            ],
            'holds': [],
        }
        with self.assertRaises(ValueError):
            domain.validate(payload)

    def test_hold_requires_until_key(self):
        payload = {**SPEC['sample'],
                   'holds': [{'contract_id': 'CT-1', 'asset_id': 'T-05'}]}
        with self.assertRaises(ValueError):
            domain.validate(payload)


class PlanTest(unittest.TestCase):
    def test_sample_plan(self):
        cutoff, catalog = domain.parse_payload(SPEC['sample'])
        plan = plan_erasure(catalog, cutoff)
        self.assertEqual(len(plan.due_tapes), 5)
        self.assertEqual(len(plan.deletion_order), 8)
        self.assertEqual(plan.deletion_order[0], 'T-01')
        self.assertEqual(plan.deletion_order[-1], 'A-HOT')
        self.assertEqual(len(plan.held_dependencies), 4)

    def test_solve_matches_expect(self):
        report = domain.solve(SPEC['sample'], None)
        self.assertEqual(report['metrics'], SPEC['expect'])
        self.assertEqual(report['cutoff'], '2026-11-21')


if __name__ == '__main__':
    unittest.main()
