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


def call(base, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.headers.get_content_type(), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get_content_type(), e.read()


def test_운영_화면_HTML을_돌려준다(base):
    status, ctype, body = call(base, "GET", "/")
    assert status == 200 and ctype == "text/html"
    assert "TRACE 부스 촬영" in body.decode()


def test_잘못된_기기_ID는_400_Problem_Details(base):
    status, ctype, body = call(base, "POST", "/api/sessions", {"deviceId": "x"})
    assert status == 400 and ctype == "application/problem+json"
    assert json.loads(body)["status"] == 400


def test_시작하지_않고_현재_세션을_물으면_404(base):
    assert call(base, "GET", "/api/sessions/current")[0] == 404


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


def test_카메라가_실패하면_503과_실패한_카메라를_알린다(base, cams):
    from trace_booth.codes import Camera

    call(base, "POST", "/api/sessions", {"deviceId": "MB01"})
    cams[Camera.CAM1].fail = True
    status, _, body = call(base, "POST", "/api/sessions/current/shoot")
    assert status == 503 and json.loads(body)["camera"] == "CAM1"


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


def test_JSON이_아닌_POST는_415로_막아_다른_사이트의_단순_요청을_받지_않는다(base):
    status, _, _ = _raw(base, "POST", "/api/sessions", b'{"deviceId":"MB01"}', {"Content-Type": "text/plain"})
    assert status == 415


def test_다른_출처에서_온_POST는_403(base):
    status, _, _ = _raw(base, "POST", "/api/sessions", b'{"deviceId":"MB01"}',
                        {"Content-Type": "application/json", "Origin": "http://evil.example"})
    assert status == 403


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


def test_접속_비밀이_있으면_비밀_없는_요청은_401(guarded):
    assert _raw(guarded, "GET", "/api/queue")[0] == 401
    assert _raw(guarded, "GET", "/")[0] == 401
    assert _raw(guarded, "POST", "/api/sessions", b'{"deviceId":"MB01"}', {"Content-Type": "application/json"})[0] == 401


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
