# 부스 카메라 설정 (C920 × 2, jetson2)

부스 촬영 도구(`trace-booth serve`)를 Jetson에서 처음 띄울 때와 조명별 카메라 값을 정할 때 따라 하는 절차입니다. 부스 제작·배치는 GAKDO/docs `booth/README.md`, 도구 설계는 `specs/001-booth-capture-tool/`에 있습니다.

값(노출·화이트밸런스·초점)은 부스에서 실제로 재서 정합니다. 이 문서에는 값을 적지 않습니다.

## 1. 설치

```bash
sudo apt install -y v4l-utils          # v4l2-ctl
git clone https://github.com/GAKDO/booth.git && cd booth
python3 -m pip install -e ".[upload]"  # OpenCV는 JetPack에 들어 있는 것을 쓴다
python3 -c "import cv2; print(cv2.__version__)"
```

## 2. 연결 확인 6가지

부스 없이 책상 위에서 할 수 있습니다.

- [ ] **① 두 대가 보인다:** `v4l2-ctl --list-devices`에 C920이 두 번 나온다. 안 나오면 한 대씩 꽂아 보고 다른 USB 포트로 옮긴다.
- [ ] **② 고정 경로를 찾았다:** `ls -l /dev/v4l/by-id/`에서 각 카메라의 `...-video-index0` 경로를 적는다. `/dev/videoN` 번호는 재부팅하면 바뀌므로 쓰지 않는다.
- [ ] **③ 앞뒤를 정했다:** 한 대만 손으로 가리고 `v4l2-ctl -d <경로> --stream-mmap --stream-count=1 --stream-to=/tmp/x.jpg`로 찍어 보며 어느 경로가 CAM1(앞)·CAM2(뒤)인지 정한다. 카메라 몸체에 마스킹 테이프로 CAM1·CAM2를 적는다.
- [ ] **④ 해상도가 된다:** `v4l2-ctl -d <경로> --list-formats-ext`에 MJPG 1920x1080이 있다.
- [ ] **⑤ 수동 조절이 된다:** `v4l2-ctl -d <경로> --list-ctrls`에 노출·화이트밸런스·초점 컨트롤이 있다. 커널 버전마다 이름이 다르므로 아래 3장의 이름을 여기에 나온 이름으로 바꿔 쓴다.
- [ ] **⑥ 도구가 찍는다:** 아래처럼 띄우고 태블릿에서 `http://<jetson 주소>:8320`을 열어 기기 ID `REG01`로 SLATE와 TOP·L1을 찍는다. 미리보기 두 장이 나오고 `~/trace-booth-data/REG01/`에 파일이 생긴다.

```bash
export TRACE_BOOTH_CAM1=/dev/v4l/by-id/<CAM1 경로>
export TRACE_BOOTH_CAM2=/dev/v4l/by-id/<CAM2 경로>
trace-booth serve --port 8320
```

`MINIO_ENDPOINT`를 넣지 않으면 업로드 없이 로컬에만 남습니다. 업로드까지 볼 때는 `MINIO_ENDPOINT`·`MINIO_ACCESS_KEY`·`MINIO_SECRET_KEY`를 환경 변수로 넣습니다. 값은 trace-secrets에 있고, 채팅·코드·이 문서에 적지 않습니다.

## 3. 조명별 값 정하기 (부스 완성 뒤)

렌즈 높이 27cm와 두 카메라 범위를 먼저 맞춘 뒤 합니다(docs `booth/README.md` "카메라·조명 맞추기"). 팀 노트북(REG01)을 놓고 조명마다 한 번씩 합니다.

1. 자동을 끈다. 자주 쓰는 이름(⑤에서 확인한 이름으로 바꿔 쓴다):
   - 노출: `auto_exposure=1`(수동), 값은 `exposure_time_absolute`
   - 화이트밸런스: `white_balance_automatic=0`, 값은 `white_balance_temperature`
   - 초점: `focus_automatic_continuous=0`, 값은 `focus_absolute`
   - 깜빡임: `power_line_frequency` (한국 전기는 60Hz)
2. 조명 하나만 켜고(L2·L3·L4는 L1까지 끔) 미리보기를 보며 값을 바꾼다. `v4l2-ctl -d <경로> -c exposure_time_absolute=<값>`
   - 상판의 가장 밝은 곳이 하얗게 날아가지 않을 만큼 노출
   - 그레이카드(오기 전에는 A4)가 회색으로 보이게 화이트밸런스
   - 상판 모서리 글자가 선명하게 초점
   - 가로 줄무늬가 보이면 LED 밝기를 최대로, 노출 시간을 바꿔 본다
3. L2에서는 알려진 흠집을 찍어 확대했을 때 보이는지 확인한다.
4. 두 카메라를 같은 값으로 쓸 수 있는지 본다. 다르면 아래 파일을 카메라별로 나눠야 하므로 B에게 알린다(지금 도구는 조명별 값 하나를 두 카메라에 같이 쓴다).
5. 정한 값을 프리셋 파일에 적는다. 조명 코드는 L1·L2·L3·L4·OFF(SCR1용)를 모두 채운다.

```json
{
  "L1": {"auto_exposure": 1, "exposure_time_absolute": 0, "white_balance_automatic": 0, "white_balance_temperature": 0, "focus_automatic_continuous": 0, "focus_absolute": 0},
  "L2": {"...": 0},
  "L3": {"...": 0},
  "L4": {"...": 0},
  "OFF": {"...": 0}
}
```

(숫자 0은 자리 표시입니다. 실측값으로 바꿉니다.)

```bash
export TRACE_BOOTH_PRESETS=~/trace-booth-presets.json
trace-booth serve --port 8320
```

화면에 "카메라 고정 설정 없음" 경고가 사라지면 적용된 것입니다. 정한 값과 렌즈 높이는 팀 채널에도 올립니다(docs `booth/README.md` "11/2 끝낼 때").

## 4. 확인 한 바퀴

`specs/001-booth-capture-tool/quickstart.md`의 Jetson 절차대로 REG01을 처음부터 끝까지 찍습니다. 중간에 랜선을 뽑았다 꽂아 "올리지 못함" 수가 0으로 돌아오는지, 끝나고 `trace-booth shots --device REG01 --verify`가 모두 "일치"인지 봅니다.
