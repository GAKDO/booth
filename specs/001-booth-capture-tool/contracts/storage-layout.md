# 저장 위치·파일명 약속 (학습 데이터 모드)

ML3(Label Studio)·ML1(데이터셋 빌더)이 읽는 쪽이라 A와 맞춰야 합니다.

- 버킷: `trace-raw` (버전 관리 켜짐, 촬영 키는 삭제 권한 없음)
- 키: `{deviceId}/{filename}`
- 파일명: `{deviceId}_{face}_{light}_{camera}_{seq:02d}.jpg` — 예: `MB01_TOP_L2_CAM1_01.jpg`, `MB01_SCR1_OFF_CAM1_01.jpg`
  - face·light·camera는 contracts `codes.yaml` 0.3.0 값
  - seq는 재촬영할 때 올라감. 같은 키를 덮어쓰지 않음
- SLATE: `{deviceId}/slate/{deviceId}_SLATE_CAM1_{seq:02d}.jpg` (면 코드가 아니므로 학습 대상 폴더와 분리)
- 객체 메타데이터: `sha256`, `face`, `light`, `camera`, `capturedAt`, `toolVersion`, `cameraSettings`(JSON 문자열)
- 기기마다 촬영이 끝나면 `{deviceId}/manifest.json`에 사진 목록·해시·SCR1 건너뛴 사유를 남긴다

파일명 정규식: `^[A-Z]{2,4}[0-9]{2}_(TOP|BOT|SF|SB|SL|SR|KB|SCR0|SCR1)_(L1|L2|L3|L4|OFF)_(CAM1|CAM2)_[0-9]{2}\.jpg$`
