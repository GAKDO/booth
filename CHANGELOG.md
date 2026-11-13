# 변경 기록

이 레포의 버전별 변경을 적는다. 형식은 [Keep a Changelog](https://keepachangelog.com/ko/1.1.0/)를 따른다.
기능 PR은 `## [Unreleased]` 아래에 한 줄을 더하고, 릴리스 PR에서 `## [x.y.z] - 날짜`로 바꾼다(CONTRIBUTING.md 3번).
main에 버전이 적힌 CHANGELOG가 올라가면 release.yml이 태그와 GitHub Release를 만든다.

## [Unreleased]

### 추가
- 수령·반납 검사 방식 고정(11/13): 수령·반납 기기는 출고 때 방식(capture-server `GET /api/v1/captures/rentals/{rentalId}/profile`)만 고를 수 있고, 다른 방식 버튼은 꺼지며 이유를 보임. 로컬 API `POST /api/sessions`도 다른 방식이면 400. 서버가 옛 버전이면 막지 않음(서버 세트 생성이 다시 막음)
- BO1 조명 손 전환 줄이기: 전체 검사 옆면을 면마다 L1→L2 / L2→L1로 번갈아 찍어 조명 전환 17번 → 13번(VL49 손 스위치 13번 → 9번), 앞 사진과 같은 조명이면 "그대로"와 켜져 있어야 할 조명을 함께 안내, 완료 확인은 단계 번호와 면·조명을 함께 대조
- BO2 전체 검사(FULL): 검사 모드에서 범위를 골라 28장(BOT·TOP·펼친 KB·옆면, SCR1 선택)을 찍고 전체 사진을 탐지해 `profile: FULL` 세트로 올림. 기본은 빠른 검사 그대로(`TRACE_BOOTH_INSPECTION`)
- BO2 Jetson 탐지: torch 없이 TensorRT 엔진 추론(`TensorRTDetector`, 마스크 → 폴리곤), `build-engine`(ONNX → FP16), `bench`(탐지 시간), 빠른 검사용 `detect_photos`·`Timeline`
- 블로그(AIGJ-01-002-blog/devlog) CI/CD를 옮겨 초기 구성
