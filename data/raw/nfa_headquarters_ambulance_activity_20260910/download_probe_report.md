# 소방청 본부별 구급활동정보 다운로드 조사

조사일: 2026-09-10 (KST)

## 결론

공식 페이지의 현재 버전(2024-12-31, 데이터 ID `15080046`)은 로그인 없이 다운로드 폼까지는 정상 처리되지만, 현재 첨부파일 `FILE_000000003246004`의 실제 파일 응답이 `200 OK`, `Content-Length: 0`, `Content-Type: text/html;charset=UTF-8`로 반환된다. 따라서 현재 CSV 원문은 공공데이터포털의 현재 첨부파일 경로에서 획득할 수 없었다. 이는 호출 파라미터나 API 키 누락이 아니라, 서버가 해당 첨부파일에 대해 빈 응답을 반환하는 상태다.

## 공식 페이지에서 확인한 호출 순서

출처: <https://www.data.go.kr/data/15080046/fileData.do?recommendDataYn=Y>

페이지의 공식 JavaScript `/js/biz/datset/script_fileDetail.js`가 사용하는 순서는 다음과 같다.

1. `POST https://www.data.go.kr/tcs/dss/selectFileDataDownload.do`
   - `publicDataDetailPk=uddi:6e753ec6-8c8d-45c1-8d7c-f8b14b2bad04`
   - `publicDataPk=15080046`
   - `atchFileId=`
   - `fileDetailSn=1`
   - `publicDataTyCode=PR0051`
   - 응답: `status=true`, `atchFileId=FILE_000000003246004`, `fileDetailSn=1`
2. `POST https://www.data.go.kr/cmm/cmm/check-limit.json`
   - `atchFileId=FILE_000000003246004`
   - `fileDetailSn=1`
   - 응답: `{"needCaptcha":false}`
3. `GET https://www.data.go.kr/cmm/cmm/fileDownload.do?atchFileId=FILE_000000003246004&fileDetailSn=1&dataNm=소방청_본부별%20구급활동정보_20241231`
   - 응답: `200`, 길이 0, `text/html;charset=UTF-8`

페이지의 JSON-LD가 제공하는 `insertDataPrcus=N` 경로와 `dataNm`을 포함한 공식 JavaScript 경로 모두 같은 빈 응답을 반환했다. 사용자 에이전트·Referer·세션 쿠키를 포함한 재현에서도 결과는 동일했다.

## 확인 가능한 과거 원문

주기성 과거 데이터의 공식 상세 팝업에서 첨부파일 ID를 확인했고, 같은 `fileDownload.do` 경로로 원문을 정상 수신했다.

| 버전 | 공식 첨부파일 | 데이터 행 수(페이지 표기) | 파일 크기 | SHA-256 |
|---|---|---:|---:|---|
| 2023-12-31 | `FILE_000000002992996` | 912 | 26,102 bytes | `48d8a83d51cad1989273be0ed94fd3687faa49aded4094c26fa2466a26cdc4bf` |
| 2020-12-31 | `FILE_000000002639589` | 228 | 6,684 bytes | `65e43f8c1e648063c37e7dec27361ceecd6d0e898dd8ef55adf6430e295a4178` |

두 파일은 `data/raw/nfa_headquarters_ambulance_activity_20260910/`에 저장했다. CP949로 읽을 때 각각 헤더 제외 912행, 228행이다.

## 추가 확인

현재 버전의 공식 OAS는 <https://infuser.odcloud.kr/oas/docs?namespace=15080046/v1>에서 노출되며, 현재 데이터 모델이 `col-0` 하나만 제시된다. 과거 버전은 `년도`, `월`, `본부구분`, `출동건수`, `이송건수`, `이송환자수` 6개 필드를 제공한다. 현재 CSV가 복구되더라도 스키마/원문 검증이 필요하다.

## 다음 단계

현재 버전이 연구에 필요하면 소방청/공공데이터포털 운영자에게 `15080046`, `FILE_000000003246004`, `소방청_본부별 구급활동정보_20241231`을 함께 제시하여 첨부파일 복구 또는 재게시를 요청해야 한다. 그 전까지 시·군별 수요 공간화에는 이 파일을 이용하지 않고, 확보된 실제 구급대 위치·인구·별도 출동활동 원천을 우선 사용한다.
