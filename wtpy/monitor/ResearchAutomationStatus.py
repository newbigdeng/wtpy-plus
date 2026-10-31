"""Small owner-scoped status feed; no task execution in page requests."""
def automation_status(workspace, owner):
    jobs = workspace._rows('factor_automation', owner)
    versions = workspace._rows('automated_factor_version', owner)
    states = {row['version_id']:row for row in workspace._rows('factor_version_state', owner)}
    versions = [dict(row, **{k:v for k,v in states.get(row['id'],{}).items()
                            if k not in ('id','version_id','created_at','updated_at')}) for row in versions]
    monitors = workspace._rows('factor_monitor', owner)
    screens = workspace._rows('factor_report_screen', owner)
    cycles = workspace._rows('factor_automation_cycle', owner)
    def fields(rows, keys):
        return [{key: row[key] for key in keys if key in row} for row in rows[:20]]
    job_rows=fields(jobs,('id','status','reasons','updated_at','version_id'))
    for item, source in zip(job_rows,jobs):
        spec=source.get('spec',{})
        item['spec']={key:spec.get(key) for key in ('name','frequency','lookback')}
    return dict(counts=dict(screened=len(screens),jobs=len(jobs),versions=len(versions),
                           research_accepted=sum(v.get('status')=='research_accepted' for v in versions)),
                cycle=fields(cycles,('status','checked_at','results'))[:1],
                jobs=job_rows,
                versions=fields(versions,('id','name','version','status','lifecycle','promotion','automation_wait_reason','updated_at','selected_candidate','source_refs')),
                monitors=fields(monitors,('id','version_id','frequency','rolling_window_bars','metrics','reasons','lifecycle_request','exposure','created_at')),
                screens=fields(screens,('report_id','report_title','status','reason','updated_at')))
