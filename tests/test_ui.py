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


def _redeem(code: str) -> WaitingDevice:
    """가짜 rental-server 부스 코드: 123456만 맞고, 999999는 만료, 나머지는 틀림"""
    if code == "123456":
        return MBA
    if code == "999999":
        raise ApiError("부스 코드", 410, "BOOTH_CODE_EXPIRED")
    raise ApiError("부스 코드", 400, "BOOTH_CODE_INVALID")


@pytest.fixture
def base(tmp_path):
    values = {light: {"exposure_time_absolute": 100} for light in ["L1", "L2", "L3", "L4", "OFF"]}
    cams = {c: FakeCamera(c) for c in (Camera.CAM1, Camera.CAM2)}
    service = CaptureService(Store(tmp_path / "booth.db"), cams, Presets(values, "test"), tmp_path / "data",
                             min_free_bytes=0)
    quick = QuickInspector(service.store, lambda: (SquareDetector(), INFO), _loader("BOT_L1_CAM1"), lambda: NOW,
                           background=False)
    server = make_server(App(service, None, None, quick, lambda: [MBA, GRAM], _redeem), "127.0.0.1", 0)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        path = os.environ.get("TRACE_UI_CHROMIUM")
        b = p.chromium.launch(executable_path=path) if path else p.chromium.launch()
        yield b
        b.close()


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


def device(page: Page, serial: str):
    return page.locator(".device", has_text=serial)


def pick(page: Page, serial: str) -> None:
    device(page, serial).click()
    expect(page.locator("#sheet")).to_be_visible()
    page.locator("#sheetYes").click()
    expect(page.locator("#capture")).to_be_visible()


def shoot(page: Page, times: int) -> None:
    for _ in range(times):
        before = page.locator("#stepNo").inner_text()
        page.locator("#shoot").click()
        expect(page.locator("#stepNo")).not_to_have_text(before)  # 다음 단계로 넘어갈 때까지 기다림


def test_기기를_누르면_바로_시작하지_않고_확인_시트가_뜬다(page):
    device(page, "TEST-0001").click()
    expect(page.locator("#sheet")).to_be_visible()
    expect(page.locator("#sheetLead")).to_have_text("MBA13-M2 · TEST-0001")
    expect(page.locator("#capture")).to_be_hidden()


def test_잘못_고른_기기는_다시_고르기로_목록에_남는다(page):
    device(page, "TEST-0001").click()
    page.locator("#sheetNo").click()
    expect(page.locator("#sheet")).to_be_hidden()
    expect(page.locator("#waiting")).to_be_visible()
    device(page, "TEST-0001").click()
    page.mouse.click(5, 5)  # 시트 바깥을 눌러도 취소
    expect(page.locator("#sheet")).to_be_hidden()
    expect(page.locator("#waiting")).to_be_visible()


def test_사진을_찍기_전에는_확인_없이_바로_목록으로_돌아간다(page):
    pick(page, "TEST-0001")
    page.locator("#back").click()
    expect(page.locator("#sheet")).to_be_hidden()
    expect(page.locator("#waiting")).to_be_visible()


def test_찍던_중_목록으로를_누르면_한_번_묻고_계속_찍기면_그대로_남는다(page):
    pick(page, "TEST-0001")
    shoot(page, 1)
    page.locator("#back").click()
    expect(page.locator("#sheetTitle")).to_have_text("촬영을 멈추고 나갈까요?")
    page.locator("#sheetNo").click()
    expect(page.locator("#capture")).to_be_visible()
    expect(page.locator("#stepNo")).to_have_text("단계 2 / 7")


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


def test_잘못_고른_기기에서_나와_다른_기기를_고르면_새로_처음부터_찍는다(page):
    pick(page, "TEST-0001")
    shoot(page, 1)
    page.locator("#back").click()
    page.locator("#sheetYes").click()
    pick(page, "GRAM-0002")
    expect(page.locator("#who")).to_have_text("LG-GRAM16 · GRAM-0002")
    expect(page.locator("#stepNo")).to_have_text("단계 1 / 7")


def test_촬영_중_모드_탭을_누르면_나갈지_묻는다(page):
    pick(page, "TEST-0001")
    shoot(page, 1)
    page.locator('#modes button[data-mode="OUT"]').click()
    expect(page.locator("#sheet")).to_be_visible()
    page.locator("#sheetNo").click()
    expect(page.locator("#capture")).to_be_visible()
    expect(page.locator('#modes button[data-mode="QUICK"]')).to_have_attribute("aria-pressed", "true")


def test_빠른_검사를_끝까지_찍으면_뒤집기_안내를_거쳐_판정과_다음_기기가_보인다(page):
    pick(page, "TEST-0001")
    expect(page.locator(".phase.now")).to_contain_text("바닥면")
    shoot(page, 3)
    expect(page.locator("#stepCard")).to_have_class("step notice")
    expect(page.locator("#shoot")).to_be_hidden()
    page.locator("#next").click()
    expect(page.locator(".phase.now")).to_contain_text("상판")
    shoot(page, 3)
    expect(page.locator("#complete")).to_have_class("btn primary big")
    page.locator("#complete").click()
    expect(page.locator("#verdict")).to_have_text("문제 있음")
    expect(page.locator("#resultPhotos figure")).to_have_count(12)
    expect(page.locator("#resultPhotos figure.hit")).to_have_count(1)
    expect(page.locator("#back")).to_be_hidden()
    page.locator("#nextDevice").click()
    expect(page.locator("#waiting")).to_be_visible()


def test_새로고침해도_찍던_빠른_검사_화면으로_돌아온다(page):
    pick(page, "TEST-0001")
    shoot(page, 2)
    page.reload()
    expect(page.locator("#capture")).to_be_visible()
    expect(page.locator("#stepNo")).to_have_text("단계 3 / 7")
    expect(page.locator("#back")).to_be_visible()


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


def test_촬영_중_지금_모드_탭을_다시_눌러도_하던_화면_그대로다(page):
    pick(page, "TEST-0001")
    shoot(page, 1)
    page.locator('#modes button[data-mode="QUICK"]').click()
    expect(page.locator("#sheet")).to_be_hidden()
    expect(page.locator("#stepNo")).to_have_text("단계 2 / 7")


def test_빠른_검사_판정_뒤_학습_모드로_가면_다시_탐지_버튼이_남지_않는다(page):
    page.evaluate("document.getElementById('redetect').hidden = false")  # 판정 실패 뒤 상태를 흉내
    page.locator('#modes button[data-mode="TRAINING"]').click()
    page.locator("#device").fill("MB01")
    page.locator("#start").click()
    expect(page.locator("#capture")).to_be_visible()
    expect(page.locator("#redetect")).to_be_hidden()


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



def finish_quick(page: Page) -> None:
    pick(page, "TEST-0001")
    shoot(page, 3)
    page.locator("#next").click()
    shoot(page, 3)
    page.locator("#complete").click()
    expect(page.locator("#verdict")).to_have_text("문제 있음")


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


def test_판정_화면에서_결과를_다시_읽어도_고른_사진이_그대로다(page):
    finish_quick(page)
    page.locator("#resultPhotos figure").nth(3).click()
    src = page.locator("#stageFrame img").get_attribute("src")
    page.evaluate("pollResult()")  # 결과를 직접 다시 읽고 끝날 때까지 기다림
    expect(page.locator("#stageCap")).to_contain_text("4 / 12")
    assert page.locator("#stageFrame img").get_attribute("src") == src


def test_실시간_크기_막대에서_방향키를_눌러도_판정_사진은_넘어가지_않는다(page):
    finish_quick(page)
    page.locator("#liveToggle").click()
    page.locator("#liveSize").focus()
    page.keyboard.press("ArrowRight")
    expect(page.locator("#stageCap")).to_contain_text("1 / 12")
    page.locator("#stage").click(position={"x": 400, "y": 20})
    page.keyboard.press("ArrowRight")
    expect(page.locator("#stageCap")).to_contain_text("2 / 12")


def test_실시간_화면은_카메라를_골라_보고_크기를_키울_수_있고_다시_열어도_기억한다(page):
    page.locator("#liveToggle").click()
    expect(page.locator("#live")).to_be_visible()
    expect(page.locator("#liveFigCAM1")).to_be_visible()
    expect(page.locator("#liveFigCAM2")).to_be_visible()
    narrow = page.locator("#liveGrid").bounding_box()["width"]
    page.locator('#liveLayout button[data-v="CAM1"]').click()
    expect(page.locator("#liveFigCAM2")).to_be_hidden()
    expect(page.locator("#liveCAM2")).not_to_have_attribute("src", re.compile("."))  # 안 보이는 카메라는 스트림을 끊음
    page.locator("#liveSize").fill("100")
    wide = page.locator("#liveGrid").bounding_box()["width"]
    assert wide > narrow, (narrow, wide)
    assert wide <= TABLET["width"], "화면 밖으로 넘침"
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.reload()
    page.locator("#liveToggle").click()
    expect(page.locator("#liveFigCAM2")).to_be_hidden()
    expect(page.locator("#liveSize")).to_have_value("100")


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


def test_출고_시작에서_부스_서버에_닿지_못하면_연결_실패를_알린다(page):
    page.route("**/api/sessions", lambda route: route.abort())
    page.locator('#modes button[data-mode="OUT"]').click()
    page.locator("#device").fill(MBA.device_id)
    page.locator("#rental").fill(MBA.rental_id)
    page.locator("#checkpoint").fill(MBA.checkpoint_id)
    page.locator("#start").click()
    expect(page.locator("#error")).to_contain_text("부스 서버에 연결하지 못했어요")


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


def test_반납_탭은_지금_쓸_수_없다고_먼저_알리고_입력_칸을_보이지_않는다(page):
    page.locator('#modes button[data-mode="RET"]').click()
    expect(page.locator("#retBlocked")).to_be_visible()
    expect(page.locator("#retBlocked")).to_contain_text("반납 촬영은 아직 쓸 수 없어요")
    expect(page.locator("#manualForm")).to_be_hidden()


def test_학습_탭은_기기_이름_규칙을_보이고_소문자로_넣어도_대문자로_시작한다(page):
    page.locator('#modes button[data-mode="TRAINING"]').click()
    expect(page.locator("#deviceLabel")).to_have_text("기기 이름")
    expect(page.locator("#quickTip")).to_be_hidden()
    page.locator("#device").fill("mb01")
    page.locator("#start").click()
    expect(page.locator("#who")).to_contain_text("MB01")


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


def test_학습_탭과_반납_탭에는_부스_코드_칸이_없다(page):
    page.locator('#modes button[data-mode="TRAINING"]').click()
    expect(page.locator("#codeBox")).to_be_hidden()
    page.locator('#modes button[data-mode="RET"]').click()
    expect(page.locator("#codeBox")).to_be_hidden()
