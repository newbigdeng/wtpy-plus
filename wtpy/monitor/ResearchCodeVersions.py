"""Named, immutable research copies; publishing always creates a new library ID."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import threading
import uuid

from fastapi import Body, Request
from .Roles import RESEARCH_ROLES, can_admin
from wtpy.StrategyRuntime import atomic_json, fcntl

_LOCK = threading.RLock()


class ResearchCodeVersions:
    def __init__(self, root):
        self.root = Path(root)

    def folder(self, owner, straid):
        if (not owner or owner in ('.', '..') or '/' in owner or '\\' in owner
                or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', straid)):
            raise ValueError('研究副本ID无效')
        return self.root / owner / straid

    @contextmanager
    def locked(self, owner, straid):
        folder = self.folder(owner, straid)
        with _LOCK:
            folder.mkdir(mode=0o700, parents=True, exist_ok=True)
            with (folder / 'versions.lock').open('a') as lock:
                if fcntl is not None:
                    fcntl.flock(lock, fcntl.LOCK_EX)
                yield folder / 'versions.json'

    def read(self, owner, straid):
        path = self.folder(owner, straid) / 'versions.json'
        return json.loads(path.read_text(encoding='utf-8')) if path.exists() else dict(head=0, versions=[])

    def get(self, owner, straid, version):
        if type(version) is not int:
            raise ValueError('请选择已保存的研究版本')
        row = next((row for row in self.read(owner, straid)['versions'] if row['version'] == version), None)
        if row is None:
            raise ValueError('研究版本不存在')
        return row

    def save(self, owner, straid, content, version_name, base_version, base_content, parent_version, upstream):
        name = version_name.strip()
        if not name or len(name) > 80 or any(ord(char) < 32 for char in name):
            raise ValueError('请填写1至80字的版本名称，不含换行')
        if not content or len(content.encode('utf-8')) > 262144:
            raise ValueError('代码不能为空或超过256KB')
        with self.locked(owner, straid) as path:
            value = self.read(owner, straid)
            if type(base_version) is not int or base_version != value['head']:
                raise ValueError('研究副本已有新版本，请刷新后再保存')
            if parent_version != 0:
                self.get(owner, straid, parent_version)
            if any(row['version_name'].casefold() == name.casefold() for row in value['versions']):
                raise ValueError('该副本已有同名版本，请使用新的版本名称')
            current = upstream('/bt/qrycode', dict(straid=straid))
            if current.get('result') != 0:
                raise ValueError(current.get('message', '研究副本不存在'))
            if current['content'] != base_content:
                raise ValueError('副本代码已被其他页面修改，请刷新后再保存')
            row = dict(version=value['head'] + 1, version_name=name, content=content,
                parent_version=parent_version, created_at=datetime.now(timezone.utc).isoformat(timespec='seconds'))
            result = upstream('/bt/setcode', dict(straid=straid, content=content))
            if result.get('result') != 0:
                raise ValueError(result.get('message', '研究副本保存失败'))
            value['head'] = row['version']
            value['versions'].append(row)
            try:
                atomic_json(path, value)
            except OSError:
                # Restore only our own just-written value, not a concurrent external edit.
                if upstream('/bt/qrycode', dict(straid=straid)).get('content') == content:
                    upstream('/bt/setcode', dict(straid=straid, content=base_content))
                raise
            return row

    def publish(self, owner, straid, version, library_id, name, upstream):
        with self.locked(owner, straid):
            row = self.get(owner, straid, version)
            library_id = library_id.strip() or 'research_' + uuid.uuid4().hex[:20]
            # No base_version: the existing library API rejects every occupied ID.
            result = upstream('/mgr/strategy_library_save', dict(library_id=library_id, name=name,
                source=row['content'], description='来自在线研究副本 %s · v%d %s。独立发布，不回写原策略。'
                    % (straid, version, row['version_name'])))
            if result.get('result') != 0:
                raise ValueError(result.get('message', '发布失败'))
            return result['strategy']


def register_code_version_routes(app, root, authenticate, upstream_call):
    versions = ResearchCodeVersions(Path(root) / 'research-code-versions')

    def authorize(request, publishing=False):
        ok, user = authenticate(request)
        if not ok:
            raise ValueError(user.get('message', '会话已失效'))
        if user.get('role') not in RESEARCH_ROLES or (publishing and not can_admin(user)):
            raise ValueError('发布需要管理员权限' if publishing else '需要策略研究权限')
        return user

    def response(action):
        try:
            return dict(result=0, **action())
        except (OSError, ValueError) as error:
            return dict(result=-2, message=str(error) if isinstance(error, ValueError) else '研究版本读写失败，请重试')

    def state(request, straid):
        user = authorize(request)
        with versions.locked(user['loginid'], straid):
            current = upstream_call(request, '/bt/qrycode', dict(straid=straid))
            if current.get('result') != 0:
                raise ValueError(current.get('message', '研究副本不存在'))
            value = versions.read(user['loginid'], straid)
        saved = value['versions'][-1] if value['versions'] else None
        return dict(content=current['content'], head=value['head'],
            current_version=value['head'] if saved and saved['content'] == current['content'] else 0,
            versions=[{key: val for key, val in row.items() if key != 'content'} for row in value['versions']])

    @app.post('/bt/research_code')
    def listing(request: Request, straid: str = Body(..., embed=True)):
        return response(lambda: state(request, straid))

    @app.post('/bt/research_code_version')
    def get(request: Request, straid: str = Body(..., embed=True), version: int = Body(..., embed=True)):
        def action():
            user = authorize(request)
            state(request, straid)  # Original monitor remains the owner/visibility authority.
            return dict(version=versions.get(user['loginid'], straid, version))
        return response(action)

    @app.post('/bt/research_code_save')
    def save(request: Request, straid: str = Body(..., embed=True), content: str = Body(..., embed=True),
             version_name: str = Body(..., embed=True), base_version: int = Body(..., embed=True),
             base_content: str = Body(..., embed=True), parent_version: int = Body(0, embed=True)):
        def action():
            user = authorize(request)
            row = versions.save(user['loginid'], straid, content, version_name, base_version, base_content,
                parent_version, lambda route, body: upstream_call(request, route, body))
            return dict(saved_version=row, message='已保存研究副本的新版本')
        return response(action)

    @app.post('/bt/research_code_publish')
    def publish(request: Request, straid: str = Body(..., embed=True), version: int = Body(..., embed=True),
                name: str = Body(..., embed=True), library_id: str = Body('', embed=True)):
        def action():
            user = authorize(request, True)
            state(request, straid)
            row = versions.publish(user['loginid'], straid, version, library_id, name,
                lambda route, body: upstream_call(request, route, body))
            return dict(strategy=row, message='已作为新的独立策略加入策略库')
        return response(action)
