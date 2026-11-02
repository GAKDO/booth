# 운영 화면 로컬 API

촬영 도구가 부스 기기에서 띄우는 HTTP API입니다(기본 `http://<jetson>:8320`). 외부 공개 API가 아니며 GAKDO/contracts에 올리지 않습니다. JSON은 camelCase, 오류는 RFC 9457 Problem Details.

| 메서드 | 경로 | 하는 일 | 응답 |
| --- | --- | --- | --- |
| GET | `/` | 운영 화면(HTML) | 200 |
| POST | `/api/sessions` | `{deviceId, mode}`로 세션 시작(있으면 이어 감) | 201 세션 · 400 기기 ID 형식 |
| GET | `/api/sessions/current` | 현재 세션, 현재 단계, 남은 단계, 대기열 수 | 200 · 404 |
| POST | `/api/sessions/current/shoot` | 현재 단계 찍기(카메라 차례로) | 201 `{shots[]}` · 409 NOTICE 단계 · 503 카메라 실패(`camera` 포함) |
| POST | `/api/sessions/current/retake` | 직전 단계 다시 찍기(새 번호) | 201 |
| POST | `/api/sessions/current/next` | NOTICE 단계 확인 후 다음으로 | 200 |
| POST | `/api/sessions/current/skip-scr1` | `{reason}`으로 SCR1 건너뛰기 | 200 · 400 사유 없음 |
| POST | `/api/sessions/current/complete` | 완료 검사 후 종료 | 200 · 409 `{missing[]}` |
| GET | `/api/shots/{id}/thumbnail` | 미리보기 JPEG(긴 변 480px) | 200 |
| GET | `/api/queue` | `{pending, failed, conflict}` 수 | 200 |

현재 단계 응답 예:

```json
{
  "sessionId": "0193a1c2-0000-7000-8000-000000000101",
  "deviceId": "MB01",
  "step": { "index": 2, "kind": "SHOT", "face": "TOP", "light": "L2", "cameras": ["CAM1", "CAM2"], "instruction": "TOP: 닫아서 상판이 위. L2만 켜세요(L1 포함 나머지는 끄기)" },
  "remainingSteps": 22,
  "queue": { "pending": 3, "failed": 0, "conflict": 0 }
}
```
