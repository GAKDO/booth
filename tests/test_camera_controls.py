from trace_booth.camera import control_steps, run_v4l2_ctl


def test_수동_노출과_WB는_자동을_한번_켠_뒤_값을_넣는다() -> None:
    steps = control_steps(
        {"exposure_time_absolute": 2047, "gain": 90, "auto_exposure": 1,
         "white_balance_temperature": 7500, "white_balance_automatic": 0}
    )
    assert steps[0] == {"auto_exposure": 3, "white_balance_automatic": 1}
    assert list(steps[1])[:2] == ["auto_exposure", "white_balance_automatic"]
    assert steps[1]["exposure_time_absolute"] == 2047


def test_자동으로_둘_때는_한_묶음만_넣는다() -> None:
    assert control_steps({"auto_exposure": 3, "gain": 0}) == [{"auto_exposure": 3, "gain": 0}]


def test_값이_없으면_아무것도_넣지_않는다(monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr("subprocess.run", lambda args, **kw: calls.append(args))
    run_v4l2_ctl("/dev/video0", {})
    assert calls == []


def test_v4l2_ctl을_묶음마다_차례로_부른다(monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr("subprocess.run", lambda args, **kw: calls.append(args))
    run_v4l2_ctl("/dev/video0", {"auto_exposure": 1, "exposure_time_absolute": 312})
    assert calls == [
        ["v4l2-ctl", "-d", "/dev/video0", "-c", "auto_exposure=3"],
        ["v4l2-ctl", "-d", "/dev/video0", "-c", "auto_exposure=1,exposure_time_absolute=312"],
    ]
