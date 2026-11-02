# 작업: BO2 Jetson 흠집 탐지

**PR 나누기**: PR 1 = 타일·중복 제거·탐지기·세트 탐지(T001~T007), PR 2 = 결과 올리기(계약 확인·CA1 뒤), Jetson 실측은 따로

- [x] T001 `codes.py`: `DEFECT_CLASSES`(inspection.defect_class)
- [x] T002 [P] `detect/tiling.py`: 겹치는 타일 좌표, 폴리곤 원본 좌표로
- [x] T003 [P] `detect/merge.py`: 클래스별 상자 IoU 중복 제거
- [x] T004 `detect/engine.py`: `Detector` 프로토콜, `ModelInfo.load`(클래스·modelVersion 검사), `UltralyticsDetector`
- [x] T005 `detect/runner.py`: 세트 면 사진 탐지(SLATE 제외), 시간 기록
- [x] T006 [P] `tests/test_detect.py`: 스토리 1 수용 시나리오
- [x] T007 `pyproject.toml`: 선택 의존성 `detect`, dev에 numpy
- [x] T010 `trace-booth detect --session <publicId> [--engine]`: 세션 면 사진 탐지 → `{데이터}/{기기}/detections/{세션}_{modelVersion}.json`(원본 px), 30초 넘으면 경고·종료 코드 2
- [x] T011 `detect/merge.py`: 타일 끝에 조금 걸린 조각도 같은 흠집으로(작은 상자 기준 겹침)
- [ ] T008 Jetson: 엔진 빌드, 한 세트 30초 실측(11/6) — 절차는 quickstart.md
- [x] T009 결과 올리기 `POST /captures/sets/{setId}/detections`: 올라간 출고·반납 세트에만, 탐지마다 (face, light, camera) + 원본 px 폴리곤(contracts 11/2 결정), 실패면 종료 코드 3
