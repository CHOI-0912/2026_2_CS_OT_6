"""Report figures for the Suwon hospital-placement result (k=1)."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BLUE, ORANGE, INK, MUTED, GRID = "#2a78d6", "#eb6834", "#0b0b0b", "#52514e", "#e6e5e1"


def korean_font() -> None:
    for name in ("Malgun Gothic", "NanumGothic", "AppleGothic"):
        if any(f.name == name for f in font_manager.fontManager.ttflist):
            plt.rcParams["font.family"] = name
            break
    plt.rcParams["axes.unicode_minus"] = False


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def short_name(name: str) -> str:
    # "경기도 수원시 권선구 대황교동 격자 0-5" -> "권선구 대황교동 0-5"
    parts = name.replace("경기도 수원시 ", "").replace(" 격자", "").split()
    return " ".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", type=Path, default=PROJECT_ROOT / "outputs/suwon_grid_k1/hospital_placement_result.json")
    parser.add_argument("--processed", type=Path, default=PROJECT_ROOT / "data/processed/gyeonggi_20260909/41110")
    parser.add_argument("--out", type=Path, default=PROJECT_ROOT / "outputs/figures")
    args = parser.parse_args()
    korean_font()
    args.out.mkdir(parents=True, exist_ok=True)
    result = json.loads(args.result.read_text(encoding="utf-8"))
    baseline = result["baseline_existing_only"]
    rows = result["alternatives"]
    episodes = result["episodes"]
    candidates = {r["candidate_id"]: r for r in read_rows(args.processed / "candidate_sites.csv")}
    hospitals = read_rows(args.processed / "existing_hospitals.csv")
    demand = read_rows(args.processed / "demand_population.csv")
    bases = read_rows(args.processed / "ambulance_bases.csv")
    source_of = {c["id"]: c["source_id"] for c in json.loads((PROJECT_ROOT / "data/gyeonggi_real/41110_suwon/placement.json").read_text(encoding="utf-8"))["candidate_hospitals"]}

    # ---- Figure 1: ranked incremental survivors with 95% CI --------------------------
    labels = [short_name(r["candidate_names"][0]) for r in rows]
    means = [r["incremental_expected_saved"]["mean"] for r in rows]
    lows = [r["incremental_expected_saved"]["ci95_low"] for r in rows]
    highs = [r["incremental_expected_saved"]["ci95_high"] for r in rows]
    fig, ax = plt.subplots(figsize=(8.5, 9), dpi=200)
    y = range(len(rows))[::-1]
    colors = [ORANGE if i == 0 else BLUE for i in range(len(rows))]
    ax.barh(list(y), means, color=colors, height=0.62)
    ax.errorbar(means, list(y), xerr=[[m - l for m, l in zip(means, lows)], [h - m for m, h in zip(means, highs)]],
                fmt="none", ecolor=INK, elinewidth=0.9, capsize=2.5)
    ax.set_yticks(list(y)); ax.set_yticklabels(labels, fontsize=8.5)
    ax.axvline(0, color=MUTED, linewidth=0.8)
    ax.set_xlabel("기존 병원 7곳 대비 하루 기대 생존자 증가 (명)", fontsize=10)
    ax.set_title(f"후보 31곳의 병원 신설 효과 순위 (수원시, {episodes}회 시뮬레이션, 95% 신뢰구간)", fontsize=11, loc="left")
    ax.grid(axis="x", color=GRID, linewidth=0.6); ax.set_axisbelow(True)
    for spine in ("top", "right"): ax.spines[spine].set_visible(False)
    span = max(highs + [0]) - min(lows + [0])
    ax.set_xlim(min(min(lows), 0) - 0.04 * span, max(highs) + 0.16 * span)
    for i, (m, h) in enumerate(zip(means, highs)):
        if i < 3:
            ax.text(h + 0.01, list(y)[i], f"+{m:.2f}", va="center", fontsize=8.5, color=INK)
    fig.text(0.01, 0.005, f"기준안(기존 병원만) 하루 기대 생존자 {baseline['mean']:.1f}명. 주황 = 1위 후보.", fontsize=8, color=MUTED)
    fig.tight_layout(rect=(0, 0.02, 1, 1))
    fig.savefig(args.out / "fig1_candidate_ranking.png"); plt.close(fig)

    # ---- Figure 2: map ---------------------------------------------------------------
    fig, ax = plt.subplots(figsize=(8.5, 7.0), dpi=200, layout="constrained")
    pops = [int(d["population"]) for d in demand]
    ax.scatter([float(d["longitude"]) for d in demand], [float(d["latitude"]) for d in demand],
               s=[p / 400 for p in pops], color="#c3c2b7", alpha=0.7, label="행정동 수요지 (원 크기 = 인구)", zorder=1)
    ax.scatter([float(b["longitude"]) for b in bases], [float(b["latitude"]) for b in bases],
               marker="s", s=28, color=INK, label="119안전센터", zorder=3)
    ax.scatter([float(h["longitude"]) for h in hospitals], [float(h["latitude"]) for h in hospitals],
               marker="P", s=110, color="#008300", label="기존 응급의료기관 (7곳)", zorder=4)
    gain = {source_of[r["candidate_ids"][0]]: r["incremental_expected_saved"]["mean"] for r in rows}
    xs = [float(candidates[k]["longitude"]) for k in gain]; ys = [float(candidates[k]["latitude"]) for k in gain]
    sc = ax.scatter(xs, ys, c=list(gain.values()), cmap="Blues", s=95, edgecolor=INK, linewidth=0.5, label="후보 격자 칸 (색 = 증가분)", zorder=5)
    best_key = rows[0]["candidate_ids"][0]
    bx, by = float(candidates[source_of[best_key]]["longitude"]), float(candidates[source_of[best_key]]["latitude"])
    ax.scatter([bx], [by], s=260, facecolor="none", edgecolor=ORANGE, linewidth=2.2, zorder=6)
    ax.annotate(f"1위: {short_name(rows[0]['candidate_names'][0])}\n+{rows[0]['incremental_expected_saved']['mean']:.2f}명/일",
                (bx, by), xytext=(-16 if bx > (min(xs) + max(xs)) / 2 else 16, 14),
                textcoords="offset points", fontsize=9, color=ORANGE,
                ha="right" if bx > (min(xs) + max(xs)) / 2 else "left",
                arrowprops=dict(arrowstyle="-", color=ORANGE, lw=1))
    cb = fig.colorbar(sc, ax=ax, shrink=0.72, pad=0.02); cb.set_label("하루 기대 생존자 증가 (명)", fontsize=9)
    cb.ax.tick_params(labelsize=8)
    ax.set_xlabel("경도"); ax.set_ylabel("위도")
    ax.set_title(f"수원시 병원 신설 후보 {len(rows)}곳과 기대 생존자 증가", fontsize=11, loc="left")
    ax.margins(0.06)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.10), ncol=4, fontsize=8,
              frameon=False, handletextpad=0.3, columnspacing=1.2)
    ax.set_aspect(1 / 0.8)
    ax.grid(color=GRID, linewidth=0.5); ax.set_axisbelow(True)
    for spine in ("top", "right"): ax.spines[spine].set_visible(False)
    fig.savefig(args.out / "fig2_candidate_map.png", bbox_inches="tight"); plt.close(fig)

    # ---- Figure 3: paired episodes, baseline vs best ---------------------------------
    best = rows[0]
    ep = best["episodes"]
    fig, ax = plt.subplots(figsize=(8.5, 4.2), dpi=200)
    x = [e["episode"] + 1 for e in ep]
    ax.plot(x, [e["baseline"] for e in ep], color=MUTED, linewidth=2, marker="o", markersize=4, label="기준안 (기존 병원 7곳)")
    ax.plot(x, [e["expected_saved"] for e in ep], color=ORANGE, linewidth=2, marker="o", markersize=4, label=f"1위 후보 신설 ({short_name(best['candidate_names'][0])})")
    ax.set_xlabel("시뮬레이션 회차 (같은 난수로 짝비교)"); ax.set_ylabel("하루 기대 생존자 (명)")
    ax.set_title(f"회차별 기대 생존자: 기준안 vs 1위 후보 (평균 증가 +{best['incremental_expected_saved']['mean']:.2f}명)", fontsize=11, loc="left")
    lo = min(min(e["baseline"] for e in ep), min(e["expected_saved"] for e in ep))
    hi = max(max(e["baseline"] for e in ep), max(e["expected_saved"] for e in ep))
    ax.set_ylim(lo - 0.08 * (hi - lo), hi + 0.34 * (hi - lo))
    ax.legend(loc="upper right", fontsize=8.5, frameon=False, ncol=2)
    ax.grid(color=GRID, linewidth=0.5); ax.set_axisbelow(True)
    for spine in ("top", "right"): ax.spines[spine].set_visible(False)
    fig.tight_layout(); fig.savefig(args.out / "fig3_paired_episodes.png"); plt.close(fig)

    # ---- Table 1: top 10 --------------------------------------------------------------
    fig, ax = plt.subplots(figsize=(9.2, 3.9), dpi=200); ax.axis("off")
    header = ["순위", "후보 위치 (구 · 법정동 · 격자)", "증가분 (명/일)", "95% 신뢰구간", "칸 추정 인구", "가장 가까운 기존 시설 (km)"]
    cells = []
    for i, r in enumerate(rows[:10], start=1):
        c = candidates[source_of[r["candidate_ids"][0]]]; inc = r["incremental_expected_saved"]
        cells.append([str(i), short_name(r["candidate_names"][0]), f"+{inc['mean']:.3f}", f"{inc['ci95_low']:.3f} ~ {inc['ci95_high']:.3f}",
                      f"{float(c['cell_population_estimate']):,.0f}", c["nearest_facility_km"]])
    table = ax.table(cellText=cells, colLabels=header, loc="center", cellLoc="center",
                     colWidths=[0.06, 0.25, 0.14, 0.19, 0.14, 0.22])
    table.auto_set_font_size(False); table.set_fontsize(8.5); table.scale(1, 1.45)
    for (row, col), cell in table.get_celld().items():
        cell.set_edgecolor(GRID)
        if row == 0: cell.set_facecolor("#f0efeb"); cell.set_text_props(weight="bold")
        if row == 1: cell.set_facecolor("#fdeee6")
    ax.set_title(f"표 1. 병원 신설 후보 상위 10곳 (수원시, {episodes}회 시뮬레이션, 기준안 {baseline['mean']:.1f}명/일)", fontsize=10.5, loc="left")
    fig.tight_layout(); fig.savefig(args.out / "table1_top10.png"); plt.close(fig)

    # ---- Table 2: inputs summary -------------------------------------------------------
    fig, ax = plt.subplots(figsize=(9.2, 2.7), dpi=200); ax.axis("off")
    total_pop = sum(pops); beds = sum(int(h["emergency_room_beds"]) for h in hospitals); amb = sum(int(b["ambulance_count"]) for b in bases)
    cells = [["행정동 수요지", f"{len(demand)}곳 (인구 {total_pop:,}명)", "행정안전부 주민등록 인구"],
             ["119안전센터 / 구급차", f"{len(bases)}곳 / {amb}대", "소방청 구급차 정보"],
             ["기존 응급의료기관", f"{len(hospitals)}곳 (응급실 병상 {beds})", "경기도 응급의료기관 현황, 심평원"],
             ["하루 119 출동", "210.4건 (2025년 76,784건)", "경기데이터드림 구급활동 현황"],
             ["병원 신설 후보", f"{len(rows)}곳 (1km 격자)", "직접 생성"],
             ["도로 이동시간", "5,652 방향쌍", "카카오모빌리티 길찾기"],
             ["시뮬레이션", f"{episodes}회 × (기준안 + 후보 {len(rows)}곳), 하루 + 냉각 6시간", "직접 제작 SMDP 시뮬레이터"]]
    table = ax.table(cellText=cells, colLabels=["항목", "값", "출처"], loc="center", cellLoc="left",
                     colWidths=[0.20, 0.45, 0.35])
    table.auto_set_font_size(False); table.set_fontsize(8.5); table.scale(1, 1.4)
    for (row, col), cell in table.get_celld().items():
        cell.set_edgecolor(GRID)
        if row == 0: cell.set_facecolor("#f0efeb"); cell.set_text_props(weight="bold")
    ax.set_title("표 2. 시뮬레이션 입력 요약 (수원시)", fontsize=10.5, loc="left")
    fig.tight_layout(); fig.savefig(args.out / "table2_inputs.png"); plt.close(fig)
    for name in ("fig1_candidate_ranking.png", "fig2_candidate_map.png", "fig3_paired_episodes.png", "table1_top10.png", "table2_inputs.png"):
        print(args.out / name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
