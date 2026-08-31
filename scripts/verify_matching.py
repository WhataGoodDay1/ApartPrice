# -*- coding: utf-8 -*-
"""collector 실행 후 매칭 품질을 점검하는 진단 스크립트.

- 매칭 0건인 단지 목록(name_variants가 실제 국토부 등록명과 안 맞는 경우)
- 한 단지 id에 raw_apt_name이 2종류 이상 섞인 경우(같은 이름을 여러 단지가 공유해서
  다른 단지 데이터가 잘못 들어왔을 가능성 - 2026-08-31 크로바아파트 사례 참고)

174개 단지 대규모 확장 때 이 스크립트로 여러 라운드 정리했다. 새 단지를 추가하거나
collector를 재실행한 뒤 다시 돌려서 이상 없는지 확인하는 용도로 남겨둔다.

실행: python scripts/verify_matching.py
"""
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "complexes.json"
DATA_PATH = ROOT / "data" / "trades.csv"


def main() -> None:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        complexes = {c["id"]: c for c in json.load(f)["complexes"]}

    with open(DATA_PATH, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    by_id: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_id[r["complex_id"]].append(r)

    zero = [cid for cid in complexes if cid not in by_id]
    ambiguous_ids = {cid for cid, c in complexes.items() if c.get("_ambiguous")}
    zero_intended = [cid for cid in zero if cid in ambiguous_ids]
    zero_unexpected = [cid for cid in zero if cid not in ambiguous_ids]

    print(f"전체 단지: {len(complexes)}개, 총 거래: {len(rows)}건")
    print(f"매칭 0건: {len(zero)}개 (의도적 보류 {len(zero_intended)} / 원인 불명 {len(zero_unexpected)})")
    if zero_unexpected:
        print("  원인 불명 0건 단지 목록 (name_variants가 실제 등록명과 안 맞을 가능성):")
        for cid in zero_unexpected:
            c = complexes[cid]
            print(f"    {c['name']} ({cid}) variants={c.get('name_variants')}")

    suspicious = []
    for cid, crows in by_id.items():
        names = set(r["raw_apt_name"] for r in crows)
        if len(names) > 1:
            suspicious.append((cid, complexes.get(cid, {}).get("name", cid), names, len(crows)))

    print(f"\nraw_apt_name이 2종류 이상 섞인 단지: {len(suspicious)}개 (직접 판단 필요 - 정상적인 여러 블록/단지 통합인지, 이름 충돌인지)")
    for cid, name, names, n in suspicious:
        print(f"  {name} ({cid}, {n}건): {sorted(names)}")


if __name__ == "__main__":
    main()
