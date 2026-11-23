# 이 파일이 확인하는 것: 부스 운영 화면이 쓰는 로컬 API(부스 Jetson 안의 작은 웹 서버).
# 촬영 시작 → 찍기 → 미리보기 → 완료 흐름, 오류 응답 형식, 보안(다른 사이트 요청 막기·접속 비밀)을 본다.

"""운영 화면 로컬 API."""

import json
import threading
import urllib.error
import urllib.request

import pytest

from trace_booth.web import App, make_server


@pytest.fixture
def base(service):
    server = make_server(App(service), "127.0.0.1", 0)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


# 테스트 도구: 로컬 API에 JSON 요청을 보내고 상태·형식·본문을 돌려받는다.
def call(base, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.headers.get_content_type(), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get_content_type(), e.read()


# [상황] 첫 화면을 연다.
# [기대] "TRACE 부스 촬영" 화면 HTML이 온다.
def test_운영_화면_HTML을_돌려준다(base):
    status, ctype, body = call(base, "GET", "/")
    assert status == 200 and ctype == "text/html"
    assert "TRACE 부스 촬영" in body.decode()


# [상황] 잘못된 기기 ID로 촬영을 시작한다.
# [기대] 400과 표준 오류 형식(Problem Details)이 온다.
#        ※ 응답 코드: 400 = 요청 내용이 형식에 맞지 않음
def test_잘못된_기기_ID는_400_Problem_Details(base):
    status, ctype, body = call(base, "POST", "/api/sessions", {"deviceId": "x"})
    assert status == 400 and ctype == "application/problem+json"
    assert json.loads(body)["status"] == 400


# [상황] 촬영을 시작하지 않고 현재 세션을 묻는다.
# [기대] 404.
#        ※ 응답 코드: 404 = 찾을 수 없음(없는 주소나 데이터)
def test_시작하지_않고_현재_세션을_물으면_404(base):
    assert call(base, "GET", "/api/sessions/current")[0] == 404


# [상황] 시작 → 기기 ID 판 찍기 → 미리보기 → 빠진 채 완료 시도 → 끝까지 찍고 완료한다.
# [기대] 찍어도 단계는 그대로이고, 작은·큰 미리보기가 JPEG로 온다. 빠진 사진이 있으면 409와 빠진 목록,
#        끝까지 찍으면 COMPLETE이고 대기열에 29장이 올라갈 차례로 있다.
#        ※ 응답 코드: 409 = 지금 상태와 맞지 않아 거절(이미 있음·순서 어긋남 등)
def test_시작_찍기_미리보기_완료_흐름(base):
    status, _, body = call(base, "POST", "/api/sessions", {"deviceId": "MB01"})
    assert status == 201 and json.loads(body)["step"]["kind"] == "SLATE"
    status, _, body = call(base, "POST", "/api/sessions/current/shoot")
    assert status == 201
    shot = json.loads(body)["shots"][0]
    assert shot["filename"] == "MB01_SLATE_CAM1_01.jpg"
    step = json.loads(body)["state"]["step"]
    assert step["kind"] == "SLATE" and step["taken"]  # 찍어도 단계는 그대로
    status, ctype, thumb = call(base, "GET", f"/api/shots/{shot['id']}/thumbnail")
    assert status == 200 and ctype == "image/jpeg" and thumb.startswith(b"\xff\xd8")
    status, ctype, _ = call(base, "GET", f"/api/shots/{shot['id']}/thumbnail?w=1600")  # 판정 화면 큰 사진
    assert status == 200 and ctype == "image/jpeg"
    status, _, body = call(base, "POST", "/api/sessions/current/complete")
    problem = json.loads(body)
    assert status == 409 and "TOP_L1_CAM1" in problem["missing"]
    while True:
        state = json.loads(call(base, "GET", "/api/sessions/current")[2])
        step = state["step"]
        if step is None:
            break
        if step["optional"]:
            assert call(base, "POST", "/api/sessions/current/skip-scr1", {"reason": "흰 화면 불가"})[0] == 200
        elif step["kind"] == "NOTICE":
            assert call(base, "POST", "/api/sessions/current/next")[0] == 200
        else:
            if not step["taken"]:
                assert call(base, "POST", "/api/sessions/current/shoot")[0] == 201
            assert call(base, "POST", "/api/sessions/current/next")[0] == 200
    status, _, body = call(base, "POST", "/api/sessions/current/complete")
    assert status == 200 and json.loads(body)["session"]["status"] == "COMPLETE"
    assert json.loads(call(base, "GET", "/api/queue")[2]) == {"pending": 29, "failed": 0, "conflict": 0}  # 필수 28 + SLATE 1


# [상황] CAM1이 고장 난 채 찍는다.
# [기대] 503과 함께 실패한 카메라(CAM1)를 알려 준다.
#        ※ 응답 코드: 503 = 서버를 지금 쓸 수 없음(점검·장애)
def test_카메라가_실패하면_503과_실패한_카메라를_알린다(base, cams):
    from trace_booth.codes import Camera

    call(base, "POST", "/api/sessions", {"deviceId": "MB01"})
    cams[Camera.CAM1].fail = True
    status, _, body = call(base, "POST", "/api/sessions/current/shoot")
    assert status == 503 and json.loads(body)["camera"] == "CAM1"


# [상황] 세션·사진 정보를 받고, 내부 번호(1)로 미리보기를 연다.
# [기대] 화면엔 21자 공개 ID만 나가고, 내부 번호로는 찾을 수 없다(404).
#        ※ 응답 코드: 404 = 찾을 수 없음(없는 주소나 데이터)
def test_화면과_API에는_NanoID_21자_publicId만_나가고_내부_정수_id는_나가지_않는다(base):
    import re

    _, _, state = call(base, "POST", "/api/sessions", {"deviceId": "MB01"})
    _, _, shot = call(base, "POST", "/api/sessions/current/shoot")
    nanoid = re.compile(r"^[A-Za-z0-9_-]{21}$")
    assert nanoid.match(json.loads(state)["sessionId"])
    for s in json.loads(shot)["shots"]:
        assert nanoid.match(s["id"])
    status, _, _ = call(base, "GET", "/api/shots/1/thumbnail")  # 내부 id로는 찾을 수 없다
    assert status == 404


# [상황] 미리보기 크기를 여러 값으로 준다.
# [기대] 기본 480, 큰 값은 1920, 작은 값은 160으로 묶고, 음수·글자는 기본값.
def test_미리보기_크기는_선택이고_범위_밖이나_이상한_값은_묶이거나_기본값이다():
    from trace_booth.web import _thumb_width

    assert _thumb_width("/api/shots/x/thumbnail") == 480
    assert _thumb_width("/api/shots/x/thumbnail?w=1600") == 1600
    assert _thumb_width("/api/shots/x/thumbnail?w=99999") == 1920
    assert _thumb_width("/api/shots/x/thumbnail?w=10") == 160
    assert _thumb_width("/api/shots/x/thumbnail?w=-5") == 480
    assert _thumb_width("/api/shots/x/thumbnail?w=abc") == 480


def _raw(base, method, path, data=b"{}", headers=None):
    req = urllib.request.Request(base + path, data=data if method == "POST" else None, method=method,
                                 headers=headers or {})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


# [상황] JSON이 아닌 형식으로 POST한다.
# [기대] 415로 막는다(다른 사이트가 몰래 보내는 단순 요청 차단).
#        ※ 응답 코드: 415 = 받지 않는 데이터 형식
def test_JSON이_아닌_POST는_415로_막아_다른_사이트의_단순_요청을_받지_않는다(base):
    status, _, _ = _raw(base, "POST", "/api/sessions", b'{"deviceId":"MB01"}', {"Content-Type": "text/plain"})
    assert status == 415


# [상황] 다른 사이트(Origin)에서 POST가 온다.
# [기대] 403.
#        ※ 응답 코드: 403 = 권한 없음(누구인지는 알지만 허락되지 않은 일)
def test_다른_출처에서_온_POST는_403(base):
    status, _, _ = _raw(base, "POST", "/api/sessions", b'{"deviceId":"MB01"}',
                        {"Content-Type": "application/json", "Origin": "http://evil.example"})
    assert status == 403


# [상황] 부스 화면 자신에서 POST한다.
# [기대] 정상으로 받는다.
def test_같은_출처의_POST는_그대로_받는다(base):
    host = base.split("//", 1)[1]
    status, _, _ = _raw(base, "POST", "/api/sessions", b'{"deviceId":"MB01"}',
                        {"Content-Type": "application/json", "Origin": f"http://{host}"})
    assert status == 201


@pytest.fixture
def guarded(service):
    server = make_server(App(service, ui_token="s3cret-test"), "127.0.0.1", 0)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


# [상황] 접속 비밀(TRACE_BOOTH_UI_TOKEN)을 설정했는데 비밀 없이 요청한다.
# [기대] 화면·API 모두 401.
#        ※ 응답 코드: 401 = 로그인 필요(토큰이 없거나 만료·위조됨)
def test_접속_비밀이_있으면_비밀_없는_요청은_401(guarded):
    assert _raw(guarded, "GET", "/api/queue")[0] == 401
    assert _raw(guarded, "GET", "/")[0] == 401
    assert _raw(guarded, "POST", "/api/sessions", b'{"deviceId":"MB01"}', {"Content-Type": "application/json"})[0] == 401


# [상황] 비밀이 든 주소로 첫 화면을 연다.
# [기대] 303으로 넘기며 쿠키를 심고(HttpOnly·SameSite=Strict, 비밀 원문은 쿠키에 없음), 그 쿠키나 헤더로 API를 쓴다.
#        비밀 원문을 쿠키에 넣거나 틀린 헤더면 401.
#        ※ 응답 코드: 303 = 다른 주소로 가라는 안내(리디렉션), 401 = 로그인 필요(토큰이 없거나 만료·위조됨)
def test_접속_비밀로_첫_화면을_열면_쿠키를_심고_쿠키로_API를_쓴다(guarded):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    opener = urllib.request.build_opener(NoRedirect)
    try:
        opener.open(guarded + "/?token=s3cret-test")
        raise AssertionError("303이어야 함")
    except urllib.error.HTTPError as e:
        assert e.code == 303
        cookie = e.headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie
    pair = cookie.split(";", 1)[0]
    assert "s3cret-test" not in pair  # 쿠키에 비밀 원문을 두지 않는다
    assert _raw(guarded, "GET", "/api/queue", headers={"Cookie": pair})[0] == 200
    assert _raw(guarded, "GET", "/api/queue", headers={"Cookie": "trace_booth_ui=s3cret-test"})[0] == 401
    assert _raw(guarded, "GET", "/api/queue", headers={"X-Booth-Token": "s3cret-test"})[0] == 200
    assert _raw(guarded, "GET", "/api/queue", headers={"X-Booth-Token": "wrong"})[0] == 401


# [상황] 한 장 찍고 다음으로 간 뒤 "이전 단계"를 누른다.
# [기대] 돌아간 단계와 그 단계에서 찍은 사진을 돌려준다. 첫 단계에서 또 누르면 409.
#        ※ 응답 코드: 409 = 지금 상태와 맞지 않아 거절(이미 있음·순서 어긋남 등)
def test_이전_단계를_누르면_돌아간_단계와_그_단계_사진을_돌려준다(base):
    call(base, "POST", "/api/sessions", {"deviceId": "MB01"})
    shot = json.loads(call(base, "POST", "/api/sessions/current/shoot")[2])["shots"]
    assert call(base, "POST", "/api/sessions/current/next", {"step": 0})[0] == 200
    status, _, body = call(base, "POST", "/api/sessions/current/prev", {"step": 1})
    data = json.loads(body)
    assert status == 200 and data["state"]["step"]["index"] == 0 and data["state"]["step"]["taken"]
    assert [s["id"] for s in data["shots"]] == [s["id"] for s in shot]
    status, ctype, _ = call(base, "POST", "/api/sessions/current/prev", {"step": 0})
    assert status == 409 and ctype == "application/problem+json"
