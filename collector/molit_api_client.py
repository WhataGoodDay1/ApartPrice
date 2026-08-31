"""
국토교통부 공공데이터포털 아파트 실거래가 API 수집기.

- 매매: RTMSDataSvcAptTrade (getRTMSDataSvcAptTrade)
- 전월세: RTMSDataSvcAptRent (getRTMSDataSvcAptRent)

두 API 모두 파라미터: LAWD_CD(법정동코드 5자리, 시군구 단위), DEAL_YMD(계약년월 YYYYMM),
serviceKey, numOfRows, pageNo. 응답은 기본 XML.

사용법:
    set MOLIT_API_KEY=발급받은_서비스키   (PowerShell: $env:MOLIT_API_KEY="...")
    python collector/molit_api_client.py --months 3

data/trades.csv 에 대상 단지(config/complexes.json)와 이름이 매칭되는 거래만
정규화하여 upsert(중복 제거 후 갱신)한다.

실제 서비스키로 호출 테스트 완료(2026-08). data/trades.csv에 source=molit_trade/
molit_rent로 수집된 실데이터가 누적되어 있다.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, asdict
from datetime import date
from pathlib import Path
from urllib.parse import urlencode, unquote
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError
import json

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "complexes.json"
DATA_PATH = ROOT / "data" / "trades.csv"

TRADE_URL = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTrade/getRTMSDataSvcAptTrade"
RENT_URL = "https://apis.data.go.kr/1613000/RTMSDataSvcAptRent/getRTMSDataSvcAptRent"

CSV_FIELDS = [
    "complex_id",
    "complex_name",
    "deal_type",       # 매매 / 전세 / 월세
    "area_exclusive",  # 전용면적(㎡)
    "floor",
    "price_or_deposit",  # 매매가 또는 보증금 (만원)
    "monthly_rent",      # 월세(만원), 매매/전세는 0
    "contract_date",     # YYYY-MM-DD (계약일)
    "deal_ymd",           # YYYYMM (조회 기준월, upsert 키 보조)
    "collected_at",       # 수집 시각 ISO
    "source",
    "raw_apt_name",       # API가 내려준 원본 아파트명(디버깅/재매칭용)
]


@dataclass
class TradeRow:
    complex_id: str
    complex_name: str
    deal_type: str
    area_exclusive: str
    floor: str
    price_or_deposit: str
    monthly_rent: str
    contract_date: str
    deal_ymd: str
    collected_at: str
    source: str
    raw_apt_name: str


def load_complexes() -> list[dict]:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    complexes = cfg["complexes"]
    ids = [c["id"] for c in complexes]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise ValueError(f"config/complexes.json에 중복된 id가 있습니다: {sorted(dupes)}")
    return complexes


def match_complex(raw_name: str, complexes: list[dict]) -> dict | None:
    """API가 내려준 아파트명이 config의 name_variants 중 하나를 포함하면 매칭.

    주의: 표시용 name(예: '예미지', '천년나무')은 여러 동네에 흩어진 서로 다른 단지가
    공유하는 브랜드명인 경우가 많아 매칭 후보에서 제외한다. name_variants에는 반드시
    실제 등기 단지명(예: '죽동금성백조예미지')처럼 해당 단지에만 고유한 문자열을 넣을 것."""
    if not raw_name:
        return None
    cleaned = raw_name.replace(" ", "")
    for c in complexes:
        candidates = c.get("name_variants", [])
        if not candidates:
            raise ValueError(f"{c['name']}(id={c['id']})에 name_variants가 비어 있습니다. "
                              "브랜드명만으로는 다른 단지와 혼동될 수 있어 고유 단지명을 반드시 지정해야 합니다.")
        for cand in candidates:
            if cand.replace(" ", "") in cleaned:
                return c
    return None


def _http_get(url: str, params: dict) -> bytes:
    qs = urlencode(params, safe=":+")
    full_url = f"{url}?{qs}"
    req = Request(full_url, headers={"User-Agent": "daejeon-apt-tracker/1.0"})
    try:
        with urlopen(req, timeout=20) as resp:
            return resp.read()
    except HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} calling {url}: {e.read()[:500]}") from e
    except URLError as e:
        raise RuntimeError(f"연결 실패 {url}: {e}") from e


def _parse_items(xml_bytes: bytes) -> list[dict]:
    """공공데이터포털 표준 응답(XML)에서 <item> 목록을 dict 리스트로 변환.
    에러 응답(resultCode != 000)이면 예외를 던진다."""
    root = ET.fromstring(xml_bytes)

    result_code = root.findtext(".//resultCode")
    if result_code is not None and result_code not in ("000", "00"):
        result_msg = root.findtext(".//resultMsg")
        raise RuntimeError(f"API 오류 resultCode={result_code} msg={result_msg}")

    items = []
    for item in root.findall(".//item"):
        row = {child.tag: (child.text or "").strip() for child in item}
        items.append(row)
    return items


def fetch_trades(lawd_cd: str, deal_ymd: str, api_key: str) -> list[dict]:
    params = {
        "serviceKey": api_key,
        "LAWD_CD": lawd_cd,
        "DEAL_YMD": deal_ymd,
        "numOfRows": "1000",
        "pageNo": "1",
    }
    body = _http_get(TRADE_URL, params)
    return _parse_items(body)


def fetch_rents(lawd_cd: str, deal_ymd: str, api_key: str) -> list[dict]:
    params = {
        "serviceKey": api_key,
        "LAWD_CD": lawd_cd,
        "DEAL_YMD": deal_ymd,
        "numOfRows": "1000",
        "pageNo": "1",
    }
    body = _http_get(RENT_URL, params)
    return _parse_items(body)


def _to_int(s: str) -> int:
    return int(s.replace(",", "").strip()) if s and s.strip() else 0


def normalize_trade_item(item: dict, complex_: dict, collected_at: str, deal_ymd: str) -> TradeRow:
    yy = item.get("dealYear", "")
    mm = item.get("dealMonth", "").zfill(2)
    dd = item.get("dealDay", "").zfill(2)
    return TradeRow(
        complex_id=complex_["id"],
        complex_name=complex_["name"],
        deal_type="매매",
        area_exclusive=item.get("excluUseAr", ""),
        floor=item.get("floor", ""),
        price_or_deposit=str(_to_int(item.get("dealAmount", "0"))),
        monthly_rent="0",
        contract_date=f"{yy}-{mm}-{dd}",
        deal_ymd=deal_ymd,
        collected_at=collected_at,
        source="molit_trade",
        raw_apt_name=item.get("aptNm", ""),
    )


def normalize_rent_item(item: dict, complex_: dict, collected_at: str, deal_ymd: str) -> TradeRow:
    yy = item.get("dealYear", "")
    mm = item.get("dealMonth", "").zfill(2)
    dd = item.get("dealDay", "").zfill(2)
    monthly_rent = _to_int(item.get("monthlyRent", "0"))
    deal_type = "월세" if monthly_rent > 0 else "전세"
    return TradeRow(
        complex_id=complex_["id"],
        complex_name=complex_["name"],
        deal_type=deal_type,
        area_exclusive=item.get("excluUseAr", ""),
        floor=item.get("floor", ""),
        price_or_deposit=str(_to_int(item.get("deposit", "0"))),
        monthly_rent=str(monthly_rent),
        contract_date=f"{yy}-{mm}-{dd}",
        deal_ymd=deal_ymd,
        collected_at=collected_at,
        source="molit_rent",
        raw_apt_name=item.get("aptNm", ""),
    )


def recent_year_months(n: int) -> list[str]:
    """오늘로부터 최근 n개월(YYYYMM), 신고 지연 소급 반영용."""
    out = []
    today = date.today()
    y, m = today.year, today.month
    for _ in range(n):
        out.append(f"{y}{m:02d}")
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return out


def upsert_csv(rows: list[TradeRow], path: Path = DATA_PATH) -> tuple[int, int]:
    """기존 CSV를 읽어 (complex_id, deal_type, area_exclusive, floor, contract_date,
    price_or_deposit, monthly_rent)를 키로 중복 제거 후 새 행을 추가한다.
    return: (기존 건수, 추가된 신규 건수)"""
    existing: dict[tuple, dict] = {}
    if path.exists():
        with open(path, encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                key = (
                    row["complex_id"], row["deal_type"], row["area_exclusive"],
                    row["floor"], row["contract_date"], row["price_or_deposit"],
                    row["monthly_rent"],
                )
                existing[key] = row

    before = len(existing)
    for r in rows:
        key = (
            r.complex_id, r.deal_type, r.area_exclusive, r.floor,
            r.contract_date, r.price_or_deposit, r.monthly_rent,
        )
        existing[key] = asdict(r)  # 신규/갱신 모두 최신 수집값으로 덮어씀

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in sorted(existing.values(), key=lambda r: (r["complex_id"], r["contract_date"])):
            writer.writerow(row)

    return before, len(existing) - before


def run(months: int, api_key: str) -> None:
    complexes = load_complexes()
    missing_codes = [c["name"] for c in complexes if not c.get("lawd_cd")]
    if missing_codes:
        print(
            f"[경고] lawd_cd(법정동코드)가 비어있는 단지: {', '.join(missing_codes)} "
            "-> config/complexes.json 에 채워야 정상 수집됩니다.",
            file=sys.stderr,
        )

    lawd_codes = sorted({c["lawd_cd"] for c in complexes if c.get("lawd_cd")})
    collected_at = date.today().isoformat()
    all_rows: list[TradeRow] = []

    for lawd_cd in lawd_codes:
        for ymd in recent_year_months(months):
            try:
                trade_items = fetch_trades(lawd_cd, ymd, api_key)
            except RuntimeError as e:
                print(f"[매매 API 오류] lawd_cd={lawd_cd} ymd={ymd}: {e}", file=sys.stderr)
                trade_items = []
            for item in trade_items:
                c = match_complex(item.get("aptNm", ""), complexes)
                if c:
                    all_rows.append(normalize_trade_item(item, c, collected_at, ymd))

            try:
                rent_items = fetch_rents(lawd_cd, ymd, api_key)
            except RuntimeError as e:
                print(f"[전월세 API 오류] lawd_cd={lawd_cd} ymd={ymd}: {e}", file=sys.stderr)
                rent_items = []
            for item in rent_items:
                c = match_complex(item.get("aptNm", ""), complexes)
                if c:
                    all_rows.append(normalize_rent_item(item, c, collected_at, ymd))

    before, added = upsert_csv(all_rows)
    print(f"수집 완료: 매칭된 거래 {len(all_rows)}건 처리, 기존 {before}건 -> 신규/갱신 {added}건 추가, 총 {before + added}건")


def main() -> None:
    parser = argparse.ArgumentParser(description="국토부 아파트 실거래가 수집기")
    parser.add_argument("--months", type=int, default=3, help="최근 N개월 소급 조회(기본 3)")
    parser.add_argument("--api-key", default=os.environ.get("MOLIT_API_KEY"), help="공공데이터포털 서비스키")
    args = parser.parse_args()

    if not args.api_key:
        print("MOLIT_API_KEY 환경변수 또는 --api-key 가 필요합니다.", file=sys.stderr)
        sys.exit(1)

    # data.go.kr이 발급하는 "Encoding" 키는 이미 %2F, %3D 등으로 percent-encode된 상태다.
    # urlencode()가 한 번 더 인코딩하면 이중 인코딩되어 인증 실패하므로, 여기서 미리 풀어둔다.
    api_key = unquote(args.api_key)

    run(args.months, api_key)


if __name__ == "__main__":
    main()
