#!/usr/bin/env python3
"""Regenerate the offline strategy reference from the installed wtpy source.
Run inside the dev container:
  python tools/generate_api_manual.py ../wtpy
"""
import argparse
import ast
import json
from pathlib import Path

def generate(source: Path, output: Path):
    entries = []
    for kind, filename in [('cta', 'CtaContext.py'), ('hft', 'HftContext.py'), ('sel', 'SelContext.py')]:
        tree = ast.parse((source / filename).read_text(encoding='utf-8'))
        cls = next(node for node in tree.body if isinstance(node, ast.ClassDef))
        for node in cls.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or not node.name.startswith('stra_'):
                continue
            args = ast.unparse(node.args)
            args = args[6:] if args.startswith('self, ') else '' if args == 'self' else args
            signature = node.name + '(' + args + ')' + (' -> ' + ast.unparse(node.returns) if node.returns else '')
            entries.append({'type': kind, 'name': node.name, 'signature': signature,
                            'description': ast.get_docstring(node) or '请参考对应 Context 源码实现。',
                            'source': 'wtpy/' + filename, 'line': node.lineno})
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(entries, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'Generated {len(entries)} interfaces in {output}')

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=Path)
    args = parser.parse_args()
    generate(args.source, Path(__file__).resolve().parents[1] / 'src/data/wt-api.json')
