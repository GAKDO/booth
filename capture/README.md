# 부스 카메라 설정 (C920 × 2, jetson2)

부스 촬영 도구(`trace-booth serve`)를 Jetson에서 처음 띄울 때와 조명별 카메라 값을 정할 때 따라 하는 절차입니다. 부스 제작·배치는 GAKDO/docs `booth/README.md`, 도구 설계는 `specs/001-booth-capture-tool/`에 있습니다.

값(노출·화이트밸런스·초점)은 부스에서 실제로 재서 정합니다. 이 문서에는 값을 적지 않습니다.

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

`MINIO_ENDPOINT`를 넣지 않으면 업로드 없이 로컬에만 남습니다. 업로드까지 볼 때는 `MINIO_ENDPOINT`·`MINIO_ACCESS_KEY`·`MINIO_SECRET_KEY`를 환경 변수로 넣습니다.

- 주소는 S3 API `http://storage.java21.net:8000`입니다. `https://storage.java21.net/`은 웹 관리 화면이라 업로드가 안 됩니다. HTTP라 키와 사진이 암호화되지 않고 오가므로 부스는 학교 내부망에서만 올리고, HTTPS S3 API 주소가 생기면 그 주소로 바꿉니다.
- 키는 촬영 도구용 서비스 계정(trace-capture) 것을 씁니다. 공용 계정(nhnacademy)은 쓰지 않습니다. 키 값은 채팅·코드·이 문서에 적지 않습니다.

키는 화면에 보이지 않게 직접 입력해 파일 하나에 두고(본인만 읽기), 띄울 때 읽어 들입니다.

```bash
umask 077; mkdir -p ~/.config
read -rp "access key: " AK; read -rsp "secret key: " SK; echo
printf 'MINIO_ENDPOINT=http://storage.java21.net:8000\nMINIO_ACCESS_KEY=%s\nMINIO_SECRET_KEY=%s\n' "$AK" "$SK" > ~/.config/trace-booth.env
unset AK SK
set -a; . ~/.config/trace-booth.env; set +a   # serve·retry·shots 전에 이 셸에서 한 번
```

업로드가 실패한 사진(버킷이 없었을 때 등)은 로컬에 남아 있으니 `trace-booth retry`로 다시 올리고, `trace-booth shots --device <기기ID> --verify`로 저장소의 SHA-256까지 맞는지 확인합니다.

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
