"""판정 시간 줄이기(11/6 강사님 피드백 1): 찍는 동안 미리 탐지, 판정 조명 고르기, bench 구간 시간."""

from __future__ import annotations

import json

import numpy as np
import pytest
from test_detect import SquareDetector
from test_quick_mode import CHECKPOINT, DEVICE, INFO, RENTAL, _loader

from trace_booth.__main__ import _judge_lights
from trace_booth.quick import QuickInspector, result_json
from trace_booth.shotlist import QUICK_SHOTLIST


class CountingDetector(SquareDetector):
    """SquareDetector + 어느 사진을 몇 번 봤는지 세기(타일 수로)."""


def _inspector(service, detector, defect_on="BOT_L1_CAM1", **kw) -> QuickInspector:
    return QuickInspector(service.store, lambda: (detector, INFO), _loader(defect_on), lambda: "now",
                          background=False, **kw)


def _shoot_all(service, inspector: QuickInspector | None) -> int:
    """화면처럼 찍을 때마다 미리 탐지를 부르고 끝까지 찍은 뒤 완료한다."""
    s = service.start(DEVICE, "QUICK", RENTAL, CHECKPOINT)
    while (step := service.view(s.id).step) is not None:
        if step.captures:
            service.shoot(s.id)
            if inspector is not None:
                inspector.prefetch(s.id)
        service.advance(s.id)
    return service.complete(s.id).id


def test_찍는_동안_미리_탐지하면_완료_때는_다시_돌리지_않고_판정은_같다(service):
    detector = CountingDetector()
    inspector = _inspector(service, detector)
    sid = _shoot_all(service, inspector)
    before = detector.calls
    assert before == 12 * 8  # 12장 × 1920×1080 타일 8개, 찍는 동안 모두 끝남

    inspector.start(sid)

    assert detector.calls == before  # 완료 때 더 돌지 않음
    r = result_json(service.store, sid, shooting=False)
    assert r["status"] == "DONE" and r["verdict"] == "DEFECT" and r["count"] == 1 and len(r["photos"]) == 12
    data = json.loads(service.store.get_quick_result(sid).result_json)
    assert data["predetected"] == 12 and data["lights"] is None


def test_미리_탐지를_끄면_완료_때_전부_돌리고_결과는_같다(service):
    detector = CountingDetector()
    inspector = _inspector(service, detector, predetect=False)
    sid = _shoot_all(service, inspector)
    assert detector.calls == 0
    inspector.start(sid)
    assert detector.calls == 12 * 8
    r = result_json(service.store, sid, shooting=False)
    assert r["verdict"] == "DEFECT" and r["count"] == 1


def test_다시_찍은_사진은_새로_탐지하고_예전_장은_판정에_넣지_않는다(service):
    detector = CountingDetector()
    inspector = _inspector(service, detector, defect_on="BOT_L1_CAM1_01")  # 첫 장에만 흠집
    s = service.start(DEVICE, "QUICK", RENTAL, CHECKPOINT)
    service.shoot(s.id)
    inspector.prefetch(s.id)
    assert any(p.found for p in inspector._done[s.id].values())  # 첫 장은 흠집으로 미리 잡혀 있었음
    service.retake(s.id)  # BOT_L1을 다시 찍음(새 번호, 흠집 없음)
    inspector.prefetch(s.id)
    service.advance(s.id)
    while (step := service.view(s.id).step) is not None:
        if step.captures:
            service.shoot(s.id)
        service.advance(s.id)
    service.complete(s.id)

    inspector.start(s.id)

    r = result_json(service.store, s.id, shooting=False)
    assert r["verdict"] == "CLEAN" and len(r["photos"]) == 12


def test_완료한_뒤에는_미리_탐지가_돌지_않는다(service):
    detector = CountingDetector()
    inspector = _inspector(service, detector, predetect=True)
    sid = _shoot_all(service, None)  # 찍는 동안 부르지 않음
    inspector.prefetch(sid)
    assert detector.calls == 0


def test_판정_조명을_L3만_고르면_L3_사진_4장만_판정한다(service):
    detector = CountingDetector()
    inspector = _inspector(service, detector, defect_on="TOP_L3_CAM2", lights=frozenset({"L3"}))
    sid = _shoot_all(service, inspector)
    assert detector.calls == 4 * 8

    inspector.start(sid)

    r = result_json(service.store, sid, shooting=False)
    assert len(r["photos"]) == 4 and {p["light"] for p in r["photos"]} == {"L3"}
    assert r["verdict"] == "DEFECT" and r["faces"] == {"BOT": 0, "TOP": 1}
    assert json.loads(service.store.get_quick_result(sid).result_json)["lights"] == ["L3"]


def test_판정_조명을_L3만_고르면_L1에만_보인_흠집은_판정에_넣지_않는다(service):
    inspector = _inspector(service, CountingDetector(), defect_on="BOT_L1_CAM1", lights=frozenset({"L3"}))
    sid = _shoot_all(service, inspector)
    inspector.start(sid)
    assert result_json(service.store, sid, shooting=False)["verdict"] == "CLEAN"


def test_판정_조명_설정은_비우면_전부이고_쉼표로_여러_개_틀리면_멈춘다():
    assert _judge_lights("") is None
    assert _judge_lights(" l3 ") == frozenset({"L3"})
    assert _judge_lights("L1,L3") == frozenset({"L1", "L3"})
    with pytest.raises(SystemExit):
        _judge_lights("L9")


def test_빠른_검사_목록의_조명은_L1_L2_L3이라_L3만_고르면_면마다_2장씩_남는다():
    lights = [s.light.value for s in QUICK_SHOTLIST if s.light is not None for _ in s.cameras]
    assert sorted(set(lights)) == ["L1", "L2", "L3"] and lights.count("L3") == 4


class TimedDetector(SquareDetector):
    """구간 시간을 모으는 탐지기 흉내(bench가 어디가 느린지 보여 주는지)."""

    def __init__(self) -> None:
        from trace_booth.detect.engine import Stats

        super().__init__()
        self.stats = Stats()

    def infer(self, tile):
        self.stats.tiles += 1
        self.stats.run += 0.05
        self.stats.post += 0.01
        return super().infer(tile)


def test_bench는_탐지기가_모은_구간_시간을_타일당_ms로_보여_준다(tmp_path, capsys):
    from trace_booth.__main__ import bench

    engine = tmp_path / "best.engine"
    engine.write_bytes(b"x")
    (tmp_path / "model.json").write_text(json.dumps({"modelVersion": "v001", "classes": list(INFO.classes)}))
    image = np.zeros((1080, 1920), dtype=np.uint8)

    code = bench(str(engine), ["a.jpg"] * 2, None, 2, make_detector=lambda _: TimedDetector(), load=lambda _: image)

    out = capsys.readouterr().out
    assert code == 0 and "타일 16개/회" in out and "엔진 50ms" in out and "후처리 10ms" in out


def test_판정_조명에_맞는_사진이_없으면_문제_없음이_아니라_실패로_남긴다(service):
    inspector = _inspector(service, CountingDetector(), lights=frozenset({"L4"}))
    sid = _shoot_all(service, inspector)
    inspector.start(sid)
    r = result_json(service.store, sid, shooting=False)
    assert r["status"] == "FAILED" and r["verdict"] is None and "판정할 사진이 없음" in r["error"]
