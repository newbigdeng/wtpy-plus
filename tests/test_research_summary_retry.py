"""Rejected output must not poison retries; excerpt bounds preserve source text."""
import copy
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).parents[1]/'wtpy/monitor'))
from ResearchSourceSummary import (
    ResearchSourceSummaries, reading_segments, select_reading_segments,
    resolve_anchors, validate_summary,
)

helper=Path(__file__).parents[1]/'scripts/research-terminal/research_records.py'
spec=importlib.util.spec_from_file_location('research_records', helper)
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class SummaryRetryTests(unittest.TestCase):
    def answer(self):
        empty=dict(text='',anchor_ids=[])
        return dict(overview=dict(question=dict(text='原文方法',anchor_ids=['P0001']),conclusion=empty,method=empty),
            formula_state='none',formulas=[],fields_and_series=[],frequency_windows=[],available_timing=[],
            sample_costs=[],limitations=[],reproduction=dict(status='not_assessed',reason=empty),anchors=[dict(id='P0001')])

    def test_selected_excerpts_are_bounded_and_exact(self):
        body=''.join(f'Paragraph {i}. '+('method and formula. ' if i==15 else 'text. ')*180+'\n' for i in range(30))
        segments=reading_segments(body)
        selected=select_reading_segments(segments)
        self.assertEqual(len(selected),10)
        self.assertLessEqual(sum(len(s['exact_quote']) for s in selected),12000)
        self.assertEqual(selected, sorted(selected,key=lambda s:s['text_start']))
        for s in selected:self.assertEqual(s['exact_quote'],body[s['text_start']:s['text_end']])
        self.assertTrue(any('method and formula' in s['exact_quote'] for s in selected))
        self.assertEqual(select_reading_segments(segments[:2]),segments[:2])

    def test_rejected_cache_is_preserved_but_not_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with sqlite3.connect(root/'research.sqlite3') as db:
                db.execute('CREATE TABLE records(kind TEXT,id TEXT,owner TEXT,data TEXT,PRIMARY KEY(kind,id,owner))')
            db.close()
            store=module.Records(root)
            source=dict(id='synthetic',title='Intraday method',body='Original method and formula x = a * b.',source_version='1')
            store._save('report','test',source)
            manager=ResearchSourceSummaries(store)
            job=manager.request('test','synthetic')
            job=store._get('report_summary_job','test',job['id'])
            def invalid(folder,*_):
                value=self.answer();value['overview']['question']['anchor_ids']=[]
                (folder/'core-summary.json').write_text(json.dumps(value))
                (folder/'core-summary-completed.json').write_text('{}')
                return value
            self.assertEqual(manager.run_job('test',job,invalid),'failed')
            folder=root/'report-summaries'/job['id']
            self.assertFalse((folder/'core-summary-completed.json').exists())
            self.assertTrue((folder/'core-summary-rejected-1.json').exists())
            self.assertEqual(manager.run_job('test',job,lambda *_:self.answer()),'completed')
            version=store._rows('report_summary','test')[0]
            self.assertEqual(version['reading_scope'],'full_text')

    def test_rewritten_formula_still_rejected(self):
        source=dict(body='Original formula x = a * b.')
        value=self.answer();value.update(formula_state='explicit',formulas=[dict(expression='x = a × b',symbols=[],anchor_ids=['P0001'])])
        with self.assertRaisesRegex(ValueError,'逐字'):
            validate_summary(resolve_anchors(value,reading_segments(source['body'])),source)


if __name__=='__main__':unittest.main()
