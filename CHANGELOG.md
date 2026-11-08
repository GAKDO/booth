# 변경 기록

이 레포의 버전별 변경을 적는다. 형식은 [Keep a Changelog](https://keepachangelog.com/ko/1.1.0/)를 따른다.
기능 PR은 `## [Unreleased]` 아래에 한 줄을 더하고, 릴리스 PR에서 `## [x.y.z] - 날짜`로 바꾼다(CONTRIBUTING.md 3번).
main에 버전이 적힌 CHANGELOG가 올라가면 release.yml이 태그와 GitHub Release를 만든다.

## [Unreleased]

### 추가
- BO2 전체 검사(FULL): 검사 모드에서 범위를 골라 28장(BOT·TOP·펼친 KB·옆면, SCR1 선택)을 찍고 전체 사진을 탐지해 `profile: FULL` 세트로 올림. 기본은 빠른 검사 그대로(`TRACE_BOOTH_INSPECTION`)
- BO2 Jetson 탐지: torch 없이 TensorRT 엔진 추론(`TensorRTDetector`, 마스크 → 폴리곤), `build-engine`(ONNX → FP16), `bench`(탐지 시간), 빠른 검사용 `detect_photos`·`Timeline`
- 블로그(AIGJ-01-002-blog/devlog) CI/CD를 옮겨 초기 구성
