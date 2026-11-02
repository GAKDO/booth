# 빠른 확인: BO1 촬영 도구

## 개발 PC (카메라 없이)

```bash
python -m pip install -e ".[dev]"
ruff check . && pytest -q
```

테스트가 가짜 카메라·가짜 업로더로 다음을 확인한다.

- 한 기기의 목록을 끝까지 찍으면 필수 사진 28장, 빠진 면 0, 파일명 규칙 위반 0
- 업로더를 끈 채 찍고 켜면 누락 0, 도구를 다시 켜도 대기열이 이어짐
- 같은 키·같은 해시를 두 번 올려도 하나만 남음
- SCR1 건너뛰기(사유)로도 완료됨

## Jetson (부스)

준비: C920 2대를 꽂고 `ls /dev/v4l/by-id/`로 경로를 확인한다.

```bash
export TRACE_BOOTH_DATA_DIR=~/trace-booth-data
export TRACE_BOOTH_CAM1=/dev/v4l/by-id/<앞 카메라>
export TRACE_BOOTH_CAM2=/dev/v4l/by-id/<뒤 카메라>
export TRACE_BOOTH_PRESETS=~/trace-booth-presets.json   # 조명별 노출·WB·초점(부스 설정 날 실측)
export TRACE_MINIO_ENDPOINT=...  TRACE_MINIO_ACCESS_KEY=...  TRACE_MINIO_SECRET_KEY=...   # 값은 trace-secrets
trace-booth serve --port 8320
```

태블릿에서 `http://<jetson 주소>:8320`을 열고:

1. 기기 ID `REG01`(팀 노트북)로 시작
2. 단계마다 안내대로 조명을 바꾸고 촬영 → 미리보기 두 장 확인
3. 중간에 Jetson 랜선을 뽑고 몇 단계 찍기 → 화면의 "올리지 못함" 수가 늘어남 → 다시 꽂으면 0으로 돌아옴
4. 완료 → MinIO `trace-raw/REG01/`에 28장(+SLATE, SCR1) 있는지, 해시가 같은지 `trace-booth shots --device REG01 --verify`로 확인

조명별 설정 측정 절차는 `capture/README.md`로 따로 쓴다(아직 없음).
