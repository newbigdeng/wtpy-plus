"""Shared access to the existing research database, without starting a server."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import time


class Records:
    def __init__(self, root):
        self.root = Path(root)

    def _rows(self, kind, owner=None):
        with closing(sqlite3.connect(self.root / 'research.sqlite3', timeout=10)) as db:
            rows = db.execute('SELECT data FROM records WHERE kind=?' + (' AND owner=?' if owner else ''),
                              (kind, owner) if owner else (kind,)).fetchall()
        return sorted([json.loads(x[0]) for x in rows], key=lambda x:x.get('created_at',0), reverse=True)

    def _get(self, kind, owner, identifier):
        with closing(sqlite3.connect(self.root / 'research.sqlite3', timeout=10)) as db:
            row = db.execute('SELECT data FROM records WHERE id=? AND kind=? AND owner=?',
                             (identifier, kind, owner)).fetchone()
        if row is None:
            raise StopIteration(identifier)
        return json.loads(row[0])

    def _save(self, kind, owner, item):
        item['updated_at'] = time.time();item.setdefault('created_at',item['updated_at'])
        with closing(sqlite3.connect(self.root / 'research.sqlite3', timeout=10)) as db:
            db.execute('INSERT OR REPLACE INTO records VALUES(?,?,?,?)',
                       (kind,item['id'],owner,json.dumps(item,ensure_ascii=False,allow_nan=False)))
            db.commit()
        return item
