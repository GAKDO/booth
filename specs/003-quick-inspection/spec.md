# 기능 명세: 부스 빠른 검사 모드 (11/3 MVP)

**기능 브랜치**: `claude/project-thread-doihnq` (Spec Kit 기본값은 `feature/003-quick-inspection`)

**작성일**: 2026-11-03

**상태**: 1부(화면·흐름·탐지·결과 API) 구현, 2부(서버 올리기·재시도 큐)는 contracts 1.1.0(#20) 머지 뒤

**입력**: 11/3 방향 전환(강사님 리뷰, 민서 승인) B 11/05 할 일: "태블릿에서 검사 기다리는 기기 목록 탭 → BOT 촬영 → 뒤집어 TOP 촬영 → Jetson 탐지 → 부스 화면에 흠집 폴리곤과 문제 있음/없음 표시 → 서버로 세트·탐지 올리기(실패 시 로컬 큐 재시도). 새 로컬 API: GET /api/sessions/current/result."

**요구사항 ID**: FR-CAP-01(부스), FR-INS-01(부스), NFR-01

## 명확화

### 세션 2026-11-03

- Q: 기기는 어떻게 고르나요? → A: 빌린 노트북이라 스티커·QR을 붙이지 않는다. 태블릿이 rental-server `GET /api/v1/rentals?status=OPEN`을 부스 JWT로 읽어, 지금 시점(currentCheckpoint)이 OPEN인 렌탈만 "검사 기다리는 기기"로 보인다
- Q: 사진은? → A: booth #21 MVP 목록에서 ID 카드(SLATE)만 뺀 것. BOT L1·L2·L3 → 뒤집기 안내 → TOP L3·L2·L1, 각 CAM1·CAM2 = 12장(민서 11/3: 옆조명도 쓴다)
- Q: 사진 파일명의 기기 자리는? → A: 목록에서 고른 rental-server 기기 publicId(21자). 학습 데이터의 MB01 같은 코드와 둘 다 받는다
- Q: 판정은? → A: 탐지가 하나라도 있으면 "문제 있음", 없으면 "문제 없음". 부스 화면용이고 등급은 capture-server가 매긴다
- Q: 엔진이 오기 전에는? → A: 탐지기는 `DetectorSource` 하나로 감싼다. 테스트는 가짜, Jetson에서 흐름만 시험할 때는 `TRACE_BOOTH_DETECTOR=none`(늘 0개, 화면에 "엔진 없는 시험" 표시)

## 사용자 스토리 1 - 목록에서 기기를 골라 BOT·TOP을 찍고 바로 판정을 본다 (P1)

**수용 시나리오** (`tests/test_quick_mode.py`):

1. 빠른 검사 목록은 ID 카드 없이 BOT을 먼저 찍고 뒤집어 TOP을 찍는 12장이다.
2. 기다리는 기기는 진행 중 렌탈의 열린 시점만, 다음 쪽까지 모은다.
3. 태블릿에서 기기를 골라 찍고 완료하면 결과 API가 판정을 준다.
4. 흠집이 하나라도 있으면 문제 있음이고, 폴리곤은 원본 px와 사진 크기로 돌려준다(화면이 축소 사진 위에 겹쳐 그림).
5. 엔진을 못 부르면 실패를 남기고, 사진은 그대로라 "다시 탐지"로 다시 돌릴 수 있다.
6. 서버 설정이 없으면 기기 목록은 503, 엔진 설정이 없으면 빠른 검사를 시작하지 않는다.

## 사용자 스토리 2 - 세트·탐지를 서버에 올리고 실패하면 로컬 큐에서 다시 (P1, contracts 1.1.0 뒤)

1. 탐지가 끝나면 `profile: QUICK` 세트를 만들고 12장을 올린 뒤 완료하고, 탐지를 `analyzedPhotos`(12장 모두)와 함께 올린다.
2. 어느 단계든 실패하면 로컬에 남기고 주기적으로 다시 시도한다. 올린 세트는 다시 만들지 않고 남은 단계만 한다.

## 로컬 API (운영 화면)

| 메서드·경로 | 하는 일 |
|---|---|
| `GET /api/rentals/waiting` | 검사 기다리는 기기 `{items:[{rentalId, deviceId, serial, modelCode, checkpointId, checkpointType}]}`. 서버 설정 없음 503, 서버 오류 502 |
| `POST /api/sessions` `{mode:"QUICK", deviceId, rentalId, checkpointId}` | 빠른 검사 시작 |
| `POST /api/sessions/current/complete` | 12장이 다 있으면 완료하고 탐지를 백그라운드로 시작 |
| `GET /api/sessions/current/result` | `status`(SHOOTING·DETECTING·DONE·FAILED), `verdict`(DEFECT·CLEAN·null), `count`, `faces`, `seconds`, `modelVersion`, `testOnly`, `error`, `photos[{shotId, face, light, camera, width, height, detections[{class, polygon, confidence}]}]` |
| `POST /api/sessions/current/detect` | 실패한 탐지를 다시 돌림 |

## 남은 확인

- rental-server `GET /api/v1/rentals`의 x-trace-access roles가 `[ADMIN, CUSTOMER]`라 부스 토큰(BOOTH)이 읽을 수 있는지 A 확인 필요(계약 변경이면 A가 결정).
- 11/6 jetson2 실측: 완료 → 판정 시간(목표 15초, 기준 30초). 화면에 "완료→판정 N초"로 보인다.

## 2부 구현 (contracts 1.1.0 뒤)

- `quick_upload.upload_quick`: `checkout.submit_session(profile="QUICK")`로 세트를 올리고(올린 세트는 다시 만들지 않음), 탐지가 DONE이면 `save_detections(..., analyzedPhotos=12장)`. 보낸 탐지 결과의 시각을 `quick_upload`에 남겨 다시 탐지하면 다시 보낸다. 409 `CAPTURE_SET_CLOSED`면 CLOSED로 두고 더 보내지 않는다.
- `QuickRetryWorker`: 탐지가 끝나면 바로 한 번 올리고, 못 올린 것은 30초마다 다시(`Store.quick_sessions_to_upload`).
- 결과 API에 `upload{set, captureSetId, detections, lastError}`, 화면에 "서버에 올림/올리는 중/못 올림(자동으로 다시)".
- 테스트 `tests/test_quick_upload.py`(가짜 서버가 QUICK 완료 규칙과 analyzedPhotos 검사를 따른다).
