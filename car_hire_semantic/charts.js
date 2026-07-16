/* Shared chart module for the Car Hire UI + report page.
   Auto-selects a chart form from the result's column types and renders SVG
   charts on the dark surface using the dataviz reference palette.
   Loaded as a plain <script> (functions are global). */

/* ---- i18n (shared) ---- */
const I18N={
  zh:{
    brand_sub:"语义层", nav_query:"查询操作台", nav_catalog:"数据目录", nav_know:"关系 & 指标",
    foot_dialect:"方言", foot_scope:"范围", foot_tag:"AI-Native 数据分析",
    q_title:"查询操作台", q_sub:"用自然语言提问 → 看到检索命中的表 / 指标 / 枚举 → 生成给分析 Agent 的 prompt",
    q_ph:"例如：上个月每个 market 的搜索量和平均报价数", btn_gen:"生成",
    st_tables:"命中表", st_mapped:"已映射物理表", st_metrics:"命中指标", st_enums:"相关枚举", st_dialect:"方言",
    res_title:"查询结果（连 Databricks 真执行 · 只读护栏：仅 SELECT · 自动 LIMIT）",
    btn_validate:"校验", btn_run:"执行", open_report:"↗ 打开完整报告",
    sql_summary:"SQL（可编辑；起始脚手架已是可直接执行的真实表查询）",
    sql_hint:"改成你要的聚合/时间即可。大表整月聚合可能超时，先缩到单日验证。",
    run_placeholder:"点上方 ▶ 执行 运行下方 SQL，结果与图表会显示在这里",
    running:"在 Databricks 上执行（大表可能需数十秒）…",
    validating:"校验中…", validated_ok:"✓ 校验通过，可执行",
    rows:"行", auto_detect:"自动识别", truncated:"已截断",
    t_auto:"自动", t_bar:"柱状", t_pie:"饼图", t_line:"折线", t_table:"表格",
    ref_title:"语义层参考（检索命中的表 / 指标 / 枚举 + Agent Prompt）",
    sec_tables:"相关表结构（含物理映射）", sec_metrics:"命中指标口径", sec_enums:"相关枚举", sec_gloss:"术语",
    prompt_title:"组装好的 Agent Prompt", btn_copy:"复制", copied:"已复制 ✓",
    phys_title:"物理映射", phys_reco:"推荐", phys_raw:"原始", phys_unmapped:"未映射，Agent 按逻辑层推理并声明假设",
    phys_union:"⚠ 按平台拆表，跨平台需 UNION", phys_note:"备注", phys_nested:"嵌套",
    fields:"字段", grain:"粒度", no_desc:"无描述", meaning:"含义",
    empty_hit:"未命中任何表，换个说法试试",
    cat_title:"数据目录", cat_sub:"浏览语义层里的全部 message 与 enum，点开看字段与物理映射",
    cat_ph:"搜索表名 / 描述…", f_all:"全部", f_enum:"枚举", cat_fields:"字段", cat_values:"取值", cat_nomatch:"没有匹配项",
    know_title:"关系 & 指标", know_sub:"Join key 命名空间、转化漏斗、指标口径、待确认项——Agent 每次都会加载这些",
    k_joinkey:"Join Key 命名空间", k_joinkey_sub:"两个不直接相连的 ID 空间——这是写错查询的头号来源。",
    k_funnel:"转化漏斗", k_metrics:"指标口径", k_open:"待确认项", k_pitfall:"陷阱",
    loading:"加载中…", exec_ok:"执行成功", no_rows:"无结果行",
    hint_timeout:"疑似全表扫描超时，缩小 dt 范围或用更轻的聚合",
    hint_nodata:"很快 → 不是超时：该表在这个 dt 分区没有数据，换个日期或换张表",
    // chart internals
    c_bymag:"· 按量级排序", c_overtime:"· 随时间", c_other:"其他", c_topn:"仅显示前 {n} 项（共 {total}）",
    // shape labels
    s_kpi:"单值 → KPI 卡", s_bar:"类别+数值 → 柱状图", s_pie:"占比 → 饼图", s_line:"时间序列 → 折线",
    s_smallmult:"多指标 → 小多图", s_multiline:"多指标时序 → 多折线", s_table:"原始/嵌套数据 → 表格",
  },
  en:{
    brand_sub:"Semantic Layer", nav_query:"Query Console", nav_catalog:"Data Catalog", nav_know:"Relations & Metrics",
    foot_dialect:"Dialect", foot_scope:"Scope", foot_tag:"AI-Native Data Analysis",
    q_title:"Query Console", q_sub:"Ask in natural language → see the matched tables / metrics / enums → generate the analysis-agent prompt",
    q_ph:"e.g. searches and avg quotes per market last month", btn_gen:"Generate",
    st_tables:"Tables", st_mapped:"Mapped", st_metrics:"Metrics", st_enums:"Enums", st_dialect:"Dialect",
    res_title:"Results (live Databricks · read-only: SELECT only · auto LIMIT)",
    btn_validate:"Validate", btn_run:"Run", open_report:"↗ Open full report",
    sql_summary:"SQL (editable; the starter scaffold is already a runnable real-table query)",
    sql_hint:"Edit into your own aggregate / time window. Full-month aggregates on big tables may time out — try a single day first.",
    run_placeholder:"Click ▶ Run above to execute the SQL below; results and charts appear here",
    running:"Running on Databricks (big tables may take tens of seconds)…",
    validating:"Validating…", validated_ok:"✓ Valid, ready to run",
    rows:"rows", auto_detect:"Auto-detected", truncated:"truncated",
    t_auto:"Auto", t_bar:"Bar", t_pie:"Pie", t_line:"Line", t_table:"Table",
    ref_title:"Semantic layer reference (matched tables / metrics / enums + Agent Prompt)",
    sec_tables:"Relevant tables (with physical mapping)", sec_metrics:"Matched metric definitions", sec_enums:"Relevant enums", sec_gloss:"Glossary",
    prompt_title:"Assembled Agent Prompt", btn_copy:"Copy", copied:"Copied ✓",
    phys_title:"Physical mapping", phys_reco:"Curated", phys_raw:"Bronze", phys_unmapped:"Unmapped — agent reasons at the logical level and states assumptions",
    phys_union:"⚠ Split by platform; UNION across platforms", phys_note:"Note", phys_nested:"Nested",
    fields:"fields", grain:"grain", no_desc:"no description", meaning:"meaning",
    empty_hit:"No table matched — try rephrasing",
    cat_title:"Data Catalog", cat_sub:"Browse every message and enum in the semantic layer; click for fields and physical mapping",
    cat_ph:"Search name / description…", f_all:"All", f_enum:"Enums", cat_fields:"fields", cat_values:"values", cat_nomatch:"No match",
    know_title:"Relations & Metrics", know_sub:"Join-key namespaces, the conversion funnel, metric definitions and open confirmations — always loaded for the agent",
    k_joinkey:"Join-key namespaces", k_joinkey_sub:"Two ID spaces that don't directly connect — the #1 source of wrong queries.",
    k_funnel:"Conversion funnel", k_metrics:"Metric definitions", k_open:"Open confirmations", k_pitfall:"Pitfalls",
    loading:"Loading…", exec_ok:"Executed", no_rows:"no rows",
    hint_timeout:"likely a full-scan timeout — narrow the dt range or use a lighter aggregate",
    hint_nodata:"fast → not a timeout: this table has no data in that dt partition; try another date or table",
    c_bymag:"· by magnitude", c_overtime:"· over time", c_other:"Other", c_topn:"Top {n} of {total}",
    s_kpi:"single value → KPI", s_bar:"category + measure → bar", s_pie:"share → pie", s_line:"time series → line",
    s_smallmult:"multi-measure → small multiples", s_multiline:"multi-measure time series → multi-line", s_table:"raw / nested → table",
  }
};
function curLang(){try{return localStorage.getItem('ch_lang')||'zh';}catch(e){return 'zh';}}
function setLang(l){try{localStorage.setItem('ch_lang',l);}catch(e){}}
function t(k,vars){let s=(I18N[curLang()]||{})[k];if(s==null)s=(I18N.zh[k]!=null?I18N.zh[k]:k);
  if(vars)for(const kk in vars)s=s.split('{'+kk+'}').join(vars[kk]);return s;}

function esc(s){return (s==null?'':String(s)).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
function num(v){const x=parseFloat(String(v==null?'':v).replace(/,/g,''));return isNaN(x)?null:x;}
function fmt(n){if(n==null)return '';const a=Math.abs(n);return a>=1000?n.toLocaleString('en-US',{maximumFractionDigits:2}):(Math.round(n*1000)/1000).toString();}

// ONLY simple scalar numeric types are measures. ARRAY/STRUCT/MAP are complex
// (their type string can contain "INT" etc — must not be treated as numeric).
const SIMPLE_NUM=/^(TINYINT|SMALLINT|INT|INTEGER|BIGINT|LONG|SHORT|FLOAT|DOUBLE|DECIMAL|NUMERIC|REAL)\b/i;
const COMPLEX=/^(ARRAY|STRUCT|MAP)/i;
const DATERE=/^(DATE|TIMESTAMP)/i;

function classify(cols,rows){
  const dims=[],meas=[],skip=[];
  cols.forEach(c=>{
    const t=(c.type||'').toUpperCase().trim(), nm=c.name;
    if(COMPLEX.test(t)){skip.push({...c,role:'complex'});return;}
    const dateish=DATERE.test(t)||(/^(dt|date|day|month|week|ymd)$/i.test(nm)&&rows.length&&/^\d{4}-\d\d(-\d\d)?/.test(String(rows[0][nm]||'')));
    if(dateish){dims.push({...c,role:'date'});return;}
    if(SIMPLE_NUM.test(t)){
      // keep as measure only if at least one value actually parses to a number
      const hasNum=rows.some(r=>num(r[nm])!=null);
      (hasNum?meas:dims).push({...c,role:hasNum?'measure':'cat'});
      return;
    }
    dims.push({...c,role:'cat'});
  });
  return {dims,meas,skip};
}
function pickForm(dims,meas,rows,skip){
  // raw preview (has nested columns, or no usable measure) → table
  if(skip&&skip.length)return 'table';
  if(!meas.length)return 'table';
  if(rows.length===1)return 'kpi';
  const hasDate=dims.some(d=>d.role==='date');
  const catDims=dims.filter(d=>d.role==='cat');
  if((catDims.length>=1||hasDate)&&meas.length===1)return hasDate?'line':'bar';
  if((catDims.length>=1||hasDate)&&meas.length>1)return hasDate?'multiline':'smallmult';
  return 'table';
}

function barChart(labels,vals,measName){
  const N=Math.min(labels.length,20),W=760,rowH=30,padT=8,padB=8;
  const gut=Math.min(180,Math.max(70,Math.max(...labels.slice(0,N).map(l=>String(l).length))*7+14)),valW=90;
  const H=padT+padB+N*rowH, max=Math.max(...vals.slice(0,N),1), barW=W-gut-valW;
  let bars='';
  for(let k=0;k<N;k++){
    const y=padT+k*rowH+4, w=Math.max(2,barW*(vals[k]/max));
    bars+=`<rect x="${gut}" y="${y}" width="${w}" height="${rowH-12}" rx="4" fill="var(--seq-3)"><title>${esc(labels[k])}: ${fmt(vals[k])}</title></rect>`;
    bars+=`<text x="${gut-8}" y="${y+(rowH-12)/2}" text-anchor="end" dominant-baseline="central" fill="var(--viz-ink2)" font-size="12">${esc(String(labels[k]).slice(0,24))}</text>`;
    bars+=`<text x="${gut+w+6}" y="${y+(rowH-12)/2}" dominant-baseline="central" fill="var(--viz-ink)" font-size="11.5" style="font-variant-numeric:tabular-nums">${fmt(vals[k])}</text>`;
  }
  const note=labels.length>N?`<div style="color:var(--faint);font-size:11px;margin-top:6px">${t('c_topn',{n:N,total:labels.length})}</div>`:'';
  return `<div style="color:var(--viz-muted);font-size:11.5px;margin-bottom:6px">${esc(measName)} ${t('c_bymag')}</div><svg viewBox="0 0 ${W} ${H}" width="100%" style="max-width:${W}px">${bars}</svg>${note}`;
}
function pieChart(labels,vals){
  const N=Math.min(labels.length,8);let L=labels.slice(0,N),V=vals.slice(0,N);
  if(labels.length>N){L=L.concat([t('c_other')]);V=V.concat([vals.slice(N).reduce((a,b)=>a+b,0)]);}
  const total=V.reduce((a,b)=>a+b,0)||1,cx=150,cy=150,r=120,W=560,H=300;
  const cats=['--cat-1','--cat-2','--cat-3','--cat-4','--cat-5','--cat-6','--cat-7','--cat-8'];
  let ang=-Math.PI/2,seg='',leg='';
  V.forEach((v,k)=>{
    const frac=v/total,a2=ang+frac*2*Math.PI;
    const x1=cx+r*Math.cos(ang),y1=cy+r*Math.sin(ang),x2=cx+r*Math.cos(a2),y2=cy+r*Math.sin(a2);
    const large=frac>0.5?1:0,col=`var(${cats[k%8]})`;
    seg+=`<path d="M${cx} ${cy} L${x1} ${y1} A${r} ${r} 0 ${large} 1 ${x2} ${y2} Z" fill="${col}" stroke="var(--viz-surface)" stroke-width="2"><title>${esc(L[k])}: ${fmt(v)} (${(frac*100).toFixed(1)}%)</title></path>`;
    if(frac>0.05){const mid=(ang+a2)/2,lx=cx+r*0.62*Math.cos(mid),ly=cy+r*0.62*Math.sin(mid);
      seg+=`<text x="${lx}" y="${ly}" text-anchor="middle" dominant-baseline="central" fill="#fff" font-size="11.5" font-weight="600">${(frac*100).toFixed(0)}%</text>`;}
    leg+=`<div style="display:flex;align-items:center;gap:8px;margin:5px 0;font-size:12px"><span style="width:11px;height:11px;border-radius:3px;background:${col};flex:none"></span><span style="color:var(--viz-ink)">${esc(L[k])}</span><span style="color:var(--faint);margin-left:auto;font-variant-numeric:tabular-nums">${fmt(v)}</span></div>`;
    ang=a2;
  });
  return `<div style="display:flex;gap:20px;align-items:center;flex-wrap:wrap"><svg viewBox="0 0 ${W} ${H}" width="320" style="max-width:100%">${seg}<circle cx="${cx}" cy="${cy}" r="52" fill="var(--panel)"/></svg><div style="min-width:200px;flex:1">${leg}</div></div>`;
}
function lineChart(labels,vals,measName){
  const W=760,H=280,padL=58,padR=16,padT=16,padB=34,n=labels.length;
  const max=Math.max(...vals,1),min=Math.min(...vals,0);
  const xs=i=>padL+(W-padL-padR)*(n<=1?0.5:i/(n-1)), ys=v=>padT+(H-padT-padB)*(1-(v-min)/((max-min)||1));
  let path='',dots='',ticks='';
  vals.forEach((v,i)=>{path+=(i?'L':'M')+xs(i)+' '+ys(v)+' ';dots+=`<circle cx="${xs(i)}" cy="${ys(v)}" r="3.5" fill="var(--seq-3)"><title>${esc(labels[i])}: ${fmt(v)}</title></circle>`;});
  const step=Math.max(1,Math.ceil(n/8));
  labels.forEach((l,i)=>{if(i%step===0)ticks+=`<text x="${xs(i)}" y="${H-12}" text-anchor="middle" fill="var(--viz-muted)" font-size="10">${esc(String(l).slice(5))}</text>`;});
  [max,(max+min)/2,min].forEach(gv=>{ticks+=`<text x="${padL-8}" y="${ys(gv)}" text-anchor="end" dominant-baseline="central" fill="var(--viz-muted)" font-size="10" style="font-variant-numeric:tabular-nums">${fmt(gv)}</text><line x1="${padL}" y1="${ys(gv)}" x2="${W-padR}" y2="${ys(gv)}" stroke="var(--line-soft)" stroke-width="1"/>`;});
  return `<div style="color:var(--viz-muted);font-size:11.5px;margin-bottom:6px">${esc(measName)} ${t('c_overtime')}</div><svg viewBox="0 0 ${W} ${H}" width="100%" style="max-width:${W}px">${ticks}<path d="${path}" fill="none" stroke="var(--seq-3)" stroke-width="2"/>${dots}</svg>`;
}
function kpiTiles(meas,row){
  const cols=['--cat-1','--cat-2','--cat-3','--cat-5'];
  return `<div style="display:flex;gap:14px;flex-wrap:wrap">`+meas.map((m,i)=>`<div class="stat" style="min-width:150px"><div class="k">${esc(m.name)}</div><div style="font-size:30px;font-weight:750;color:var(${cols[i%4]});margin-top:4px">${fmt(num(row[m.name]))}</div></div>`).join('')+`</div>`;
}
function dataTable(cols,rows){
  const th=cols.map(c=>`<th style="text-align:left;padding:7px 12px;border-bottom:2px solid var(--line);color:var(--muted);font-size:11.5px;white-space:nowrap">${esc(c.name)}<span style="color:var(--faint);font-weight:400"> ${esc(c.type||'')}</span></th>`).join('');
  const trs=rows.map(r=>`<tr>${cols.map(c=>`<td style="padding:6px 12px;border-bottom:1px solid var(--line-soft);font-family:var(--mono);font-size:12px;color:var(--text);white-space:nowrap;font-variant-numeric:tabular-nums">${esc(typeof r[c.name]==='object'?JSON.stringify(r[c.name]):r[c.name])}</td>`).join('')}</tr>`).join('');
  return `<div style="overflow:auto;border:1px solid var(--line);border-radius:10px;background:var(--panel);max-height:560px"><table style="border-collapse:collapse;width:100%"><thead><tr>${th}</tr></thead><tbody>${trs}</tbody></table></div>`;
}
function pairsFor(rows,dim,m){
  return rows.map(r=>[r[dim.name],num(r[m.name])]).filter(p=>p[1]!=null);
}
function buildChart(form,cols,rows,dims,meas){
  if(form==='table')return dataTable(cols,rows);
  if(form==='kpi')return kpiTiles(meas,rows[0]);
  const dim=dims[0]||{name:cols[0].name};
  if(form==='bar'){const m=meas[0];const p=pairsFor(rows,dim,m).sort((a,b)=>b[1]-a[1]);return p.length?barChart(p.map(x=>x[0]),p.map(x=>x[1]),m.name):dataTable(cols,rows);}
  if(form==='pie'){const m=meas[0];const p=pairsFor(rows,dim,m).sort((a,b)=>b[1]-a[1]);return p.length?pieChart(p.map(x=>x[0]),p.map(x=>x[1])):dataTable(cols,rows);}
  if(form==='line'){const m=meas[0];const p=pairsFor(rows,dim,m).sort((a,b)=>String(a[0]).localeCompare(String(b[0])));return p.length?lineChart(p.map(x=>x[0]),p.map(x=>x[1]),m.name):dataTable(cols,rows);}
  if(form==='smallmult')return meas.map(m=>{const p=pairsFor(rows,dim,m).sort((a,b)=>b[1]-a[1]);return `<div style="margin-bottom:18px">${barChart(p.map(x=>x[0]),p.map(x=>x[1]),m.name)}</div>`;}).join('');
  if(form==='multiline')return meas.map(m=>{const p=pairsFor(rows,dim,m).sort((a,b)=>String(a[0]).localeCompare(String(b[0])));return `<div style="margin-bottom:18px">${lineChart(p.map(x=>x[0]),p.map(x=>x[1]),m.name)}</div>`;}).join('');
  return dataTable(cols,rows);
}
function availTypes(dims,meas,skip){
  const a=[['auto',t('t_auto')]];
  const catOrDate=dims.filter(d=>d.role==='cat'||d.role==='date').length;
  if(!(skip&&skip.length)&&catOrDate>=1&&meas.length>=1){a.push(['bar',t('t_bar')]);a.push(['pie',t('t_pie')]);}
  if(!(skip&&skip.length)&&dims.some(d=>d.role==='date')&&meas.length>=1)a.push(['line',t('t_line')]);
  a.push(['table',t('t_table')]);
  return a;
}
function shapeLabel(form){return t('s_'+form)||form;}
function stats(rows,m){
  const v=rows.map(r=>num(r[m.name])).filter(x=>x!=null);
  if(!v.length)return null;
  const sum=v.reduce((a,b)=>a+b,0);
  return {sum,avg:sum/v.length,min:Math.min(...v),max:Math.max(...v),n:v.length};
}
