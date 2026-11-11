# 운영 화면 로컬 API

촬영 도구가 부스 기기에서 띄우는 HTTP API입니다(기본 `http://<jetson>:8320`). 외부 공개 API가 아니며 GAKDO/contracts에 올리지 않습니다. JSON은 camelCase, 오류는 RFC 9457 Problem Details.

| 메서드 | 경로 | 하는 일 | 응답 |
| --- | --- | --- | --- |
| GET | `/` | 운영 화면(HTML) | 200 |
| POST | `/api/sessions` | `{deviceId, mode}`로 세션 시작(있으면 이어 감). `mode`가 OUT·RET이면 `rentalId`·`checkpointId`(관리자 화면이 고른 publicId 21자) 필수 | 201 세션 · 400 기기 ID·렌탈·시점 형식, 서버 연결 설정 없음 · 409 같은 기기에 다른 시점 진행 중 |
| GET | `/api/sessions/current` | 현재 세션, 현재 단계, 남은 단계, 대기열 수 | 200 · 404 |
| POST | `/api/sessions/current/shoot` | 현재 단계 찍기(카메라 차례로) | 201 `{shots[]}` · 409 NOTICE 단계 · 503 카메라 실패(`camera` 포함) |
| POST | `/api/sessions/current/retake` | 직전 단계 다시 찍기(새 번호) | 201 |
| POST | `/api/sessions/current/next` | NOTICE 단계 확인 후 다음으로 | 200 |
| POST | `/api/sessions/current/skip-scr1` | `{reason}`으로 SCR1 건너뛰기 | 200 · 400 사유 없음 |
| POST | `/api/sessions/current/complete` | 완료 검사 후 종료. 출고·반납이면 바로 capture-server 세트로 올림 | 200 `{session, state}` · 409 `{missing[]}` |
| POST | `/api/sessions/current/submit` | 출고·반납 세트를 다시 올리기(실패했을 때) | 200 상태 · 400 서버 연결 설정 없음 |
| GET | `/api/shots/{publicId}/thumbnail` | 미리보기 JPEG(긴 변 480px). 사진 `id`는 publicId(NanoID 21자) | 200 |
| GET | `/api/queue` | `{pending, failed, conflict}` 수 | 200 |

현재 단계 응답 예:

```json
{
  "sessionId": "V1StGXR8_Z5jdHi6B-myT",
  "deviceId": "MB01",
  "step": { "index": 2, "kind": "SHOT", "face": "TOP", "light": "L2", "cameras": ["CAM1", "CAM2"], "instruction": "TOP: 닫아서 상판이 위. L2만 켜세요(L1 포함 나머지는 끄기)" },
  "remainingSteps": 22,
  "queue": { "pending": 3, "failed": 0, "conflict": 0 },
  "checkout": null,
  "checkoutEnabled": true
}
```

출고·반납 세션이면 `checkout`에 `{rentalId, checkpointId, captureSetId, status(PENDING·SUBMITTED·FAILED), attempts, lastError}`가 온다. 출고·반납 사진은 trace-raw 대기열과 `queue` 수에 들어가지 않는다.
