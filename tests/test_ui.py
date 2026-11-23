# 이 파일이 확인하는 것: 부스 운영 화면(태블릿)을 실제 브라우저(Playwright)로 하나하나 눌러 보는 시험.
# 진짜 카메라·Jetson 대신 가짜 카메라·가짜 탐지기로 부스 서버를 띄우고, 운영자가 하는 그대로 누른다.
# 기다리는 기기는 두 대다: MBA13-M2(TEST-0001, 출고 OUT)와 LG-GRAM16(GRAM-0002, 반납 RET).
# 확인하는 범위: 기기 고르기 확인 시트, 촬영·다시 찍기·이어 찍기, 빠른 검사 판정 화면, 실시간 화면,
# 출고 탭의 세 ID(기기·렌탈·시점) 입력 검사, 6자리 부스 코드, 검사 범위(빠른/전체), 반납 때 방식 고정.
# 화면 크기는 태블릿(1280px)과 폰(375px) 둘 다 본다.

"""운영 화면(태블릿) 하네스: 가짜 카메라·탐지기로 서버를 띄우고 실제 브라우저(Playwright)로 눌러 본다.

- playwright가 없으면 건너뛴다(Jetson·개발 PC). CI는 TRACE_UI_TESTS=1로 돌려 빠지면 실패한다.
- 브라우저 경로를 바꾸려면 TRACE_UI_CHROMIUM(예: /opt/pw-browsers/chromium)
"""

from __future__ import annotations

import os
import re
import threading

import pytest
from fakes import FakeCamera
from test_detect import SquareDetector
from test_quick_mode import INFO, NOW, _loader

from trace_booth.camera_presets import Presets
from trace_booth.capture_client import ApiError
from trace_booth.codes import Camera
from trace_booth.quick import QuickInspector
from trace_booth.rentals import WaitingDevice
from trace_booth.session import CaptureService
from trace_booth.store import Store
from trace_booth.web import App, make_server

if os.environ.get("TRACE_UI_TESTS") == "1":
    from playwright.sync_api import Page, expect, sync_playwright
else:
    sync_api = pytest.importorskip("playwright.sync_api", reason="playwright가 없어 화면 하네스를 건너뜀")
    Page, expect, sync_playwright = sync_api.Page, sync_api.expect, sync_api.sync_playwright

MBA = WaitingDevice("Ws0HLv6QRbDILPWOcz5I2", "aAXQFO2Vz6ZTFbPctC2dq", "TEST-0001", "MBA13-M2",
                    "h40b9AN9xMQhXRHrOyJWB", "OUT")
GRAM = WaitingDevice("Xs0HLv6QRbDILPWOcz5I2", "bAXQFO2Vz6ZTFbPctC2dq", "GRAM-0002", "LG-GRAM16",
                     "i40b9AN9xMQhXRHrOyJWB", "RET")
TABLET = {"width": 1280, "height": 800}
LOCKED: dict[str, str] = {}  # 렌탈 → 출고 때 검사 방식(가짜 capture-server)


# 테스트 도구: 가짜 부스 코드 확인. 123456은 MBA 기기를 돌려주고, 999999는 만료, 나머지는 틀린 코드다.
def _redeem(code: str) -> WaitingDevice:
    """가짜 rental-server 부스 코드: 123456만 맞고, 999999는 만료, 나머지는 틀림"""
    if code == "123456":
        return MBA
    if code == "999999":
        raise ApiError("부스 코드", 410, "BOOTH_CODE_EXPIRED")
    raise ApiError("부스 코드", 400, "BOOTH_CODE_INVALID")


# 테스트 도구: 가짜 카메라 두 대·가짜 탐지기로 부스 서버를 띄우고 주소를 돌려준다(시험이 끝나면 끈다).
@pytest.fixture
def base(tmp_path):
    values = {light: {"exposure_time_absolute": 100} for light in ["L1", "L2", "L3", "L4", "OFF"]}
    cams = {c: FakeCamera(c) for c in (Camera.CAM1, Camera.CAM2)}
    service = CaptureService(Store(tmp_path / "booth.db"), cams, Presets(values, "test"), tmp_path / "data",
                             min_free_bytes=0)
    quick = QuickInspector(service.store, lambda: (SquareDetector(), INFO), _loader("BOT_L1_CAM1"), lambda: NOW,
                           background=False)
    # 출고 때 방식(11/13 검사 방식 고정). 시험이 LOCKED에 넣은 렌탈만 고정된다
    required = lambda rental, checkpoint: LOCKED.get(rental)
    server = make_server(App(service, None, None, quick, lambda: [MBA, GRAM], _redeem, required_profile=required),
                         "127.0.0.1", 0)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


# 테스트 도구: 시험 파일 전체에서 함께 쓰는 크롬 브라우저 하나.
@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        path = os.environ.get("TRACE_UI_CHROMIUM")
        b = p.chromium.launch(executable_path=path) if path else p.chromium.launch()
        yield b
        b.close()


# 테스트 도구: 태블릿 크기(터치 가능) 창으로 부스 화면을 열고, 기기 2대가 보일 때까지 기다린다.
#        시험이 끝나면 화면 스크립트 오류가 하나도 없었는지도 확인한다.
@pytest.fixture
def page(browser, base):
    ctx = browser.new_context(viewport=TABLET, has_touch=True)
    pg = ctx.new_page()
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(base + "/")
    expect(pg.locator(".device")).to_have_count(2)
    yield pg
    ctx.close()
    assert errors == [], f"화면 스크립트 오류: {errors}"


# 테스트 도구: 기기 목록에서 시리얼이 맞는 카드를 찾는다.
def device(page: Page, serial: str):
    return page.locator(".device", has_text=serial)


# 테스트 도구: 기기 카드를 누르고 확인 시트에서 "시작"을 눌러 촬영 화면으로 들어간다.
def pick(page: Page, serial: str) -> None:
    device(page, serial).click()
    expect(page.locator("#sheet")).to_be_visible()
    page.locator("#sheetYes").click()
    expect(page.locator("#capture")).to_be_visible()


# 테스트 도구: "촬영"을 누르고(단계 번호는 그대로) "다음"을 눌러 다음 단계로 넘어가는 것을 times번 한다.
def shoot(page: Page, times: int) -> None:
    for _ in range(times):
        before = page.locator("#stepNo").inner_text()
        page.locator("#shoot").click()
        expect(page.locator("#next")).to_be_visible()  # 찍어도 단계는 그대로, '다음'이 뜬다
        expect(page.locator("#stepNo")).to_have_text(before)
        page.locator("#next").click()
        expect(page.locator("#stepNo")).not_to_have_text(before)  # 다음 단계로 넘어갈 때까지 기다림


# [상황] 기다리는 기기 목록에서 MBA 카드를 누른다.
# [기대] 바로 찍기 시작하지 않고 "MBA13-M2 · TEST-0001" 확인 시트가 뜬다(잘못 누른 기기로 시작하는 실수 방지).
def test_기기를_누르면_바로_시작하지_않고_확인_시트가_뜬다(page):
    device(page, "TEST-0001").click()
    expect(page.locator("#sheet")).to_be_visible()
    expect(page.locator("#sheetLead")).to_have_text("MBA13-M2 · TEST-0001")
    expect(page.locator("#capture")).to_be_hidden()


# [상황] 기기를 눌렀다가 "다시 고르기"를 누르거나, 시트 바깥을 누른다.
# [기대] 두 경우 모두 시트가 닫히고 기기 목록에 그대로 남는다.
def test_잘못_고른_기기는_다시_고르기로_목록에_남는다(page):
    device(page, "TEST-0001").click()
    page.locator("#sheetNo").click()
    expect(page.locator("#sheet")).to_be_hidden()
    expect(page.locator("#waiting")).to_be_visible()
    device(page, "TEST-0001").click()
    page.mouse.click(5, 5)  # 시트 바깥을 눌러도 취소
    expect(page.locator("#sheet")).to_be_hidden()
    expect(page.locator("#waiting")).to_be_visible()


# [상황] 촬영 화면에 들어왔지만 아직 한 장도 안 찍고 "목록으로"를 누른다.
# [기대] 잃을 사진이 없으니 묻지 않고 바로 목록으로 돌아간다.
def test_사진을_찍기_전에는_확인_없이_바로_목록으로_돌아간다(page):
    pick(page, "TEST-0001")
    page.locator("#back").click()
    expect(page.locator("#sheet")).to_be_hidden()
    expect(page.locator("#waiting")).to_be_visible()


# [상황] 한 장 찍은 뒤 "다시 찍기"를 누르고, 서버 응답을 일부러 잠시 붙잡아 둔다.
# [기대] 찍는 동안 버튼이 "찍는 중…"으로 바뀌고 다시 찍기·다음·목록으로가 모두 잠긴다(두 번 누르기 방지).
#        응답이 오면 "다시 찍기"로 돌아오고 버튼이 다시 눌린다.
def test_다시_찍기를_누르면_촬영처럼_찍는_중이_보이고_끝나면_돌아온다(page):
    pick(page, "TEST-0001")
    page.locator("#shoot").click()
    expect(page.locator("#next")).to_be_visible()
    held = []
    page.route("**/api/sessions/current/retake", lambda route: held.append(route))  # 응답을 잠시 붙잡아 둔다
    page.locator("#retake").click()
    expect(page.locator("#retake")).to_have_text("찍는 중…")
    expect(page.locator("#retake")).to_be_disabled()
    expect(page.locator("#next")).to_be_disabled()
    expect(page.locator("#back")).to_be_disabled()
    page.wait_for_function("1")  # 붙잡힌 요청을 처리할 틈
    assert len(held) == 1
    held[0].continue_()
    expect(page.locator("#retake")).to_have_text("다시 찍기")
    expect(page.locator("#retake")).to_be_enabled()
    expect(page.locator("#next")).to_be_enabled()
    expect(page.locator("#back")).to_be_enabled()


# [상황] 1단계를 찍은 뒤 "목록으로"를 누르고, 묻는 시트에서 "계속 찍기"를 고른다.
# [기대] "촬영을 멈추고 나갈까요?"라고 한 번 묻고, 계속 찍기면 촬영 화면 2/7단계에 그대로 남는다.
def test_찍던_중_목록으로를_누르면_한_번_묻고_계속_찍기면_그대로_남는다(page):
    pick(page, "TEST-0001")
    shoot(page, 1)
    page.locator("#back").click()
    expect(page.locator("#sheetTitle")).to_have_text("촬영을 멈추고 나갈까요?")
    page.locator("#sheetNo").click()
    expect(page.locator("#capture")).to_be_visible()
    expect(page.locator("#stepNo")).to_have_text("단계 2 / 7")


# [상황] 2단계까지 찍고 목록으로 나갔다가 같은 기기를 다시 고른다.
# [기대] 그 기기 카드에만 "이어 찍기"가 붙고, 다시 고르면 "찍던 기기를 이어 찍을까요?"를 거쳐 3/7단계부터 이어진다.
def test_목록으로_나가면_찍던_기기에_이어_찍기가_보이고_멈춘_단계부터_이어진다(page):
    pick(page, "TEST-0001")
    shoot(page, 2)
    page.locator("#back").click()
    page.locator("#sheetYes").click()
    expect(page.locator("#waiting")).to_be_visible()
    expect(device(page, "TEST-0001")).to_contain_text("이어 찍기")
    expect(device(page, "GRAM-0002")).not_to_contain_text("이어 찍기")
    device(page, "TEST-0001").click()
    expect(page.locator("#sheetTitle")).to_have_text("찍던 기기를 이어 찍을까요?")
    page.locator("#sheetYes").click()
    expect(page.locator("#stepNo")).to_have_text("단계 3 / 7")


# [상황] MBA를 1단계 찍다가 나와서 GRAM을 고른다.
# [기대] GRAM 이름이 보이고 1/7단계부터 새로 찍는다(MBA 사진이 GRAM에 섞이지 않음).
def test_잘못_고른_기기에서_나와_다른_기기를_고르면_새로_처음부터_찍는다(page):
    pick(page, "TEST-0001")
    shoot(page, 1)
    page.locator("#back").click()
    page.locator("#sheetYes").click()
    pick(page, "GRAM-0002")
    expect(page.locator("#who")).to_have_text("LG-GRAM16 · GRAM-0002")
    expect(page.locator("#stepNo")).to_have_text("단계 1 / 7")


# [상황] 빠른 검사를 찍는 중에 위쪽 "출고" 탭을 누르고, 묻는 시트에서 취소한다.
# [기대] 나갈지 묻는 시트가 뜨고, 취소하면 촬영 화면과 "빠른 검사" 탭이 그대로다.
def test_촬영_중_모드_탭을_누르면_나갈지_묻는다(page):
    pick(page, "TEST-0001")
    shoot(page, 1)
    page.locator('#modes button[data-mode="OUT"]').click()
    expect(page.locator("#sheet")).to_be_visible()
    page.locator("#sheetNo").click()
    expect(page.locator("#capture")).to_be_visible()
    expect(page.locator('#modes button[data-mode="QUICK"]')).to_have_attribute("aria-pressed", "true")


# [상황] 빠른 검사를 처음부터 끝까지 찍는다: 바닥면 3단계 → 뒤집기 안내 → 윗면 3단계 → 완료.
# [기대] 뒤집기 안내 단계에서는 촬영 버튼이 숨고, 끝나면 완료 버튼이 크게 강조된다.
#        판정은 "문제 있음", "확인할 곳 1곳 · 관리자 검수 뒤 확정"이 보이고, 사진 12장 중 1장에 흠집 표시가 있다.
#        판정 뒤에는 "목록으로" 대신 "다음 기기"로 목록에 돌아간다.
def test_빠른_검사를_끝까지_찍으면_뒤집기_안내를_거쳐_판정과_다음_기기가_보인다(page):
    pick(page, "TEST-0001")
    expect(page.locator(".phase.now")).to_contain_text("바닥면")
    shoot(page, 3)
    expect(page.locator("#stepCard")).to_have_class("step notice")
    expect(page.locator("#shoot")).to_be_hidden()
    page.locator("#next").click()
    expect(page.locator(".phase.now")).to_contain_text("윗면")
    shoot(page, 3)
    expect(page.locator("#complete")).to_have_class("btn primary big")
    page.locator("#complete").click()
    expect(page.locator("#verdict")).to_have_text("문제 있음")
    # 사람 검수(11/8): 부스는 AI가 잡은 곳을 "확인할 곳"으로 알리고, 확정은 관리자 검수 뒤
    expect(page.locator("#verdictMeta")).to_contain_text("확인할 곳 1곳")
    expect(page.locator("#verdictMeta")).to_contain_text("관리자 검수 뒤 확정")
    expect(page.locator("#resultPhotos figure")).to_have_count(12)
    expect(page.locator("#resultPhotos figure.hit")).to_have_count(1)
    expect(page.locator("#back")).to_be_hidden()
    page.locator("#nextDevice").click()
    expect(page.locator("#waiting")).to_be_visible()


# [상황] 2단계까지 찍은 뒤 태블릿에서 새로고침한다.
# [기대] 촬영 화면 3/7단계로 그대로 돌아온다(찍던 내용이 날아가지 않음).
def test_새로고침해도_찍던_빠른_검사_화면으로_돌아온다(page):
    pick(page, "TEST-0001")
    shoot(page, 2)
    page.reload()
    expect(page.locator("#capture")).to_be_visible()
    expect(page.locator("#stepNo")).to_have_text("단계 3 / 7")
    expect(page.locator("#back")).to_be_visible()


# [상황] 태블릿(1280px)과 폰(375px) 폭에서 기기 목록과 촬영 화면을 연다.
# [기대] 어느 폭에서도 가로 스크롤이 없고, 기기 카드·촬영 버튼은 64px 이상, 목록으로 버튼은 44px 이상이다(손가락으로 누르기 쉬운 크기).
#        촬영 버튼이 화면 오른쪽 밖으로 밀리지 않는다.
@pytest.mark.parametrize("size", [TABLET, {"width": 375, "height": 812}])
def test_태블릿과_폰_폭에서_가로_스크롤이_없고_주_버튼이_손가락_크기다(browser, base, size):
    ctx = browser.new_context(viewport=size, has_touch=True)
    pg = ctx.new_page()
    pg.goto(base + "/")
    expect(pg.locator(".device")).to_have_count(2)
    no_hscroll = "document.documentElement.scrollWidth <= window.innerWidth"
    assert pg.locator(".device").first.bounding_box()["height"] >= 64
    assert pg.evaluate(no_hscroll), "기기 목록"
    pick(pg, "TEST-0001")
    shoot_box = pg.locator("#shoot").bounding_box()
    assert shoot_box["height"] >= 64
    assert shoot_box["x"] + shoot_box["width"] <= size["width"], "촬영 버튼이 화면 밖으로 밀림"
    assert pg.locator("#back").bounding_box()["height"] >= 44
    assert pg.evaluate(no_hscroll), "촬영"
    ctx.close()


# [상황] 빠른 검사를 찍는 중에 이미 켜져 있는 "빠른 검사" 탭을 다시 누른다.
# [기대] 묻는 시트도 뜨지 않고 2/7단계 화면이 그대로다.
def test_촬영_중_지금_모드_탭을_다시_눌러도_하던_화면_그대로다(page):
    pick(page, "TEST-0001")
    shoot(page, 1)
    page.locator('#modes button[data-mode="QUICK"]').click()
    expect(page.locator("#sheet")).to_be_hidden()
    expect(page.locator("#stepNo")).to_have_text("단계 2 / 7")


# [상황] 판정 실패 뒤처럼 "다시 탐지" 버튼이 보이는 상태에서 학습 탭으로 가 MB01을 찍기 시작한다.
# [기대] 학습 촬영 화면에는 "다시 탐지" 버튼이 남아 있지 않다.
def test_빠른_검사_판정_뒤_학습_모드로_가면_다시_탐지_버튼이_남지_않는다(page):
    page.evaluate("document.getElementById('redetect').hidden = false")  # 판정 실패 뒤 상태를 흉내
    page.locator('#modes button[data-mode="TRAINING"]').click()
    page.locator("#device").fill("MB01")
    page.locator("#start").click()
    expect(page.locator("#capture")).to_be_visible()
    expect(page.locator("#redetect")).to_be_hidden()


# [상황] 키보드만으로 목록을 새로 불러오고, 기기 카드에서 Enter로 시트를 연 뒤 Tab·Esc를 누른다.
# [기대] 시트가 열리면 "시작"에 초점이 가고, Tab은 시트 안 두 버튼만 오간다.
#        Esc로 닫으면 처음 눌렀던 기기 카드로 초점이 돌아간다(키보드·화면 낭독기 사용자 배려).
def test_확인_시트가_열리면_Tab은_시트_안에서만_돌고_닫으면_연_자리로_포커스가_돌아간다(page):
    page.locator("#reload").focus()
    page.locator("#reload").press("Enter")  # 목록을 다시 불러와도 같은 자리
    expect(page.locator(".device")).to_have_count(2)
    card = device(page, "TEST-0001")
    card.focus()
    card.press("Enter")
    expect(page.locator("#sheetYes")).to_be_focused()
    page.keyboard.press("Tab")
    expect(page.locator("#sheetNo")).to_be_focused()
    page.keyboard.press("Tab")
    expect(page.locator("#sheetYes")).to_be_focused()
    page.keyboard.press("Escape")
    expect(page.locator("#sheet")).to_be_hidden()
    expect(card).to_be_focused()



# 테스트 도구: MBA로 빠른 검사 12장을 끝까지 찍고 "문제 있음" 판정이 뜰 때까지 기다린다.
def finish_quick(page: Page) -> None:
    pick(page, "TEST-0001")
    shoot(page, 3)
    expect(page.locator("#stepCard")).to_have_class("step notice")
    page.locator("#next").click()
    expect(page.locator(".phase.now")).to_contain_text("윗면")
    shoot(page, 3)
    page.locator("#complete").click()
    expect(page.locator("#verdict")).to_have_text("문제 있음")


# [상황] 빠른 검사 판정 화면에서 오른쪽 사진 목록을 누르고, 이전/다음 버튼과 왼쪽 방향키도 써 본다.
# [기대] 12장 중 흠집 있는 사진이 맨 앞이고 처음에 가운데에 크게(1600px 썸네일) 보인다("흠집 1곳 · 1/12").
#        다른 사진을 누르면 그 사진이 크게 보이고(5/12 "흠집 없음"), 다음 버튼·방향키로 한 장씩 넘어간다.
#        첫 장에서는 이전, 마지막 장에서는 다음 버튼이 잠긴다. 오른쪽 목록은 화면 안에서만 스크롤된다.
def test_판정_사진은_오른쪽_목록에서_고르면_가운데에_크게_하나씩_보인다(page):
    finish_quick(page)
    rail = page.locator("#resultPhotos figure")
    expect(rail).to_have_count(12)
    expect(page.locator("#photoCount")).to_have_text("12장")
    # 흠집 많은 사진이 먼저, 처음엔 그 사진이 크게 보인다
    expect(rail.first).to_have_class("hit")
    expect(rail.first).to_have_attribute("aria-current", "true")
    expect(page.locator("#stageCap")).to_contain_text("흠집 1곳")
    expect(page.locator("#stageCap")).to_contain_text("1 / 12")
    expect(page.locator("#stageFrame img")).to_have_attribute("src", re.compile(r"thumbnail\?w=1600$"))
    expect(page.locator("#prevPhoto")).to_be_disabled()
    rail.nth(4).click()
    expect(page.locator("#stageCap")).to_contain_text("5 / 12")
    expect(page.locator("#stageCap")).to_contain_text("흠집 없음")
    expect(rail.nth(4)).to_have_attribute("aria-current", "true")
    expect(rail.first).to_have_attribute("aria-current", "false")
    page.locator("#nextPhoto").click()
    expect(page.locator("#stageCap")).to_contain_text("6 / 12")
    page.keyboard.press("ArrowLeft")
    expect(page.locator("#stageCap")).to_contain_text("5 / 12")
    rail.last.click()
    expect(page.locator("#nextPhoto")).to_be_disabled()
    # 오른쪽 목록은 화면 안에서 스크롤된다(페이지가 길게 늘어나지 않음)
    rail_box = page.locator("#resultPhotos").bounding_box()
    stage_box = page.locator("#stage").bounding_box()
    assert rail_box["height"] <= stage_box["height"], (rail_box, stage_box)
    assert page.evaluate("getComputedStyle(document.getElementById('resultPhotos')).overflowY") == "auto"


# [상황] 판정 화면에서 4번째 사진을 고른 뒤, 화면이 결과를 서버에서 다시 읽어 온다.
# [기대] 고른 사진(4/12)이 그대로 크게 보인다(새로 읽을 때마다 첫 장으로 튀지 않음).
def test_판정_화면에서_결과를_다시_읽어도_고른_사진이_그대로다(page):
    finish_quick(page)
    page.locator("#resultPhotos figure").nth(3).click()
    src = page.locator("#stageFrame img").get_attribute("src")
    page.evaluate("pollResult()")  # 결과를 직접 다시 읽고 끝날 때까지 기다림
    expect(page.locator("#stageCap")).to_contain_text("4 / 12")
    assert page.locator("#stageFrame img").get_attribute("src") == src


# [상황] 판정 화면에서 실시간 화면을 열고 크기 조절 막대에서 오른쪽 방향키를 누른다.
# [기대] 막대만 움직이고 판정 사진은 1/12 그대로다. 사진 쪽을 누른 뒤 방향키를 누르면 그때 2/12로 넘어간다.
def test_실시간_크기_막대에서_방향키를_눌러도_판정_사진은_넘어가지_않는다(page):
    finish_quick(page)
    page.locator("#liveToggle").click()
    page.locator("#liveSize").focus()
    page.keyboard.press("ArrowRight")
    expect(page.locator("#stageCap")).to_contain_text("1 / 12")
    page.locator("#stage").click(position={"x": 400, "y": 20})
    page.keyboard.press("ArrowRight")
    expect(page.locator("#stageCap")).to_contain_text("2 / 12")


# [상황] 실시간 화면을 열고 카메라 버튼(CAM1·CAM2)을 골라 보고, 크기 막대를 최대로 올린 뒤 새로고침한다.
# [기대] 처음엔 두 카메라가 다 보이고, 옆면 카메라가 없으면 옆면 버튼·화면은 숨는다.
#        CAM1 하나만 고르면 그 화면만 크게 보이고 안 보이는 CAM2는 영상 받기를 끊는다.
#        CAM2를 더 누르면 둘이 나란히, 다시 누르면 CAM2만 빠진다. 크기를 키워도 화면 밖으로 넘치지 않는다.
#        새로고침 뒤 다시 열어도 고른 카메라와 크기를 기억한다.
def test_실시간_화면은_카메라를_골라_보고_크기를_키울_수_있고_다시_열어도_기억한다(page):
    page.locator("#liveToggle").click()
    expect(page.locator("#live")).to_be_visible()
    expect(page.locator("#liveFigCAM1")).to_be_visible()
    expect(page.locator("#liveFigCAM2")).to_be_visible()
    # 옆면 카메라(TRACE_BOOTH_SIDE_CAM)가 없으면 옆면 버튼·화면을 숨긴다
    expect(page.locator('#liveLayout button[data-v="SIDE"]')).to_be_hidden()
    expect(page.locator("#liveFigSIDE")).to_be_hidden()
    narrow = page.locator("#liveGrid").bounding_box()["width"]
    # 모두에서 하나를 누르면 그 하나만 크게
    page.locator('#liveLayout button[data-v="CAM1"]').click()
    expect(page.locator("#liveFigCAM2")).to_be_hidden()
    expect(page.locator("#liveCAM2")).not_to_have_attribute("src", re.compile("."))  # 안 보이는 카메라는 스트림을 끊음
    one = page.locator("#liveFigCAM1").bounding_box()["width"]
    # 다른 카메라를 더 누르면 칸이 늘어나 둘만 보인다
    page.locator('#liveLayout button[data-v="CAM2"]').click()
    expect(page.locator("#liveFigCAM2")).to_be_visible()
    expect(page.locator('#liveLayout button[data-v="all"]')).to_have_attribute("aria-pressed", "false")
    assert page.locator("#liveFigCAM1").bounding_box()["width"] < one * 0.7
    # 다시 누르면 그 카메라만 빠진다
    page.locator('#liveLayout button[data-v="CAM2"]').click()
    expect(page.locator("#liveFigCAM2")).to_be_hidden()
    expect(page.locator("#liveFigCAM1")).to_be_visible()
    page.locator("#liveSize").fill("100")
    wide = page.locator("#liveGrid").bounding_box()["width"]
    assert wide > narrow, (narrow, wide)
    assert wide <= TABLET["width"], "화면 밖으로 넘침"
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.reload()
    page.locator("#liveToggle").click()
    expect(page.locator("#liveFigCAM2")).to_be_hidden()
    expect(page.locator("#liveSize")).to_have_value("100")


# [상황] 폰 폭(375px)에서 빠른 검사를 끝까지 찍고 판정 화면을 본다.
# [기대] 사진 12장이 보이고 가로 스크롤이 없으며, "다음 사진" 버튼이 화면 밖으로 밀리지 않는다.
def test_폰_폭에서도_판정_사진_화면이_가로로_넘치지_않는다(browser, base):
    ctx = browser.new_context(viewport={"width": 375, "height": 812}, has_touch=True)
    pg = ctx.new_page()
    pg.goto(base + "/")
    expect(pg.locator(".device")).to_have_count(2)
    finish_quick(pg)
    expect(pg.locator("#resultPhotos figure")).to_have_count(12)
    assert pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "판정 화면"
    box = pg.locator("#nextPhoto").bounding_box()
    assert box["x"] + box["width"] <= 375, "다음 사진 버튼이 화면 밖으로 밀림"
    ctx.close()


# [상황] 출고 탭을 연다.
# [기대] 빠른 검사로 가라는 안내와, 기기 ID(시리얼 번호 아님)·렌탈 ID·시점 ID가 무엇이고 어디서 찾는지 설명이 보인다.
#        렌탈 ID를 20자 넣으면 "20 / 21자"로 길이를 알려 준다. 안내 버튼을 누르면 빠른 검사 기기 목록으로 간다.
def test_출고_탭은_세_ID가_무엇이고_어디서_찾는지_보이고_빠른_검사로_보내_준다(page):
    page.locator('#modes button[data-mode="OUT"]').click()
    expect(page.locator("#quickTip")).to_be_visible()
    expect(page.locator("#deviceLabel")).to_have_text("기기 ID")
    expect(page.locator("#deviceHelp")).to_contain_text("시리얼 번호 아님")
    expect(page.locator("#rentalHelp")).to_contain_text("부스에 넣을 ID")
    expect(page.locator("#checkpointHelp")).to_contain_text("출고 시작")
    page.locator("#rental").fill(MBA.rental_id[:20])
    expect(page.locator("#rentalLen")).to_have_text("20 / 21자")
    page.locator("#toQuick").click()
    expect(page.locator("#waiting")).to_be_visible()
    expect(page.locator(".device")).to_have_count(2)


# [상황] 출고 탭에서 렌탈 ID만 20자(한 글자 모자람)로 넣고 시작을 누른다.
# [기대] 서버에 보내지 않고 "렌탈 ID가 21자가 아니에요(지금 20자)"를 보여 주며, 렌탈 칸만 틀림 표시·초점이 간다.
#        촬영 화면으로 넘어가지 않는다.
def test_출고에서_ID가_21자가_아니면_서버에_보내기_전에_어느_칸인지_알려_준다(page):
    page.locator('#modes button[data-mode="OUT"]').click()
    page.locator("#device").fill(MBA.device_id)
    page.locator("#rental").fill(MBA.rental_id[:20])
    page.locator("#checkpoint").fill(MBA.checkpoint_id)
    page.locator("#start").click()
    expect(page.locator("#formError")).to_contain_text("렌탈 ID가 21자가 아니에요(지금 20자)")
    expect(page.locator("#rental")).to_have_attribute("aria-invalid", "true")
    expect(page.locator("#rental")).to_have_attribute("aria-errormessage", "formError")
    expect(page.locator("#rental")).to_be_focused()
    expect(page.locator("#checkpoint")).not_to_have_attribute("aria-invalid", "true")
    expect(page.locator("#capture")).to_be_hidden()


# [상황] 렌탈 ID 앞에 한 글자가 더 붙은 22자를 붙여 넣고, 다음엔 21자지만 "!"가 섞인 값을 넣는다.
# [기대] 22자는 마음대로 자르지 않고 "22 / 21자"와 "21자가 아니에요(지금 22자)"를 알린다.
#        21자라도 쓸 수 없는 글자가 있으면 그것을 따로 알린다. 어느 경우에도 촬영을 시작하지 않는다.
def test_출고에서_22자를_붙여_넣으면_자르지_않고_길이를_알리며_21자라도_못_쓰는_글자는_따로_알린다(page):
    page.locator('#modes button[data-mode="OUT"]').click()
    page.locator("#device").fill(MBA.device_id)
    page.locator("#rental").fill("x" + MBA.rental_id)  # 앞에 한 글자가 더 붙은 복사 실수
    page.locator("#checkpoint").fill(MBA.checkpoint_id)
    expect(page.locator("#rentalLen")).to_have_text("22 / 21자")
    page.locator("#start").click()
    expect(page.locator("#formError")).to_contain_text("렌탈 ID가 21자가 아니에요(지금 22자)")
    page.locator("#rental").fill(MBA.rental_id[:20] + "!")
    page.locator("#start").click()
    expect(page.locator("#formError")).to_contain_text("렌탈 ID에 쓸 수 없는 글자가 있어요")
    expect(page.locator("#capture")).to_be_hidden()


# [상황] 세 ID를 바르게 넣고 시작했는데 부스 서버 요청이 끊긴다.
# [기대] "부스 서버에 연결하지 못했어요"를 보여 준다.
def test_출고_시작에서_부스_서버에_닿지_못하면_연결_실패를_알린다(page):
    page.route("**/api/sessions", lambda route: route.abort())
    page.locator('#modes button[data-mode="OUT"]').click()
    page.locator("#device").fill(MBA.device_id)
    page.locator("#rental").fill(MBA.rental_id)
    page.locator("#checkpoint").fill(MBA.checkpoint_id)
    page.locator("#start").click()
    expect(page.locator("#error")).to_contain_text("부스 서버에 연결하지 못했어요")


# [상황] 세 ID를 바르게 넣고 출고 시작을 누른다(시험용 서버는 요청을 받고 일부러 거절한다).
# [기대] 기기·렌탈·시점 ID를 대소문자 그대로, 모드 OUT으로 한 번 보낸다(대문자로 바꾸면 다른 기기가 됨).
#        서버가 보낸 거절 사유("시험용 거절")를 그대로 보여 주고, 입력 오류 문구는 뜨지 않는다.
def test_출고는_기기_ID의_대소문자를_그대로_보내고_세_ID가_맞으면_시작을_요청한다(page):
    sent: list[dict] = []

    def capture(route):  # 이 가짜 서버에는 서버 연결 설정이 없어 출고가 거절되므로 보낸 값만 확인한다
        sent.append(route.request.post_data_json)
        route.fulfill(status=503, json={"detail": "시험용 거절"})

    page.route("**/api/sessions", capture)
    page.locator('#modes button[data-mode="OUT"]').click()
    page.locator("#device").fill(MBA.device_id)
    page.locator("#rental").fill(MBA.rental_id)
    page.locator("#checkpoint").fill(MBA.checkpoint_id)
    expect(page.locator("#checkpointLen")).to_have_class("len ok")
    page.locator("#start").click()
    expect(page.locator("#error")).to_have_text("시험용 거절")
    assert sent == [{"deviceId": MBA.device_id, "mode": "OUT", "rentalId": MBA.rental_id,
                     "checkpointId": MBA.checkpoint_id}]  # 대문자로 바뀌면 다른 기기가 됨
    expect(page.locator("#formError")).to_be_hidden()


# [상황] 반납 탭을 연다.
# [기대] "수령·반납은 빠른 검사에서 찍어요" 안내만 보이고 ID 입력 칸은 숨는다.
def test_반납_탭은_빠른_검사에서_찍으라고_알리고_입력_칸을_보이지_않는다(page):
    page.locator('#modes button[data-mode="RET"]').click()
    expect(page.locator("#retBlocked")).to_be_visible()
    expect(page.locator("#retBlocked")).to_contain_text("수령·반납은 빠른 검사에서 찍어요")
    expect(page.locator("#manualForm")).to_be_hidden()


# [상황] 학습 탭에서 기기 이름을 소문자 "mb01"로 넣고 시작한다.
# [기대] 칸 이름은 "기기 이름"이고, 촬영은 대문자 MB01로 시작한다(학습 데이터 이름 규칙).
def test_학습_탭은_기기_이름_규칙을_보이고_소문자로_넣어도_대문자로_시작한다(page):
    page.locator('#modes button[data-mode="TRAINING"]').click()
    expect(page.locator("#deviceLabel")).to_have_text("기기 이름")
    expect(page.locator("#quickTip")).to_be_hidden()
    page.locator("#device").fill("mb01")
    page.locator("#start").click()
    expect(page.locator("#who")).to_contain_text("MB01")


# [상황] 출고 탭에서 관리자가 준 6자리 부스 코드를 "123 456"처럼 띄어 써서 넣고 Enter를 누른 뒤 시작한다.
# [기대] "MBA13-M2 · TEST-0001 불러왔어요"가 뜨고 기기·렌탈·시점 ID 세 칸이 채워진다(긴 ID를 손으로 칠 필요 없음).
#        시작하면 채워진 세 ID 그대로 출고를 요청한다.
def test_출고_탭에_부스_코드_6자리를_넣으면_세_ID가_채워지고_기기를_확인하라고_알린다(page):
    sent: list[dict] = []

    def capture(route):
        sent.append(route.request.post_data_json)
        route.fulfill(status=503, json={"detail": "시험용 거절"})

    page.route("**/api/sessions", capture)
    page.locator('#modes button[data-mode="OUT"]').click()
    expect(page.locator("#codeBox")).to_be_visible()
    page.locator("#boothCode").fill("123 456")  # 띄어 쓴 채로 넣어도 된다
    page.locator("#boothCode").press("Enter")
    expect(page.locator("#codeOk")).to_contain_text("MBA13-M2 · TEST-0001 불러왔어요")
    expect(page.locator("#device")).to_have_value(MBA.device_id)
    expect(page.locator("#rental")).to_have_value(MBA.rental_id)
    expect(page.locator("#checkpoint")).to_have_value(MBA.checkpoint_id)
    expect(page.locator("#rentalLen")).to_have_class("len ok")
    page.locator("#start").click()
    expect(page.locator("#error")).to_have_text("시험용 거절")
    assert sent == [{"deviceId": MBA.device_id, "mode": "OUT", "rentalId": MBA.rental_id,
                     "checkpointId": MBA.checkpoint_id}]


# [상황] 부스 코드를 5자리, 틀린 6자리(000000), 만료된 코드(999999) 순서로 넣는다.
# [기대] 각각 "숫자 6자리예요", "코드가 맞지 않아요", "만료됐거나 이미 쓴 코드예요"를 알리고 ID 칸은 비어 있다.
def test_틀리거나_만료된_부스_코드는_이유를_알리고_칸을_채우지_않는다(page):
    page.locator('#modes button[data-mode="OUT"]').click()
    page.locator("#boothCode").fill("12345")
    page.locator("#redeem").click()
    expect(page.locator("#codeError")).to_have_text("부스 코드는 숫자 6자리예요")
    page.locator("#boothCode").fill("000000")
    page.locator("#redeem").click()
    expect(page.locator("#codeError")).to_contain_text("코드가 맞지 않아요")
    page.locator("#boothCode").fill("999999")
    page.locator("#redeem").click()
    expect(page.locator("#codeError")).to_contain_text("만료됐거나 이미 쓴 코드예요")
    expect(page.locator("#rental")).to_have_value("")
    expect(page.locator("#codeOk")).to_be_hidden()


# [상황] 맞는 코드로 세 ID를 채운 뒤, 다른 기기용으로 새 코드를 넣었는데 틀렸다.
# [기대] 앞 코드로 채운 세 ID가 모두 지워진다(앞 기기로 잘못 출고하는 일 방지).
def test_코드로_채운_뒤_새_코드가_틀리면_앞_코드의_ID는_지워져_엉뚱한_기기로_시작하지_않는다(page):
    page.locator('#modes button[data-mode="OUT"]').click()
    page.locator("#boothCode").fill("123456")
    page.locator("#redeem").click()
    expect(page.locator("#rental")).to_have_value(MBA.rental_id)
    page.locator("#boothCode").fill("000000")
    page.locator("#redeem").click()
    expect(page.locator("#codeError")).to_contain_text("코드가 맞지 않아요")
    for field in ("#device", "#rental", "#checkpoint"):
        expect(page.locator(field)).to_have_value("")


# [상황] 렌탈 ID를 손으로 넣은 뒤 틀린 부스 코드를 넣는다.
# [기대] 손으로 넣은 값은 지우지 않고 그대로 둔다.
def test_직접_넣은_ID는_틀린_코드를_넣어도_그대로_둔다(page):
    page.locator('#modes button[data-mode="OUT"]').click()
    page.locator("#rental").fill(MBA.rental_id)
    page.locator("#boothCode").fill("000000")
    page.locator("#redeem").click()
    expect(page.locator("#codeError")).to_contain_text("코드가 맞지 않아요")
    expect(page.locator("#rental")).to_have_value(MBA.rental_id)


# [상황] 부스 코드를 넣고 Enter를 두 번 누르고, 답이 오기 전에 학습 탭으로 옮긴다.
# [기대] 코드 확인 요청은 한 번만 간다. 답이 오면 화면이 출고 탭으로 돌아와 세 ID를 채우고 "불러왔어요"를 보여 준다.
def test_코드를_기다리는_동안_Enter를_더_눌러도_한_번만_보내고_탭을_옮겨도_출고_탭에_채운다(page):
    calls: list[str] = []
    gate: list = []

    def slow(route):
        calls.append(route.request.post_data_json["code"])
        gate.append(route)  # 탭을 옮길 때까지 답을 미룬다

    page.route("**/api/booth-code", slow)
    page.locator('#modes button[data-mode="OUT"]').click()
    page.locator("#boothCode").fill("123456")
    page.locator("#boothCode").press("Enter")
    page.locator("#boothCode").press("Enter")
    for _ in range(100):  # 첫 요청이 가로채기에 닿을 때까지 기다린다(최대 5초)
        if gate:
            break
        page.wait_for_timeout(50)
    page.locator('#modes button[data-mode="TRAINING"]').click()
    expect(page.locator("#deviceLabel")).to_have_text("기기 이름")
    assert len(gate) == 1
    gate[0].fulfill(json=MBA.to_json())
    expect(page.locator('#modes button[data-mode="OUT"]')).to_have_attribute("aria-pressed", "true")
    expect(page.locator("#rental")).to_have_value(MBA.rental_id)
    expect(page.locator("#codeOk")).to_contain_text("불러왔어요")
    assert calls == ["123456"]


# [상황] 학습 탭과 반납 탭을 차례로 연다.
# [기대] 두 탭 모두 부스 코드 칸이 보이지 않는다(부스 코드는 출고 전용).
def test_학습_탭과_반납_탭에는_부스_코드_칸이_없다(page):
    page.locator('#modes button[data-mode="TRAINING"]').click()
    expect(page.locator("#codeBox")).to_be_hidden()
    page.locator('#modes button[data-mode="RET"]').click()
    expect(page.locator("#codeBox")).to_be_hidden()


# [상황] 첫 단계(오른쪽 큰 조명 L1)에서 "촬영"을 누른다.
# [기대] 같은 단계에 머물며 촬영 버튼이 숨고 "사진을 확인하고 다음" 안내와 두 카메라 사진 2장이 보인다.
#        "다음"을 눌러야 왼쪽 막대 조명(L2) 단계로 넘어가고, 앞 단계 사진은 사라진다(흐린 사진을 보고 다시 찍을 기회).
def test_촬영을_누르면_같은_단계에_머물고_다음을_눌러야_다음_조명으로_넘어간다(page):
    pick(page, "TEST-0001")
    expect(page.locator("#chips")).to_contain_text("오른쪽 큰 조명")
    page.locator("#shoot").click()
    expect(page.locator("#next")).to_be_visible()
    expect(page.locator("#shoot")).to_be_hidden()
    expect(page.locator("#stepKind")).to_contain_text("사진을 확인하고 다음")
    expect(page.locator("#thumbs figure")).to_have_count(2)
    expect(page.locator("#chips")).to_contain_text("오른쪽 큰 조명")
    page.locator("#next").click()
    expect(page.locator("#chips")).to_contain_text("왼쪽 막대 조명")
    expect(page.locator("#shoot")).to_be_visible()
    expect(page.locator("#lastShots")).to_be_hidden()


# [상황] 검사 범위를 고르지 않고 기기를 골라 시작한다.
# [기대] 빠른 검사가 기본으로 골라져 있고(전체 검사는 28장으로 표시), 빠른 검사 바닥면 1/7단계부터 찍는다.
def test_검사_범위는_빠른_검사가_기본이고_고르지_않으면_지금처럼_빠른_검사로_찍는다(page):
    expect(page.locator("#profilePick")).to_be_visible()
    expect(page.locator('#profiles [data-name="QUICK"]')).to_have_attribute("aria-checked", "true")
    expect(page.locator('#profiles [data-name="FULL"]')).to_contain_text("28장")
    pick(page, "TEST-0001")
    expect(page.locator("#modeTag")).to_have_text("빠른 검사")
    expect(page.locator(".phase").first).to_contain_text("바닥면 촬영")
    expect(page.locator("#stepNo")).to_have_text("단계 1 / 7")


# [상황] 전체 검사를 고르고 기기를 시작한 뒤, 화면이 흰 화면(SCR1) 단계에 닿을 때까지 계속 찍는다.
# [기대] 확인 시트와 촬영 화면에 "전체 검사"가 보이고, 두 번째 묶음은 "열어서 키보드"다.
#        뚜껑을 100°로 펴서 앞쪽 자리로 옮기라는 안내는 키보드 묶음에서 나온다.
#        그 전까지는 "건너뛰기"가 없고, 흰 화면 단계(옆면 묶음)에서만 건너뛰기가 보이고 눌린다(필요하면 이 단계를 건너뛸 수 있음).
def test_전체_검사를_고르면_확인_시트에_범위가_보이고_SCR1에서만_건너뛰기가_뜬다(page):
    page.locator('#profiles [data-name="FULL"]').click()
    device(page, "TEST-0001").click()
    expect(page.locator("#sheetTitle")).to_contain_text("전체 검사")
    page.locator("#sheetYes").click()
    expect(page.locator("#modeTag")).to_have_text("전체 검사")
    expect(page.locator(".phase").nth(1)).to_contain_text("열어서 키보드")
    seen = set()
    while "흰 화면" not in page.locator("#instruction").inner_text():
        expect(page.locator("#skip")).to_be_hidden()
        text = page.locator("#instruction").inner_text()
        if "100° 표시" in text and "앞쪽 자리로 옮기" in text:
            expect(page.locator(".phase.now")).to_contain_text("열어서 키보드")  # 펼치기 안내는 키보드 칸
            seen.add("KB")
        before = page.locator("#stepNo").inner_text()
        if page.locator("#shoot").is_visible():
            shoot(page, 1)
        else:
            page.locator("#next").click()
            expect(page.locator("#stepNo")).not_to_have_text(before)
    assert seen == {"KB"}
    expect(page.locator(".phase.now")).to_contain_text("옆면")
    expect(page.locator("#skip")).to_be_visible()
    expect(page.locator("#skip")).to_be_enabled()


# 테스트 도구: 반납 기기 GRAM이 출고 때 전체 검사로 찍혔다고 가짜 서버에 정해 둔다(시험이 끝나면 지움).
@pytest.fixture
def locked_gram():
    LOCKED[GRAM.rental_id] = "FULL"  # 반납 기기(GRAM)는 출고를 전체 검사로 했다
    yield
    LOCKED.clear()


# [상황] GRAM은 출고 때 전체 검사였다. 목록을 다시 불러와 GRAM을 누르고, 취소했다가 다시 시작한다.
# [기대] 카드에 "전체 검사만"이 붙고, 시트에 "반납 전체 검사"와 "다른 방식은 고를 수 없어요"가 보인다.
#        빠른 검사 선택은 꺼지고 그 이유("출고 때 고른 전체 검사로만")가 보인다(출고·반납 사진을 같은 조건으로 비교하려고).
#        취소하면 빠른 검사가 다시 켜지고, 다시 시작하면 전체 검사로 찍는다.
def test_반납_기기는_출고_때_방식만_고를_수_있고_다른_방식은_꺼지고_이유가_보인다(page, locked_gram):
    page.locator("#reload").click()
    card = device(page, "GRAM-0002")
    expect(card).to_contain_text("전체 검사만")
    card.click()
    expect(page.locator("#sheetTitle")).to_contain_text("반납 전체 검사")
    expect(page.locator("#sheetBody")).to_contain_text("다른 방식은 고를 수 없어요")
    expect(page.locator('#profiles [data-name="QUICK"]')).to_be_disabled()
    expect(page.locator('#profiles [data-name="FULL"]')).to_have_attribute("aria-checked", "true")
    expect(page.locator("#profileHelp")).to_contain_text("출고 때 고른 전체 검사로만")
    page.locator("#sheetNo").click()
    expect(page.locator('#profiles [data-name="QUICK"]')).to_be_enabled()
    expect(page.locator('#profiles [data-name="QUICK"]')).to_have_attribute("aria-checked", "true")
    device(page, "GRAM-0002").click()
    page.locator("#sheetYes").click()
    expect(page.locator("#modeTag")).to_have_text("전체 검사")
