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
    status, ctype, thumb = call(base, "GET", f"/api/shots/{shot['id']}/thumbnail")
    assert status == 200 and ctype == "image/jpeg" and thumb.startswith(b"\xff\xd8")
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
            assert call(base, "POST", "/api/sessions/current/shoot")[0] == 201
    status, _, body = call(base, "POST", "/api/sessions/current/complete")
    assert status == 200 and json.loads(body)["session"]["status"] == "COMPLETE"
    assert json.loads(call(base, "GET", "/api/queue")[2]) == {"pending": 29, "failed": 0, "conflict": 0}  # 필수 28 + SLATE 1


def test_카메라가_실패하면_503과_실패한_카메라를_알린다(base, cams):
    from trace_booth.codes import Camera

    call(base, "POST", "/api/sessions", {"deviceId": "MB01"})
    cams[Camera.CAM1].fail = True
    status, _, body = call(base, "POST", "/api/sessions/current/shoot")
    assert status == 503 and json.loads(body)["camera"] == "CAM1"
