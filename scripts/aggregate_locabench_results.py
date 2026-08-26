#!/usr/bin/env python3

from __future__ import annotations
import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = ROOT / "benchmarks" / "locabench-results"

TARGET_TASKS = {
    'WoocommerceNewWelcomeS2LEnv':    'WooNew',
    'WoocommerceStockAlertS2LEnv':    'WooStock',
    'FilterLowSellingProductsS2LEnv': 'FilterLow',
    'ApplyPhDEmailS2LEnv':            'ApplyPhD',
    'SetConfCrDdlS2LEnv':             'SetConf',
    'CourseAssistantS2LEnv':          'Course',
    'CanvasArrangeExamS2LEnv':        'CanvExam',
    'CanvasListTestS2LEnv':           'CanvList',
}
STD_EVAL_SEEDS = {42, 123, 456, 789, 2024}
EVOLVE_SEEDS = {101, 102}
EVAL_SPLITS = ['8k', '16k', '32k', '64k', '128k', '256k']


def classify_team(run_id: str, team: str) -> str:
    rid = run_id.lower()
    t = team.lower()
    if 'single' in t or 'solo' in t or '_sa_' in rid or 'sa_baseline' in rid:
        return 'SA'
    if 'evolved_final' in t or 'evolved_woonew' in t or '_ev_' in rid:
        return 'MT_evolved'
    if 'evolve' in t:
        return 'MT_evolved' if 'final' in t else 'MT_evolve_start'
    if 'locabench' in t and 'single' not in t:
        return 'MT'
    return f'?({team})'


def load_all_runs() -> list[dict]:
    records = []
    for sfile in sorted(RESULTS_DIR.glob('*/summary.json')):
        run_id = sfile.parent.name
        try:
            s = json.loads(sfile.read_text(encoding='utf-8'))
        except Exception as e:
            print(f'[warn] skip {run_id}: {e}', file=sys.stderr)
            continue
        split = s.get('split', '')
        team = s.get('team', '')
        evolve = bool(s.get('evolve', False))
        team_cat = classify_team(run_id, team)
        for r in s.get('records', []):
            tn = r.get('task_name', '')
            records.append({
                'run_id': run_id,
                'team': team,
                'team_cat': team_cat,
                'evolve_mode': evolve,
                'split': r.get('split') or split or r.get('context_level', ''),
                'context_level': r.get('context_level', split),
                'task_full': tn,
                'task_short': TARGET_TASKS.get(tn, tn),
                'seed': r.get('seed', 0),
                'score': float(r.get('score', 0.0)),
                'success': bool(r.get('success', False)),
                'reward': float(r.get('reward', 0.0)),
                'forced_termination': r.get('forced_termination'),
                'run_error': r.get('run_error', ''),
            })
    return records


def dedupe_by_latest(records: list[dict]) -> list[dict]:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in records:
        key = (r['team_cat'], r['context_level'], r['task_short'], r['seed'])
        groups[key].append(r)
    latest = []
    for key, rows in groups.items():
        rows.sort(key=lambda x: x['run_id'], reverse=True)
        latest.append(rows[0])
    return latest


def paper_filter(records: list[dict]) -> list[dict]:
    result = []
    for r in records:
        if r['task_short'] not in TARGET_TASKS.values():
            continue
        lvl = r['context_level']
        seed = r['seed']
        if seed in STD_EVAL_SEEDS and lvl in EVAL_SPLITS:
            r['_paper_set'] = 'eval'
            result.append(r)
        elif seed in EVOLVE_SEEDS and lvl == '96k':
            r['_paper_set'] = 'evolve'
            result.append(r)
    return result


def build_table(records: list[dict]) -> str:
    # (team_cat, split) → list of scores
    cells: dict[tuple, list[float]] = defaultdict(list)
    for r in records:
        cells[(r['team_cat'], r['context_level'])].append(r['score'])

    team_cats = sorted({r['team_cat'] for r in records})
    splits = [s for s in ['evolve_96k', *EVAL_SPLITS] if
              any(r['context_level'] in (s, '96k' if s == 'evolve_96k' else s) for r in records)]

    lines = []
    lines.append('# LOCA-bench Paper Experiment — Aggregated Results\n')
    lines.append(f'Total records after dedupe: **{len(records)}**\n')

    evolve_recs = [r for r in records if r.get('_paper_set') == 'evolve']
    eval_recs = [r for r in records if r.get('_paper_set') == 'eval']

    if evolve_recs:
        lines.append('## Evolution set (96K × seeds {101,102})\n')
        lines.append(_split_table(evolve_recs, ['96k']))
    if eval_recs:
        lines.append('\n## Evaluation set (6 splits × seeds {42,123,456,789,2024})\n')
        lines.append(_split_table(eval_recs, EVAL_SPLITS))

    # Per-task breakdown
    if eval_recs:
        lines.append('\n## Per-Task × Split Pass Rate Matrix\n')
        lines.append(_per_task_matrix(eval_recs, EVAL_SPLITS))

    return '\n'.join(lines)


def _split_table(records, splits):
    cells = defaultdict(lambda: {'pass': 0, 'total': 0, 'scores': []})
    for r in records:
        key = (r['team_cat'], r['context_level'])
        cells[key]['total'] += 1
        if r['score'] >= 1.0:
            cells[key]['pass'] += 1
        cells[key]['scores'].append(r['score'])

    team_cats = sorted({r['team_cat'] for r in records})

    head = '| split | ' + ' | '.join(team_cats) + ' |'
    sep = '|---|' + '|'.join(['---'] * len(team_cats)) + '|'
    out = [head, sep]
    for split in splits:
        row = [split]
        for tc in team_cats:
            c = cells.get((tc, split))
            if c and c['total']:
                pct = c['pass'] / c['total'] * 100
                row.append(f"{c['pass']}/{c['total']} ({pct:.1f}%)")
            else:
                row.append('—')
        out.append('| ' + ' | '.join(row) + ' |')
    return '\n'.join(out)


def _per_task_matrix(records, splits):
    # rows = task, cols = (split, team_cat), cell = pass_rate
    tasks = sorted({r['task_short'] for r in records})
    team_cats = sorted({r['team_cat'] for r in records})

    # header: task | 8k SA | 8k MT | ... 
    head = ['| task |']
    for split in splits:
        for tc in team_cats:
            head.append(f' {split} {tc} |')
    head_str = ''.join(head)
    sep = '|---|' + '|'.join(['---'] * (len(splits) * len(team_cats))) + '|'
    out = [head_str, sep]

    for task in tasks:
        row = [f'| {task} |']
        for split in splits:
            for tc in team_cats:
                # count matching records
                matches = [r for r in records
                           if r['task_short'] == task
                           and r['context_level'] == split
                           and r['team_cat'] == tc]
                if matches:
                    p = sum(1 for r in matches if r['score'] >= 1.0)
                    t = len(matches)
                    row.append(f' {p}/{t} |')
                else:
                    row.append(' — |')
        out.append(''.join(row))
    return '\n'.join(out)


def coverage_report(records: list[dict]) -> str:
    lines = ['## Coverage Report vs Paper Targets\n']

    # Evolution set target: 8 tasks × 2 seeds = 16 cases (per team_cat)
    # Evaluation set target: 8 tasks × 5 seeds × 6 splits = 240 cases (per team_cat)
    for team_cat in sorted({r['team_cat'] for r in records}):
        ev_covered = 0
        ev_total = 16
        for t in TARGET_TASKS.values():
            for s in EVOLVE_SEEDS:
                if any(r['team_cat']==team_cat and r['task_short']==t
                       and r['seed']==s and r['context_level']=='96k'
                       and r.get('_paper_set')=='evolve' for r in records):
                    ev_covered += 1

        eval_covered = 0
        eval_total = 8 * 5 * 6
        for t in TARGET_TASKS.values():
            for s in STD_EVAL_SEEDS:
                for lvl in EVAL_SPLITS:
                    if any(r['team_cat']==team_cat and r['task_short']==t
                           and r['seed']==s and r['context_level']==lvl
                           and r.get('_paper_set')=='eval' for r in records):
                        eval_covered += 1

        lines.append(f'### {team_cat}')
        lines.append(f'- Evolution: **{ev_covered}/{ev_total}**')
        lines.append(f'- Evaluation: **{eval_covered}/{eval_total}**\n')

        if eval_covered < eval_total:
            missing_by_split = defaultdict(int)
            for t in TARGET_TASKS.values():
                for s in STD_EVAL_SEEDS:
                    for lvl in EVAL_SPLITS:
                        if not any(r['team_cat']==team_cat and r['task_short']==t
                                   and r['seed']==s and r['context_level']==lvl
                                   and r.get('_paper_set')=='eval' for r in records):
                            missing_by_split[lvl] += 1
            lines.append('  Missing by split:')
            for lvl in EVAL_SPLITS:
                cnt = missing_by_split.get(lvl, 0)
                if cnt:
                    lines.append(f'    - {lvl}: {cnt}/40 missing')
            lines.append('')

    return '\n'.join(lines)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--paper-filter', action='store_true',
                   help='Only include records matching paper setup (target tasks + std/evolve seeds)')
    p.add_argument('--out', type=str, default=None,
                   help='Output markdown file path (default: stdout)')
    p.add_argument('--json-out', type=str, default=None,
                   help='Also dump deduped records as JSON')
    args = p.parse_args()

    all_records = load_all_runs()
    print(f'[load] {len(all_records)} raw case records across {len(set(r["run_id"] for r in all_records))} runs',
          file=sys.stderr)

    deduped = dedupe_by_latest(all_records)
    print(f'[dedupe] kept {len(deduped)} unique (team_cat, split, task, seed) tuples', file=sys.stderr)

    if args.paper_filter:
        deduped = paper_filter(deduped)
        print(f'[paper-filter] kept {len(deduped)} records matching paper setup', file=sys.stderr)

    md = build_table(deduped) + '\n\n' + coverage_report(deduped)

    if args.out:
        Path(args.out).write_text(md, encoding='utf-8')
        print(f'[write] {args.out}', file=sys.stderr)
    else:
        print(md)

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(deduped, ensure_ascii=False, indent=2),
            encoding='utf-8',
        )
        print(f'[write] {args.json_out}', file=sys.stderr)


if __name__ == '__main__':
    main()
