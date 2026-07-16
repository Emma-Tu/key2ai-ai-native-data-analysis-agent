/* Shared chart module for the Car Hire UI + report page.
   Auto-selects a chart form from the result's column types and renders SVG
   charts on the dark surface using the dataviz reference palette.
   Loaded as a plain <script> (functions are global). */

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
  const note=labels.length>N?`<div style="color:var(--faint);font-size:11px;margin-top:6px">仅显示前 ${N} 项（共 ${labels.length}）</div>`:'';
  return `<div style="color:var(--viz-muted);font-size:11.5px;margin-bottom:6px">${esc(measName)} · 按量级排序</div><svg viewBox="0 0 ${W} ${H}" width="100%" style="max-width:${W}px">${bars}</svg>${note}`;
}
function pieChart(labels,vals){
  const N=Math.min(labels.length,8);let L=labels.slice(0,N),V=vals.slice(0,N);
  if(labels.length>N){L=L.concat(['其他']);V=V.concat([vals.slice(N).reduce((a,b)=>a+b,0)]);}
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
  return `<div style="color:var(--viz-muted);font-size:11.5px;margin-bottom:6px">${esc(measName)} · 随时间</div><svg viewBox="0 0 ${W} ${H}" width="100%" style="max-width:${W}px">${ticks}<path d="${path}" fill="none" stroke="var(--seq-3)" stroke-width="2"/>${dots}</svg>`;
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
  const a=[['auto','自动']];
  const catOrDate=dims.filter(d=>d.role==='cat'||d.role==='date').length;
  if(!(skip&&skip.length)&&catOrDate>=1&&meas.length>=1){a.push(['bar','柱状']);a.push(['pie','饼图']);}
  if(!(skip&&skip.length)&&dims.some(d=>d.role==='date')&&meas.length>=1)a.push(['line','折线']);
  a.push(['table','表格']);
  return a;
}
function stats(rows,m){
  const v=rows.map(r=>num(r[m.name])).filter(x=>x!=null);
  if(!v.length)return null;
  const sum=v.reduce((a,b)=>a+b,0);
  return {sum,avg:sum/v.length,min:Math.min(...v),max:Math.max(...v),n:v.length};
}
