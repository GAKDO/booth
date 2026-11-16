# booth

TRACE(노트북 렌탈 상태 기록 서비스)의 **booth**: 부스 Jetson 앱과 학습 데이터 촬영 도구

태블릿으로 부스 화면을 열어 기기를 고르면, 위 카메라 2대(C920)와 조명 3개로 노트북 아랫면·윗면을 같은 조건으로 12장 찍고, Jetson에서 TensorRT FP16으로 흠집을 찾아 "문제 있음/없음"을 바로 띄웁니다. 사진과 탐지 결과는 capture-server로 올라가고, 관리자가 웹에서 흠집 후보를 확정·기각(사람 검수)한 것만 등급과 봉인 기록에 들어갑니다.

- 담당: B
- 설계: [GAKDO/docs](https://github.com/GAKDO/docs) `booth/README.md`(부스 제작·배치), 이 레포 `specs/`(기능 명세). 촬영 면·조명 코드는 contracts의 `codes.yaml`
- 기술: Python 3.10 이상(Jetson JetPack 6, CI는 3.12), OpenCV + v4l2-ctl 고정 카메라 값, TensorRT FP16(torch·ultralytics 없이 추론), 표준 라이브러리 HTTP 서버(운영 화면), boto3(학습 사진 업로드)
- 대회 모델: **v004**(YOLO11m-seg, [GAKDO/ml](https://github.com/GAKDO/ml)에서 학습). 같은 ONNX를 학교 서버 inspect-worker도 씁니다

## 부스에서 하는 일

| 모드 | 찍는 것 | 하는 일 |
|---|---|---|
| **빠른 검사**(QUICK, 기본) | 아랫면 L1·L2·L3 → 뒤집기 → 윗면 L3·L2·L1, 카메라 2대 = 12장 | 찍는 동안 미리 탐지, 끝나면 흠집 폴리곤과 판정 표시, 세트(`profile: QUICK`)와 탐지 결과를 서버에 올림 |
| 전체 검사(FULL) | 아랫면·윗면·펼친 키보드·옆면 28장 | 검사 화면에서 범위를 고르면 전체 사진을 탐지해 `profile: FULL` 세트로 올림 |
| 학습 데이터 촬영 | 촬영 목록 `mvp`(위·아래)·`kb`(키보드)·`side`(옆면)·`full`(보관용) | 기기 코드(예: `MB02`)로 찍어 학습용 원본 저장소(`trace-raw`)에 올림 |

빠른 검사 흐름:

1. 태블릿에서 `http://<부스 주소>:8320`을 엽니다. `TRACE_BOOTH_UI_TOKEN`이 있으면 처음 한 번 `?token=<비밀>`을 붙여 엽니다.
2. "검사 기다리는 기기"에서 기기를 고릅니다. 목록은 rental-server의 진행 중 렌탈 중 지금 시점이 열린 것만 보이고, 관리자 화면의 일회용 6자리 부스 코드로도 고를 수 있습니다.
3. 화면 안내대로 조명을 바꾸며 12장을 찍습니다. 카메라 2대는 동시에 찍고, 카메라는 열어 둔 채 실시간 보기와 같이 씁니다.
4. 긁힘·찍힘·균열이 하나라도 있으면 "문제 있음", 오염만 잡히면 "문제 없음"입니다. 등급은 capture-server가 매깁니다.
5. 다 찍자마자 사진을 사전 서명 URL로 올리고, 판정이 끝나면 탐지 결과를 올립니다. 실패한 것은 부스에 남겨 30초마다 다시 올립니다.

- 수령·반납 기기는 출고 때 고른 검사 방식(빠른·전체)만 고를 수 있습니다(11/13).
- 탐지 시간: v004 엔진으로 12장 **2.53초**(jetson2 실측, 목표 30초·기준 60초). 측정은 `trace-booth bench`.

## 설치 (Jetson)

Jetson은 JetPack의 OpenCV·TensorRT를 그대로 쓰므로 가상환경을 `--system-site-packages`로 만듭니다.

```bash
git clone https://github.com/GAKDO/booth.git ~/booth
python3 -m venv --system-site-packages ~/trace-venv
~/trace-venv/bin/pip install -e ~/booth"[upload,detect]"
~/trace-venv/bin/trace-booth --version
```

sudo가 없거나 pypi.org가 막힌 학교 망, 카메라 고정 경로(`/dev/v4l/by-path`)·조명별 카메라 값 정하기는 [capture/README.md](capture/README.md)에 단계별로 있습니다.

## 실행

```bash
set -a; . ~/.config/trace-booth.env; set +a     # 주소·카메라·비밀은 이 파일에만 둔다(레포·채팅에 넣지 않음)
trace-booth build-engine --onnx <best.onnx>      # 처음 한 번: ONNX → TensorRT FP16 엔진(~/trace-models)
TRACE_BOOTH_ENGINE=~/trace-models/current/best.engine trace-booth serve   # 운영 화면 8320
```

새 모델은 ml 레포의 `deploy-jetson.sh`가 해시를 확인하고 `~/trace-models/<버전>`에 엔진을 만든 뒤 `~/trace-models/current`로 연결합니다. serve를 다시 띄우면 새 엔진을 씁니다.

| 명령 | 하는 일 |
|---|---|
| `trace-booth serve [--port 8320]` | 운영 화면(태블릿)과 로컬 API |
| `trace-booth build-engine --onnx <파일> [--out ~/trace-models]` | ONNX → TensorRT FP16 엔진과 `model.json`(Jetson, trtexec) |
| `trace-booth bench --engine <엔진> --session <id>` | 탐지 시간 재기. `--engine best.onnx`면 GPU 없는 PC에서 같은 결과를 맞춰 봄(`[onnx]`) |
| `trace-booth detect --session <id>` | 세션 하나의 사진을 탐지해 로컬 JSON으로 남김 |
| `trace-booth light-check --light L1` | 조명 밝기·빛샘을 11/5 확정 숫자와 비교(`--leak`은 조명을 모두 끈 빛샘 확인) |
| `trace-booth shots --device <기기> [--verify]` | 기기의 사진 목록과 업로드 상태, 저장소 해시 비교 |
| `trace-booth retry` | 올리지 못한 사진을 지금 올림 |
| `trace-booth netcheck` | 서버 주소·연결 시간 확인(업로드가 느릴 때) |

부스와 학교 서버가 같은 사진에 같은 판정을 내는지는 `scripts/booth_vs_server.py`로 비교합니다.

## 환경 변수

값은 Jetson의 `~/.config/trace-booth.env`에만 둡니다. 부스 자격(client id·secret)과 UI 비밀은 관리자에게 직접 받습니다.

| 이름 | 뜻 | 기본값 |
|---|---|---|
| `TRACE_API_URL` | 서버 입구(gateway). 없으면 학습 데이터 모드만 | 없음 |
| `TRACE_BOOTH_CLIENT_ID`·`TRACE_BOOTH_CLIENT_SECRET` | 부스 자격(부스 JWT 발급) | 없음 |
| `TRACE_BOOTH_UI_TOKEN` | 운영 화면 접속 비밀. 없으면 같은 망 누구나 열 수 있음 | 없음 |
| `TRACE_BOOTH_CAM1`·`TRACE_BOOTH_CAM2` | 위 카메라 장치 경로(`/dev/v4l/by-path/...`) | 필수 |
| `TRACE_BOOTH_PRESETS` | 조명별 카메라 값 JSON(11/5 확정값) | 없음(경고) |
| `TRACE_BOOTH_ENGINE` | TensorRT 엔진(.engine, 같은 폴더에 `model.json`). 없으면 빠른 검사 꺼짐 | 없음 |
| `TRACE_BOOTH_DETECTOR` | `none`이면 엔진 없이 흐름만 시험(탐지 0개) | 없음 |
| `TRACE_BOOTH_DETECT_LIGHTS` | 빠른 검사 판정에 쓸 조명(예: `L3`, `L1,L3`) | 전부 |
| `TRACE_BOOTH_PREDETECT` | `0`이면 찍는 동안 미리 탐지하지 않음 | `1` |
| `TRACE_BOOTH_INSPECTION` | 검사 화면이 처음 고르는 범위 `quick`·`full` | `quick` |
| `TRACE_BOOTH_SHOTLIST` | 학습 촬영 목록 `mvp`·`kb`·`side`·`full` | `mvp` |
| `TRACE_BOOTH_KEEP_OPEN` | `1`이면 카메라를 열어 둔 채 실시간 보기와 촬영이 같이 씀 | `1` |
| `TRACE_BOOTH_PARALLEL_CAPTURE` | `1`이면 CAM1·CAM2 동시 촬영 | `1` |
| `TRACE_BOOTH_WARMUP_FRAMES` | 카메라 값을 넣은 뒤 버리는 프레임 수 | `4` |
| `TRACE_BOOTH_SIDE_CAM`·`_SIDE_ROTATE`·`_SIDE_PRESETS` | 옆면 전용 카메라 장치·회전·프리셋(없으면 CAM1을 내려 찍음) | 없음·`180`·위 카메라 값 |
| `TRACE_BOOTH_SIDE_EXCLUSIVE` | `1`이면 위 카메라와 옆면 카메라를 번갈아 열어 USB2 대역폭 부족을 막음 | `1` |
| `TRACE_BOOTH_UPLOAD_WORKERS` | 동시에 올리는 사진 수(1~8) | `4` |
| `MINIO_ENDPOINT`·`MINIO_ACCESS_KEY`·`MINIO_SECRET_KEY`, `TRACE_RAW_BUCKET` | 학습 사진 원본 저장소. 없으면 로컬에만 남김 | 없음, `trace-raw` |
| `TRACE_BOOTH_DATA_DIR` | 사진·로컬 DB 폴더 | `~/trace-booth-data` |

## 문서

- [capture/README.md](capture/README.md): Jetson 설치, 카메라 연결 확인, 조명별 카메라 값(11/5 확정값)
- [specs/001-booth-capture-tool](specs/001-booth-capture-tool/): 학습 데이터 촬영 도구
- [specs/002-jetson-detection](specs/002-jetson-detection/): Jetson TensorRT 탐지·엔진 빌드·시간 측정
- [specs/003-quick-inspection](specs/003-quick-inspection/spec.md): 빠른 검사 화면·흐름·서버 올리기
- [CHANGELOG.md](CHANGELOG.md): 바뀐 것

## 테스트

```bash
python -m pip install -e ".[dev]"
python -m ruff check . && python -m pytest
```

카메라·엔진·서버는 함수 하나로 감싸 테스트에서 가짜로 바꿔 끼우므로 GPU·카메라 없이 돕니다.

## CI

| 워크플로 | 언제 | 하는 일 |
|---|---|---|
| `python-ci` | PR, develop·main push | ruff + pytest (ubuntu, Python 3.12) |
| `비밀값 검사`·`release`·알림 | | 다른 레포와 같음 |
