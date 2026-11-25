"""이 파일이 확인하는 것: 웹캠(C920) 설정 넣기(v4l2-ctl). 노출·게인·화이트밸런스를 고정값으로 넣고 다시 읽어 확인한다.
조명마다 같은 설정으로 찍어야 사진끼리 비교할 수 있기 때문. 실제 카메라 대신 가짜 명령을 쓴다.
"""

import subprocess

import pytest

from trace_booth.camera import ControlMismatch, control_order, parse_controls, run_v4l2_ctl

WANT = {"exposure_time_absolute": 2047, "gain": 90, "auto_exposure": 1,
        "white_balance_temperature": 7500, "white_balance_automatic": 0}


# 테스트 도구: 가짜 v4l2-ctl. 어떤 값은 처음 한 번 빠뜨리고(flaky), 어떤 값은 늘 빠뜨린다(broken).
class FakeCamera:
    """v4l2-ctl 대신. flaky 컨트롤은 처음 한 번 값을 빼먹고, broken 컨트롤은 늘 빼먹는다."""

    def __init__(self, flaky: set[str] | None = None, broken: set[str] | None = None) -> None:
        self.values: dict[str, int] = {}
        self.flaky = set(flaky or ())
        self.broken = set(broken or ())
        self.sets: list[str] = []

    def __call__(self, args: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
        if args[3] == "-c":
            name, value = args[4].split("=")
            self.sets.append(args[4])
            if name in self.flaky:
                self.flaky.discard(name)
            elif name not in self.broken:
                self.values[name] = int(value)
            return subprocess.CompletedProcess(args, 0, "", "")
        text = "\n".join(f"{k}: {self.values.get(k, 0)}" for k in args[4].split(","))
        return subprocess.CompletedProcess(args, 0, text, "")


# [상황] 넣을 설정의 순서를 정한다.
# [기대] 자동 노출·자동 WB 상태를 먼저 맞추고, 그 두 값을 맨 앞에 넣는다(순서가 틀리면 카메라가 수동 값을 무시함).
def test_수동_노출과_WB는_자동을_먼저_켜고_자동_컨트롤부터_하나씩_넣는다() -> None:
    pre, order = control_order(WANT)
    assert pre == {"auto_exposure": 3, "white_balance_automatic": 1}
    assert [k for k, _ in order][:2] == ["auto_exposure", "white_balance_automatic"]
    assert len(order) == len(WANT)


# [상황] v4l2-ctl이 출력한 현재 설정 글자를 해석한다.
# [기대] 이름별 숫자로 바꾼다.
def test_v4l2_ctl_읽기_출력을_숫자로_바꾼다() -> None:
    text = "auto_exposure: 1 (Manual Mode)\ngain: 90\nexposure_time_absolute: 2047\n"
    assert parse_controls(text) == {"auto_exposure": 1, "gain": 90, "exposure_time_absolute": 2047}


# [상황] 설정을 넣는다.
# [기대] 한 번에 하나씩 넣고(쉼표로 묶지 않음) 들어간 값을 확인한다.
def test_값을_하나씩_넣고_확인한다() -> None:
    cam = FakeCamera()
    run_v4l2_ctl("/dev/video0", WANT, run=cam)
    assert cam.sets[:2] == ["auto_exposure=3", "white_balance_automatic=1"]
    assert all("," not in s for s in cam.sets)
    assert cam.values["gain"] == 90


# [상황] 게인 값이 처음엔 안 들어간다.
# [기대] 한 번 더 넣어 맞춘다.
def test_빠진_값은_한_번_더_넣는다() -> None:
    cam = FakeCamera(flaky={"gain"})
    run_v4l2_ctl("/dev/video0", WANT, run=cam)
    assert cam.values["gain"] == 90


# [상황] 게인 값이 두 번 넣어도 안 들어간다.
# [기대] 설정 불일치 오류로 실패한다(설정이 틀린 채 찍지 않게).
def test_두_번_넣어도_다르면_실패한다() -> None:
    with pytest.raises(ControlMismatch):
        run_v4l2_ctl("/dev/video0", WANT, run=FakeCamera(broken={"gain"}))


# [상황] 넣을 설정이 비어 있다.
# [기대] 아무 명령도 보내지 않는다.
def test_값이_없으면_아무것도_넣지_않는다() -> None:
    cam = FakeCamera()
    run_v4l2_ctl("/dev/video0", {}, run=cam)
    assert cam.sets == []


# 테스트 도구: 넣은 값과 조금 다르게 읽어 주는 가짜 카메라(실제 C920이 반올림해 돌려주는 상황).
class OffByCamera(FakeCamera):
    """넣은 값과 조금 다르게 읽어 주는 카메라."""

    def __init__(self, offsets: dict[str, int]) -> None:
        super().__init__()
        self.offsets = offsets

    def __call__(self, args: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
        r = super().__call__(args, **kw)
        if args[3] == "-c":
            name = args[4].split("=")[0]
            if name in self.offsets:
                self.values[name] += self.offsets[name]
        return r


# [상황] 화이트밸런스를 넣었더니 20K 다르게 읽힌다.
# [기대] 허용 범위라 통과한다.
def test_화이트밸런스를_20K_다르게_읽어도_통과한다() -> None:
    run_v4l2_ctl("/dev/video0", WANT, run=OffByCamera({"white_balance_temperature": -20}))


# [상황] 화이트밸런스가 50K 넘게 다르게 읽힌다.
# [기대] 설정 불일치로 실패한다.
def test_화이트밸런스가_50K보다_다르면_실패한다() -> None:
    with pytest.raises(ControlMismatch):
        run_v4l2_ctl("/dev/video0", WANT, run=OffByCamera({"white_balance_temperature": -100}))


# [상황] 게인이 2 다르게 읽힌다.
# [기대] 설정 불일치로 실패한다(게인은 엄격하게 봄).
def test_gain은_2_다르면_실패한다() -> None:
    with pytest.raises(ControlMismatch):
        run_v4l2_ctl("/dev/video0", WANT, run=OffByCamera({"gain": -2}))
