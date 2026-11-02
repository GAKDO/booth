---
description: "BO1 촬영 도구 작업 목록"
---

# 작업: BO1 촬영 도구·부스 촬영

**입력**: `specs/001-booth-capture-tool/`의 설계 문서

**전제**: plan.md, spec.md, research.md, data-model.md, contracts/

**테스트**: 헌법 IV에 따라 수용 시나리오마다 자동 테스트를 둔다. 테스트 이름은 수용 시나리오 문장을 쓴다.

**PR 나누기**: 코드 400줄을 넘으면 나눈다. PR 1 = Phase 1~3(스토리 1, 학습 데이터 촬영 MVP), PR 2 = Phase 4(재시도·업로드), PR 3 = Phase 5(출고·반납, C1·CA1 이후).

## 형식: `[ID] [P?] [스토리] 설명`

- **[P]**: 다른 파일이라 함께 진행 가능
- **[US1]~[US3]**: spec.md의 사용자 스토리

## Phase 1: 준비

- [x] T001 `pyproject.toml`: `opencv-python-headless`는 선택 의존성 `camera`(Jetson은 시스템 OpenCV), 정적 파일 포함. `boto3`·`requests`는 PR 2·3에서 추가. Python 3.10 호환 유지
- [x] T002 [P] `src/trace_booth/codes.py`: codes.yaml 0.3.0의 `Face`·`Light`·`Camera` 값, `FACE_REQUIRED_BOOTH`, `CODES_VERSION = "0.3.0"`
- [x] T003 [P] `tests/fakes.py`: 정해진 JPEG 바이트를 내는 `FakeCamera`(실패 켜기 가능), 실패를 켜고 끌 수 있는 `FakeUploader`

---

## Phase 2: 기반 (모든 스토리 전에)

- [x] T004 [P] `src/trace_booth/naming.py`: 기기 ID 검사 `^[A-Z]{2,4}[0-9]{2}$`, 파일명 만들기·읽기, SLATE 이름 (contracts/storage-layout.md)
- [x] T005 [P] `tests/test_naming.py`: 파일명 규칙 위반 0건, 잘못된 기기 ID 거절, 정규식 왕복
- [x] T006 [P] `src/trace_booth/shotlist.py`: 기본 촬영 목록(SLATE → 패스 1 → NOTICE → 패스 2 → SCR1 선택), 필수 8면, 안내 문구
- [x] T007 [P] `tests/test_shotlist.py`: 필수 사진 28장, 스침광 단계 안내에 "만 켜세요", 패스 2는 L1·L2만·CAM1만, SCR1은 OFF·선택
- [x] T008 `src/trace_booth/store.py`: SQLite 스키마(session, shot), 열기·마이그레이션, 행 추가·조회 (data-model.md). 행 삭제 함수 없음
- [x] T009 `src/trace_booth/camera.py`: `Camera` 프로토콜, `OpenCvCamera`(장치 열기 → 프레임 버리기 → 한 장 → 닫기), `apply_settings()`로 `v4l2-ctl` 호출을 함수 하나에 감쌈
- [x] T010 [P] `src/trace_booth/camera_presets.py`: 조명별 노출·WB·초점 JSON 읽기, 없는 조명은 오류

**체크포인트**: 이름·목록·기록·카메라 추상화가 준비됨

---

## Phase 3: 스토리 1 - 학습 데이터 모드로 노트북 한 대 찍기 (P1) 🎯 MVP

**목표**: 기기 ID를 넣고 목록대로 찍으면 파일·해시·기록이 남고, 필수가 다 차면 "완료"
**독립 테스트**: 가짜 카메라 두 대로 목록을 끝까지 돌려 파일 수·이름·해시 확인

### 테스트

- [x] T011 [P] [US1] `tests/test_session.py`: "기기 ID MB01로 시작하면 첫 단계에 자세·켤 조명·남은 단계 수가 보인다"
- [x] T012 [P] [US1] `tests/test_session.py`: "TOP·L2 단계에서 버튼을 한 번 누르면 CAM1·CAM2 사진이 한 장씩 저장되고 이름이 규칙을 따른다"
- [x] T013 [P] [US1] `tests/test_session.py`: "목록을 끝까지 찍으면 완료가 뜨고 빠진 면이 0개이며 장수가 목록과 같다"
- [x] T014 [P] [US1] `tests/test_session.py`: "찍은 사진을 다시 읽어 계산한 SHA-256이 기록과 같다"
- [x] T015 [P] [US1] `tests/test_session.py`: "패스 2 SL 단계는 L1 한 장, L2 한 장을 차례로 안내하고 CAM1만 찍는다"
- [x] T016 [P] [US1] `tests/test_session.py`: "SCR1 건너뛰기에 사유를 넣으면 SCR1 없이도 완료되고 사유가 남는다"
- [x] T017 [P] [US1] `tests/test_session.py` 경계: 카메라 한 대 실패 시 단계가 넘어가지 않고 실패 카메라를 알림, 재촬영은 새 번호로 남고 이전 파일 유지, 다시 켜면 마지막 단계 다음부터

### 구현

- [x] T018 [US1] `src/trace_booth/session.py`: 세션 시작·이어 가기, 찍기(카메라 차례로, 파일 쓰고 fsync 뒤 기록), 재촬영, NOTICE 넘기기, SCR1 건너뛰기, 완료 검사
- [x] T019 [US1] `src/trace_booth/web.py`: 로컬 API(contracts/operator-web-api.md), Problem Details 오류, 미리보기 JPEG
- [x] T020 [P] [US1] `src/trace_booth/static/index.html`: 단계 안내(크게), 촬영·다시 찍기·다음·SCR1 건너뛰기 버튼, 미리보기 두 장, 대기열 수. 태블릿 가로·세로 모두
- [x] T021 [P] [US1] `tests/test_web.py`: 시작 → 찍기 → 완료 흐름, 잘못된 기기 ID 400, 빠진 면 409
- [x] T022 [US1] `src/trace_booth/__main__.py`: `trace-booth serve`(환경 변수: 데이터 폴더, CAM1·CAM2 경로, 프리셋, 포트)

**체크포인트**: 네트워크 없이도 한 대를 끝까지 찍고 로컬에 다 남음

---

## Phase 4: 스토리 2 - 네트워크가 끊겨도 사진을 잃지 않기 (P1)

**목표**: 업로드 실패분이 남아 자동으로 다시 올라감
**독립 테스트**: 업로더를 끈 채 한 세트를 찍고 켜서 누락 0 확인

### 테스트

- [x] T023 [P] [US2] `tests/test_upload_retry.py`: "저장소에 닿지 않을 때 찍으면 촬영은 실패하지 않고 대기열에 남으며 올리지 못한 장수가 보인다"
- [x] T024 [P] [US2] `tests/test_upload_retry.py`: "저장소가 다시 닿으면 아무것도 하지 않아도 모두 올라가고 누락이 0건이다"
- [x] T025 [P] [US2] `tests/test_upload_retry.py`: "도구를 다시 켜면 이전에 올리지 못한 사진을 계속 올린다"
- [x] T026 [P] [US2] `tests/test_upload_retry.py`: "같은 사진을 두 번 올려도 같은 이름·해시 파일 하나만 남는다", 같은 키 다른 해시는 CONFLICT

### 구현

- [x] T027 [US2] `src/trace_booth/uploader.py`: `Uploader` 프로토콜, `RawBucketUploader`(boto3, `head_object`로 중복 확인, 메타데이터 sha256 등), 백그라운드 재시도 루프(지수 백오프, 최대 60초)
- [x] T028 [US2] `src/trace_booth/web.py`·`index.html`: 대기열 수 표시 연결, `/api/queue`
- [ ] T029 [US2] 기기 완료 때 `{deviceId}/manifest.json` 올리기(사진 목록·해시·SCR1 사유) — 미룸: 한 기기를 여러 번 찍으면 같은 키를 덮어쓰게 되어 키 규칙을 A와 정한 뒤 한다. 지금은 `trace-booth shots --device`로 대신
- [x] T030 [US2] `src/trace_booth/__main__.py`: `trace-booth shots --device <ID> --verify`(저장소 해시와 기록 비교), `trace-booth retry`

**체크포인트**: 학습 데이터 촬영에 쓸 수 있음 (11/4 그램 촬영 목표)

---

## Phase 5: 스토리 3 - 출고·반납 모드 (P2) ⏸ C1·CA1 이후

**전제**: plan.md "계약 확인 필요" 2건은 11/2 결정됨. C1 OpenAPI 반영과 CA1을 기다림. `complete`에 `skippedFaces` 보내기 포함

- [ ] T031 [P] [US3] 테스트: "출고 모드에서 찍으면 서버가 준 URL로 올라가고 학습용 저장소에는 올라가지 않는다"
- [ ] T032 [P] [US3] 테스트: "사전 서명 URL이 만료되면 새 URL을 받아 올리고 누락이 0건이다"
- [ ] T033 [US3] `src/trace_booth/capture_client.py`: BOOTH 자격 → JWT, `POST /captures/sets`, `complete` (C1 OpenAPI 기준)
- [ ] T034 [US3] `src/trace_booth/uploader.py`: `PresignedUploader`(requests PUT), 만료 시 URL 다시 받기
- [ ] T035 [US3] 운영 화면에 모드·렌탈·시점 선택

---

## Phase 6: 마무리

- [ ] T036 [P] `README.md`: 실행 방법·환경 변수 갱신
- [ ] T037 [P] `capture/README.md`: Jetson에 C920 연결 확인, `/dev/v4l/by-id` 찾기, 조명별 노출·WB·초점 측정과 프리셋 파일 만들기
- [ ] T038 `ruff check . && pytest -q`, `scripts/check-no-secrets.sh` 통과
- [ ] T039 Jetson에서 quickstart.md 절차대로 REG01 한 바퀴(랜선 뽑기 포함)

## 의존 관계

- Phase 1 → Phase 2 → Phase 3(US1) → Phase 4(US2) → Phase 6. Phase 5(US3)는 C1·CA1 이후.
- US2는 US1의 세션·기록을 씀. US3는 US2의 업로더 구조를 씀.

## 함께 진행할 수 있는 것

- Phase 1: T002, T003
- Phase 2: T004·T005, T006·T007, T010 (T008·T009는 순서대로)
- US1 테스트 T011~T017은 한 파일이라 한 번에 쓰고, T020·T021은 T019와 따로

## 구현 전략

1. Phase 1~3으로 MVP: 네트워크 없이 한 대 촬영 → PR 1
2. Phase 4: 업로드·재시도 → PR 2, 11/4 그램 촬영에 사용
3. Phase 5: 계약 고정·CA1 뒤 → PR 3
