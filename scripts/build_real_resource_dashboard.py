"""Build an offline municipality resource map from verified real inputs."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


def rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--processed-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.processed_dir.resolve()
    summary = {row["municipality_code"]: row for row in rows(root / "municipality_summary.csv")}
    municipalities = []
    for code, item in sorted(summary.items(), key=lambda pair: pair[1]["municipality_name"]):
        folder = root / code
        readiness = json.loads((folder / "readiness.json").read_text(encoding="utf-8"))
        hospitals = [
            {
                "id": f"{code}::hospital::{index:03d}", "name": row["hospital_name"],
                "category": row["emergency_category"],
                "emergency_beds": int(row["emergency_room_beds"]) if row.get("emergency_room_beds") else None,
                "critical_care_beds": int(row["critical_care_beds"]) if row.get("critical_care_beds") else None,
                "address": row["road_address"] or row["lot_address"],
                "lat": float(row["latitude"]), "lon": float(row["longitude"]),
                "source": row["coordinate_source"],
            }
            for index, row in enumerate(rows(folder / "existing_hospitals.csv"), start=1)
            if row.get("latitude") and row.get("longitude")
        ]
        bases = [
            {
                "id": f"{code}::ambulance_base::{index:03d}",
                "name": row["base_name"], "station": row["fire_station"],
                "address": row["matched_address"] or row["address"],
                "ambulances": int(row["ambulance_count"]),
                "lat": float(row["latitude"]), "lon": float(row["longitude"]),
                "source": row["coordinate_source"],
            }
            for index, row in enumerate(rows(folder / "ambulance_bases.csv"), start=1)
            if row.get("latitude") and row.get("longitude")
        ]
        candidates = [
            {
                "id": f"{code}::candidate::{row['candidate_id']}",
                "name": row["candidate_name"],
                "address": row["matched_address"] or row["road_address"],
                "status": row["land_feasibility_status"],
                "lat": float(row["latitude"]), "lon": float(row["longitude"]),
                "source": row["candidate_source"],
            }
            for row in rows(folder / "candidate_sites.csv")
            if row.get("latitude") and row.get("longitude")
        ]
        route_path = folder / "road_times.csv"
        routes = [
            {
                "origin": row["origin_id"], "destination": row["destination_id"],
                "minutes": float(row["duration_minutes"]),
                "distance_meters": int(float(row["distance_meters"])),
            }
            for row in rows(route_path)
        ] if route_path.exists() else []
        route_manifest_path = folder / "road_times_manifest.json"
        route_manifest = (
            json.loads(route_manifest_path.read_text(encoding="utf-8"))
            if route_manifest_path.exists() else {}
        )
        municipalities.append({
            "code": code, "name": item["municipality_name"],
            "population": int(item["population_202608"]),
            "demand_points": int(item["demand_point_count"]),
            "hospitals": hospitals, "bases": bases, "candidates": candidates,
            "routes": routes, "route_manifest": route_manifest,
            "ambulances": sum(base["ambulances"] for base in bases),
            "blockers": readiness["blocking_inputs"],
        })

    data = json.dumps(municipalities, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    document = HTML.replace("__DATA__", data)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(document, encoding="utf-8")
    print(args.output.resolve())
    return 0


HTML = r'''<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>경기도 시·군 응급의료 자원 지도</title>
<style>
:root{--ink:#102a3a;--paper:#eef3f4;--panel:#fff;--line:#b8c8cd;--hospital:#e24a3b;--ambulance:#008b95;--candidate:#bb7a10;--pending:#bb7a10;--quiet:#61757e}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font-family:"Pretendard","Noto Sans KR","Malgun Gothic",sans-serif}.app{max-width:1550px;margin:auto;padding:18px}.top{display:flex;justify-content:space-between;align-items:end;gap:20px;padding:8px 2px 16px;border-bottom:3px solid var(--ink)}h1{margin:0;font-size:clamp(25px,3.5vw,46px);letter-spacing:-.055em;line-height:1}.top p{margin:8px 0 0;color:var(--quiet);font-size:13px}.picker label{font-size:12px;color:var(--quiet);display:block;margin-bottom:5px}.picker select{min-width:190px;padding:10px 34px 10px 12px;border:1px solid var(--ink);background:white;color:var(--ink);font-weight:700}.truth{display:flex;align-items:center;gap:10px;padding:11px 14px;margin:14px 0;background:#fff5da;border-left:5px solid var(--pending);font-size:13px}.truth b{white-space:nowrap}.metrics{display:grid;grid-template-columns:repeat(5,1fr);border:1px solid var(--line);background:var(--panel);margin-bottom:14px}.metric{padding:15px 18px;border-right:1px solid var(--line)}.metric:last-child{border:0}.metric span{display:block;color:var(--quiet);font-size:12px;margin-bottom:3px}.metric strong{font-size:25px;font-variant-numeric:tabular-nums}.stage{display:grid;grid-template-columns:minmax(0,1fr) 330px;border:1px solid var(--line);background:var(--panel);min-height:610px}.map-wrap{position:relative;border-right:1px solid var(--line);overflow:hidden;background-image:linear-gradient(#dfe8ea 1px,transparent 1px),linear-gradient(90deg,#dfe8ea 1px,transparent 1px);background-size:40px 40px}.map-head{position:absolute;z-index:2;left:16px;top:14px;background:rgba(255,255,255,.92);border:1px solid var(--line);padding:9px 11px;font-size:12px}.map-head b{display:block;font-size:14px;margin-bottom:2px}#map{display:block;width:100%;height:610px}.axis{stroke:#afc0c5;stroke-width:1}.marker{cursor:pointer;outline:none}.hospital-dot{fill:var(--hospital);stroke:white;stroke-width:2}.base-dot{fill:var(--ambulance);stroke:white;stroke-width:2}.mark-label{font-size:11px;fill:var(--ink);paint-order:stroke;stroke:white;stroke-width:4;stroke-linejoin:round}.rail{padding:18px;display:flex;flex-direction:column;gap:18px}.rail h2{font-size:15px;margin:0 0 9px}.legend{display:grid;gap:8px;font-size:13px}.swatch{display:inline-block;width:12px;height:12px;border-radius:50%;margin-right:8px;vertical-align:-1px}.detail{min-height:130px;border-top:2px solid var(--ink);padding-top:12px}.detail h3{margin:0 0 7px;font-size:18px}.detail p{margin:4px 0;color:var(--quiet);font-size:12px}.blockers{margin:0;padding-left:18px;color:var(--quiet);font-size:12px;line-height:1.6}.table-panel{margin-top:14px;border:1px solid var(--line);background:white;padding:16px;overflow:auto}.table-panel h2{font-size:16px;margin:0 0 10px}table{width:100%;border-collapse:collapse;font-size:12px}th,td{text-align:left;padding:9px 10px;border-bottom:1px solid #e3eaec}th{position:sticky;top:0;background:white;color:var(--quiet)}.tag{display:inline-block;padding:2px 6px;color:white;font-size:11px}.tag.h{background:var(--hospital)}.tag.a{background:var(--ambulance)}
@media(max-width:900px){.metrics{grid-template-columns:repeat(2,1fr)}.metric{border-bottom:1px solid var(--line)}.stage{grid-template-columns:1fr}.map-wrap{border-right:0;border-bottom:1px solid var(--line)}.rail{display:grid;grid-template-columns:1fr 1fr}.top{align-items:start;flex-direction:column}}
@media(max-width:560px){.app{padding:9px}.metrics{grid-template-columns:1fr 1fr}.stage{min-height:0}#map{height:480px}.rail{display:block}.rail section{margin-bottom:18px}}
.metrics{grid-template-columns:repeat(6,1fr)}.candidate-dot{fill:var(--candidate);stroke:white;stroke-width:2}.tag.c{background:var(--candidate)}
@media(max-width:900px){.metrics{grid-template-columns:repeat(2,1fr)}}
</style></head><body><main class="app">
<header class="top"><div><h1>시·군별 응급의료 자원</h1><p>경기도 전체를 합산하지 않고, 선택한 시·군 안의 실제 병원과 119 자원만 표시합니다.</p></div><div class="picker"><label for="region">분석 시·군</label><select id="region"></select></div></header>
<div class="truth"><b>입력 검증 화면</b><span>현재 지도는 시뮬레이션 결과가 아닙니다. 수요·후보지·도로시간이 완성되기 전에는 최적 입지를 표시하지 않습니다.</span></div>
<section class="metrics"><div class="metric"><span>주민등록인구</span><strong id="population">-</strong></div><div class="metric"><span>응급의료기관</span><strong id="hospitalCount">-</strong></div><div class="metric"><span>119 거점</span><strong id="baseCount">-</strong></div><div class="metric"><span>구급차</span><strong id="ambulanceCount">-</strong></div><div class="metric"><span>읍면동 수요단위</span><strong id="demandCount">-</strong></div></section>
<section class="stage"><div class="map-wrap"><div class="map-head"><b id="mapTitle"></b><span>실제 위·경도 상대 배치 · 도로망 미표시</span></div><svg id="map" viewBox="0 0 1000 610" role="img" aria-label="선택 시군의 병원 및 119 자원 위치"></svg></div><aside class="rail"><section><h2>표시 기준</h2><div class="legend"><span><i class="swatch" style="background:var(--hospital)"></i>기존 응급의료기관</span><span><i class="swatch" style="background:var(--ambulance)"></i>119 구급차 거점</span></div></section><section class="detail" id="detail"><h3>지점 선택</h3><p>지도 위 표식을 누르면 이름·주소·출처를 확인합니다.</p></section><section><h2>계산 전 남은 입력</h2><ul class="blockers" id="blockers"></ul></section></aside></section>
<section class="table-panel"><h2 id="tableTitle">시설 목록</h2><table><thead><tr><th>구분</th><th>시설명</th><th>세부</th><th>주소</th><th>위도</th><th>경도</th></tr></thead><tbody id="facilityRows"></tbody></table></section>
</main><script type="application/json" id="dataset">__DATA__</script><script>
const DATA=JSON.parse(document.getElementById('dataset').textContent),NS='http://www.w3.org/2000/svg';const select=document.getElementById('region'),svg=document.getElementById('map');document.querySelector('.metrics').insertAdjacentHTML('beforeend','<div class="metric"><span>계획검토 후보</span><strong id="candidateCount">-</strong></div>');document.querySelector('.legend').insertAdjacentHTML('beforeend','<span><i class="swatch" style="background:var(--candidate)"></i>공공보건시설 기반 후보</span>');
const blockerNames={'demand-point geocoding':'읍면동 수요지의 검증 좌표','actual directional road travel-time matrix':'방향별 실제 자동차 이동시간','municipal 119 demand-rate calibration':'시·군별 119 발생률 보정','physically feasible new-hospital candidate sites':'건설 가능한 신설 후보지','candidate-site land feasibility review':'후보지 토지·법규 타당성 검토','hospital capacity/capability calibration':'병원별 수용능력·진료역량'};
DATA.forEach((d,i)=>{const o=document.createElement('option');o.value=d.code;o.textContent=`${d.name} (${d.code})`;if(d.name==='수원시')o.selected=true;select.appendChild(o)});function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function node(name,attrs={}){const n=document.createElementNS(NS,name);Object.entries(attrs).forEach(([k,v])=>n.setAttribute(k,v));return n}function showDetail(p,type){const subtitle=type==='hospital'?p.category:type==='base'?`${p.station} · 구급차 ${p.ambulances}대`:'공공보건시설 기반 후보 · 계획 검토 필요';document.getElementById('detail').innerHTML=`<h3>${esc(p.name)}</h3><p>${esc(subtitle)}</p><p>${esc(p.address||'주소 미기재')}</p><p>출처: ${esc(p.source)}</p>`;const d=DATA.find(x=>x.code===select.value)||DATA[0],all=[...d.hospitals,...d.bases,...d.candidates],names=Object.fromEntries(all.map(x=>[x.id,x.name]));const outgoing=d.routes.filter(r=>r.origin===p.id).sort((a,b)=>a.minutes-b.minutes);if(outgoing.length){const items=outgoing.slice(0,8).map(r=>`<li>${esc(names[r.destination]||r.destination)} · ${r.minutes.toFixed(1)}분 · ${(r.distance_meters/1000).toFixed(1)}km</li>`).join('');document.getElementById('detail').insertAdjacentHTML('beforeend',`<p><b>실제 자동차 경로시간 (빠른 순)</b></p><ul class="blockers">${items}</ul>`)}}
function render(){const d=DATA.find(x=>x.code===select.value)||DATA[0];document.getElementById('population').textContent=d.population.toLocaleString();document.getElementById('hospitalCount').textContent=d.hospitals.length;document.getElementById('baseCount').textContent=d.bases.length;document.getElementById('ambulanceCount').textContent=d.ambulances;document.getElementById('demandCount').textContent=d.demand_points;document.getElementById('candidateCount').textContent=d.candidates.length;document.getElementById('mapTitle').textContent=`${d.name} 자원 위치`;document.getElementById('blockers').innerHTML=d.blockers.map(x=>`<li>${esc(blockerNames[x]||x)}</li>`).join('');
 while(svg.firstChild)svg.removeChild(svg.firstChild);const points=[...d.hospitals.map(x=>({...x,type:'hospital'})),...d.bases.map(x=>({...x,type:'base'})),...d.candidates.map(x=>({...x,type:'candidate'}))];if(!points.length){const t=node('text',{x:500,y:305,'text-anchor':'middle',fill:'#61757e'});t.textContent='표시할 좌표 없음';svg.appendChild(t);return}let minX=Math.min(...points.map(p=>p.lon)),maxX=Math.max(...points.map(p=>p.lon)),minY=Math.min(...points.map(p=>p.lat)),maxY=Math.max(...points.map(p=>p.lat));if(maxX-minX<.002){minX-=.001;maxX+=.001}if(maxY-minY<.002){minY-=.001;maxY+=.001}const padX=(maxX-minX)*.08,padY=(maxY-minY)*.08;minX-=padX;maxX+=padX;minY-=padY;maxY+=padY;const project=p=>({x:45+(p.lon-minX)/(maxX-minX)*910,y:570-(p.lat-minY)/(maxY-minY)*525});
 for(let i=1;i<5;i++){svg.appendChild(node('line',{x1:45,x2:955,y1:45+i*105,y2:45+i*105,class:'axis'}));svg.appendChild(node('line',{y1:45,y2:570,x1:45+i*182,x2:45+i*182,class:'axis'}))}points.forEach((p,i)=>{const q=project(p),g=node('g',{class:'marker',tabindex:'0','aria-label':p.name,'data-resource-id':p.id});const radius=p.type==='base'?Math.min(15,6+Math.sqrt(p.ambulances||1)*2):8;const markerClass=p.type==='hospital'?'hospital-dot':p.type==='base'?'base-dot':'candidate-dot';g.appendChild(node('circle',{cx:q.x,cy:q.y,r:radius,class:markerClass}));if(i<35){const label=node('text',{x:q.x+radius+4,y:q.y+4,class:'mark-label'});label.textContent=p.name;g.appendChild(label)}g.addEventListener('click',()=>showDetail(p,p.type));g.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' ')showDetail(p,p.type)});svg.appendChild(g)});
 const table=[...d.hospitals.map(p=>({type:'hospital',p})),...d.bases.map(p=>({type:'base',p})),...d.candidates.map(p=>({type:'candidate',p}))];document.getElementById('facilityRows').innerHTML=table.map(({type,p})=>{const tag=type==='hospital'?'병원':type==='base'?'119':'후보';const cls=type==='hospital'?'h':type==='base'?'a':'c';const detail=type==='hospital'?p.category:type==='base'?`구급차 ${p.ambulances}대`:'계획 검토 필요';return `<tr><td><span class="tag ${cls}">${tag}</span></td><td>${esc(p.name)}</td><td>${esc(detail)}</td><td>${esc(p.address)}</td><td>${p.lat.toFixed(6)}</td><td>${p.lon.toFixed(6)}</td></tr>`}).join('');document.getElementById('tableTitle').textContent=`${d.name} 시설 목록 (${table.length})`;document.getElementById('detail').innerHTML='<h3>지점 선택</h3><p>지도 위 표식을 누르면 이름·주소·출처를 확인합니다.</p>'}
select.addEventListener('change',render);render();
</script></body></html>'''


if __name__ == "__main__":
    raise SystemExit(main())
