# 빠른 확인: BO2 Jetson 흠집 탐지

## 개발 PC (GPU 없이)

```bash
python -m pip install -e ".[dev]"
ruff check . && pytest -q   # 가짜 탐지기로 타일·중복 제거·세트 탐지·detect 명령 확인
```

## Jetson (T008, 한 세트 30초 실측)

1. ML 쪽에서 받은 가중치 `best.pt`(YOLO11-seg, 클래스 순서 scratch·dent·crack·stain)를 Jetson에 둔다. MinIO `trace-models`에서 받을 때는 trace-jetson 키(읽기 전용)를 쓰고, 촬영 도구의 `MINIO_*`(trace-capture)와 섞지 않는다.

   ```bash
   mc alias set models http://storage.java21.net:8000   # 키는 물어볼 때 직접 입력
   mc ls --recursive models/trace-models/               # 올라간 가중치의 실제 경로 확인
   mc cp models/trace-models/<위에서 본 경로>/best.pt ./best.pt
   ```

   S3 API가 HTTP라 모델 파일과 서명된 요청은 암호화되지 않는다(secret key 자체는 SigV4라 보내지 않음). 학교 내부망에서만 받는다. 자세한 조건은 capture/README.md의 MinIO 절.
2. Jetson에서 엔진을 만든다(엔진은 만든 기기에서만 돈다).

   ```bash
   # pypi가 열린 망이 필요하다(detect extra가 numpy·ultralytics를 받는다). 학교 와이파이에서는 휴대폰 핫스팟 등으로
   ~/trace-venv/bin/pip install -e ~/booth"[detect]"   # capture/README.md 1장의 가상환경
   yolo export model=best.pt format=engine half=True imgsz=640 device=0
   mkdir -p ~/trace-models && mv best.engine ~/trace-models/
   ```

3. 엔진 옆에 `model.json`을 둔다. 클래스는 codes.yaml `inspection.defect_class`와 같아야 시작한다.

   ```json
   { "modelVersion": "yolo11s-seg-v001-trt-fp16", "classes": ["scratch", "dent", "crack", "stain"] }
   ```

4. 운영 화면으로 한 세트를 찍은 뒤, 화면의 세션 ID(`sessionId`, 21자)로 탐지한다.

   ```bash
   export TRACE_BOOTH_ENGINE=~/trace-models/best.engine
   trace-booth detect --session <sessionId>
   # 사진 28장, 흠집 N개, 12.3초 → ~/trace-booth-data/MB01/detections/<sessionId>_<modelVersion>.json
   ```

5. 걸린 시간이 30초 안인지 본다(넘으면 경고와 종료 코드 2). 첫 실행은 엔진을 올리느라 느리므로 두 번째 값을 적는다.

결과 JSON의 폴리곤은 원본 사진 px(`"coordinates": "photo_px"`)이다. 출고·반납 세트가 이미 올라간 세션이면(`TRACE_API_URL`과 부스 자격이 있을 때) 같은 결과를 `POST /captures/sets/{setId}/detections`로 그 세트에 올린다. 탐지마다 `camera`가 붙고 폴리곤은 원본 px 그대로다(펼치기는 서버·worker). 올리지 못하면 로컬 JSON은 남고 종료 코드 3, 올린 세트가 없으면 로컬에만 남긴다. 다시 돌리면 다시 보내고, 서버는 그 세트의 부스 탐지를 마지막 요청으로 통째로 바꾼다(중복 없음). 등급이 매겨진 세트면 409 `CAPTURE_SET_CLOSED`로 거절되고 종료 코드 3이다(A 11/2).
