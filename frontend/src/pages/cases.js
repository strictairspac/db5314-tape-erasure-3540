import {heading, panel} from '../components/common/heading.js';
import {entries, records} from '../components/common/records.js';
import {date, esc} from '../utils/dom.js';
import {topic} from '../constants/project.js';
import {submit} from '../hooks/submit.js';
import {go, route} from '../router/hash.js';

function replicaPanel(data) {
  if (!data.seeded) return '<p class="note">尚未建立隔离副本。首次执行或补录合同时从原始档案播种，副本留到下个检查日接着用，已擦除的资产不会从原始档案恢复。</p>';
  const tapes = data.archive.reduce((total,row)=>total+row.tape_count, 0);
  const holds = data.holds.length
    ? records(['合同编号','资产编号','保护至'], data.holds.map(row=>[esc(row.contract_id),esc(row.asset_id),esc(row.hold_until || '无固定截止日')]))
    : '<p class="note">当前副本没有合同留存关联。</p>';
  return `<dl><dt>副本建立</dt><dd>${esc(date(data.seeded_at))}</dd><dt>留存资产</dt><dd>${data.assets} 项</dd><dt>匿名归档</dt><dd>${data.archive.length} 组 · 累计样带 ${tapes} 条</dd></dl>${holds}`;
}

export function casesPage(state, identity) {
  const {spec, session, cases, runs} = state;
  const selected = cases.find(row=>row.id===identity) || cases[0];
  const operator = session.role==='operator';
  const rows = cases.map(row=>[`<a href="#cases/${row.id}" ${row.id===selected?.id?'aria-current="true"':''}>${esc(row.title)}</a>`, esc(row.author), esc(date(row.created_at))]);
  const instruction = selected ? `<h2>${esc(selected.title)}</h2><p>${esc(spec.guidance)}</p>` : '';
  const input = selected ? Object.entries(selected.payload).map(([key, value])=>{
    const label = spec.field_labels[key] || key;
    const rows = Array.isArray(value) && value.every(x=>x && typeof x === 'object' && !Array.isArray(x));
    return panel(label, rows ? entries(value.slice(0,10), spec.field_labels) + (value.length>10?`<p class="note">显示前 10 条，共 ${value.length} 条。</p>`:'') : `<pre>${esc(value)}</pre>`);
  }).join('') : '';
  const workspace = selected ? [
    panel('隔离副本', '<div id="replica-panel"><p class="note">正在读取隔离副本状态…</p></div>'),
    operator ? panel('提交执行', `<form id="run-form"><label class="field"><span>检查日</span><input type="date" name="cutoff" value="${esc(selected.payload?.cutoff || '')}"></label><p class="note">留空按档案登记的检查日执行。同一档案的多次执行按提交顺序串行，较晚提交的按变化后的目录重算。</p><button>提交执行</button></form>`) : '',
    operator ? panel('补录合同', `<form id="holds-form"><label class="field data"><span>合同留存关联 JSON</span><textarea name="holds" rows="5" spellcheck="false">[{"contract_id":"CT-217","asset_id":"T-06","hold_until":"2026-12-15"}]</textarea></label><p class="note">擦除排队后仍可补录；先入库的合同保护尚未擦除的资产及其依赖。</p><button>补录合同</button></form>`) : '',
  ].join('') : '';
  return `${heading(topic.modules[0], spec.summary, operator?'<a class="button" href="#editor">建立档案</a>':'')}
    <div class="spread"><section aria-label="档案目录">${records([spec.entity_labels[0], '经办', '建立时间'], rows, '尚未建立档案。经办员可以从演示资料建立第一份。')}</section>
    <aside class="brief">${instruction || `<h2>${esc(spec.customer)}</h2>`}${selected?`<p class="note">该档案记录 ${runs.filter(row=>row.case_id===selected.id).length} 次执行。</p>`:''}${selected&&operator?`<a href="#editor/${selected.id}">复制为新档案</a>`:''}</aside></div>
    ${workspace ? `<div class="inspection">${workspace}</div>`:''}
    ${input ? `<div class="inspection">${input}</div>`:''}`;
}

export function bindCases(state, api, fail, done) {
  const selected = state.cases.find(row=>row.id===route().id) || state.cases[0];
  if (!selected) return;
  const slot = document.getElementById('replica-panel');
  if (slot) api(`/cases/${selected.id}/replica`).then(data=>{ slot.innerHTML = replicaPanel(data); }).catch(fail);
  submit('run-form', async data=>{
    const cutoff = data.get('cutoff');
    const run = await api(`/cases/${selected.id}/runs`, {method:'POST', data: cutoff ? {cutoff} : {}});
    go(`runs/${run.id}`);
  }, fail);
  submit('holds-form', async data=>{
    let holds;
    try { holds = JSON.parse(data.get('holds')); }
    catch { throw new Error('请检查合同留存关联的 JSON 格式。'); }
    await api(`/cases/${selected.id}/holds`, {method:'POST', data:{holds}});
    done();
  }, fail);
}
