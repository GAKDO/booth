"""이 파일이 확인하는 것: 판정 시간 줄이기(11/6 강사님 피드백). 심사 목표 30초 안에 판정하기 위해
  - 찍는 동안 미리 탐지: 사진이 찍히는 대로 바로 흠집을 찾아, 완료 때 다시 돌리지 않음
  - 판정 조명 고르기: 흠집이 잘 보이는 조명(예: L3) 사진만 판정
  - bench: 어느 구간(엔진·후처리)이 느린지 시간 보기

판정 시간 줄이기(11/6 강사님 피드백 1): 찍는 동안 미리 탐지, 판정 조명 고르기, bench 구간 시간.
"""

from __future__ import annotations

import json

import numpy as np
import pytest
from test_detect import SquareDetector
from test_quick_mode import CHECKPOINT, DEVICE, INFO, RENTAL, _loader

from trace_booth.__main__ import _judge_lights
from trace_booth.quick import QuickInspector, result_json
from trace_booth.shotlist import QUICK_SHOTLIST


# 테스트 도구: 가짜 탐지기(정해 둔 사진에 네모 흠집) + 몇 번 돌았는지(조각 수) 세기.
class CountingDetector(SquareDetector):
    """SquareDetector + 어느 사진을 몇 번 봤는지 세기(타일 수로)."""


def _inspector(service, detector, defect_on="BOT_L1_CAM1", **kw) -> QuickInspector:
    return QuickInspector(service.store, lambda: (detector, INFO), _loader(defect_on), lambda: "now",
                          background=False, **kw)


# 테스트 도구: 화면처럼 찍을 때마다 미리 탐지를 부르며 끝까지 찍고 완료한다.
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


# [상황] 찍는 동안 미리 탐지를 켜고 12장을 찍는다.
# [기대] 찍는 동안 12장 × 조각 8개를 모두 끝내고, 완료 때는 더 돌지 않는다. 판정은 흠집 1개(DEFECT).
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


# [상황] 미리 탐지 없이 찍고 완료한다.
# [기대] 완료 때 전부 돌리고, 결과는 미리 탐지한 경우와 같다.
def test_미리_탐지를_끄면_완료_때_전부_돌리고_결과는_같다(service):
    detector = CountingDetector()
    inspector = _inspector(service, detector, predetect=False)
    sid = _shoot_all(service, inspector)
    assert detector.calls == 0
    inspector.start(sid)
    assert detector.calls == 12 * 8
    r = result_json(service.store, sid, shooting=False)
    assert r["verdict"] == "DEFECT" and r["count"] == 1


# [상황] 첫 장에서 흠집이 미리 잡혔는데, 그 장을 다시 찍었다(새 사진엔 흠집 없음).
# [기대] 다시 찍은 사진을 새로 탐지하고 예전 장은 판정에서 빼서 CLEAN이 된다.
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


# [상황] 촬영을 완료한 뒤 미리 탐지를 부른다.
# [기대] 돌지 않는다.
def test_완료한_뒤에는_미리_탐지가_돌지_않는다(service):
    detector = CountingDetector()
    inspector = _inspector(service, detector, predetect=True)
    sid = _shoot_all(service, None)  # 찍는 동안 부르지 않음
    inspector.prefetch(sid)
    assert detector.calls == 0


# [상황] 판정 조명을 L3만 고른다.
# [기대] L3 사진 4장만 탐지·판정하고, 결과에 고른 조명이 기록된다.
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


# [상황] 흠집이 L1 사진에서만 보이고 판정 조명은 L3다.
# [기대] 판정은 CLEAN이다.
def test_판정_조명을_L3만_고르면_L1에만_보인_흠집은_판정에_넣지_않는다(service):
    inspector = _inspector(service, CountingDetector(), defect_on="BOT_L1_CAM1", lights=frozenset({"L3"}))
    sid = _shoot_all(service, inspector)
    inspector.start(sid)
    assert result_json(service.store, sid, shooting=False)["verdict"] == "CLEAN"


# [상황] 판정 조명 설정을 비우거나, 소문자·공백, 쉼표로 여러 개, 틀린 값으로 준다.
# [기대] 비우면 전부, 나머지는 정리해 읽고, 틀린 값이면 시작하지 않는다.
def test_판정_조명_설정은_비우면_전부이고_쉼표로_여러_개_틀리면_멈춘다():
    assert _judge_lights("") is None
    assert _judge_lights(" l3 ") == frozenset({"L3"})
    assert _judge_lights("L1,L3") == frozenset({"L1", "L3"})
    with pytest.raises(SystemExit):
        _judge_lights("L9")


# [상황] 빠른 검사 목록의 조명을 센다.
# [기대] L1·L2·L3이고 L3 사진은 4장(면마다 카메라 2대).
def test_빠른_검사_목록의_조명은_L1_L2_L3이라_L3만_고르면_면마다_2장씩_남는다():
    lights = [s.light.value for s in QUICK_SHOTLIST if s.light is not None for _ in s.cameras]
    assert sorted(set(lights)) == ["L1", "L2", "L3"] and lights.count("L3") == 4


# 테스트 도구: 구간 시간을 모으는 가짜 탐지기.
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


# [상황] bench 명령을 돌린다.
# [기대] 회당 조각 수와 조각당 엔진·후처리 시간을 ms로 보여 준다.
def test_bench는_탐지기가_모은_구간_시간을_타일당_ms로_보여_준다(tmp_path, capsys):
    from trace_booth.__main__ import bench

    engine = tmp_path / "best.engine"
    engine.write_bytes(b"x")
    (tmp_path / "model.json").write_text(json.dumps({"modelVersion": "v001", "classes": list(INFO.classes)}))
    image = np.zeros((1080, 1920), dtype=np.uint8)

    code = bench(str(engine), ["a.jpg"] * 2, None, 2, make_detector=lambda _: TimedDetector(), load=lambda _: image)

    out = capsys.readouterr().out
    assert code == 0 and "타일 16개/회" in out and "엔진 50ms" in out and "후처리 10ms" in out


# [상황] 판정 조명에 해당하는 사진이 하나도 없다.
# [기대] "문제 없음"이 아니라 "판정할 사진이 없음" 실패로 남긴다(검사 안 한 것을 깨끗하다고 하지 않게).
def test_판정_조명에_맞는_사진이_없으면_문제_없음이_아니라_실패로_남긴다(service):
    inspector = _inspector(service, CountingDetector(), lights=frozenset({"L4"}))
    sid = _shoot_all(service, inspector)
    inspector.start(sid)
    r = result_json(service.store, sid, shooting=False)
    assert r["status"] == "FAILED" and r["verdict"] is None and "판정할 사진이 없음" in r["error"]
