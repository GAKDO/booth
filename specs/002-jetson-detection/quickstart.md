# 빠른 확인: BO2 Jetson 흠집 탐지

## 개발 PC (GPU 없이)

```bash
python -m pip install -e ".[dev]"
ruff check . && pytest -q   # 가짜 탐지기로 타일·중복 제거·세트 탐지·detect 명령 확인
```

## Jetson (T008, 한 세트 30초 실측)

1. ML 쪽에서 받은 가중치 `best.pt`(YOLO11-seg, 클래스 순서 scratch·dent·crack·stain)를 Jetson에 둔다.
2. Jetson에서 엔진을 만든다(엔진은 만든 기기에서만 돈다).

   ```bash
   pip install -e ".[detect]"
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

결과 JSON의 폴리곤은 원본 사진 px(`"coordinates": "photo_px"`)이다. capture-server로 올리기(T009)는 detections 계약 질문에 A가 답한 뒤 붙인다.
