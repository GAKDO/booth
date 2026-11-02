# 구현 계획: BO1 촬영 도구·부스 촬영

**브랜치**: `claude/project-thread-sy1p9l` | **작성일**: 2026-11-02 | **명세**: [spec.md](spec.md)

**입력**: `specs/001-booth-capture-tool/spec.md`

## 요약

부스 Jetson에서 도는 촬영 도구를 만든다. 도구는 작은 웹 서버를 띄워 운영 담당이 태블릿·모니터 브라우저로 촬영 목록을 따라가게 하고, 버튼 한 번에 그 단계의 C920을 차례로 찍는다. 사진은 규칙대로 이름 붙이고 SHA-256을 계산해 로컬 디스크와 로컬 기록(SQLite)에 먼저 남긴 뒤, 백그라운드 업로더가 학습 데이터 모드는 MinIO `trace-raw`로, 출고·반납 모드는 capture-server 사전 서명 URL로 올린다. 올리지 못하면 기록에 남겨 두었다가 다시 올린다. 출고·반납 모드는 CA1(11/6)과 contracts C1(11/3)이 나온 뒤 붙인다.

## 기술 맥락

**언어/버전**: Python 3.10(Jetson JetPack 6), CI는 3.12로 검사. 3.11 이후 문법 금지.

**주요 의존성**: OpenCV(`opencv-python-headless`, Jetson에서는 시스템 OpenCV 허용), `v4l2-ctl`(외부 명령, 카메라 고정 설정), `boto3`(trace-raw), `requests`(사전 서명 URL PUT). 웹 화면은 표준 라이브러리 `http.server` + 정적 HTML·JS 한 장. 기록은 표준 라이브러리 `sqlite3`.

**저장소**: 로컬 사진 폴더 `TRACE_BOOTH_DATA_DIR`(기본 `~/trace-booth-data`) + 같은 폴더의 SQLite `booth.db`. 원격은 MinIO `trace-raw`(학습), `trace-captures`(사전 서명 URL, 출고·반납).

**테스트**: pytest + 가짜 카메라(정해진 JPEG 바이트를 내는 클래스) + 가짜 업로더(실패를 켜고 끌 수 있음). 네트워크·카메라·`v4l2-ctl`은 함수 하나로 감싸 테스트에서 바꿔 끼운다. ruff 통과.

**대상 플랫폼**: Jetson Orin Nano(jetson2, Ubuntu 22.04/JetPack 6), C920 2대(USB). 개발·CI는 일반 Linux.

**프로젝트 형태**: 단일 파이썬 패키지(`src/trace_booth/`) + 명령줄 진입점 `trace-booth serve`.

**성능 목표**: 버튼부터 두 장 저장·미리보기까지 5초 안. 노트북 한 대 20분 목표(사람 손 포함).

**제약**: 업로드가 촬영을 막지 않는다(촬영은 로컬 저장까지만 기다림). 원본은 덮어쓰지 않는다. 비밀은 환경 변수.

**규모**: 하루 맥북 10대 × 약 30장 = 300장 안팎, 장당 수 MB.

## 헌법 점검

*Phase 0 전에 통과, Phase 1 뒤 다시 점검.*

| 원칙 | 점검 | 결과 |
| --- | --- | --- |
| I. 계약이 먼저 | 면·조명·카메라 값은 `codes.yaml` 0.2.0만 쓰고 `codes.py` 한 곳에 모은다. 출고·반납 API는 C1 고정 뒤 그대로 따른다. 아래 "계약 확인 필요" 2건은 코드를 고치지 않고 A에게 알린다. | 통과(확인 대기 2건) |
| II. 서비스 경계 | 사진은 서버를 거치지 않고 MinIO에 직접(trace-raw 직접, 출고·반납은 사전 서명 URL). 부스 탐지는 BO2 범위. | 통과 |
| III. 기록은 고칠 수 없다 | 모든 사진 SHA-256, 덮어쓰기·삭제 없음, 재촬영은 새 번호. 건너뛴 SCR1은 사유로 남김. | 통과 |
| IV. 테스트 | 명세의 수용 시나리오 문장을 테스트 이름으로. `ruff check . && pytest -q`. | 통과 |
| V. 비밀 | MinIO 키·BOOTH client secret은 환경 변수, 로그에 URL 서명 남기지 않음. | 통과 |
| VI. 단순하게 | 웹 프레임워크·큐 서버 없이 표준 라이브러리. 조명 자동 전환·턴테이블 없음. | 통과 |

### 계약 확인 필요 (11/3 고정 전에 A에게)

1. **업로드 URL에 카메라·번호가 없음.** REST API v0의 `POST /captures/sets` 응답 `uploads[]`는 `face`·`light`만 있어 부스처럼 같은 면·조명을 CAM1·CAM2 두 장으로 찍으면 어느 URL이 어느 사진인지 정할 수 없다. 응답에 `camera`(또는 요청 순서와 같은 인덱스)가 필요하다.
2. **SCR1 선택 면과 `complete`의 409.** `POST /captures/sets/{id}/complete`는 빠진 면이 있으면 409인데, 부스의 필수 면은 8면(TOP·BOT·SF·SB·SL·SR·KB·SCR0)이고 SCR1은 선택이다. 부스 세트에서 SCR1이 없어도 409가 나지 않아야 한다. 또 KB는 폰에 없는 면이라 소스(BOOTH·PHONE)마다 필수 면이 다르다.

## 프로젝트 구조

### 문서

```text
specs/001-booth-capture-tool/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── operator-web-api.md   # 운영 화면 ↔ 도구 (로컬 HTTP)
│   └── storage-layout.md     # trace-raw 키·파일명·메타데이터
└── tasks.md                  # /speckit-tasks
```

### 소스

```text
src/trace_booth/
├── __main__.py        # trace-booth serve | shots | retry
├── codes.py           # codes.yaml 0.2.0 값 (Face, Light, Camera)
├── shotlist.py        # 기본 촬영 목록(단계·안내 문구), 필수 8면
├── naming.py          # 기기 ID 검사, 파일명 만들기·읽기
├── camera.py          # Camera 프로토콜, OpenCvCamera, v4l2-ctl 래퍼
├── camera_presets.py  # 조명별 노출·WB·초점 설정 파일 읽기
├── store.py           # SQLite: session, shot, upload 상태
├── session.py         # 촬영 진행(현재 단계, 찍기, 재촬영, SCR1 건너뛰기, 완료 검사)
├── uploader.py        # Uploader 프로토콜, RawBucketUploader(boto3), 재시도 루프
├── web.py             # http.server 핸들러(JSON API + 정적 파일)
└── static/index.html  # 운영 화면

tests/
├── fakes.py                   # FakeCamera, FakeUploader
├── test_naming.py
├── test_shotlist.py
├── test_session.py            # 스토리 1 수용 시나리오
├── test_upload_retry.py       # 스토리 2 수용 시나리오
└── test_web.py                # 운영 API
```

**구조 결정**: 기존 `src/trace_booth/` 패키지에 모듈을 더하는 단일 프로젝트. 출고·반납 모드(`PresignedUploader`, capture-server 클라이언트)는 스토리 3으로 C1 고정 뒤 같은 `uploader.py`에 더한다.

## 복잡도 기록

헌법 위반 없음.
