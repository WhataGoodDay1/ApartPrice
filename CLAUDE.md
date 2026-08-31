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

- `molit_api_client.py` 상단 docstring에는 "아직 실제 서비스키로 테스트 안 함"이라고
  적혀 있지만, `data/trades.csv`에 `source=molit_trade/molit_rent`로 실제 수집된
  데이터가 있는 것으로 보아 이미 최소 한 번은 성공 호출됨. docstring이 오래된 상태이니
  다음에 손댈 때 정리할 것.
- `config/complexes.json`의 `_note`: lawd_cd는 실호출로 검증 완료(유성구=30200, 서구=30170).
- 전세가율 등은 5년 전체 데이터 기준으로 계산하고, 대시보드 표는 최근 6개월 개별 거래만
  나열함 (`build_dashboard.py`의 `recent_months` 파라미터).
