from pathlib import Path
import ast, collections, csv, json, math, re, shutil, statistics
root = Path(__file__).resolve().parents[3]
out = root / 'json2scapi/out/corpus/similarity-shape-v4-local-20261025'
m = json.loads((out/'manifest.json').read_text(encoding='utf-8'))
expected = {p.stem for group in ('core','edge') for p in (root/'datasets/DeepCAD/smoke'/group).glob('*.json')}
expected.update(('00000447','00000137','00007271','00002722'))
ids = [r['stem'] for r in m['results']]
assert len(ids) == len(set(ids)) == len(expected) == 23
assert set(ids) == expected
assert m['evaluation_version'] == 'similarity-shape-v4'
entries = [json.loads(line) for line in (out/'train_ready.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
ready = {r['stem'] for r in m['results'] if r['status']=='train_ready'}
assert len(entries) == len(ready) and {r['stem'] for r in entries} == ready
by_id = {r['stem']: r for r in m['results']}
for item in entries:
    r=by_id[item['stem']]
    compare=r['metrics']['reference_vs_readable']
    assert set(compare['checks']) == {'volume_iou','volume_relative_error','chamfer_relative'}
    assert all(v=='pass' for v in compare['checks'].values())
    assert compare['volume_iou'] >= .999 and compare['volume_iou'] <= 1+1e-8
    assert compare['volume_relative_error'] <= .001 and compare['chamfer_relative'] <= .01
    assert not any(e.get('required') for e in r['metric_errors'])
    assert compare['registration']['status']=='resolved'
    assert math.isclose(compare['registration']['determinant'],1,abs_tol=1e-9)
    assert compare['transform']['scale'] > 0
    assert r['build']['roundtrip']['decision']['status']=='train_ready'
    assert set(r['warnings']) <= {'local_surface_deviation'}
    assert item['evaluation_version']=='similarity-shape-v4'
    assert item['cd_protocol']=='similarity-surface-v3'
    code=Path(r['paths']['readable_code']).read_text(encoding='utf-8')
    assert code==item['code']
    ast.parse(code)
    for key in ('ir','symbols','readable_step','reference_report','reference_step'):
        assert Path(r['paths'][key]).is_file(), (r['stem'],key)
rows=list(csv.DictReader((out/'errors.csv').open(encoding='utf-8-sig',newline='')))
assert len(rows)==23 and {r['stem'] for r in rows}==expected
assert all(set(r['warnings']) <= {'local_surface_deviation'} for r in m['results'])
log=root/'json2scapi/out/tmp/similarity-shape-v4-unittest.log'
s=log.read_text(encoding='utf-8-sig',errors='replace')
match=re.search(r'Ran (\d+) tests in ([\d.]+)s\s+OK',s)
assert match, 'unit suite did not pass'
shutil.copyfile(log,out/'unittest.log')
old_path=root/'json2scapi/out/corpus/similarity-shape-v3-local-20261025-r2/manifest.json'
old={r['stem']:r for r in json.loads(old_path.read_text(encoding='utf-8'))['results']} if old_path.exists() else {}
times=[r['elapsed_seconds'] for r in m['results'] if 'elapsed_seconds' in r]
summary={'evaluation_version':m['evaluation_version'],'scope_verified':True,'scope_count':23,
 'unit_tests':int(match[1]),'unit_seconds':float(match[2]),'counts':m['counts'],
 'export_verified':True,'export_count':len(entries),'candidate_seconds_sum':sum(times),
 'seconds_median':statistics.median(times),'seconds_max':max(times),
 'warnings':dict(collections.Counter(w for r in m['results'] for w in r.get('warnings',[]))),
 'diagnostics':dict(collections.Counter(w for r in m['results'] for w in r.get('diagnostics',[]))),
 'status_changes_from_v3_r2':[{'stem':r['stem'],'old':old[r['stem']]['status'],'new':r['status']} for r in m['results'] if r['stem'] in old and old[r['stem']]['status']!=r['status']],
 'not_ready':[{'stem':r['stem'],'status':r['status'],'stage':r['stage'],'reasons':r['reasons'],
   'build_status':r.get('build',{}).get('status')} for r in m['results'] if r['status']!='train_ready']}
accepted_metrics=[by_id[stem]['metrics']['reference_vs_readable'] for stem in sorted(ready)]
summary['accepted_metric_ranges']={key:{'min':min(r[key] for r in accepted_metrics),'max':max(r[key] for r in accepted_metrics)} for key in ('volume_iou','volume_relative_error','chamfer_relative')}
summary['iou_roundoff_verified']=[stem for stem in sorted(ready) if 'volume_iou_roundoff_verified' in by_id[stem]['metrics']['reference_vs_readable'].get('diagnostics',[])]
(out/'verification_summary.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding='utf-8')
report=out/'LOCAL_REGRESSION.md'
text=report.read_text(encoding='utf-8')
text+='\n## 测试与导出核对\n\n'
text+=f"- 单元测试：{summary['unit_tests']}项全部通过，耗时{summary['unit_seconds']:.3f}秒；完整日志见 `unittest.log`。\n"
text+=f"- 固定样本：23例，ID集合与core14、edge5和四个定点样本完全一致；未运行0000全量。\n"
text+=f"- 训练导出：{len(entries)}例，与train_ready集合完全一致；代码语法、内容一致性、三项硬门槛、变换及证据文件均已核对。\n"
text+=f"- 单例耗时中位数：{statistics.median(times):.3f}秒；最长{max(times):.3f}秒；逐例耗时合计{sum(times):.3f}秒。\n"
text+='- 本轮修复：微小IoU越界保留原值，只有独立Cut验证一致时才通过；接受阈值未放宽。\n'
text+='- v3到v4状态变化来自验收协议变化，仅用于解释，不能据此声称转换器几何质量提高。\n'
text+='- 机器可读核对结果见 `verification_summary.json`。\n'
report.write_text(text,encoding='utf-8')
print(json.dumps(summary,indent=2,ensure_ascii=False))
