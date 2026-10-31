"""Focused official-source parsing, deduplication and causal factor checks."""
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager
from types import SimpleNamespace


def module(name):
    sys.path.insert(0,str(Path(__file__).parents[1]/'wtpy/monitor'))
    spec=importlib.util.spec_from_file_location(name,Path(__file__).parents[1]/'wtpy/monitor'/(name+'.py'))
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value


reports=module('ResearchReports');factors=module('ResearchFactors')


class Store:
    def __init__(self,root):
        self.root=Path(root)
        with self._db() as db:db.execute('CREATE TABLE records(kind TEXT,id TEXT PRIMARY KEY,owner TEXT,data TEXT)')
    @contextmanager
    def _db(self):
        db=sqlite3.connect(self.root/'store.db')
        try:
            with db:yield db
        finally:db.close()
    def _rows(self,kind,owner):
        with self._db() as db:return [json.loads(x[0]) for x in db.execute('SELECT data FROM records WHERE kind=? AND owner=?',(kind,owner))]
    def _get(self,kind,owner,identifier):
        return next((row for row in self._rows(kind,owner) if row['id']==identifier),None) or self.missing()
    def missing(self):raise ValueError('missing')
    def _save(self,kind,owner,row):
        with self._db() as db:db.execute('INSERT OR REPLACE INTO records VALUES(?,?,?,?)',(kind,row['id'],owner,json.dumps(row)))
        return row


class LibraryTests(unittest.TestCase):
    def test_future_prices_do_not_change_past_factor_values(self):
        rows=[dict(time=202610010900+i,close=3000+i+(i%3)) for i in range(80)]
        future=[dict(row,close=row['close']*2 if i>40 else row['close']) for i,row in enumerate(rows)]
        for builtin in factors.BUILTINS:
            original=factors.calculate(rows,builtin['id'],5)
            changed=factors.calculate(future,builtin['id'],5)
            self.assertEqual([r['value'] for r in original['observations'] if r['time']<=rows[40]['time']],
                             [r['value'] for r in changed['observations'] if r['time']<=rows[40]['time']])
            first=original['observations'][0]
            self.assertEqual(first['available_at'],rows[5]['time'])
            self.assertEqual(first['evaluation_entry'],rows[6]['time'])
            self.assertEqual(first['evaluation_exit'],rows[7]['time'])
            self.assertAlmostEqual(first['forward_return'],rows[7]['close']/rows[6]['close']-1)
            self.assertEqual(original['diagnostics']['unlabelled_rows'],2)

    def test_invalid_inputs_are_not_silently_sorted_or_executed(self):
        with self.assertRaisesRegex(ValueError,'严格递增'):
            factors.calculate([dict(time=1,close=1)]*10,'momentum',2)
        with self.assertRaisesRegex(ValueError,'内置因子'):
            factors.parameters(dict(builtin_id='__import__("os")',lookback=5))
        with self.assertRaises(ValueError):factors.parameters(dict(builtin_id='momentum',lookback=True))

    def test_official_duplicate_failure_and_cross_process_throttle(self):
        class Client:
            def catalog(self,provider,limit):return [dict(provider_id='42',title='官方铜周报',published_at='2026-10-23',source_url='https://www.shcifco.com/Index/investmentinfo/id/42')]
            def document(self,provider,row):raise ValueError('需要登录')
        with tempfile.TemporaryDirectory() as root:
            store=Store(root);library=reports.ResearchReports(store,Client())
            result=library.collect(['shcifco'])
            self.assertEqual(result[0]['failed'],1)
            row=library.reports()[0]
            self.assertEqual(row['id'],'official-shcifco-42');self.assertEqual(row['body'],'')
            self.assertEqual(row['fetch_state'],'failed');self.assertIn('需要登录',row['error'])
            other=reports.ResearchReports(store,Client())
            self.assertEqual(other.collect(['shcifco'])[0]['state'],'throttled')
            self.assertEqual(len(library.reports('铜')),1)
            self.assertEqual(library.reports('螺纹'),[])
            self.assertEqual(len(library.reports()),1)

    def test_shanghai_directory_filters_navigation_and_detail_owns_date(self):
        client=reports.OfficialClient()
        directory='<a href="/Index/aboutinfo/id/9">公司介绍</a><span>2026-10-15</span><a href="/Index/investmentinfo/id/8554">上海中期一周投资策略</a>'
        detail='<h2 class="boxinfo_title">上海中期一周投资策略</h2><span>发布时间：<strong>2026-10-16</strong></span><div class="boxinfo_wen"><a href="/report.pdf">PDF</a></div>'
        calls=[]
        def fetch(provider,url,payload=None):
            calls.append(url)
            return ((directory if url.endswith('investment') else detail).encode(),'text/html') if not url.endswith('.pdf') else (b'%PDF-1.4 fixture','application/pdf')
        client.fetch=fetch
        rows=client.catalog('shcifco',6)
        self.assertEqual(len(rows),1)
        # PDF extraction is optional. This test isolates parser output from PDF parsers.
        try:client.document('shcifco',rows[0])
        except Exception:pass
        self.assertEqual(rows[0]['published_at'],'2026-10-16')
        self.assertEqual(rows[0]['document_url'],'https://www.shcifco.com/report.pdf')

    def test_network_client_rejects_nonofficial_urls_before_network(self):
        for url in ('http://www.shcifco.com/a','https://127.0.0.1/a','https://example.com/a'):
            with self.assertRaisesRegex(ValueError,'官网'):reports.OfficialClient().fetch('shcifco',url)

    def test_full_text_search_matches_beyond_polling_excerpt(self):
        with tempfile.TemporaryDirectory() as root:
            store=Store(root)
            store._save('official_report','__public__',dict(id='official-ruida-1',provider='ruida',title='铜周报',body='甲'*1800+'远端检索词'))
            library=reports.ResearchReports(store)
            result=library.reports('远端检索词')
            self.assertEqual(len(result),1)
            self.assertTrue(result[0]['body_truncated'])
            self.assertEqual(len(result[0]['body']),1600)
            self.assertIn('远端检索词',library.get('official-ruida-1')['body'])

    def test_source_categories_and_title_labels_are_distinct_and_filterable(self):
        item=reports.classified(dict(title='螺纹钢专题研究',category='周度报告 / 黑色',source_category='周度报告 / 黑色 / 螺纹'))
        self.assertEqual(item['source_category'],'周度报告 / 黑色 / 螺纹')
        self.assertEqual(item['report_type'],'周报');self.assertEqual(item['report_type_origin'],'source')
        self.assertEqual(item['topic_tags'],['黑色']);self.assertIn('不是原文',item['tag_origin'])
        inferred=reports.classified(dict(title='铜专题研究',category='研究中心'))
        self.assertEqual(inferred['report_type'],'专题/调研');self.assertEqual(inferred['report_type_origin'],'title_rule')
        with tempfile.TemporaryDirectory() as root:
            store=Store(root)
            store._save('official_report','__public__',dict(item,id='official-nanhua-1',provider='nanhua',body=''))
            library=reports.ResearchReports(store)
            self.assertEqual(len(library.reports(provider='nanhua',report_type='周报',topic='黑色')),1)
            self.assertEqual(library.reports(topic='贵金属'),[])

    def test_latest_reports_are_sorted_before_limit_and_filters_still_apply(self):
        rows=[dict(id='official-ruida-'+str(i),provider='ruida',title='铜日报',category='日度报告',
                   published_at='2026-10-16',body='') for i in range(501)]
        latest=[dict(id='official-nanhua-'+suffix,provider='nanhua',title='螺纹周报',category='周度报告 / 黑色',
                     published_at='2026-10-26',body='') for suffix in ('a','z')]
        rows.extend(latest)
        rows.append(dict(id='official-shcifco-undated',provider='shcifco',title='目录信息',published_at=None,body=''))
        library=reports.ResearchReports(SimpleNamespace(_rows=lambda kind,owner:rows))
        default=library.reports()
        self.assertEqual(len(default),500)
        self.assertEqual([row['id'] for row in default[:2]],['official-nanhua-z','official-nanhua-a'])
        filtered=library.reports(query='螺纹',provider='nanhua',report_type='周报',topic='黑色')
        self.assertEqual([row['id'] for row in filtered],['official-nanhua-z','official-nanhua-a'])
        self.assertEqual(library.reports(provider='nanhua',report_type='日报/晨会'),[])
        self.assertEqual(library.reports(provider='nanhua',topic='贵金属'),[])


if __name__=='__main__':unittest.main()
