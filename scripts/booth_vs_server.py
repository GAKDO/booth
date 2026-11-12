"""부스 Jetson(TensorRT)과 학교 서버(onnxruntime)가 같은 사진에 같은 판정을 내는지 비교한다(11/12).

두 단계로 쓴다. 표준 라이브러리만 쓰는 pack은 Jetson(Python 3.10 이상)에서, compare는 학교 서버의
inspect-worker 컨테이너(서버가 실제로 쓰는 best.onnx·탐지 코드)에서 돈다. 새 이미지를 배포하지 않아도 된다.

  Jetson:  python3 scripts/booth_vs_server.py pack <부스 탐지 JSON> --out /tmp/cmp.tar   (이 스크립트도 tar에 넣는다)
  서버:    tar로 푼 폴더를 컨테이너에 복사한 뒤
           docker compose exec -T inspect-worker python - compare /tmp/cmp < cmp/booth_vs_server.py

부스 탐지 JSON은 `trace-booth detect --session <id>`가 남긴 파일(사진마다 원본 px 폴리곤)이다.
같은 사진·같은 클래스에서 상자 겹침(IoU)이 0.3 이상이면 같은 흠집으로 본다.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tarfile
import time
from pathlib import Path

PROBLEM_FREE = {"stain"}  # 부스 화면·서버 등급과 같이 오염만이면 "문제 없음"
MATCH_IOU = 0.3


def pack(booth_json: Path, data_dir: Path, out: Path) -> int:
    """부스 탐지 JSON과 그 사진들을 tar 하나로 묶는다. 사진은 data_dir 아래에서 파일 이름으로 찾는다."""
    result = json.loads(booth_json.read_text(encoding="utf-8"))
    names = {p["filename"] for p in result["photos"]}
    found: dict[str, Path] = {}
    for root, _dirs, files in os.walk(data_dir):
        for name in files:
            if name in names and name not in found:
                found[name] = Path(root) / name
    missing = sorted(names - found.keys())
    if missing:
        print(f"사진을 찾지 못했어요({len(missing)}장): {missing[:3]}", file=sys.stderr)
        return 1
    with tarfile.open(out, "w") as tar:
        tar.add(booth_json, arcname="booth.json")
        tar.add(Path(__file__).resolve(), arcname="booth_vs_server.py")  # 서버에서 같은 스크립트로 비교
        for name, path in sorted(found.items()):
            tar.add(path, arcname=f"photos/{name}")
    out.chmod(0o644)
    print(f"{out}: 사진 {len(found)}장 + 부스 탐지(모델 {result.get('modelVersion')}, 흠집 {result.get('count')}개)")
    return 0


def _box(polygon: list[list[float]]) -> tuple[float, float, float, float]:
    xs = [p[0] for p in polygon]
    ys = [p[1] for p in polygon]
    return min(xs), min(ys), max(xs), max(ys)


def _iou(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0.0
    inter = w * h
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def match(booth: list[dict], server: list[dict]) -> tuple[list[tuple[dict, dict, float]], list[dict], list[dict]]:
    """같은 클래스끼리 IoU가 큰 쌍부터 짝짓는다. (짝, 부스만, 서버만)."""
    pairs = []
    for i, b in enumerate(booth):
        for j, s in enumerate(server):
            if b["class"] == s["class"]:
                iou = _iou(_box(b["polygon"]), _box(s["polygon"]))
                if iou >= MATCH_IOU:
                    pairs.append((iou, i, j))
    used_b: set[int] = set()
    used_s: set[int] = set()
    matched = []
    for iou, i, j in sorted(pairs, reverse=True):
        if i not in used_b and j not in used_s:
            used_b.add(i)
            used_s.add(j)
            matched.append((booth[i], server[j], iou))
    return (matched, [b for i, b in enumerate(booth) if i not in used_b],
            [s for j, s in enumerate(server) if j not in used_s])


def _problem(dets: list[dict]) -> bool:
    return any(d["class"] not in PROBLEM_FREE for d in dets)


def compare(folder: Path) -> int:
    """서버 탐지기(inspect-worker와 같은 load_detector)로 같은 사진을 다시 탐지해 부스 결과와 비교한다."""
    import cv2
    from trace_ml.inspect.yolo_detect import load_detector

    booth = json.loads((folder / "booth.json").read_text(encoding="utf-8"))
    detector = load_detector(os.environ)
    print(f"부스 모델 {booth.get('modelVersion')} / 서버 모델 {detector.model_version}")
    totals = {"photos": 0, "match": 0, "booth_only": 0, "server_only": 0, "same_verdict": 0}
    conf_diff: list[float] = []
    server_seconds = 0.0
    booth_problem = server_problem = False
    print("면·조명·카메라 | 부스 | 서버 | 짝 | 부스만 | 서버만 | 판정")
    for photo in booth["photos"]:
        image = cv2.imread(str(folder / "photos" / photo["filename"]))
        if image is None:
            print(f"사진을 읽지 못함: {photo['filename']}", file=sys.stderr)
            return 1
        start = time.perf_counter()
        server = [{"class": d.cls, "polygon": d.polygon, "confidence": d.confidence} for d in detector.detect(image)]
        server_seconds += time.perf_counter() - start
        mine = photo["detections"]
        pairs, only_b, only_s = match(mine, server)
        same = _problem(mine) == _problem(server)
        booth_problem |= _problem(mine)
        server_problem |= _problem(server)
        totals["photos"] += 1
        totals["match"] += len(pairs)
        totals["booth_only"] += len(only_b)
        totals["server_only"] += len(only_s)
        totals["same_verdict"] += int(same)
        conf_diff += [abs(b["confidence"] - s["confidence"]) for b, s, _ in pairs]
        print(f"{photo['face']}·{photo['light']}·{photo['camera']} | {len(mine)} | {len(server)} | {len(pairs)} | "
              f"{len(only_b)} | {len(only_s)} | {'같음' if same else '다름'}")
        for d in only_b + only_s:
            side = "부스만" if d in only_b else "서버만"
            print(f"    {side}: {d['class']} conf {d['confidence']:.2f} 상자 {[round(v) for v in _box(d['polygon'])]}")
    n = totals["match"] + totals["booth_only"] + totals["server_only"]
    agree = totals["match"] / n * 100 if n else 100.0
    print("---")
    print(f"사진 {totals['photos']}장, 사진별 문제 있음/없음 일치 {totals['same_verdict']}/{totals['photos']}")
    print(f"흠집 후보: 짝 {totals['match']}, 부스만 {totals['booth_only']}, 서버만 {totals['server_only']} "
          f"(일치율 {agree:.1f}%)")
    if conf_diff:
        print(f"짝지은 후보 신뢰도 차이 평균 {sum(conf_diff) / len(conf_diff):.3f}, 최대 {max(conf_diff):.3f}")
    print(f"세트 판정: 부스 {'문제 있음' if booth_problem else '문제 없음'} / 서버 {'문제 있음' if server_problem else '문제 없음'}"
          f" → {'같음' if booth_problem == server_problem else '다름'}")
    print(f"탐지 시간: 부스 {booth.get('seconds')}초(Jetson TensorRT) / 서버 {server_seconds:.2f}초(CPU onnxruntime)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("pack", help="Jetson: 부스 탐지 JSON과 사진을 tar로 묶기")
    p.add_argument("booth_json", type=Path)
    p.add_argument("--data", type=Path, default=Path("~/trace-booth-data"))
    p.add_argument("--out", type=Path, default=Path("/tmp/booth-vs-server.tar"))
    c = sub.add_parser("compare", help="서버 inspect-worker 컨테이너: 같은 사진을 서버 모델로 탐지해 비교")
    c.add_argument("folder", type=Path, help="tar를 푼 폴더(booth.json, photos/)")
    args = parser.parse_args(argv)
    if args.command == "pack":
        return pack(args.booth_json.expanduser(), args.data.expanduser(), args.out.expanduser())
    return compare(args.folder)


if __name__ == "__main__":
    sys.exit(main())
