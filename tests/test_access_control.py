import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

from fastapi.testclient import TestClient
from main import app


class AccessControlTests(unittest.TestCase):
    def setUp(self):
        self.saved = {key: os.environ.get(key) for key in ('SPORTS_PRODUCTION', 'SPORTS_LOCAL_PREVIEW', 'SPORTS_ADMIN_API_KEY', 'SPORTS_DEV_API_KEY')}
        os.environ['SPORTS_PRODUCTION'] = '1'
        os.environ['SPORTS_LOCAL_PREVIEW'] = '0'
        os.environ['SPORTS_ADMIN_API_KEY'] = 'phase15-admin-key'
        os.environ['SPORTS_DEV_API_KEY'] = 'phase15-dev-key'
        self.client = TestClient(app)

    def tearDown(self):
        for key, value in self.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_an_analyst_cannot_activate_a_model_or_change_policy(self):
        headers = {'X-Sports-Dev-Key': 'phase15-dev-key'}
        activate = self.client.post('/api/sports-ai/models/missing/activate', headers=headers)
        policy = self.client.put('/api/sports-ai/qualification/policy', headers=headers, json={})
        anonymous = self.client.post('/api/sports-ai/models/missing/activate')
        self.assertEqual(activate.status_code, 403)
        self.assertEqual(policy.status_code, 403)
        self.assertEqual(anonymous.status_code, 401)

    def test_a_super_administrator_passes_the_role_check(self):
        response = self.client.post(
            '/api/sports-ai/models/missing/activate',
            headers={'X-Sports-Admin-Key': 'phase15-admin-key'},
        )
        self.assertEqual(response.status_code, 404)

    def test_preview_authentication_is_rejected_in_production(self):
        os.environ['SPORTS_LOCAL_PREVIEW'] = '1'
        client = TestClient(app, client=('127.0.0.1', 50000))
        response = client.get('/api/sports-ai/roles')
        self.assertEqual(response.status_code, 503)
