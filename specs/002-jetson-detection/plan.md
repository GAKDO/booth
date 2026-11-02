# 구현 계획: BO2 Jetson 흠집 탐지

**브랜치**: `claude/project-thread-sy1p9l-detect` | **작성일**: 2026-11-02 | **명세**: [spec.md](spec.md)

## 요약

`trace_booth.detect` 패키지: 타일 자르기·좌표 되돌리기(`tiling`), 타일 사이 중복 제거(`merge`), 탐지기 프로토콜과 TensorRT 탐지기(`engine`), 세트 탐지와 시간 기록(`runner`). 올리는 부분은 계약 확인 뒤 PR을 따로 낸다.

## 기술 맥락

- Python 3.10(Jetson JetPack 6), CI 3.12. numpy(Jetson 기본), 엔진 추론은 ultralytics(선택 의존성 `detect`, Jetson에서만)
- 이미지 읽기는 OpenCV(기존 `camera` 선택 의존성). 테스트는 numpy 배열과 가짜 탐지기로, GPU·OpenCV 없이
- 타일: 기본 640px, 겹침 20%(엔진 입력 크기와 같게). 중복 제거: 같은 클래스, 상자 IoU ≥ 0.5
- 모델 정보: 엔진 옆 `model.json` `{modelVersion, classes}`

## 헌법 점검

| 원칙 | 점검 | 결과 |
| --- | --- | --- |
| I. 계약 | 클래스는 codes.yaml `inspection.defect_class`. 결과 올리기 형식은 계약 질문 1건을 A에게 했고 코드로 바꾸지 않음 | 보류(올리기만) |
| II. 경계 | 부스는 결과를 gateway → capture-server로만 | 통과 |
| III. 기록 | 원본 사진을 바꾸지 않음(읽기만), 결과마다 modelVersion | 통과 |
| IV. 테스트 | 가짜 탐지기로 수용 시나리오 | 통과 |
| V. 비밀 | 올리기 단계에서 BOOTH 자격은 환경 변수 | 통과 |
| VI. 단순 | 타일·NMS는 numpy로 직접(라이브러리 없이 60줄 안) | 통과 |

## 구조

```text
src/trace_booth/detect/
├── __init__.py
├── tiling.py    # 타일 좌표, 원본 좌표로 되돌리기
├── merge.py     # 클래스별 상자 IoU 중복 제거
├── engine.py    # Detector 프로토콜, ModelInfo(model.json), UltralyticsDetector(지연 import)
└── runner.py    # 세트의 면 사진 탐지, 시간 기록
tests/test_detect.py
```
