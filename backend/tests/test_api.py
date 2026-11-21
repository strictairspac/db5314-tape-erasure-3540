"""HTTP 层：登录、可选检查日提交、补录合同与副本状态。"""

import unittest

import support
from fastapi.testclient import TestClient

import src.main as main_module
import src.services.security as security
from src.models.tables import users
from src.services import workflow
from src.services.domain import SPEC
from src.services.security import hash_password

PASSWORD = 'Demo-Walkthrough-2026'


class ApiTest(support.WorkflowTestCase):
    def setUp(self):
        super().setUp()
        self._restores.append((security, 'engine', security.engine))
        security.engine = self.engine
        with self.engine.begin() as conn:
            for role in ('operator', 'reviewer', 'viewer'):
                conn.execute(users.insert().values(
                    username=role, role=role, password_hash=hash_password(PASSWORD)))
        self.client = TestClient(main_module.app)

    def login(self, username='operator'):
        response = self.client.post('/login', json={'username': username, 'password': PASSWORD})
        self.assertEqual(response.status_code, 200, response.text)
        return {'Authorization': f"Bearer {response.json()['token']}"}

    def test_run_cutoff_holds_and_replica_endpoints(self):
        headers = self.login()
        created = self.client.post('/cases', headers=headers,
                                   json={'title': '雨夜留存核对', 'payload': SPEC['sample']})
        self.assertEqual(created.status_code, 201, created.text)
        case_id = created.json()['id']

        # 检查日可选：空表单或不带请求体都按档案登记日，显式指定则按指定日
        default_run = self.client.post(f'/cases/{case_id}/runs', headers=headers, json={})
        self.assertEqual(default_run.status_code, 202, default_run.text)
        self.assertEqual(default_run.json()['cutoff'], '2026-11-21')
        empty_body = self.client.post(f'/cases/{case_id}/runs', headers=headers)
        self.assertEqual(empty_body.status_code, 202, empty_body.text)
        self.assertEqual(empty_body.json()['cutoff'], '2026-11-21')
        later_run = self.client.post(f'/cases/{case_id}/runs', headers=headers,
                                     json={'cutoff': '2026-11-22'})
        self.assertEqual(later_run.status_code, 202, later_run.text)
        self.assertEqual(later_run.json()['cutoff'], '2026-11-22')
        invalid_day = self.client.post(f'/cases/{case_id}/runs', headers=headers,
                                       json={'cutoff': '2026-11-31'})
        self.assertEqual(invalid_day.status_code, 422)
        malformed = self.client.post(f'/cases/{case_id}/runs', headers=headers,
                                     json={'cutoff': '11-22'})
        self.assertEqual(malformed.status_code, 422)

        # 擦除排队后仍可补录合同，副本随之播种
        appended = self.client.post(f'/cases/{case_id}/holds', headers=headers, json={'holds': [
            {'contract_id': 'CT-217', 'asset_id': 'T-03', 'hold_until': '2026-11-30'}]})
        self.assertEqual(appended.status_code, 201, appended.text)
        self.assertEqual(appended.json()['added'], 1)
        self.assertEqual(len(appended.json()['holds']), 4)
        state = self.client.get(f'/cases/{case_id}/replica', headers=headers)
        self.assertEqual(state.status_code, 200, state.text)
        self.assertTrue(state.json()['seeded'])
        self.assertEqual(state.json()['assets'], 15)

        # 未知资产被拒绝，观察员只读，缺失档案返回 404
        rejected = self.client.post(f'/cases/{case_id}/holds', headers=headers, json={'holds': [
            {'contract_id': 'CT-218', 'asset_id': 'T-99', 'hold_until': None}]})
        self.assertEqual(rejected.status_code, 422)
        viewer = self.login('viewer')
        forbidden = self.client.post(f'/cases/{case_id}/holds', headers=viewer, json={'holds': [
            {'contract_id': 'CT-219', 'asset_id': 'T-06', 'hold_until': None}]})
        self.assertEqual(forbidden.status_code, 403)
        readonly = self.client.get(f'/cases/{case_id}/replica', headers=viewer)
        self.assertEqual(readonly.status_code, 200)
        missing = self.client.get('/cases/9999/replica', headers=headers)
        self.assertEqual(missing.status_code, 404)


if __name__ == '__main__':
    unittest.main()
