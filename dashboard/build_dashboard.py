"""
data/trades.csv(국토부 실거래가 누적 데이터) -> dashboard/template.html의 마커 구간을 실데이터로
치환해 dashboard/dist.html을 생성한다.

표 구조(2026-08 개편): 매매/전세/월세를 한 표에 통합, 컬럼별 필터, 5년 추이, 전세가율(동일월
매매 대비 전세 평균가 기준) 포함.

사용법: python dashboard/build_dashboard.py
"""
from __future__ import annotations

import csv
import json
import re
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = ROOT / "data" / "trades.csv"
TEMPLATE_PATH = ROOT / "dashboard" / "template.html"
OUT_PATH = ROOT / "dashboard" / "dist.html"
CONFIG_PATH = ROOT / "config" / "complexes.json"

DEAL_TYPES = ["매매", "전세", "월세"]
PYEONG = 3.3058  # 1평 = 3.3058㎡
EXCLUSIVE_RATIO_ASSUMPTION = 0.78  # 전용률 가정치(공급면적 추정용, 국토부 API에 공급면적 없음)
TREND_OUTLIER_THRESHOLD = 0.07  # 추이 그래프: 전월 평균가 대비 이 비율 넘게 급변하면 제외


def load_rows() -> list[dict]:
    with open(DATA_PATH, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def load_complexes() -> dict[str, dict]:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    complexes = cfg["complexes"]
    ids = [c["id"] for c in complexes]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise ValueError(f"config/complexes.json에 중복된 id가 있습니다: {sorted(dupes)}")
    return {c["id"]: c for c in complexes}


def area_bucket(area_str: str) -> str:
    try:
        return f"{round(float(area_str))}㎡"
    except ValueError:
        return area_str


def to_pyeong(area_m2: float) -> float:
    return area_m2 / PYEONG


def estimate_supply_pyeong(area_m2: float) -> int:
    return round(area_m2 / EXCLUSIVE_RATIO_ASSUMPTION / PYEONG)


def build_jeonse_index(rows: list[dict]) -> dict[tuple, list[int]]:
    """(complex_id, area_bucket, deal_ymd) -> 전세 가격 리스트. 전세가율(동일월) 계산용."""
    idx: dict[tuple, list[int]] = defaultdict(list)
    for r in rows:
        if r["deal_type"] == "전세":
            key = (r["complex_id"], area_bucket(r["area_exclusive"]), r["deal_ymd"])
            idx[key].append(int(r["price_or_deposit"]))
    return idx


def build_jeonse_recent_index(rows: list[dict]) -> dict[tuple, list[dict]]:
    """(complex_id, area_bucket) -> 전세 거래를 최신순으로 정렬한 리스트(5년 전체).
    '최근 전세 1건' / '최근 10건 평균' 컬럼 계산용."""
    idx: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        if r["deal_type"] == "전세":
            key = (r["complex_id"], area_bucket(r["area_exclusive"]))
            idx[key].append(r)
    for key in idx:
        idx[key].sort(key=lambda r: r["contract_date"], reverse=True)
    return idx


def filter_trend_outliers(points: list[dict], threshold: float = TREND_OUTLIER_THRESHOLD) -> list[dict]:
    """전월(직전 시점) 평균가 대비 threshold(기본 7%) 넘게 급변하는 포인트는 추이
    그래프에서 제외한다 — 국지적으로 튀는 값(월별 표본이 적어 생기는 순간적 급등락)만
    걸러내고, 여러 달에 걸쳐 이어지는 정상적인 5년치 시세 상승/하락 추세는 그대로 둔다.
    각 포인트는 항상 원본(제외 여부 반영 전) 직전 포인트와 비교하므로, 판단 기준이
    앞선 포인트의 제외 여부에 연쇄적으로 영향받지 않는다. 첫 포인트는 비교 대상이
    없어 항상 유지한다."""
    if len(points) < 2:
        return points
    out = [points[0]]
    for prev, cur in zip(points, points[1:]):
        prev_avg = prev["avg"]
        if not prev_avg or abs(cur["avg"] - prev_avg) / prev_avg <= threshold:
            out.append(cur)
    return out


def build_group_trends(rows: list[dict]) -> dict[tuple, list[dict]]:
    """(complex_id, deal_type, area_bucket) -> 5년 전체 raw 데이터 기준 월별 평균가 추이.
    표에 보이는 행 범위(최근 6개월)와 무관하게 항상 전체 이력으로 계산한다. 최근월 평균가
    대비 크게 튀는 포인트는 filter_trend_outliers()에서 제외한다."""
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        key = (r["complex_id"], r["deal_type"], area_bucket(r["area_exclusive"]))
        groups[key].append(r)

    trends: dict[tuple, list[dict]] = {}
    for key, items in groups.items():
        monthly: dict[str, list[int]] = defaultdict(list)
        for it in items:
            monthly[it["deal_ymd"]].append(int(it["price_or_deposit"]))
        points = [
            {"ym": ym, "avg": round(sum(vals) / len(vals))}
            for ym, vals in sorted(monthly.items())
        ]
        trends[key] = filter_trend_outliers(points)
    return trends


def build_rows(rows: list[dict], complexes: dict[str, dict], recent_months: int = 6) -> list[dict]:
    """표에는 최근 recent_months개월 내 '개별' 거래를 모두 행으로 나열한다(요약 아님).
    추이(trend)와 전세가율은 5년 전체 raw 데이터를 근거로 계산해 각 행에 그대로 붙인다."""
    jeonse_idx = build_jeonse_index(rows)  # 전체 5년 기준
    jeonse_recent_idx = build_jeonse_recent_index(rows)  # 전체 5년 기준
    group_trends = build_group_trends(rows)  # 전체 5년 기준

    cutoff = (datetime.now() - timedelta(days=recent_months * 30)).strftime("%Y-%m-%d")

    out: list[dict] = []
    for r in rows:
        if r["contract_date"] < cutoff:
            continue

        cid = r["complex_id"]
        deal_type = r["deal_type"]
        bucket = area_bucket(r["area_exclusive"])
        area_m2 = float(r["area_exclusive"])

        jeonse_ratio = None
        if deal_type == "매매":
            comps = jeonse_idx.get((cid, bucket, r["deal_ymd"]))
            if comps:
                avg_jeonse = sum(comps) / len(comps)
                sale_price = int(r["price_or_deposit"])
                if sale_price:
                    jeonse_ratio = round(avg_jeonse / sale_price * 100, 1)

        recent_jeonse = jeonse_recent_idx.get((cid, bucket), [])
        jeonse_latest = None
        jeonse_avg10 = None
        if recent_jeonse:
            jeonse_latest = {
                "price": int(recent_jeonse[0]["price_or_deposit"]),
                "date": recent_jeonse[0]["contract_date"],
            }
            top10 = recent_jeonse[:10]
            jeonse_avg10 = {
                "avg": round(sum(int(x["price_or_deposit"]) for x in top10) / len(top10)),
                "n": len(top10),
            }

        complex_ = complexes.get(cid, {})
        entry = {
            "id": cid,
            "name": complex_.get("name", cid),
            "region": complex_.get("region", ""),
            "dealType": deal_type,
            "areaM2": round(area_m2, 2),
            "areaBucket": bucket,
            "pyeongExclusive": round(to_pyeong(area_m2), 1),
            "pyeongSupplyEst": estimate_supply_pyeong(area_m2),
            "date": r["contract_date"],
            "floor": r["floor"],
            "value": int(r["price_or_deposit"]),
            "rent": int(r["monthly_rent"]) if deal_type == "월세" else None,
            "jeonseRatio": jeonse_ratio,
            "jeonseLatest": jeonse_latest,
            "jeonseAvg10": jeonse_avg10,
            "trend": group_trends.get((cid, deal_type, bucket), []),
        }
        out.append(entry)

    out.sort(key=lambda e: e["date"], reverse=True)
    return out


def _area_sort_key(bucket: str) -> float:
    try:
        return float(bucket.rstrip("㎡"))
    except ValueError:
        return 0.0


def build_complex_summaries(rows: list[dict], complexes: dict[str, dict]) -> list[dict]:
    """"단지 정보" 탭용 단지별 요약. 5년 전체 raw 데이터 기준으로 계산한다.

    세대수(households)는 국토부 실거래가 API에 없는 값이라 config/complexes.json에
    아직 없음 — complex_.get("households")가 None이면 프론트에서 "정보 없음"으로 표시.
    같은 이유로 정식 회전율(거래량/세대수)은 계산할 수 없어, 대신 데이터 기간 기준
    "연평균 거래량"을 참고용 근사치로 제공한다.
    """
    by_complex: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_complex[r["complex_id"]].append(r)

    group_trends = build_group_trends(rows)  # 전월 대비 7% 급변 이상치 제외된 월별 평균가 추이

    summaries: list[dict] = []
    for cid, complex_ in complexes.items():
        crows = by_complex.get(cid, [])
        if not crows:
            continue

        area_groups: dict[str, list[dict]] = defaultdict(list)
        for r in crows:
            area_groups[area_bucket(r["area_exclusive"])].append(r)

        area_stats = []
        for bucket in sorted(area_groups, key=_area_sort_key):
            items = area_groups[bucket]
            sale = [r for r in items if r["deal_type"] == "매매"]
            jeonse = [r for r in items if r["deal_type"] == "전세"]
            wolse = [r for r in items if r["deal_type"] == "월세"]
            sale_prices = [int(r["price_or_deposit"]) for r in sale]
            jeonse_prices = [int(r["price_or_deposit"]) for r in jeonse]
            sale_latest_row = max(sale, key=lambda r: r["contract_date"], default=None)
            jeonse_latest_row = max(jeonse, key=lambda r: r["contract_date"], default=None)

            area_stats.append({
                "bucket": bucket,
                "pyeongExclusive": round(_area_sort_key(bucket) / PYEONG, 1),
                "saleAvg": round(sum(sale_prices) / len(sale_prices)) if sale_prices else None,
                "saleMax": max(sale_prices) if sale_prices else None,
                "saleMin": min(sale_prices) if sale_prices else None,
                "saleLatest": {"price": int(sale_latest_row["price_or_deposit"]), "date": sale_latest_row["contract_date"]} if sale_latest_row else None,
                "saleCount": len(sale),
                "jeonseAvg": round(sum(jeonse_prices) / len(jeonse_prices)) if jeonse_prices else None,
                "jeonseLatest": {"price": int(jeonse_latest_row["price_or_deposit"]), "date": jeonse_latest_row["contract_date"]} if jeonse_latest_row else None,
                "jeonseCount": len(jeonse),
                "wolseCount": len(wolse),
                "trendSale": group_trends.get((cid, "매매", bucket), []),  # 5년 매매 평균가 추이(이상치 제외)
                "trendJeonse": group_trends.get((cid, "전세", bucket), []),  # 5년 전세 평균가 추이(이상치 제외)
            })

        sale_rows = [r for r in crows if r["deal_type"] == "매매"]
        sale_max_row = max(sale_rows, key=lambda r: int(r["price_or_deposit"]), default=None)
        sale_min_row = min(sale_rows, key=lambda r: int(r["price_or_deposit"]), default=None)

        dates = [r["contract_date"] for r in crows]
        span_days = (datetime.strptime(max(dates), "%Y-%m-%d") - datetime.strptime(min(dates), "%Y-%m-%d")).days if len(dates) >= 2 else 0
        avg_trades_per_year = round(len(crows) / (span_days / 365), 1) if span_days > 0 else None

        summaries.append({
            "id": cid,
            "name": complex_["name"],
            "region": complex_.get("region", ""),
            "households": complex_.get("households"),  # 미보유 시 None -> 프론트에서 "정보 없음"
            "areaStats": area_stats,
            "saleMax": {"price": int(sale_max_row["price_or_deposit"]), "areaBucket": area_bucket(sale_max_row["area_exclusive"]), "date": sale_max_row["contract_date"]} if sale_max_row else None,
            "saleMin": {"price": int(sale_min_row["price_or_deposit"]), "areaBucket": area_bucket(sale_min_row["area_exclusive"]), "date": sale_min_row["contract_date"]} if sale_min_row else None,
            "totalTrades": len(crows),
            "totalSale": len(sale_rows),
            "totalJeonse": len([r for r in crows if r["deal_type"] == "전세"]),
            "totalWolse": len([r for r in crows if r["deal_type"] == "월세"]),
            "avgTradesPerYear": avg_trades_per_year,
            "dataSpan": f"{min(dates)} ~ {max(dates)}" if dates else "-",
        })

    return summaries


def build_summary_html(rows: list[dict], complexes: dict[str, dict], entries: list[dict]) -> str:
    total = len(rows)
    by_type = defaultdict(int)
    for r in rows:
        by_type[r["deal_type"]] += 1
    type_line = " · ".join(f"{t} {by_type.get(t, 0)}" for t in DEAL_TYPES)

    dates = [r["contract_date"] for r in rows]
    span = f"{min(dates)} ~ {max(dates)}" if dates else "-"

    # 전세가율 계산 가능한 매매 건 평균 (entries는 build()에서 이미 계산된 것을 재사용)
    ratios = [e["jeonseRatio"] for e in entries if e["dealType"] == "매매" and e["jeonseRatio"] is not None]
    avg_ratio = f"{sum(ratios) / len(ratios):.1f}%" if ratios else "산출 불가"

    return f"""
    <div class="stat">
      <div class="label">누적 거래(5년)</div>
      <div class="value">{total}건</div>
      <div class="sub">{type_line}</div>
    </div>
    <div class="stat">
      <div class="label">평균 전세가율</div>
      <div class="value">{avg_ratio}</div>
      <div class="sub">매매·동월 전세 평균 기준, {len(ratios)}건 산출</div>
    </div>
    <div class="stat">
      <div class="label">데이터 기간</div>
      <div class="value" style="font-size:1rem">{span}</div>
      <div class="sub">국토부 실거래가, 신고 지연 최대 30일</div>
    </div>
"""


def build() -> Path:
    rows = load_rows()
    complexes = load_complexes()
    row_entries = build_rows(rows, complexes)

    html = TEMPLATE_PATH.read_text(encoding="utf-8")

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M") + " (로컬 실행 기준)"
    html = re.sub(
        r"<!--UPDATED_AT-->.*?<!--/UPDATED_AT-->",
        f"<!--UPDATED_AT-->{now_str}<!--/UPDATED_AT-->",
        html, flags=re.S,
    )

    banner = (
        '  <div class="banner">✅ <strong>실데이터 연결됨</strong> — 국토부 실거래가 API 기준, '
        '표는 최근 6개월 개별 거래 전부를 나열하고 추이·전세가율은 5년 전체 데이터로 계산합니다.</div>'
    )
    html = re.sub(
        r"<!--BANNER_START-->.*?<!--BANNER_END-->",
        f"<!--BANNER_START-->\n{banner}\n  <!--BANNER_END-->",
        html, flags=re.S,
    )

    summary_html = build_summary_html(rows, complexes, row_entries)
    html = re.sub(
        r'(<section class="summary" aria-label="요약">).*?(</section>)',
        lambda m: m.group(1) + summary_html + m.group(2),
        html, flags=re.S,
    )

    rows_js = "const ROWS = " + json.dumps(row_entries, ensure_ascii=False, indent=2) + ";"
    html = re.sub(
        r"/\*DATA_START\*/.*?/\*DATA_END\*/",
        f"/*DATA_START*/\n  {rows_js}\n  /*DATA_END*/",
        html, flags=re.S,
    )

    complex_summaries = build_complex_summaries(rows, complexes)
    complex_js = "const COMPLEX_SUMMARIES = " + json.dumps(complex_summaries, ensure_ascii=False, indent=2) + ";"
    html = re.sub(
        r"/\*COMPLEX_DATA_START\*/.*?/\*COMPLEX_DATA_END\*/",
        f"/*COMPLEX_DATA_START*/\n  {complex_js}\n  /*COMPLEX_DATA_END*/",
        html, flags=re.S,
    )

    OUT_PATH.write_text(html, encoding="utf-8")
    return OUT_PATH


if __name__ == "__main__":
    out = build()
    print(f"생성 완료: {out}")
