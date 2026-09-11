from __future__ import annotations

from html import escape
import json
from pathlib import Path
from typing import Any


def write_visualization(
    payload: dict[str, Any],
    path: str | Path,
    *,
    title: str = "구급차 SMDP 상황실",
) -> Path:
    """Write a self-contained, interactive HTML playback of one traced episode."""
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    document = _HTML.replace("__TITLE__", escape(title)).replace("__TRACE_JSON__", data)
    output.write_text(document, encoding="utf-8")
    return output


_HTML = r'''<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>
:root{--night:#071722;--panel:#0c2230;--panel2:#102c3b;--line:#31505f;--muted:#91a7b2;--paper:#eaf1f3;--orange:#ff6b35;--cyan:#38d6c3;--red:#ff4057;--blue:#54a7ff;--yellow:#ffd166}
*{box-sizing:border-box}html,body{margin:0;min-height:100%;background:var(--night);color:var(--paper);font-family:"Pretendard","Noto Sans KR","Malgun Gothic",sans-serif}button,input,select{font:inherit}
body{min-height:100vh;background-image:linear-gradient(rgba(61,99,116,.08) 1px,transparent 1px),linear-gradient(90deg,rgba(61,99,116,.08) 1px,transparent 1px);background-size:32px 32px}
.app{min-height:100vh;display:grid;grid-template-rows:auto 1fr auto;padding:18px;gap:14px;max-width:1800px;margin:auto}
header{display:flex;align-items:end;justify-content:space-between;border-bottom:1px solid var(--line);padding:2px 2px 14px;gap:20px}.brand h1{font-size:clamp(22px,3vw,38px);line-height:1;margin:0 0 8px;letter-spacing:-.04em}.brand p{margin:0;color:var(--muted);font-size:14px}.clock{text-align:right;font-variant-numeric:tabular-nums}.clock strong{font-size:clamp(28px,4vw,50px);font-weight:650;letter-spacing:-.04em}.clock span{display:block;color:var(--cyan);font-size:13px;margin-top:3px}
.workspace{display:grid;grid-template-columns:minmax(0,1fr) 340px;gap:14px;min-height:0}.map-panel{position:relative;min-height:620px;background:rgba(9,31,43,.94);border:1px solid var(--line);overflow:hidden}.map-panel:before{content:"";position:absolute;inset:0;background:radial-gradient(circle at 55% 45%,rgba(56,214,195,.08),transparent 43%);pointer-events:none}.map-title{position:absolute;z-index:2;left:18px;top:15px;margin:0;font-size:13px;font-weight:600;color:var(--muted)}#map{display:block;width:100%;height:100%;min-height:620px}.road{stroke:#365363;stroke-width:3;stroke-linecap:round}.node-label{fill:#b9c8cf;font-size:13px;paint-order:stroke;stroke:var(--night);stroke-width:4px;stroke-linejoin:round}.node-shape{stroke-width:3}.village{fill:#173b4b;stroke:#79a8b8}.station{fill:#263746;stroke:var(--blue)}.hospital{fill:#123b39;stroke:var(--cyan)}.patient-badge{fill:var(--red);stroke:var(--paper);stroke-width:2}.patient-count{fill:white;font-size:11px;font-weight:700;text-anchor:middle;dominant-baseline:central}.ambulance-body{fill:var(--orange);stroke:#fff;stroke-width:2}.ambulance-label{fill:white;font-size:11px;font-weight:700;text-anchor:middle;paint-order:stroke;stroke:#071722;stroke-width:4px}.route-active{stroke:var(--orange);stroke-width:3;stroke-dasharray:8 8;opacity:.8}.event-ring{fill:none;stroke:var(--yellow);stroke-width:5;opacity:.9}.map-key{position:absolute;left:16px;bottom:14px;display:flex;gap:14px;flex-wrap:wrap;padding:9px 12px;background:rgba(7,23,34,.84);border:1px solid var(--line);font-size:12px;color:var(--muted)}.key-dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:5px}
.rail{display:grid;grid-template-rows:auto auto 1fr;gap:12px;min-height:0}.metric-strip{display:grid;grid-template-columns:repeat(3,1fr);border:1px solid var(--line);background:var(--panel)}.metric{padding:13px 10px;border-right:1px solid var(--line)}.metric:last-child{border-right:0}.metric b{display:block;font-size:22px;font-variant-numeric:tabular-nums}.metric span{color:var(--muted);font-size:11px}.metric.saved b{color:var(--cyan)}.metric.lost b{color:var(--red)}
.fleet,.events{border:1px solid var(--line);background:var(--panel);padding:15px}.section-title{margin:0 0 12px;font-size:14px;font-weight:650}.fleet-list{display:grid;gap:7px}.unit{display:grid;grid-template-columns:38px 1fr auto;align-items:center;gap:8px;border-top:1px solid rgba(49,80,95,.65);padding-top:8px}.unit:first-child{border-top:0;padding-top:0}.unit-id{display:grid;place-items:center;width:32px;height:25px;background:var(--orange);color:white;font-size:11px;font-weight:700;clip-path:polygon(8% 15%,72% 15%,100% 48%,92% 85%,8% 85%,0 48%)}.unit-place{font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.unit-status{font-size:11px;color:var(--muted)}.events{overflow:auto;min-height:230px}.event-list{display:grid}.event{display:grid;grid-template-columns:48px 1fr;gap:9px;padding:8px 0;border-top:1px solid rgba(49,80,95,.5);font-size:12px}.event:first-child{border:0}.event time{color:var(--cyan);font-variant-numeric:tabular-nums}.event.current{color:white}.event:not(.current){color:var(--muted)}
.controls{display:grid;grid-template-columns:auto auto minmax(180px,1fr) auto;align-items:center;gap:12px;border:1px solid var(--line);background:var(--panel);padding:12px 14px}.control-button{border:1px solid #547181;background:#102c3b;color:white;min-width:72px;padding:9px 14px;cursor:pointer}.control-button.primary{background:var(--orange);border-color:var(--orange);font-weight:700}.control-button:focus-visible,select:focus-visible,input:focus-visible{outline:3px solid var(--yellow);outline-offset:2px}.timeline{accent-color:var(--orange);width:100%;cursor:pointer}.speed{display:flex;align-items:center;gap:7px;color:var(--muted);font-size:12px}.speed select{color:white;background:var(--panel2);border:1px solid #547181;padding:8px}.summary{position:absolute;right:16px;bottom:14px;text-align:right;color:var(--muted);font-size:11px;background:rgba(7,23,34,.84);padding:8px 10px;border:1px solid var(--line)}
@media(max-width:980px){.app{padding:10px}.workspace{grid-template-columns:1fr}.map-panel,#map{min-height:55vh}.rail{grid-template-columns:1fr 1fr;grid-template-rows:auto 260px}.metric-strip{grid-column:1/-1}.controls{grid-template-columns:auto 1fr auto}.controls .reset{display:none}}
@media(max-width:620px){header{align-items:start}.brand p{display:none}.workspace{display:block}.rail{display:grid;grid-template-columns:1fr;margin-top:10px}.events{max-height:230px}.controls{grid-template-columns:1fr 1fr}.timeline{grid-column:1/-1;grid-row:1}.speed{justify-self:end}.map-key{display:none}}
@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important}}
.fleet-list{max-height:260px;overflow:auto}
</style>
</head>
<body>
<main class="app">
  <header><div class="brand"><h1>__TITLE__</h1><p>기대 생존량을 중심으로 보는 중앙 관제 시뮬레이션</p></div><div class="clock"><strong id="clock">00:00</strong><span id="eventName">초기 상태</span></div></header>
  <section class="workspace">
    <div class="map-panel"><h2 class="map-title">도로망과 실시간 자원 상태</h2><svg id="map" viewBox="0 0 1000 680" role="img" aria-label="구급차 시뮬레이션 지도"></svg><div class="map-key"><span><i class="key-dot" style="background:#ff6b35"></i>구급차</span><span><i class="key-dot" style="background:#ff4057"></i>대기 환자</span><span><i class="key-dot" style="background:#38d6c3"></i>의료기관</span></div><div class="summary" id="summary"></div></div>
    <aside class="rail">
      <div class="metric-strip"><div class="metric saved"><b id="saved">0.00</b><span>기대 생존</span></div><div class="metric lost"><b id="lost">0.00</b><span>기대 손실</span></div><div class="metric"><b id="waiting">0</b><span>대기 환자</span></div></div>
      <section class="fleet"><h2 class="section-title">구급차 상태</h2><div class="fleet-list" id="fleet"></div></section>
      <section class="events"><h2 class="section-title">최근 사건</h2><div class="event-list" id="events" aria-live="polite"></div></section>
    </aside>
  </section>
  <section class="controls"><button class="control-button primary" id="play">재생</button><button class="control-button reset" id="reset">처음으로</button><input class="timeline" id="timeline" type="range" min="0" value="0" step="0.1" aria-label="시뮬레이션 시간"><label class="speed">속도<select id="speed"><option value="30">30분/초</option><option value="120" selected>2시간/초</option><option value="600">10시간/초</option></select></label></section>
</main>
<script type="application/json" id="simulation-data">__TRACE_JSON__</script>
<script>
const data=JSON.parse(document.getElementById('simulation-data').textContent);const frames=data.frames;const svg=document.getElementById('map');const NS='http://www.w3.org/2000/svg';const horizon=data.horizon_minutes||Math.max(...frames.map(f=>f.time));const nodes=[...new Set(Object.keys(data.network.edges).concat(Object.values(data.network.edges).flatMap(Object.keys)))];
const eventNames={initial:'초기 상태',initial_available:'구급차 재가용',patient_arrival:'환자 발생',arrive_scene:'현장 도착',scene_complete:'현장 처치 완료',arrive_hospital:'병원 도착',arrive_mortuary:'사망 환자 운송 완료',hospital_release:'병상 해제',transfer_ready:'전원 준비 완료',restock_complete:'재정비 완료',reposition_complete:'대기지 도착',horizon_end:'하루 종료'};
const statusNames={idle:'대기',unavailable:'사용 불가',to_scene:'환자에게 이동',on_scene:'현장 처치',to_hospital:'병원 이송',restocking:'재정비',repositioning:'재배치'};
const rawPos=data.network.positions||{};const positions={};nodes.forEach((n,i)=>{const p=rawPos[n]||[50+40*Math.cos((i/nodes.length)*Math.PI*2),50+40*Math.sin((i/nodes.length)*Math.PI*2)];positions[n]={x:70+p[0]*8.6,y:45+p[1]*5.8}});
function el(name,attrs={}){const n=document.createElementNS(NS,name);Object.entries(attrs).forEach(([k,v])=>n.setAttribute(k,v));return n}function textNode(x,y,value,cls){const n=el('text',{x,y,class:cls});n.textContent=value;return n}
const layers={roads:el('g'),routes:el('g'),nodes:el('g'),patients:el('g'),units:el('g'),pulse:el('g')};Object.values(layers).forEach(l=>svg.appendChild(l));
const seen=new Set;Object.entries(data.network.edges).forEach(([a,targets])=>Object.keys(targets).forEach(b=>{const key=[a,b].sort().join('|');if(seen.has(key)||!positions[a]||!positions[b])return;seen.add(key);layers.roads.appendChild(el('line',{x1:positions[a].x,y1:positions[a].y,x2:positions[b].x,y2:positions[b].y,class:'road'}))}));
const hospitalByNode=Object.fromEntries(Object.entries(data.hospitals).map(([id,h])=>[h.location,id]));const villageSet=new Set(data.villages);nodes.forEach(n=>{const p=positions[n];const hospital=hospitalByNode[n];const cls=hospital?'hospital':villageSet.has(n)?'village':'station';const shape=hospital?el('rect',{x:p.x-10,y:p.y-10,width:20,height:20,rx:3,class:`node-shape ${cls}`}):el('circle',{cx:p.x,cy:p.y,r:villageSet.has(n)?9:8,class:`node-shape ${cls}`});layers.nodes.appendChild(shape);layers.nodes.appendChild(textNode(p.x+14,p.y-12,n,'node-label'))});
if(data.metadata?.detail==='municipal'){layers.nodes.querySelectorAll('.node-label').forEach(label=>{if(label.textContent.endsWith(' 119')||label.textContent.endsWith(' 대표응급'))label.remove();else label.textContent=label.textContent.replace(/ 수요$/,'')})}
let simTime=0,playing=false,lastReal=0,currentIndex=0;const timeline=document.getElementById('timeline');timeline.max=horizon;
function frameAt(t){let lo=0,hi=frames.length-1;while(lo<hi){const mid=Math.ceil((lo+hi)/2);if(frames[mid].time<=t)lo=mid;else hi=mid-1}currentIndex=lo;return frames[lo]}
function posFor(unit,t){const from=positions[unit.location]||{x:500,y:340};if(!unit.destination||unit.movement_started_at==null||unit.movement_ends_at==null)return from;const to=positions[unit.destination]||from;const span=unit.movement_ends_at-unit.movement_started_at;const q=span<=0?1:Math.max(0,Math.min(1,(t-unit.movement_started_at)/span));return{x:from.x+(to.x-from.x)*q,y:from.y+(to.y-from.y)*q}}
function clear(layer){while(layer.firstChild)layer.removeChild(layer.firstChild)}function fmtTime(t){const m=Math.max(0,Math.floor(t));return`${String(Math.floor(m/60)%24).padStart(2,'0')}:${String(m%60).padStart(2,'0')}`}function esc(v){return String(v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function render(t){const frame=frameAt(t);document.getElementById('clock').textContent=fmtTime(t);document.getElementById('eventName').textContent=eventNames[frame.event]||frame.event;document.getElementById('saved').textContent=frame.totals.saved.toFixed(2);document.getElementById('lost').textContent=frame.totals.lost.toFixed(2);document.getElementById('waiting').textContent=frame.totals.waiting;timeline.value=t;
 clear(layers.routes);clear(layers.units);clear(layers.patients);clear(layers.pulse);const waitingByNode={};Object.values(frame.patients).forEach(p=>{if(['waiting','assigned','on_scene'].includes(p.status))waitingByNode[p.location]=(waitingByNode[p.location]||0)+1});Object.entries(waitingByNode).forEach(([node,count])=>{const p=positions[node];if(!p)return;layers.patients.appendChild(el('circle',{cx:p.x-12,cy:p.y+13,r:10,class:'patient-badge'}));layers.patients.appendChild(textNode(p.x-12,p.y+13,count,'patient-count'))});
 const fleet=[];Object.entries(frame.ambulances).forEach(([id,u])=>{const p=posFor(u,t);if(u.destination&&positions[u.location]&&positions[u.destination])layers.routes.appendChild(el('line',{x1:positions[u.location].x,y1:positions[u.location].y,x2:positions[u.destination].x,y2:positions[u.destination].y,class:'route-active'}));const g=el('g',{transform:`translate(${p.x},${p.y})`});g.appendChild(el('rect',{x:-13,y:-8,width:26,height:16,rx:4,class:'ambulance-body'}));g.appendChild(el('rect',{x:-4,y:-13,width:8,height:5,rx:1,fill:'#54a7ff'}));g.appendChild(textNode(0,25,id,'ambulance-label'));layers.units.appendChild(g);fleet.push(`<div class="unit"><span class="unit-id">${esc(id)}</span><span class="unit-place">${esc(u.destination?`${u.location} → ${u.destination}`:u.location)}</span><span class="unit-status">${esc(statusNames[u.status]||u.status)}</span></div>`)});document.getElementById('fleet').innerHTML=fleet.join('');
 Object.entries(frame.hospitals).forEach(([id,h])=>{const node=data.hospitals[id]?.location,p=positions[node];if(!p)return;const cap=h.capacity==null?'∞':h.capacity;layers.patients.appendChild(textNode(p.x+14,p.y+18,`${h.occupied}/${cap} 병상`,'node-label'))});const recent=frames.slice(Math.max(0,currentIndex-7),currentIndex+1).reverse();document.getElementById('events').innerHTML=recent.map((f,i)=>`<div class="event ${i===0?'current':''}"><time>${fmtTime(f.time)}</time><span>${esc(eventNames[f.event]||f.event)}${f.subject?.ambulance_id?` · ${esc(f.subject.ambulance_id)}`:''}${f.subject?.patient_id?.startsWith('P')?` · ${esc(f.subject.patient_id)}`:''}</span></div>`).join('');
 const sid=frame.subject?.ambulance_id;if(sid&&frame.ambulances[sid]){const p=posFor(frame.ambulances[sid],t);layers.pulse.appendChild(el('circle',{cx:p.x,cy:p.y,r:24,class:'event-ring'}))}const meta=data.metadata||{};document.getElementById('summary').innerHTML=`${meta.policy?`정책 ${meta.policy} · seed ${meta.seed}<br>`:''}전체 환자 ${data.summary.seeded_patients}<br>최종 기대 생존 ${data.summary.expected_saved.toFixed(2)}`}
function tick(now){if(!playing)return;if(!lastReal)lastReal=now;simTime+=((now-lastReal)/1000)*Number(document.getElementById('speed').value);lastReal=now;if(simTime>=horizon){simTime=horizon;playing=false;document.getElementById('play').textContent='재생'}render(simTime);if(playing)requestAnimationFrame(tick)}
document.getElementById('play').addEventListener('click',()=>{playing=!playing;document.getElementById('play').textContent=playing?'일시정지':'재생';lastReal=0;if(simTime>=horizon)simTime=0;if(playing)requestAnimationFrame(tick)});document.getElementById('reset').addEventListener('click',()=>{playing=false;simTime=0;document.getElementById('play').textContent='재생';render(0)});timeline.addEventListener('input',e=>{simTime=Number(e.target.value);render(simTime)});render(0);
</script>
</body></html>'''
