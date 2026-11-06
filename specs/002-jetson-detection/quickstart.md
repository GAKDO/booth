# 빠른 확인: BO2 Jetson 흠집 탐지

## 개발 PC (GPU 없이)

```bash
python -m pip install -e ".[dev]"
ruff check . && pytest -q   # 가짜 탐지기로 타일·중복 제거·세트 탐지·detect 명령 확인
```

## Jetson (T008, 엔진 빌드와 시간 재기)

jetson2(11/3 확인): L4T R39, Python 3.12, TensorRT 10.16(시스템 파이썬), `/usr/src/tensorrt/bin/trtexec` 있음, torch·ultralytics 없음.
그래서 엔진은 `trtexec --fp16`으로 만들고, 추론은 tensorrt 파이썬 + libcudart(ctypes) + numpy·OpenCV로 한다(torch 불필요).
`~/trace-venv`는 `include-system-site-packages = true`라 시스템 tensorrt·cv2를 그대로 쓴다. 코드는 `cd ~/booth && git pull`만 하면 된다.

1. ONNX와 model.json을 받는다(학습 스레드 결과: `trace-models/v001/<실행>/best.onnx`, `model.json`). 받을 때는 trace-jetson 키(읽기 전용)를 쓰고, 촬영 도구의 `MINIO_*`(trace-capture)와 섞지 않는다.

   ```bash
   mc alias set models http://storage.java21.net:8000   # 키는 물어볼 때 직접 입력
   mc ls --recursive models/trace-models/v001/
   mkdir -p ~/trace-models/v001-src && mc cp --recursive models/trace-models/v001/<실행>/ ~/trace-models/v001-src/
   ```

   S3 API가 HTTP라 모델 파일과 서명된 요청은 암호화되지 않는다(secret key 자체는 SigV4라 보내지 않음). 학교 내부망에서만 받는다.
2. 엔진을 만든다(엔진은 만든 기기에서만 돈다. Orin Nano에서 몇 분 걸림). model.json의 클래스가 codes.yaml과 다르면 trtexec를 부르기 전에 멈춘다.

   ```bash
   cd ~/booth && git pull
   ~/trace-venv/bin/python -m trace_booth build-engine --onnx ~/trace-models/v001-src/best.onnx --out ~/trace-models/v001
   # 엔진 ~/trace-models/v001/best.engine (..MB, ..초), 로그 ~/trace-models/v001/best.trtexec.log
   ```

   v001 전에는 기본 yolo11n-seg(COCO)로 흐름만 확인한다. `--test`는 클래스 검사 없이 `testOnly` model.json을 쓰고, 이 모델 결과는 서버에 올리지 않는다.

   ```bash
   ~/trace-venv/bin/python -m trace_booth build-engine --onnx ~/yolo11n-seg.onnx --out ~/trace-models/test --test
   ```
3. 시간을 잰다. 엔진 올리기 1번, 첫 실행(데우기) 1번, 이어서 `--runs`번 돌려 중간값을 목표 30초·기준 60초와 비교한다(넘으면 종료 코드 2).

   ```bash
   export TRACE_BOOTH_ENGINE=~/trace-models/v001/best.engine
   ~/trace-venv/bin/python -m trace_booth bench --images bot1.jpg bot2.jpg top1.jpg top2.jpg --runs 5
   ~/trace-venv/bin/python -m trace_booth bench --session <sessionId>   # 찍은 세션의 마지막 장들로
   ~/trace-venv/bin/python -m trace_booth bench --session <sessionId> --lights L3   # L3만 판정할 때 시간
   ```

   bench는 탐지만 잰다. 끝에 타일당 "입력 만들기·엔진·후처리" ms가 나와 어디가 느린지 보인다(11/6). 완료 뒤 기다린 탐지 시간은 결과의 `seconds`(서버에는 `inferenceMs`), 촬영 시작→판정 전체는 `boothTotalMs`로 남는다. 아래 `Timeline`은 손으로 구간을 잴 때 쓰는 예시다.

   1분을 넘을 때 순서(11/6 강사님 피드백 1): ① 찍는 동안 미리 탐지(기본 켬, `TRACE_BOOTH_PREDETECT=0`이면 끔) → ② `TRACE_BOOTH_DETECT_LIGHTS=L3`(12장 → 4장) → ③ 엔진 ms가 크면 yolo11s-seg로 다시 학습·내보내기, 후처리 ms가 크면 `min_confidence`를 올림 → ④ INT8(보정 사진 필요).

4. 찍은 세션 하나를 탐지해 로컬 JSON으로 남기고, 올라간 세트가 있으면 결과도 올린다.

   ```bash
   ~/trace-venv/bin/python -m trace_booth detect --session <sessionId>
   ```

GPU 없는 PC(맥 등)에서는 같은 ONNX를 onnxruntime으로 돌려 결과를 맞춰 볼 수 있다: `pip install -e ".[onnx]"` 뒤 `trace-booth bench --engine best.onnx --images ...`(ONNX 옆에 model.json). 11/3 기본 yolo11n-seg로 ultralytics 결과와 비교해 마스크 IoU 0.86~0.99, 상자·신뢰도 같음.

결과 JSON의 폴리곤은 원본 사진 px(`"coordinates": "photo_px"`)이다. 출고·반납 세트가 이미 올라간 세션이면(`TRACE_API_URL`과 부스 자격이 있을 때) 같은 결과를 `POST /captures/sets/{setId}/detections`로 그 세트에 올린다. 탐지마다 `camera`가 붙고 폴리곤은 원본 px 그대로다(펼치기는 서버·worker). 올리지 못하면 로컬 JSON은 남고 종료 코드 3, 올린 세트가 없으면 로컬에만 남긴다. 다시 돌리면 다시 보내고, 서버는 그 세트의 부스 탐지를 마지막 요청으로 통째로 바꾼다(중복 없음). 등급이 매겨진 세트면 409 `CAPTURE_SET_CLOSED`로 거절되고 종료 코드 3이다(A 11/2).

## 빠른 검사 모드에서 부르기(파이썬)

엔진은 serve를 띄울 때 한 번만 올리고(몇 초 걸림) 검사마다 다시 쓴다. 사진은 찍은 OpenCV 배열을 그대로 넘긴다.

```python
from pathlib import Path
from trace_booth.detect.engine import ModelInfo, open_detector
from trace_booth.detect.runner import Photo, detect_photos
from trace_booth.detect.timing import Timeline

engine = Path("~/trace-models/v001/best.engine").expanduser()
info = ModelInfo.load(engine.parent / "model.json")   # DetectError: 모델 정보가 맞지 않음
detector = open_detector(engine)                       # DetectError: 엔진·tensorrt·CUDA 없음

t = Timeline()                     # 버튼 누를 때
# ... BOT 촬영
t.mark("촬영 BOT")
# ... TOP 촬영
t.mark("촬영 TOP")
result = detect_photos([Photo("BOT", "L1", "CAM1", img1), Photo("BOT", "L1", "CAM2", img2),
                        Photo("TOP", "L1", "CAM1", img3), Photo("TOP", "L1", "CAM2", img4)], detector, info)
t.mark("탐지")
result.has_problem      # 부스 화면 "문제 있음/없음"
result.to_json()        # 화면·로컬 기록용: photos[].detections[].polygon(원본 px), hasProblem, seconds
result.detections()     # POST /captures/sets/{setId}/detections 의 detections(face·light·camera·class·polygon·confidence)
info.test_only          # True면 시험용 모델: 서버에 올리지 않는다
t.to_json()             # {"total": 12.3, "grade": "OK|SLOW|OVER", "steps": [...]}
```

탐지기는 스레드 하나에서만 부른다(엔진 컨텍스트 하나).
