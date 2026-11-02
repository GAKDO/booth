# 빠른 확인: BO1 촬영 도구

## 개발 PC (카메라 없이)

```bash
python -m pip install -e ".[dev]"          # Jetson: capture/README.md 1장(venv --system-site-packages, OpenCV는 시스템 것)
ruff check . && pytest -q
```

테스트가 가짜 카메라·가짜 업로더로 다음을 확인한다.

- 한 기기의 목록을 끝까지 찍으면 필수 사진 28장, 빠진 면 0, 파일명 규칙 위반 0
- 업로더를 끈 채 찍고 켜면 누락 0, 도구를 다시 켜도 대기열이 이어짐
- 같은 키·같은 해시를 두 번 올려도 하나만 남음
- SCR1 건너뛰기(사유)로도 완료됨

## Jetson (부스)

준비: 카메라 2대를 꽂고 `capture/README.md` 연결 확인 ②에 따라 경로를 정한다. by-id 이름이 서로 다르면 by-id를, C920 두 대처럼 이름이 겹치면 by-path(USB 포트)를 쓴다.

```bash
export TRACE_BOOTH_DATA_DIR=~/trace-booth-data
export TRACE_BOOTH_CAM1=<앞 카메라 경로>
export TRACE_BOOTH_CAM2=<뒤 카메라 경로>
export TRACE_BOOTH_PRESETS=~/trace-booth-presets.json   # 조명별 노출·WB·초점(부스 설정 날 실측)
export MINIO_ENDPOINT=http://storage.java21.net:8000   # S3 API(https://storage.java21.net/은 웹 화면이라 업로드 안 됨)
# MINIO_ACCESS_KEY·MINIO_SECRET_KEY는 trace-capture 키. capture/README.md의 키 파일 방법으로 넣고 채팅·코드에 쓰지 않음
export TRACE_RAW_BUCKET=trace-raw                     # 기본값
# 출고·반납 모드(CA1 배포 뒤). 없으면 학습 데이터 모드만 켜진다
export TRACE_API_URL=https://<gateway 주소>
export TRACE_BOOTH_CLIENT_ID=...  TRACE_BOOTH_CLIENT_SECRET=...   # 값은 trace-secrets, 채팅·코드에 쓰지 않음
trace-booth serve --port 8320
```

태블릿에서 `http://<jetson 주소>:8320`을 열고:

1. 기기 ID `REG01`(팀 노트북)로 시작
2. 단계마다 안내대로 조명을 바꾸고 촬영 → 미리보기 두 장 확인
3. 중간에 Jetson 랜선을 뽑고 몇 단계 찍기 → 화면의 "올리지 못함" 수가 늘어남 → 다시 꽂으면 0으로 돌아옴
4. 완료 → MinIO `trace-raw/REG01/`에 28장(+SLATE, SCR1) 있는지, 해시가 같은지 `trace-booth shots --device REG01 --verify`로 확인

조명별 설정 측정 절차는 `capture/README.md`.

### 출고·반납 모드 (CA1 배포 뒤)

1. 운영 화면에서 모드 "출고"를 고르고 기기 ID, 관리자 화면의 렌탈 ID·시점 ID(21자)를 넣고 시작
2. 목록대로 찍고 완료 → "완료. 완료 책상으로 보내세요"가 뜨면 capture-server 세트로 올라간 것
3. 랜선을 뽑은 채 완료 → "서버에 올리지 못했어요" → 다시 꽂고 "다시 올리기" → 완료
4. trace-raw에는 이 사진이 올라가지 않았는지 확인
