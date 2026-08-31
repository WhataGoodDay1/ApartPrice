# 대전 아파트 실거래가 추적 프로젝트

국토교통부 실거래가 공공 API로 대전 특정 아파트 단지들의 매매/전세/월세 시세를 수집해
누적하고, 정적 HTML 대시보드로 시각화하는 개인용 프로젝트.

## 진행 상황 / 다음 할 일

**세션 시작 시 먼저 [ApartPriceProgress.md](ApartPriceProgress.md)를 읽고, 거기 적힌
"다음에 할 일"부터 이어서 진행할 것.** 작업이 끝나면 ApartPriceProgress.md에 그날 날짜로
새 항목을 추가해 기록한다.

## 구조

- `config/complexes.json` — 추적 대상 단지 목록. `lawd_cd`(법정동코드), `name_variants`(API
  응답 매칭용 별칭)를 관리.
- `collector/molit_api_client.py` — 국토부 API(`RTMSDataSvcAptTrade`, `RTMSDataSvcAptRent`)를
  호출해 대상 단지와 매칭되는 거래만 정규화, `data/trades.csv`에 upsert.
  실행: `set MOLIT_API_KEY=...` 후 `python collector/molit_api_client.py --months 3`
- `data/trades.csv` — 누적 원본 데이터(현재 5년치, 2500여 건). 이 저장소의 "진실 소스".
- `dashboard/build_dashboard.py` — `trades.csv` + `template.html` → `dist.html` 생성.
  실행: `python dashboard/build_dashboard.py`
- `dashboard/template.html` — 대시보드 뼈대(마커 주석 구간을 빌드 스크립트가 치환).
- `dashboard/dist.html` — 빌드 결과물(실제로 열어보는 최종 산출물).

## 알아둘 것

- `config/complexes.json`의 `_note`: lawd_cd는 실호출로 검증 완료(유성구=30200, 서구=30170).
- 전세가율 등은 5년 전체 데이터 기준으로 계산하고, 대시보드 표는 최근 6개월 개별 거래만
  나열함 (`build_dashboard.py`의 `recent_months` 파라미터).
- 추이(5년) 그래프는 전월 대비 7% 넘게 급변하는 포인트를 이상치로 제외함
  (`build_dashboard.py`의 `filter_trend_outliers`, `TREND_OUTLIER_THRESHOLD`).

## 브랜치 / PR 워크플로 (2026-08-31부터 적용)

**main에 직접 커밋하지 않는다.** 변경 작업은 다음 순서로 진행:

1. `git checkout main && git pull` 후 `git checkout -b <종류>/<짧은-설명>`
   (예: `feat/price-filter`, `fix/trend-outlier`)
2. 변경 작업. 커밋 전에 반드시 로컬 테스트:
   - 수집기를 건드렸으면 `python collector/molit_api_client.py --months 1` 등으로 실행 확인
     (또는 최소한 `python -c "import collector.molit_api_client"`로 문법/임포트 확인)
   - 대시보드를 건드렸으면 `python dashboard/build_dashboard.py`로 재빌드 후
     `dashboard/dist.html`을 브라우저로 열어 실제로 확인
3. 커밋 후 `git push -u origin <브랜치명>`
4. GitHub에서 PR 생성 후 머지:
   - `gh` CLI가 있으면 `gh pr create --fill` → 확인 후 `gh pr merge --squash`
   - 없으면 `https://github.com/WhataGoodDay1/ApartPrice/compare/main...<브랜치명>?expand=1`
     주소로 접속해 PR 생성 → GitHub 웹에서 머지
5. 머지 후 로컬: `git checkout main && git pull && git branch -d <브랜치명>`

이 프로젝트엔 자동화된 테스트/CI가 없으므로 "테스트"는 위 2단계의 수동 실행 확인을 뜻함.
