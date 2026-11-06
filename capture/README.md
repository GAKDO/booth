# 부스 카메라 설정 (C920 × 2, jetson2)

부스 촬영 도구(`trace-booth serve`)를 Jetson에서 처음 띄울 때와 조명별 카메라 값을 정할 때 따라 하는 절차입니다. 부스 제작·배치는 GAKDO/docs `booth/README.md`, 도구 설계는 `specs/001-booth-capture-tool/`에 있습니다.

값(노출·화이트밸런스·초점)은 부스에서 실제로 재서 정합니다. 11/5 jetson2에서 정한 확정값은 3절 "11/5 확정값"에 있습니다.

## 1. 설치

```bash
sudo apt install -y v4l-utils python3-venv   # v4l2-ctl과 venv. sudo 권한이 있는 관리자 계정으로
git clone https://github.com/GAKDO/booth.git ~/booth
# 우분투 24.04 계열은 시스템 파이썬에 pip 설치를 막는다(externally-managed-environment). 가상환경을 쓴다.
# --system-site-packages: JetPack에 들어 있는 OpenCV를 그대로 쓴다
python3 -m venv --system-site-packages ~/trace-venv
~/trace-venv/bin/pip install -e ~/booth"[upload]"
~/trace-venv/bin/python -c "import cv2; print(cv2.__version__)"
export PATH="$HOME/trace-venv/bin:$PATH"   # trace-booth 명령. ~/.bashrc에 넣어 두면 편하다
trace-booth --version
```

### sudo가 없거나 pypi가 막힌 망에서 (11/2 jetson2에서 확인)

학교 와이파이는 pypi.org·files.pythonhosted.org를 막는다(github.com·ports.ubuntu.com은 열림). 이때는 우분투 패키지와 GitHub 소스를 홈 폴더에 풀어 쓴다. sudo가 필요 없다.

먼저 임시 폴더에 venv를 실제로 만들어 본다. `python3-venv`(ensurepip)가 없으면 여기서 실패하므로 관리자에게 `sudo apt install python3-venv`를 한 번 부탁한다(jetson2에는 이미 있었다).

```bash
t=$(mktemp -d); python3 -m venv --system-site-packages "$t/v"; rc=$?; rm -rf "$t"
[ "$rc" -eq 0 ] && echo "venv OK" || echo "venv 실패: 관리자에게 python3-venv 설치 부탁"
```

```bash
# v4l2-ctl과 필요한 런타임 라이브러리를 홈에 푼다
mkdir -p ~/.local/v4l-debs ~/.local/bin && cd ~/.local/v4l-debs
apt download v4l-utils libv4l2rds0t64 libv4l-0t64 libv4lconvert0t64   # 런타임 라이브러리까지 함께 받는다
for d in *.deb; do dpkg -x "$d" ~/.local/v4l; done
cat > ~/.local/bin/v4l2-ctl <<'SH'
#!/bin/sh
LD_LIBRARY_PATH="$HOME/.local/v4l/usr/lib/aarch64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" exec "$HOME/.local/v4l/usr/bin/v4l2-ctl" "$@"
SH
chmod +x ~/.local/bin/v4l2-ctl && ~/.local/bin/v4l2-ctl --version

# boto3는 우분투 패키지, nanoid는 GitHub 소스
mkdir -p ~/.local/py-debs ~/.local/src && cd ~/.local/py-debs
apt download python3-boto3 python3-botocore python3-jmespath python3-s3transfer
# botocore가 쓰는 python3-dateutil·python3-requests·python3-six·python3-urllib3는 보통 이미 있다. 없으면 같은 줄에 더해 받는다:
#   python3 -c "import dateutil, requests, six, urllib3"
for d in *.deb; do dpkg -x "$d" ~/.local/pydeb; done
git clone https://github.com/puyuan/py-nanoid.git ~/.local/src/py-nanoid

python3 -m venv --system-site-packages ~/trace-venv
SP=$(~/trace-venv/bin/python -c "import site; print(site.getsitepackages()[0])")
echo "$HOME/.local/pydeb/usr/lib/python3/dist-packages" > "$SP/trace-debs.pth"
~/trace-venv/bin/pip install --no-index --no-build-isolation --no-deps ~/.local/src/py-nanoid
# --no-build-isolation이라 venv에 보이는 시스템 setuptools로 빌드한다. pyproject는 >=69를 적었지만
# jetson2의 시스템 setuptools 68.1.2로 빌드·설치되는 것을 확인했다(pip가 버전 경고만 낼 수 있음).
~/trace-venv/bin/pip install --no-index --no-build-isolation --no-deps -e ~/booth
~/trace-venv/bin/trace-booth --version
```

도구는 `v4l2-ctl`을 PATH에서 찾으므로 지금 셸에 적용하고 `~/.bashrc`에도 넣는다.

```bash
export PATH="$HOME/.local/bin:$HOME/trace-venv/bin:$PATH"
grep -qF '$HOME/.local/bin:$HOME/trace-venv/bin' ~/.bashrc || echo 'export PATH="$HOME/.local/bin:$HOME/trace-venv/bin:$PATH"' >> ~/.bashrc
```

카메라 장치(`/dev/video*`)를 열려면 계정이 `video` 그룹에 있어야 한다(`id`로 확인). 없으면 관리자에게 `sudo usermod -aG video <계정>`을 부탁하고 다시 로그인한다.

## 2. 연결 확인 6가지

부스 없이 책상 위에서 할 수 있습니다.

- [ ] **① 두 대가 보인다:** `v4l2-ctl --list-devices`에 C920이 두 번 나온다. 안 나오면 한 대씩 꽂아 보고 다른 USB 포트로 옮긴다.
- [ ] **② 고정 경로를 찾았다:** `ls -l /dev/v4l/by-id/`에서 각 카메라의 `...-video-index0` 경로를 적는다. `/dev/videoN` 번호는 재부팅하면 바뀌므로 쓰지 않는다.
  - C920은 by-id 이름에 시리얼이 없다(`usb-046d_HD_Pro_Webcam_C920-video-index0`, 11/2 jetson2에서 확인). 두 대를 같이 꽂으면 이름이 겹치므로 `ls -l /dev/v4l/by-path/`의 USB 포트 경로(`...-usb-0:2.3:1.0-video-index0` 꼴)를 쓰고, 카메라마다 꽂는 포트를 정해 둔다.
- [ ] **③ 앞뒤를 정했다:** 한 대만 손으로 가리고 `v4l2-ctl -d <경로> --stream-mmap --stream-count=1 --stream-to=/tmp/x.jpg`로 찍어 보며 어느 경로가 CAM1(앞)·CAM2(뒤)인지 정한다. 카메라 몸체에 마스킹 테이프로 CAM1·CAM2를 적는다.
- [ ] **④ 해상도가 된다:** `v4l2-ctl -d <경로> --list-formats-ext`에 MJPG 1920x1080이 있다.
- [ ] **⑤ 수동 조절이 된다:** `v4l2-ctl -d <경로> --list-ctrls`에 노출·화이트밸런스·초점 컨트롤이 있다. 커널 버전마다 이름이 다르므로 아래 3장의 이름을 여기에 나온 이름으로 바꿔 쓴다.
- [ ] **⑥ 도구가 찍는다:** 아래처럼 띄우고 태블릿에서 `http://<jetson 주소>:8320`을 열어 기기 ID `REG01`로 SLATE와 TOP·L1을 찍는다. 미리보기 두 장이 나오고 `~/trace-booth-data/REG01/`에 파일이 생긴다.

```bash
export TRACE_BOOTH_CAM1=<CAM1 경로>   # ②에서 적은 by-id 또는 by-path 경로
export TRACE_BOOTH_CAM2=<CAM2 경로>
trace-booth serve --port 8320
```

촬영 목록은 기본이 `mvp`(11/3 방향 전환)입니다. BOT은 L1 → L2 → L3, 뒤집어 TOP은 L3 → L2 → L1로 한 장씩, CAM1·CAM2로 찍어 한 대에 12장(+ID 카드 1장)입니다. 조명 값은 프리셋 파일의 L1·L2·L3를 씁니다. 보관한 28장 목록(9면×여러 조명)이 필요하면 `TRACE_BOOTH_SHOTLIST=full`을 넣습니다. 목록을 바꾸면 하던 촬영은 끝내고 새로 시작합니다.

**옆면 전용 카메라(11/6):** 옆면(`side`) 목록은 CAM1을 책상으로 내리는 대신 책상 높이에 따로 단 카메라로 찍을 수 있습니다. 모델이 달라도(Logitech Brio 등) MJPG 1920x1080을 내면 됩니다. 넣지 않으면 예전처럼 CAM1로 찍습니다.

```bash
export TRACE_BOOTH_SIDE_CAM=<옆면 카메라 by-path 경로>   # 예: ...-usb-0:1.4:1.0-video-index0
export TRACE_BOOTH_SIDE_ROTATE=180                      # 뒤집어 달았으면 180(기본). 바로 달았으면 0
export TRACE_BOOTH_SIDE_PRESETS=~/trace-booth-presets.side.json   # 선택. 없으면 위 카메라 프리셋(L1·L2·OFF)을 그대로 넣음
```

- 옆면 목록의 CAM1 사진(SLATE 포함)만 이 카메라로 찍고, 파일 이름은 `..._CAM1_...` 그대로입니다(카메라 코드 계약을 바꾸지 않음). 어느 카메라였는지는 날짜·`camera_settings`로 구분합니다.
- 실시간 보기에 `옆면` 화면이 생기고, 저장 사진과 같은 방향(회전 적용 뒤)으로 보입니다.

`MINIO_ENDPOINT`를 넣지 않으면 업로드 없이 로컬에만 남습니다. 업로드까지 볼 때는 `MINIO_ENDPOINT`·`MINIO_ACCESS_KEY`·`MINIO_SECRET_KEY`를 환경 변수로 넣습니다.

- 주소는 S3 API `http://storage.java21.net:8000`입니다. `https://storage.java21.net/`은 웹 관리 화면이라 업로드가 안 됩니다. HTTP지만 S3 요청은 SigV4 서명이라 secret key 자체는 전송되지 않습니다(Access Key ID와 서명만). 다만 사진 내용은 같은 망에서 보일 수 있고 가로챈 요청은 약 15분 안에 다시 보낼 수 있어, 부스는 학교 내부망에서만 올립니다. trace-capture 키는 trace-raw에 올리기·확인만 되고 지우기는 안 되며, trace-raw는 버전 관리가 켜져 있습니다. HTTPS는 학교 관리자만 열 수 있어 MVP는 이대로 갑니다(A 11/2).
- 키는 촬영 도구용 서비스 계정(trace-capture) 것을 씁니다. 공용 계정(nhnacademy)은 쓰지 않습니다. 키 값은 채팅·코드·이 문서에 적지 않습니다.

키는 화면에 보이지 않게 직접 입력해 파일 하나에 두고(본인만 읽기), 띄울 때 읽어 들입니다.

```bash
umask 077; mkdir -p ~/.config
read -rsp "access key: " AK; echo; read -rsp "secret key: " SK; echo
printf 'MINIO_ENDPOINT=http://storage.java21.net:8000\nMINIO_ACCESS_KEY=%s\nMINIO_SECRET_KEY=%s\n' "$AK" "$SK" > ~/.config/trace-booth.env
chmod 600 ~/.config/trace-booth.env   # 이미 있던 파일이어도 본인만 읽기
unset AK SK
set -a; . ~/.config/trace-booth.env; set +a   # serve·retry·shots 전에 이 셸에서 한 번
```

업로드가 실패한 사진(버킷이 없었을 때 등)은 로컬에 남아 있으니 `trace-booth retry`로 다시 올리고, `trace-booth shots --device <기기ID> --verify`로 저장소의 SHA-256까지 맞는지 확인합니다.

## 3. 조명별 값 정하기 (부스 완성 뒤)

### 조명 장비 놓기 (11/4 도착분)

| 코드 | 장비 | 자리 | 켜고 끄기 |
|---|---|---|---|
| L1 확산 | VIJIM VL49 1개 | 앞 기둥 꼭대기(38cm), 1/4인치 나사·콜드슈로 고정, 노트북 쪽 40° 내림. 확산지는 A4 여러 장(또는 종이호일)을 이어 크게 만들어 램프에서 15~20cm 떨어뜨려 노트북 위를 넓게 덮음. 밝기 2단계 | VL49 전원 버튼(손) |
| L2 스침광 | 5V USB LED 바 35cm 6000K #1 | 왼쪽 위에 붙이고, 카메라 쪽을 A4로 가림(렌즈로 빛이 바로 들어가지 않게) | USB 허브 포트 스위치 |
| L3 스침광 | 같은 바 #2 | 뒤쪽 바닥 | USB 허브 포트 스위치 |

### 11/5 확정값 (jetson2 실기, MB95 한 바퀴 통과)

모든 학습 사진은 이 배치·값으로 찍습니다. 바꾸면 그 전후 사진이 섞이므로, 바꿀 일이 생기면 먼저 팀에 알립니다.

| 조명 | 노출 | gain | WB | 초점 | 결과(미드나이트 맥북, 노트북 가운데 밝기·날림) |
|---|---|---|---|---|---|
| L1 | 1250 | 80 | 6500 | 25 | 34~42, 0.01% 이하 (가장 어두운 기종 기준) |
| L2 | 2047 | 56 | 6500 | 25 | 53~71, 0.04% 이하 |
| L3 | 2047 | 40 | 6500 | 25 | 98~105, 0.01% 이하, 흠집이 가장 잘 보임 |

- 프리셋 키는 위 값 + `auto_exposure=1`, `white_balance_automatic=0`, `focus_automatic_continuous=0`. jetson2 백업은 `~/trace-booth-presets.final-1105.json`.
- 배경은 무광 검정 천(주름 펴기). 크림색 천은 빛을 튕겨 색을 물들이고 밝기가 달라져서 바꿨습니다.
- 방 조명은 영향 없음(부스 조명을 다 끄면 밝기 3.5). 그래도 촬영 중에는 한 상태로 둡니다.
- VL49를 3단계 이상 올리면 노트북은 거의 밝아지지 않고 CAM2 쪽 정반사만 커져 날립니다. 밝기가 모자라면 단계 대신 확산지를 키웁니다.
- L3를 높이 두면 CAM1(앞) 렌즈로 빛이 들어가 무지개색 플레어가 생깁니다. 바닥에 둡니다.
- 노트북은 받침의 L자 표시에 맞춰 반듯하게 놓고, BOT은 앞 모서리를 들어 뒤로 넘겨 고무발이 보이게 놓습니다. SLATE 뒤에는 ID 카드를 치웁니다.

- VL49는 색온도를 바와 같은 6000K로, 밝기는 50%에서 시작해 노출을 맞춘 뒤 그 값으로 고정합니다. 화면 숫자를 사진으로 남기고, 켤 때마다 같은지 봅니다.
- VL49는 USB-C를 꽂아 둔 채 씁니다(배터리가 줄면서 밝기가 바뀌는 것을 막음). 충전하면서 켜지는지 11/4에 먼저 확인합니다.
- 바 두 개는 Jetson USB에 꽂지 않습니다. 카메라 두 대가 이미 Jetson USB를 쓰므로 전류가 모자라 카메라가 끊길 수 있습니다. 포트마다 스위치가 있는 USB 허브를 5V 2A 이상 충전기에 꽂아 씁니다.
- 앱은 BOT을 L1 → L2 → L3, 뒤집은 뒤 TOP을 L3 → L2 → L1 순서로 안내합니다. VL49는 한 대에 한 번 끄고 한 번 켜면 됩니다.
- 사진에 가로 줄무늬가 보이면 바·VL49 밝기를 최대로 두고 노출 시간을 바꿔 봅니다(LED 디밍 깜빡임).
- 자동 전환(선택): Jetson GPIO + 3.3V로 동작하는 2채널 릴레이로 바 #1·#2의 USB 5V 선을 끊고 잇습니다. VL49는 배터리가 있어 릴레이로 끌 수 없으므로 손으로 둡니다.


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

## 5. 빠른 검사 (11/5 MVP)

운영 화면 맨 위 선택이 **빠른 검사**(기본)면 "검사 기다리는 기기" 목록이 나옵니다. 관리자 화면에서 시점을 시작한 렌탈만 보입니다. 기기를 누르고 안내대로 BOT(뒤집어서) 3장씩 → 다시 뒤집어 TOP 3장씩 찍고 **완료**를 누르면, 몇 초 뒤 "문제 있음/없음"과 흠집 폴리곤이 그려진 사진이 나옵니다. 다음 기기는 **다음 기기**.

서버 주소는 `https://gakdo.site/`입니다(11/3 개통, 강사님 nginx → 학교 서버. `trace.java21.net`은 쓰지 않음). 부스 자격(client id·secret)은 학교 서버 `.env`의 `BOOTH_CLIENT_ID`·`BOOTH_CLIENT_SECRET` 값으로, A에게 따로 받습니다. 처음 한 번만 아래처럼 `~/.config/trace-booth.env`에 더합니다. secret은 화면·셸 기록에 남지 않게 `read -s`로 넣습니다.

`read` 두 줄은 **한 줄씩** 칩니다. 한꺼번에 붙여 넣으면 뒤에 붙은 줄을 입력값으로 먹어 버립니다.

```bash
read -r -p 'BOOTH_CLIENT_ID: ' CID
read -r -s -p 'BOOTH_CLIENT_SECRET(입력이 안 보여요): ' CSECRET; echo
```

나머지는 한꺼번에 붙여 넣어도 됩니다. 다시 해도 같은 줄이 겹치지 않게 먼저 지우고 넣습니다(MINIO_* 줄은 그대로).

```bash
sed -i '/^TRACE_API_URL=/d;/^TRACE_BOOTH_CLIENT_ID=/d;/^TRACE_BOOTH_CLIENT_SECRET=/d;/^TRACE_BOOTH_SHOTLIST=/d' ~/.config/trace-booth.env
umask 077
printf 'TRACE_API_URL=https://gakdo.site/\nTRACE_BOOTH_CLIENT_ID=%s\nTRACE_BOOTH_CLIENT_SECRET=%s\nTRACE_BOOTH_SHOTLIST=mvp\n' "$CID" "$CSECRET" >> ~/.config/trace-booth.env
unset CID CSECRET
chmod 600 ~/.config/trace-booth.env
cut -d= -f1 ~/.config/trace-booth.env   # 이름만 보임. TRACE_ 네 개가 한 번씩이면 됨
```

매번 띄울 때:

```bash
set -a; . ~/.config/trace-booth.env; set +a
# 엔진 만들기·의존성(tensorrt, Jetson 시스템 패키지)은 specs/002-jetson-detection/quickstart.md
export TRACE_BOOTH_ENGINE=~/trace-models/v001/best.engine   # 같은 폴더에 model.json
# 엔진이 오기 전 흐름만 시험: TRACE_BOOTH_ENGINE 대신 export TRACE_BOOTH_DETECTOR=none (늘 "문제 없음" + 시험 표시)
# 판정 시간 줄이기(11/6): 찍을 때마다 미리 탐지는 기본으로 켜져 있음(끄려면 TRACE_BOOTH_PREDETECT=0)
# 그래도 1분을 넘으면 가장 선명한 L3 사진만 판정: export TRACE_BOOTH_DETECT_LIGHTS=L3 (12장 → 4장)
trace-booth serve
```

- 서버 설정이 없으면 목록 자리에 "서버 연결 설정이 없어…"가 나옵니다.
- 목록이 비어 있으면 관리자 화면에서 렌탈을 열고 출고(OUT) 시점을 시작했는지 봅니다. 부스는 진행 중(OPEN) 렌탈 가운데 지금 시점이 OPEN인 것만 받습니다. MVP는 렌탈당 출고 검사 1번입니다.
- 사진은 서버가 준 `https://gakdo.site/trace-captures/...` 주소로 바로 올라갑니다(한 장 64MB까지, 부스 JPEG는 훨씬 작음).
- 엔진을 못 부르면 "판정 못 함"과 이유가 나오고, 고친 뒤 **다시 탐지**를 누르면 같은 사진으로 다시 돌립니다.
- 화면의 "완료→판정 N초"가 11/6 실측값입니다(목표 30초, 기준 60초, 11/5 민서 결정). 찍는 동안 미리 탐지한 사진은 빠지므로, 완료를 누른 뒤 실제로 기다린 시간입니다(BOT 6장은 뒤집는 사이 끝남).
