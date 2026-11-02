# 데이터 모델: BO1 촬영 도구

로컬 SQLite(`booth.db`)와 코드 상수. 시각은 ISO 8601 + 오프셋, ID는 헌법 1.1.0 규칙: 기본 키는 정수 `id`(SQLite 자동 증가)로 화면·API에 내보내지 않고, 밖에 보이는 ID는 `public_id`(NanoID 21자)다.

## 촬영 단계 Step (코드 상수, shotlist.py)

| 필드 | 설명 |
| --- | --- |
| index | 0부터 순서 |
| kind | `SLATE` · `SHOT` · `NOTICE`(바 #2 옮기기, CAM1 내리기처럼 찍지 않는 안내) |
| face | Face 또는 없음(SLATE·NOTICE) |
| light | Light 또는 없음 |
| cameras | 찍을 카메라 목록 (패스 1: CAM1·CAM2, 패스 2·SCR1·SLATE: CAM1) |
| optional | SCR1만 true |
| instruction | 안내 문구(자세, "L2만 켜세요(L1 포함 나머지는 끄기)") |

기본 목록: SLATE → TOP L1·L2·L3 → BOT L1·L2·L3 → NOTICE 바 #2를 앞 자리(L4)로 → KB L1·L2·L4 → NOTICE CAM1 내리기·배경판 → SF·SB·SL·SR·SCR0 각 L1·L2 → SCR1 OFF(선택) → NOTICE CAM1 다시 올리기.
필수 사진 수 = 패스 1 18장(9단계 × 2) + 패스 2 10장 = 28장, 그 밖에 SLATE 1장, SCR1 0~1장.

## 촬영 세션 session

| 컬럼 | 타입 | 규칙 |
| --- | --- | --- |
| id | integer | 기본 키(자동 증가), 밖에 내보내지 않음 |
| public_id | text | NanoID 21자, 운영 화면 API의 `sessionId` |
| device_id | text | `^[A-Z]{2,4}[0-9]{2}$` |
| mode | text | `TRAINING` · `OUT` · `RET` (OUT·RET는 스토리 3) |
| rental_id, checkpoint_id | text | 출고·반납 모드만. rental-server의 publicId(21자) 그대로, FK 없음 |
| current_step | int | 다음에 찍을 단계 |
| scr1_skip_reason | text | SCR1을 건너뛰었을 때만 |
| status | text | `IN_PROGRESS` → `COMPLETE` |
| started_at, completed_at | text | |

전이: `IN_PROGRESS`에서 필수 단계가 모두 찍히고(SCR1은 찍었거나 사유가 있음) 운영 담당이 완료를 누르면 `COMPLETE`. 같은 기기로 새 세션을 열면 이전 `IN_PROGRESS` 세션을 이어 간다.

## 출고·반납 checkout

출고·반납(OUT·RET) 세션 하나에 한 행. 렌탈·시점은 다른 서버(rental-server)의 publicId 문자열로만 둔다(FK 없음, 헌법 1.1.0).

| 칸 | 형식 | 설명 |
| --- | --- | --- |
| session_id | INTEGER PK | session.id |
| rental_id | TEXT | 관리자 화면이 고른 렌탈 publicId(21자) |
| checkpoint_id | TEXT | 시점 publicId(21자) |
| capture_set_id | TEXT NULL | capture-server가 준 세트 publicId |
| status | TEXT | PENDING → SUBMITTED, 실패하면 FAILED(다시 올리기 가능) |
| attempts | INTEGER | 올리기 시도 횟수 |
| last_error | TEXT NULL | 마지막 실패 사유(사전 서명 URL은 넣지 않음) |
| updated_at | TEXT NULL | 마지막 시도 시각 |

## 사진 shot

| 컬럼 | 타입 | 규칙 |
| --- | --- | --- |
| id | integer | 기본 키(자동 증가), 밖에 내보내지 않음 |
| public_id | text | NanoID 21자, 운영 화면 API의 사진 `id`·미리보기 주소 |
| session_id | integer | session.id |
| step_index | int | |
| face, light, camera | text | codes.yaml 값(SLATE는 face 없음) |
| seq | int | 같은 기기·면·조명·카메라에서 1부터, 재촬영 때 +1 |
| filename | text | 유일. `{기기ID}_{면}_{조명}_{카메라}_{번호:02d}.jpg` |
| local_path | text | 데이터 폴더 아래 |
| sha256 | text | 64자 소문자 hex |
| size_bytes | int | |
| camera_settings | text(JSON) | 적용한 노출·WB·초점 |
| captured_at | text | |
| upload_status | text | `PENDING` → `UPLOADED`, 실패 시 `FAILED`(재시도), 같은 키 다른 해시면 `CONFLICT` |
| upload_attempts | int | |
| last_error | text | 마지막 실패 사유(서명 URL은 남기지 않음) |
| uploaded_at | text | |

규칙: 행은 지우지 않는다. 재촬영해도 이전 행·파일은 남는다. 완료 검사는 단계마다 "최신 사진이 모든 카메라에 있는가"로 본다.
