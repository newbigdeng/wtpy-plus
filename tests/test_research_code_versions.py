"""Research saves and publication cannot overwrite the source library strategy."""
import importlib
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
package = types.ModuleType('wtpy'); package.__path__ = [str(ROOT/'wtpy')]
monitor = types.ModuleType('wtpy.monitor'); monitor.__path__ = [str(ROOT/'wtpy/monitor')]
with patch.dict(sys.modules, {'wtpy': package, 'wtpy.monitor': monitor}):
    module = importlib.import_module('wtpy.monitor.ResearchCodeVersions')
    libraries = importlib.import_module('wtpy.monitor.StrategyLibrary')

SOURCE = 'from wtpy import BaseCtaStrategy\nclass Synthetic(BaseCtaStrategy):\n    pass\n'


class CodeVersionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.versions = module.ResearchCodeVersions(root/'copies')
        self.library = libraries.StrategyLibrary(root/'library')
        # Production uses flock; portable tests exercise the same store with an inert OS lock.
        if sys.platform == 'win32':
            fake = types.SimpleNamespace(LOCK_EX=1, flock=lambda *args: None)
            p = patch.dict(sys.modules, {'fcntl': fake}); p.start(); self.addCleanup(p.stop)
        self.library.save('original', 'Original', SOURCE)
        self.content = SOURCE
        self.calls = []

    def upstream(self, route, body):
        self.calls.append((route, body))
        if route == '/bt/qrycode':return dict(result=0, content=self.content)
        if route == '/bt/setcode':self.content=body['content']; return dict(result=0)
        if route == '/mgr/strategy_library_save':
            try:
                row = self.library.save(body['library_id'], body['name'], body['source'], body['description'])
                return dict(result=0, strategy=row)
            except ValueError as error:return dict(result=-2, message=str(error))
        raise AssertionError(route)

    def save(self, name='first', content=None, head=0, parent=0):
        return self.versions.save('owner', 'abc123', content or SOURCE+'# copied\n', name,
                                  head, self.content, parent, self.upstream)

    def test_save_and_publish_keep_original_and_older_versions(self):
        first = self.save()
        second = self.save('second', SOURCE+'# newer\n', head=1, parent=1)
        published = self.versions.publish('owner', 'abc123', 1, 'independent', 'Published', self.upstream)
        self.assertEqual(self.library.get('original')['source'], SOURCE)
        self.assertEqual(self.library.get('original')['version'], 1)
        self.assertEqual(self.content, second['content'])
        self.assertEqual(published['source'], first['content'])
        self.assertEqual(published['version'], 1)
        self.assertEqual(self.versions.get('owner', 'abc123', 1), first)

    def test_publish_rejects_every_occupied_id(self):
        self.save()
        with self.assertRaises(ValueError):
            self.versions.publish('owner', 'abc123', 1, 'original', 'Changed', self.upstream)
        self.assertEqual(self.library.get('original')['source'], SOURCE)
        self.assertEqual(self.library.get('original')['version'], 1)

    def test_stale_head_external_edit_and_duplicate_names_cannot_overwrite(self):
        self.save()
        original = self.content
        with self.assertRaises(ValueError):self.save('stale', head=0)
        with self.assertRaises(ValueError):self.save('FIRST', head=1)
        self.assertEqual(self.content, original)
        with self.assertRaises(ValueError):
            self.versions.save('owner', 'abc123', SOURCE, 'external', 1, SOURCE, 1, self.upstream)
        self.assertEqual(self.content, original)

    def test_names_required_drafts_allowed_and_ownership_isolated(self):
        with self.assertRaises(ValueError):self.save(' ')
        row = self.save('unfinished', 'unfinished python (')
        self.assertEqual(row['version_name'], 'unfinished')
        with self.assertRaises(ValueError):self.versions.get('other', 'abc123', 1)
        with self.assertRaises(ValueError):self.versions.get('owner', 'other', 1)
        with self.assertRaises(ValueError):
            self.versions.publish('owner', 'abc123', 1, 'draft', 'Draft', self.upstream)

    def test_failed_persistence_restores_the_copy_and_never_library(self):
        with patch.object(module, 'atomic_json', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):self.save()
        self.assertEqual(self.content, SOURCE)
        self.assertEqual(self.versions.read('owner', 'abc123')['head'], 0)
        self.assertEqual(self.library.get('original')['source'], SOURCE)

    def test_failed_upstream_save_does_not_create_a_version(self):
        def failed(route, body):
            return dict(result=0,content=SOURCE) if route=='/bt/qrycode' else dict(result=-2,message='write failed')
        with self.assertRaises(ValueError):
            self.versions.save('owner', 'abc123', SOURCE, 'first', 0, SOURCE, 0, failed)
        self.assertEqual(self.versions.read('owner', 'abc123')['head'], 0)

    def client(self, role='admin', authenticated=True):
        app = FastAPI()
        module.register_code_version_routes(app, Path(self.temp.name)/'api',
            lambda _request: (authenticated, dict(loginid='owner', role=role)),
            lambda _request, route, body: self.upstream(route, body))
        return TestClient(app)

    def test_routes_deny_guests_invalid_sessions_and_nonadmin_publication(self):
        for role, logged_in in (('guest', True), ('admin', False)):
            client = self.client(role, logged_in)
            result = client.post('/bt/research_code', json=dict(straid='abc123')).json()
            self.assertLess(result['result'], 0)
        result = self.client('researcher').post('/bt/research_code_publish',
            json=dict(straid='abc123', version=1, name='New')).json()
        self.assertLess(result['result'], 0)
        self.assertEqual(self.calls, [])

    def test_publish_route_uses_saved_version_even_if_request_supplies_other_code(self):
        client = self.client()
        saved = client.post('/bt/research_code_save', json=dict(straid='abc123', content=SOURCE+'# saved\n',
            version_name='named', base_version=0, base_content=SOURCE, parent_version=0)).json()
        self.assertEqual(saved['result'], 0)
        result = client.post('/bt/research_code_publish', json=dict(straid='abc123', version=1,
            library_id='published', name='New', content='unexpected editor content')).json()
        self.assertEqual(result['result'], 0)
        self.assertEqual(result['strategy']['source'], SOURCE+'# saved\n')
        self.assertEqual(self.library.get('original')['source'], SOURCE)

    def test_legacy_editor_changes_are_not_mislabeled_as_a_saved_version(self):
        client = self.client()
        saved = client.post('/bt/research_code_save', json=dict(straid='abc123', content=SOURCE+'# saved\n',
            version_name='named', base_version=0, base_content=SOURCE, parent_version=0)).json()
        self.assertEqual(saved['result'], 0)
        self.content = SOURCE+'# legacy editor change\n'
        state = client.post('/bt/research_code',json=dict(straid='abc123')).json()
        self.assertEqual(state['head'],1)
        self.assertEqual(state['current_version'],0)
        self.assertEqual(state['content'],self.content)


if __name__ == '__main__':unittest.main()
