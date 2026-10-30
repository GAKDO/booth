# booth

TRACE(노트북 렌탈 상태 기록 서비스)의 **booth**: 부스 Jetson 앱(촬영·조명 전환·TensorRT 탐지)과 학습 데이터 촬영 도구

- 담당: B
- 설계: [GAKDO/docs](https://github.com/GAKDO/docs) — 촬영 면·조명 코드는 contracts의 codes.yaml
- 기술: Python 3.12

## 실행

```bash
python -m pip install -e ".[dev]"
```

```bash
python -m trace_booth
```

## 테스트

```bash
python -m ruff check . && python -m pytest
```

## CI

| 워크플로 | 언제 | 하는 일 |
|---|---|---|
| `python-ci` | PR, develop·main push | ruff + pytest (ubuntu) |
| `비밀값 검사`·`release`·알림 | | 다른 레포와 같음 |
