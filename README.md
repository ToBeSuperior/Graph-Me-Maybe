# Graph-Me-Maybe

PDF, XLSX, PPTX, DOCX 문서를 읽어 하나의 Neo4j 그래프로 만들고, 그 그래프로 질문에 답하는 local-first 웹 앱입니다.
저장소에는 코드만 있습니다. 문서, 그래프, 키는 모두 각자의 컴퓨터에 둡니다.

---

## 설치 프로그램으로 (macOS · Windows · Linux/WSL)

터미널에 한 줄을 붙여 넣으면 앱과 필요한 것(Python, 웹 앱, 문서 뷰어, 페이지를 그릴 브라우저,
샘플 문서)이 설치되고 앱이 열립니다. 다시 실행하면 업데이트됩니다.

```bash
# macOS
curl -fsSL https://raw.githubusercontent.com/ToBeSuperior/Graph-Me-Maybe/main/install.sh | sh
```

```powershell
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/ToBeSuperior/Graph-Me-Maybe/main/install.ps1 | iex"
```

Linux는 macOS와 같은 `install.sh` 한 줄을 씁니다. Windows에서는 WSL 안이 아니라 PowerShell에서 설치합니다.
WSL에서 `install.sh`를 실행하면 Windows 설치 한 줄을 알려 주고 멈춥니다(그래도 WSL에 설치하려면
`curl … | GRAPHME_WSL=1 sh`). 전에 WSL에 깔았던 앱은 WSL에서 이렇게 완전히 지웁니다(키·그래프까지):

```bash
pkill -f graphme
curl -fsSL https://raw.githubusercontent.com/ToBeSuperior/Graph-Me-Maybe/main/install.sh | GRAPHME_PURGE=1 sh -s -- --uninstall
docker rm -f graphme-neo4j; docker volume rm graphme-neo4j-data graphme-neo4j-logs
``` 한글은 앱에 든 글꼴(Pretendard)로 그리므로
글꼴을 따로 설치하지 않아도 됩니다. WSL에서 `graphme app`은 Windows 기본 브라우저로 화면을 엽니다.

- 그 뒤로는 응용 프로그램 폴더(macOS) 또는 시작 메뉴(Windows)의 **Graph-Me-Maybe**로 엽니다. 주소창 없는
  앱 창으로 열리고(Windows는 Edge WebView2, macOS는 WebKit), 창을 닫으면 앱도 꺼집니다. 터미널에서
  `graphme app`으로 열면 브라우저 탭으로 열리고, 그 터미널을 닫아야 꺼집니다. Linux와 WSL은 늘 브라우저로 엽니다.
  키는 앱의 **관리 › 키와 모델**에서 넣습니다.
- 그래프 DB(Neo4j)는 Docker로 뜹니다. Docker Desktop이 없으면 설치할지 묻습니다(개인·소규모
  회사는 무료, 큰 회사는 유료 구독. 처음 한 번 관리자 권한, Windows는 WSL 2와 재시작이 필요할 수
  있음). 설치하지 않아도 앱은 열리고, Docker가 생기면 그래프를 만들 수 있습니다. 그래프가 필요할 때
  Docker Desktop이 꺼져 있으면 앱이 켜고, 준비될 때까지 "Docker를 켜는 중"을 보여 줍니다(Windows·macOS).
  끄려면 앱 폴더의 `.env`에 `GRAPHME_DOCKER_AUTOSTART=0`. 처음 켤 때 뜨는 Docker의 약관 동의는 직접 해야 합니다.
- Chrome이 없으면 Playwright의 Chromium을 받아 씁니다(관리자 권한 없음).
- 앱 폴더: macOS `~/Library/Application Support/Graph-Me-Maybe`, Windows
  `%LOCALAPPDATA%\Graph-Me-Maybe` — `.env`(키), 그래프 데이터, 웹 앱, 뷰어. 터미널의 `graphme` 명령도
  어느 폴더에서 실행하든 이 폴더의 키·데이터·뷰어를 씁니다(지금 폴더에 `.env`가 있으면 그것을 씀).
- `GRAPHME_WSL=1`로 한 PC에 WSL 앱과 Windows 앱을 같이 두었다면 하나만 켜서 씁니다. WSL에서 연 포트(앱 8765, 그래프 DB 7687)는
  Windows의 `127.0.0.1`로도 열리기 때문입니다. 두 앱은 서로를 자기 것으로 여기지 않습니다. 다른 쪽 앱이
  켜져 있으면 이쪽 앱은 다음 포트(8766…)에 뜨고, 이쪽 Docker가 꺼져 있을 때 답하는 다른 쪽 그래프 DB는 쓰지
  않고 멈춥니다. 다만 Docker Desktop의 WSL 통합을 켜 두면 두 쪽이 Docker를 함께 써서 그래프도 하나가 됩니다.
  그러면 한쪽에서 올린 문서를 다른 쪽에서 열 때 파일 경로가 맞지 않아 페이지를 그리지 못합니다.
- 옵션(환경변수): `GRAPHME_VERSION`(버전 고정), `GRAPHME_YES=1`(묻지 않고 설치),
  `GRAPHME_NO_DOCKER=1`, `GRAPHME_HOME`(앱 폴더). 환경변수는 파이프 뒤의 `sh`에 붙어야 스크립트에
  닿습니다(`GRAPHME_YES=1 curl … | sh`는 `curl`에만 붙어 효과가 없음):

  ```bash
  curl -fsSL https://raw.githubusercontent.com/ToBeSuperior/Graph-Me-Maybe/main/install.sh | GRAPHME_YES=1 sh
  curl -fsSL https://raw.githubusercontent.com/ToBeSuperior/Graph-Me-Maybe/main/install.sh | sh -s -- -y
  ```

  ```powershell
  $env:GRAPHME_YES='1'; irm https://raw.githubusercontent.com/ToBeSuperior/Graph-Me-Maybe/main/install.ps1 | iex
  ```
- 업데이트는 앱을 닫고 실행합니다. 앱이 열려 있으면 설치가 아무것도 바꾸지 않고 멈춥니다. 앱 창을 닫으면
  됩니다. 창이 보이지 않으면 macOS는 `pkill -f graphme`, Windows는 작업 관리자에서 `graphme-app.exe`를 끝냅니다.
- 지우기: macOS `curl -fsSL …/install.sh | sh -s -- --uninstall`, Windows는 설정 › 앱에서
  Graph-Me-Maybe 제거(또는 `$env:GRAPHME_UNINSTALL=1` 뒤 같은 한 줄). 키와 그래프까지 지우려면
  `GRAPHME_PURGE=1`: 앱 폴더에서 앱이 만든 것(`.env`, `.graphme`, `web`, `viewer`, `app.log`,
  `webview`(앱 창의 저장소), Windows의 `install.ps1`)만 지우고, 폴더는 그 뒤 비어 있을 때만 지웁니다. Docker 안의 그래프는 아래
  "그래프 DB(Neo4j)는 어떻게 뜨나"의 명령으로. 앱 폴더가 사용자의 홈 폴더이거나 그것을 품고 있으면
  `GRAPHME_PURGE`는 지우지 않고 거절합니다. Windows는 앱을 먼저 닫아야 지워집니다.
- 지우기가 남기는 것: Playwright가 받은 Chromium(macOS `~/Library/Caches/ms-playwright`, Windows
  `%LOCALAPPDATA%\ms-playwright` 폴더를 지움)과 uv 자체(`uv cache clean` 뒤
  [uv 제거 안내](https://docs.astral.sh/uv/getting-started/installation/#uninstallation)대로).

아래 Quick Start는 저장소를 받아 개발하는 경로입니다.

## Quick Start — 저장소에서

폴더를 고르면 문서를 읽어 그래프로 만들고, 질문하면 답과 관련 문서를 폴더로 모아 보여주는
웹 앱을 clone 직후부터 띄우는 순서입니다. 서버는 `tools/hierarchy_demo/app.py`(`graphme serve`에 샘플 문서를 더한 데모),
화면은 `frontend/`입니다.

### 준비물

| 항목 | 용도 | 없으면 |
|---|---|---|
| Python 3.11+, [`uv`](https://docs.astral.sh/uv/) | 앱 실행 (`.python-version`의 3.11을 uv가 알아서 받음) | 실행 불가 |
| Node.js 24+ (npm 11+) | 웹 앱 빌드 (`frontend/`), 뷰어 번들 빌드 (npm 10은 뷰어 설치에서 멈춤) | 화면이 없음 |
| Google Chrome (또는 `uv run playwright install chromium --only-shell`) | DOCX·PPTX·XLSX 렌더링 | DOCX·PPTX·XLSX를 그리지 못함 (PDF는 브라우저 없이 됨) |
| [rustup](https://rustup.rs/) + `rustup target add wasm32-unknown-unknown`, [`wasm-pack`](https://github.com/rustwasm/wasm-pack) | 뷰어 번들 1회 빌드 (XLSX 파서를 wasm으로 컴파일) | 〃 (`setup.sh`가 `wasm-pack: not found`로 멈춤) |
| Docker | 그래프 DB(Neo4j) 자동 실행, `.dump`·Aura 내보내기 | 아래 "그래프 DB(Neo4j)는 어떻게 뜨나" 참고 |
| 인터넷 | 뷰어 번들 받기(npm·GitHub), Neo4j 이미지 받기, 모델·임베딩 호출 | 설치·실행 불가 |

이 컴퓨터에서 비어 있어야 하는 포트: 8765(데모), 7687(Neo4j).
데모와 Neo4j 포트는 바꿀 수 있습니다(3단계, "그래프 DB(Neo4j)는 어떻게 뜨나").

순서는 **0 준비물 설치 → 1 내려받기와 설치 → 2 모델 연결 → 3 실행 → 4 키 넣고 체험하기**이고, 각 단계 끝에 확인할 것이 있습니다.

### 0. 준비물 설치

이미 있는 것은 건너뜁니다. 끝나면 버전을 확인합니다(PDF만 다룬다면 Chrome·rustup은 없어도 됩니다).

```bash
uv --version; node --version; docker info >/dev/null && echo docker ok
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --version   # Linux: google-chrome --version
```

**macOS** ([Homebrew](https://brew.sh) 기준)

```bash
brew install uv node                                   # Python은 uv가 알아서 받음
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y   # rustup (새 터미널부터 PATH에 잡힘)
~/.cargo/bin/rustup target add wasm32-unknown-unknown
brew install wasm-pack                                # 뷰어 번들의 XLSX 파서 빌드
brew install --cask google-chrome                     # 이미 있으면 생략

# Docker: 아래 둘 중 하나
# Docker Desktop: https://www.docker.com/products/docker-desktop/ 에서 받아 설치하고 앱을 한 번 실행
brew install colima docker && colima start --cpu 2 --memory 4 --port-forwarder grpc   # 또는 colima (앱 없이 터미널로)
docker info                                           # 오류 없이 나오면 준비 완료
```

**Linux** (Ubuntu 기준)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash && nvm install 24   # 새 터미널에서 nvm
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
~/.cargo/bin/rustup target add wasm32-unknown-unknown
# wasm-pack: 미리 빌드된 릴리스를 ~/.cargo/bin에 (cargo install wasm-pack은 컴파일이 오래 걸림)
curl -fsSL https://github.com/rustwasm/wasm-pack/releases/download/v0.15.0/wasm-pack-v0.15.0-x86_64-unknown-linux-musl.tar.gz \
  | tar xz -C /tmp && install -m 755 /tmp/wasm-pack-v0.15.0-x86_64-unknown-linux-musl/wasm-pack ~/.cargo/bin/
# Chrome (/usr/bin/google-chrome에 설치되면 앱이 알아서 찾음)
wget -q -P /tmp https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
sudo apt install -y /tmp/google-chrome-stable_current_amd64.deb
# 한글은 앱에 든 글꼴로 그림. 중국어·일본어 문서도 그린다면 CJK 글꼴:
sudo apt install -y fonts-noto-cjk && fc-cache -f
# Docker Engine: https://docs.docker.com/engine/install/ubuntu/ 를 따른 뒤
sudo usermod -aG docker "$USER"                       # 다시 로그인해야 적용 -- 앱은 sudo 없이 docker를 부릅니다
docker info
```

**Windows** — WSL2 안의 Ubuntu에서 위 Linux 순서를 따릅니다(WSL2 + Ubuntu 20.04에서 확인).

- 명령은 모두 WSL 터미널에서 실행합니다. `sudo`는 비밀번호를 물으므로 일반 터미널(VS Code 터미널, WSL 창)에서 칩니다 —
  비밀번호를 입력할 수 없는 곳(에이전트의 셸 등)에서는 `sudo: a terminal is required`로 멈춥니다.
- Docker는 Docker Desktop의 WSL 통합을 켜거나 WSL 안에 Docker Engine을 설치합니다. WSL 안에서 `docker info`가 sudo 없이 되면 됩니다.
- WSL 안에서 띄운 데모(`127.0.0.1:8765`)는 Windows 브라우저에서 같은 주소로 열립니다. `graphme app`은 WSL에서 Windows 기본
  브라우저로 화면을 엽니다(`wslview`, 없으면 `explorer.exe`). 다른 브라우저를 쓰려면 `BROWSER`에 적습니다.
- macOS 경로(`/Applications/Google Chrome.app/...`)가 아니라 WSL 안의 `google-chrome`이 필요합니다 — 페이지를 그리는 데
  쓰므로 지우지 않습니다. Windows에 설치된 Chrome은 서버가 쓸 수 없습니다.
- 한글은 앱에 든 글꼴(Pretendard)로 그립니다. 페이지를 그리는 WSL 안의 Chrome은 Windows의 글꼴(맑은 고딕 등)을 보지 못하므로,
  앱이 Chrome에 이 글꼴을 직접 알려 줍니다.
- Ubuntu의 기본 Node는 너무 오래됐으므로 nvm으로 24를 받습니다. Node 22(npm 10)는 뷰어 번들을 설치하다 멈춥니다.

**Docker에 대해**
- 앱은 `docker` 명령만 부르므로 무엇으로 돌리든 상관없습니다(Docker Desktop, OrbStack, colima, Docker Engine).
  `docker info`가 sudo 없이 되면 됩니다.
- Docker는 그래프 DB(Neo4j)를 띄우는 데 씁니다. Neo4j를 따로 설치하거나 계정을 만들 필요는 없습니다.
- Docker VM 메모리는 4GB 이상을 권합니다(Neo4j 기본 heap 1g + 페이지 캐시 512m). colima 기본값(2GB)으로는 빠듯합니다.
- colima는 `--port-forwarder grpc`로 띄웁니다. 기본값(ssh)에서는 처음 띄운 Neo4j의 포트로 응답이 오지 않아 `120초 안에 응답하지 않았습니다`로 멈추는 일이 있었습니다.
- colima와 Docker Desktop은 재부팅하면 꺼집니다. 데모 전에 `colima start`(또는 Docker Desktop 실행)를 먼저 합니다.
  colima를 로그인 때 자동으로 켜려면 `brew services start colima`.

### 1. 내려받기와 설치


```bash
git clone https://github.com/ToBeSuperior/Graph-Me-Maybe.git
cd Graph-Me-Maybe
uv sync --extra dev                               # Python 3.11과 의존성
(cd frontend && npm ci && npm run build)          # 웹 앱 (npm의 fsevents 경고는 무시해도 됨)
cp .env.example .env                              # 설정 파일 -- 다른 컴퓨터의 .env를 복사해 오지 않습니다
src/graphme/adapters/rendering/viewer/setup.sh   # 문서 뷰어 번들, 1회 (인터넷 필요, 약 1~2분)
```

- 뷰어 번들은 `~/.cache/graphme/ooxml-viewer`에 생깁니다. 다른 곳에 두려면 `setup.sh` 전에 `.env`에
  `GRAPHME_VIEWER_DIR`을 적습니다. 저장소 안에 둔다면 `.gitignore`에도 그 경로를 더합니다.
- `setup.sh`는 rustup의 Rust를 Homebrew `rustc`보다 먼저 씁니다(Homebrew `rustc`에는 wasm32 타깃이 없습니다).
  `rust-lld: Library not loaded: @rpath/libLLVM.dylib`로 멈추면 `viewer/README.md`의 심볼릭 링크로 해결합니다.
- Chrome이 기본 위치에 없으면 `.env`에 `GRAPHME_CHROME_EXECUTABLE`을 적습니다.

### 2. 모델 연결

문서 읽기와 답변은 OpenAI·Anthropic·Gemini 중 하나의 API로 부릅니다(`GRAPHME_PROVIDER`). 따로 설치할 것은 없고, 4단계에서 고른 제공자의 키만 넣습니다. 라이브러리로만 쓰려면 clone한 폴더에서 `pip install .` 하나로 CLI·Python API·서버가 모두 깔립니다(PyPI에는 아직 올리지 않았습니다). 웹 앱과 뷰어까지 한 번에는 위 설치 프로그램을 쓰세요.

### 3. 실행

Docker가 켜져 있는지 먼저 봅니다(재부팅 뒤에는 꺼져 있습니다).

```bash
docker info >/dev/null && echo docker ok      # 안 되면 colima start 또는 Docker Desktop 실행
uv run python tools/hierarchy_demo/app.py     # http://127.0.0.1:8765 (다른 포트: 끝에 8766처럼 붙임)
```

브라우저에서 `http://127.0.0.1:8765`를 엽니다.

- 샘플 없이 서버만 쓰려면 `uv run graphme serve`입니다(아래 "HTTP로 쓰기"). 화면은 같고 **샘플 문서로 체험**만 없습니다.
- 처음 실행이면 앱이 Docker로 Neo4j를 받아 띄우는 1~2분 동안 상단에 **그래프 DB 준비 중…**이 보이고, 터미널에
  `로컬 Neo4j 준비됨`이 찍히면 끝난 것입니다. Neo4j를 따로 설치하거나 계정을 만들 필요는 없습니다.
- 키가 없거나 거절되면, 또는 모델 서버가 꺼져 있거나 뷰어 번들이 없으면 화면 맨 위와 터미널에 `준비 안 됨: …`이
  나옵니다. 키는 다음 단계에서 넣습니다.

### 4. 키 넣고 체험하기

**관리 › 키와 모델**(화면 맨 위의 **설정 열기**)에서 넣습니다. 저장하기 전에 키가 맞는지 확인하고(모델 목록만 조회,
비용 없음), 저장하면 `.env`에 남아 바로 적용됩니다. 키는 화면에 "설정됨 · …끝 4자리"로만 보입니다.

| 키 | 쓰는 곳 |
|---|---|
| `OPENAI_API_KEY` | `GRAPHME_PROVIDER=openai`이면 문서 읽기와 답변, 임베딩이 openai이면 검색 (platform.openai.com) |
| `ANTHROPIC_API_KEY` | `GRAPHME_PROVIDER=anthropic`이면 문서 읽기와 답변. Anthropic에는 임베딩이 없어 검색에는 OpenAI 키(또는 `GRAPHME_EMBEDDING_PROVIDER=gemini`와 Gemini 키)가 따로 필요 |
| `GEMINI_API_KEY` | `GRAPHME_PROVIDER=gemini`이면 문서 읽기와 답변, 임베딩이 gemini이면 검색 |

검색을 위해 문서 구절이 임베딩 제공자로 갑니다.

- **페이지당 읽기 횟수**는 데모 기본값이 2입니다(페이지마다 두 번 읽고, 갈린 페이지만 한 번 더). 비용을 줄이려면 1로 둡니다.
- Neo4j 계정은 필요 없습니다(Aura 계정은 내보낼 때 화면에서만 입력).
- 화면 대신 `.env`를 직접 고쳐도 됩니다. 셸 환경변수가 `.env`보다 우선합니다.

맨 위 경고가 사라지면 **질문** 탭에서 **샘플 문서로 체험**을 누릅니다(내 문서는 폴더를 끌어다 놓거나 **폴더 선택**).
가상의 회사 문서 10개(그 안에 첨부된 문서까지 그래프에는 19개)를 만들어 올리기 → 읽기 → 잇기 → 검색 준비를 거치며, 1분 안팎 걸립니다. 폴더 안의
xlsx·pptx·docx·pdf가 아닌 파일과 숨김·잠금(`~$`) 파일은 건너뜁니다.

**끝까지 됐는지 확인**
1. 상단에 `문서 19 · 연결 …`이 보입니다.
2. `WO-2026-031`을 물으면 답변과 함께 작업지시서가 1위로 나오고, 결과를 누르면 오른쪽에 그 페이지가 그려집니다.
3. 아무 뜻 없는 말을 물으면 **관련 문서 없음**이 나옵니다.

같은 일을 CLI로도 할 수 있습니다(같은 그래프 공간을 씁니다).

```bash
uv run graphme build-graph <폴더>     # 읽고, 그래프 공간에 넣고, 문서끼리 잇기
uv run graphme embed-graph            # 질문용 청크 임베딩, 중요도, 내용 유사 연결
uv run graphme ask "질문"              # 답과 근거
```

데모와 `build-graph`는 항상 계층을 읽습니다(VLM 호출이 페이지마다 있습니다).

---

## 화면 읽는 법

화면 언어는 브라우저를 따르고, 설정(오른쪽 위)에서 바꿀 수 있습니다. 영어 화면에서는 **질문**=Ask, **관리**=Manage,
**샘플 문서로 체험**=Try sample documents입니다.

상단 막대에는 **질문**과 **관리** 두 탭, 그래프에 든 문서·연결 수(빌드 중이면 진행률), 설정(테마·언어)이 있습니다.
`⌘K`(Ctrl+K)는 어디서나 질문 칸으로 갑니다.

**질문** — 질문하면 세 칸으로 바뀝니다(넓은 창 기준. 좁은 창에서는 가운데 칸만 보입니다).

- **왼쪽:** 이 탭에서 한 최근 질문 10개(새 질문은 맨 위, 다시 연 질문은 제자리). 질문은 주소(`?q=…&k=…`)에도 남아 북마크·공유할 수 있고, 다시 열면 지금 그래프로 다시 찾습니다.
- **가운데 위 — 답변:** 찾은 문서로 쓴 답입니다. 답은 질문의 언어로 쓰고, 숫자·코드만 있어 언어를 알 수 없으면 영어로 씁니다. 처음엔 네 줄만 보이고, 답 안의 `[파일명]`을 누르면 아래 폴더에서 그 문서가 선택되고
  오른쪽에 페이지가 열립니다. 답변은 멈추거나 다시 쓸 수 있습니다.
- **가운데 — 찾은 문서(폴더):** 질문과 맞는 구절의 문서가 위 폴더(1 hop, 순위 번호), 그 안에 연결된 문서(2 hop), 그 안에 그 연결(3 hop).
  - 행마다 hop 번호, 위 문서와의 관계(← 인용됨 · → 인용함 · ≈ 유사), 왜 나왔는지 보여주는 구절 한 줄, 파일 수정일이 있습니다.
  - **답변 근거** 표시는 답이 인용한 문서, **↗ n곳에 더**는 다른 폴더에도 있는 문서입니다.
  - 처음엔 상위 세 폴더만 펼쳐지고, 접힌 폴더는 안에 든 수(+N)를 보여줍니다. 형식(DOCX·PPTX…)으로 거를 수 있습니다.
  - 키보드: ↑↓ 이동, → 펼치기/들어가기, ← 접기/위로, Enter 열기.
  - "구절"은 처음 찾을 구절 수입니다. 문서가 적은데 이 값이 크면 모든 문서가 1 hop으로 나옵니다.
  - 합친 결과에 남는 구절이 없으면 **관련 문서 없음**으로 보고, 답변도 만들지 않습니다(아래 "검색 방식"의 4).
  - 검색은 하이브리드 검색입니다(아래 "검색 방식"). "상대 관련도"는 두 검색 모두에서 1등이면 1.00입니다.
- **오른쪽 — 미리보기:** 경로(질문 › 문서 › 연결 문서), 일치한 구절을 하나씩(‹ ›), 그 구절이 있는 페이지. 구절이 있는 페이지 탭에는 점이 찍힙니다.
  좁은 화면에서는 옆에서 밀려 나오는 창으로 열립니다.

**관리**

- **빌드 취소:** 진행 표시의 **취소**를 누르면 아직 읽지 않은 문서는 건너뛰고, 이미 넣은 문서는 그래프에 남긴 채 검색 준비까지 마칩니다.
  이미 보낸 모델 호출은 끝까지 가고, 그렇게 다 읽힌 문서는 목록의 "그래프 밖"에서 **다시 읽기**로 비용 없이 넣거나 **목록에서 빼기**로 지웁니다.

- **문서:** 그래프에 든 문서와 아직 그래프 밖에 있는 문서(읽는 중·실패)를 상태와 파일 수정일과 함께 보여줍니다.
- **문서 연결:** 문서마다 원 하나. 실선 화살표는 인용(인용하는 쪽 → 인용되는 쪽), 점선은 내용 유사 연결입니다. 클릭하면 연결된 문서에
  테두리가 생기고, 더블클릭하면 그 문서를 엽니다. 연결이 많은 문서부터 최대 300개만 그립니다.
- **문서 화면:** 왼쪽은 계층 그래프(원형·트리·Neo4j식 배치, 목록 보기), 오른쪽은 렌더된 페이지입니다. 노드를 누르면 그 노드의 내용·크롭·
  속성이 페이지 자리에 나옵니다. 청크(구절) 범위는 노드 뒤의 옅은 띠로, 진할수록 문서에서 중요한 구절입니다.
  목록 보기는 문서의 읽는 순서 그대로, 그래프에서 접힌 노드까지 모두 보여 줍니다.
- **인용 연결:** 다른 문서의 이름·문서 번호·제목을 적은 곳에서, 그 이름에 맞는 문서가 있으면 모두 잇습니다.
  같은 파일의 여러 버전은 모두 연결됩니다.
- **내용 유사 연결:** 두 문서에서 서로 가장 가까운 청크 쌍의 cosine을, 양쪽 청크의 중요도로 가중 평균한 값이
  0.8 이상일 때만 잇습니다. 문서마다 상위 20개까지입니다. 주제가 같아도 이 기준에 못 미치면 연결되지 않습니다.
- **문서 삭제:** 문서 화면이나 목록의 휴지통에서, 문서 이름을 보여주는 확인 창을 거쳐 그래프 DB에서 지웁니다. 문서의 노드·청크와
  그 문서로 들어오고 나가는 인용·유사 연결이 함께 지워지고, 다른 문서의 연결은 그대로입니다. 그래프를 만드는 중에는
  지울 수 없습니다. CLI는 `uv run graphme delete-document <document_id>...`(확인을 묻고, `--yes`면 묻지 않음).
- **위험 구역 — 그래프 초기화:** 지워질 문서 수를 보여주고, 확인 단어(`초기화`/`reset`)를 입력해야 실행됩니다. 그래프 DB의 문서를
  모두 지우고(인덱스는 남김) 데모가 읽어 둔 결과도 함께 지우므로, 그 뒤에 폴더를 추가하면 처음부터 다시 읽습니다.
  CLI는 `uv run graphme reset-graph`.
- **같은 파일을 다시 올리면:** 내용이 그래프에 이미 있으면 다시 읽지 않습니다(VLM 비용 없음). 샘플 문서도 매번 같은 바이트로
  만들어지므로 두 번 눌러도 한 번만 읽습니다. 다시 읽게 하려면 `graphme build-graph <폴더> --again`.

---

## 검색 방식: 하이브리드 검색 (벡터 + BM25, 가중 RRF)

질문은 두 가지 검색을 함께 돌려 그 순위를 합칩니다. 두 검색 모두 Neo4j 인덱스에서 실행되고, 합치는 쿼리도 Neo4j 안에서 돕니다.

| 단계 | 내용 |
|---|---|
| 1. 벡터 검색 (ANN) | 질문을 임베딩해 Neo4j 벡터 인덱스(HNSW)에서 뜻이 가까운 구절을 찾습니다. |
| 2. 전문 검색 (BM25) | Neo4j 전문 검색 인덱스에서 단어가 일치하는 구절을 찾습니다. 문서번호·부품번호처럼 글자 그대로 맞아야 하는 것을 잡습니다. 한국어는 `cjk` 분석기로 두 글자씩 잘라, 조사가 붙어도(압력은·압력을) 맞습니다. |
| 3. 가중 RRF로 합치기 | 두 검색에서 각각 top_k의 4배(최소 40, 최대 100)까지 후보를 가져와, 순위 r의 구절에 `가중치 / (k + r)`를 주고 더합니다. 한쪽에서만 찾은 구절도 순위에 들어갑니다. |
| 4. 관련 문서 없음 | 순위는 관련 여부를 말해주지 않으므로(어떤 질문이든 1등은 있음), 합칠 때 근거가 있는 구절만 남깁니다. 질문과의 cosine이 `GRAPHME_RELEVANCE_FLOOR`(text-embedding-3-large는 0.2) 이상이거나, 질문의 단어를 모두 담았거나, 질문이 말한 문서번호를 담은 구절입니다. 그래서 "FLUXTRAN"처럼 단어 하나만 물어도 그 단어가 있는 구절은 찾고, 일부 단어만 우연히 겹친 엉뚱한 질문은 대개 아무것도 찾지 않고, 하한을 넘는 구절이 있더라도 답변 단계가 근거가 질문의 전제를 담지 않으면 답할 수 없다고 말합니다. 남는 구절이 없으면 관련 문서 없음입니다. |
| 5. 그래프 따라가기 | 찾은 구절의 문서에서 인용·인용됨·내용 유사 연결을 따라 2·3 hop 문서와 그 문서에서 질문에 가까운 구절을 가져옵니다. |

| 설정 | 기본값 | 근거 |
|---|---|---|
| `GRAPHME_RRF_K` | 60 | Cormack et al.(SIGIR 2009)이 고정한 값, 이후 표준 기본값 |
| `GRAPHME_HYBRID_DENSE_WEIGHT` | 0.6 (BM25 0.4) | 조정 없이는 가중치 없는 RRF를 이기기 어렵다는 결과(Bruch et al., TOIS 2023)로 균형 가까이, 일반 질문엔 벡터가 낫다는 결과로 벡터 쪽으로 약간 |
| 문서번호가 든 질문 | 0.4 (BM25 0.6) | 구체적인 질문엔 BM25가 낫다는 결과와 질문별 동적 가중 RRF(arXiv 2504.05324) |

가중치는 연구의 방향을 따른 출발값입니다. 실제 문서에 대한 질문으로 조정하는 것이 좋습니다. neo4j-graphrag 라이브러리의 하이브리드 검색기는 두 점수를
정규화해 합치는 방식(naive·linear)만 제공해서, RRF는 이 저장소의 쿼리로 구현했습니다. 그래서 내보낸 그래프를 다른 Neo4j에서
neo4j-graphrag 기본 검색기로 물으면 순위가 이 데모와 조금 다를 수 있습니다.

---

## 그래프 DB(Neo4j)는 어떻게 뜨나

처음 그래프를 쓸 때 `bolt://127.0.0.1:7687`(`GRAPHME_NEO4J_URI`)에서 아무것도 응답하지 않으면, 앱이 Docker로
Neo4j 컨테이너(`graphme-neo4j`)를 그 포트에 띄웁니다. 처음에는 이미지를 받느라 1~2분 걸립니다.

- 비밀번호 없이 뜨고, 이 컴퓨터에서만 접속할 수 있습니다.
- 데이터는 `graphme-neo4j-data` 볼륨에 남아서, 다시 띄워도 그대로입니다.
- Docker가 꺼져 있으면 그 주소에서 무엇이 답하든 쓰지 않고 멈춥니다. 앱의 것인지 알 수 없기 때문입니다
  (같은 PC의 WSL 앱의 Neo4j일 수 있음). 앱의 컨테이너가 꺼져 있는데 그 포트에서 다른 Neo4j가 답할 때도 멈춥니다.
- 이미 그 주소에 Neo4j가 떠 있으면 아무것도 띄우지 않고 그것을 씁니다. 그 Neo4j가 비밀번호를 요구하는데(Neo4j Desktop,
  brew 등) `GRAPHME_NEO4J_PASSWORD`를 적지 않았다면, 그것은 건드리지 않고 앱의 Neo4j를 빈 포트(7688부터)에 띄워 씁니다.
  Neo4j 아이디·비밀번호는 직접 띄운 Neo4j를 일부러 쓸 때와 Aura로 내보낼 때만 필요합니다.
- 컨테이너와 볼륨 이름은 고정이라, 같은 Docker를 쓰는 저장소 사본은 모두 같은 그래프를 씁니다. 따로 쓰려면
  다른 Docker(예: `colima start <프로필>` 뒤 `DOCKER_HOST`를 그 소켓으로)에서 다른 포트(`GRAPHME_NEO4J_URI=bolt://127.0.0.1:7688`)로 띄웁니다.
  이미 다른 포트에 묶인 컨테이너가 있으면 옮기지 않고 그렇다고 알려 줍니다.

> **주의 — 이미 Neo4j가 있는 컴퓨터.** Neo4j Desktop·`brew install neo4j`로 7687에 Neo4j를 띄워 둔 컴퓨터, 또는
> 예전 안내대로 비밀번호(`NEO4J_AUTH=neo4j/…`)를 넣어 `graphme-neo4j` 컨테이너를 만든 컴퓨터에서는 앱이 그 Neo4j에 비밀번호
> 없이 붙다가 `그래프 DB에 연결할 수 없습니다: {neo4j_code: Neo.ClientError.Security.Unauthorized}`로 멈췄습니다. 지금은
> 이렇게 처리합니다.
>
> - 다른 프로그램의 Neo4j: 건드리지 않고 앱의 Neo4j를 빈 포트(7688부터)에 따로 띄웁니다. 터미널에
>   `…의 Neo4j는 비밀번호를 요구해서, 이 앱의 Neo4j를 bolt://127.0.0.1:7688에서 씁니다`가 찍힙니다. 그 Neo4j에 있던
>   데이터와는 별개의 그래프입니다.
> - 비밀번호로 만든 `graphme-neo4j`: 비밀번호 없는 컨테이너로 다시 만들고, 데이터 볼륨은 그대로 둡니다.
> - `.env`에 `GRAPHME_NEO4J_PASSWORD`가 남아 있으면 그 Neo4j를 쓰려는 뜻으로 보고 옮기지 않습니다. 앱의 Neo4j를 쓰려면
>   `GRAPHME_NEO4J_*`를 모두 비워 둡니다.

| 이 컴퓨터 | 그래프 만들기·질문 | `.dump`·Aura 업로드 |
|---|---|---|
| Docker 있음 | 앱이 Neo4j를 알아서 띄움 | 가능 |
| Docker 없음 + Neo4j 직접 설치(Desktop, `brew install neo4j` 등) | 가능 — `.env`에 `GRAPHME_NEO4J_PASSWORD` 설정 | 불가 |
| 둘 다 없음 | 불가 | 불가 |

Aura에 올릴 때만 인스턴스 주소·사용자·비밀번호를 데모의 내보내기 창에서 입력합니다.

**문서가 수천 개 이상이면** 앱이 띄우는 Neo4j의 메모리를 늘립니다. 기본값(heap 1g, 페이지 캐시
512m)은 작은 공간용입니다. 페이지 캐시에는 청크 벡터가 올라가므로 벡터 전체 크기 이상을 줍니다
(청크 하나에 약 12KB). Docker VM에도 그만큼 메모리가 있어야 합니다(colima는 `colima start --memory 16`).

```dotenv
GRAPHME_NEO4J_HEAP=4g
GRAPHME_NEO4J_PAGECACHE=8g
```

값을 바꾸면 다음 실행 때 컨테이너를 새로 만들고, 데이터(볼륨)는 그대로 둡니다.

앱의 Neo4j를 완전히 지우려면(그래프가 모두 사라지고, 다음 실행 때 새로 만듭니다):

```bash
docker rm -f graphme-neo4j
docker volume rm graphme-neo4j-data graphme-neo4j-logs
```

문서 연결 지도는 연결이 많은 문서부터 최대 300개만 그립니다. 나머지는 문서 목록과 질문에서 찾습니다.

---

## 자주 막히는 곳

| 증상 | 원인 → 해결 |
|---|---|
| `로컬 그래프 DB(Neo4j)를 띄우려면 Docker가 필요합니다` | Docker가 설치돼 있지 않음 → 0단계의 Docker 설치(macOS는 `brew install colima docker && colima start --memory 4 --port-forwarder grpc`) 후 데모 재시작. Docker 없이 쓰려면 이미 설치된 Neo4j(brew 등)를 켜고 `.env`에 `GRAPHME_NEO4J_PASSWORD` |
| `Docker가 실행 중이 아닙니다` | Docker가 꺼져 있음 → Docker Desktop을 켜거나 `colima start`. 직접 띄운 Neo4j를 쓰려면 `.env`에 `GRAPHME_NEO4J_PASSWORD` |
| `7687번 포트에서 이 앱의 것이 아닌 Neo4j가 답합니다` | 같은 PC의 다른 앱(대개 WSL 쪽)의 Neo4j가 그 포트를 쓰고 있음 → 그쪽 앱과 Docker를 끄거나, `.env`의 `GRAPHME_NEO4J_URI`를 다른 포트(예: `bolt://127.0.0.1:7688`)로 |
| `OPENAI_API_KEY is required` | 키가 없음 → 앱의 관리 › 키와 모델에서 넣기. 터미널의 `graphme`는 앱 폴더의 `.env`를 읽음 |
| `로컬 Neo4j가 120초 안에 응답하지 않았습니다` | 컨테이너는 떠 있는데 포트 전달이 안 됨. colima의 기본 포트 전달(ssh)은 새로 연 포트로 응답을 넘기지 못할 때가 있음 → `colima stop && colima start --port-forwarder grpc`(한 번 바꾸면 유지됨) 후 다시 실행. 급하면 `colima restart` |
| 화면 대신 "The web app is not built" | 웹 앱을 빌드하지 않음 → `(cd frontend && npm ci && npm run build)` |
| DOCX·PPTX·XLSX 페이지가 안 그려짐 | 뷰어 번들 미설치(`~/.cache/graphme/ooxml-viewer/dist`가 없음) → 1단계의 `setup.sh`. Chrome이 기본 위치에 없으면 `.env`에 `GRAPHME_CHROME_EXECUTABLE` |
| `setup.sh`가 `wasm-pack: not found`로 멈춤 | wasm-pack 미설치 → 0단계의 wasm-pack 설치 후 `setup.sh` 다시 실행 |
| `setup.sh`가 `building patched xlsx from …`에서 몇 분째 진행이 없음 | 내부 `npm install`이 레지스트리 연결을 연 채로 멈춤(WSL에서 간헐적으로 생김, 정상은 1~2분) → Ctrl+C 후 `npm_config_audit=false npm_config_fund=false src/graphme/adapters/rendering/viewer/setup.sh`로 다시 실행. 앞서 받은 패키지는 npm 캐시에 남아 있어 다시 하면 대개 넘어감 |
| `…의 Neo4j가 비밀번호를 받지 않습니다` | `.env`에 `GRAPHME_NEO4J_PASSWORD`가 있는데 틀림 → 고치거나, 앱의 Neo4j를 쓰려면 지움 |
| `OPENAI_API_KEY is required to embed or search (…)` | 임베딩 키가 비어 있음 → 관리 › 키와 모델에서 넣음 |
| 키와 모델 화면에서 `환경변수 …가 .env보다 우선합니다` | 셸에 같은 이름의 변수가 있음 → `unset` 후 데모 재시작 |

---

## 무엇을 하나

- **문서 읽기:** 페이지를 그려 VLM이 문서의 계층(무엇이 무엇 아래에 오는지)을 읽고, 그림·표마다 설명을 붙입니다.
  파일에서 직접 뽑은 텍스트·표·그림이 그래프의 기준이고, 렌더된 페이지와 VLM은 그것을 묶고 잇는 데 씁니다.
- **그래프 공간(Neo4j):** 폴더의 모든 문서가 한 그래프에 들어갑니다. 문서마다의 계층, 한 문서가 다른 문서를 인용하는 연결,
  내용이 비슷한 문서끼리의 연결이 함께 담깁니다.
- **질문:** 계층 단위로 자른 구절을 벡터 검색과 전문 검색(BM25)으로 함께 찾아 가중 RRF로 합치고, 그래프를 따라 연결된
  문서까지 가져와 답합니다(위 "검색 방식").
- **내보내기:** Neo4j import 묶음, `.dump`(Aura·Desktop), Aura로 바로 올리기.

읽는 규칙은 [`finalized_source.md`](finalized_source.md), 그 근거는 [`docs/method.md`](docs/method.md)에 있습니다.

---

## Python에서 쓰기

```python
import graphme

doc = graphme.read("report.pptx")        # 문서 하나 -> ExtractedDocument (Neo4j 없이)
print(doc.model_dump_json()[:200])

with graphme.GraphMe() as g:              # 설정은 .env / 환경변수 (GraphMe(settings=...)로 덮어쓰기)
    report = g.add("docs/")               # 폴더나 파일 하나 -> BuildReport
    g.embed()                             # 검색할 구절을 자르고 임베딩
    for hit in g.search("기밀 시험 압력은?"):
        print(graphme.shown_name(hit.file), hit.score)   # 첨부 파일의 내부 구분자(.emb…)는 빼고
    answer = g.ask("기밀 시험 압력은?")
    print(answer.text)
    g.export("out/")                      # neo4j-admin import 묶음 (dump=True면 .dump)
```

공개 API는 `graphme.__all__`에 있는 이름뿐입니다. `read`도 렌더러와 모델 엔드포인트를 씁니다.

## HTTP로 쓰기

`graphme serve`는 같은 파이프라인과 그래프 공간을 `/v1`로 엽니다. 요청·응답 형식은 `http://127.0.0.1:8765/docs`에 있고,
`frontend/dist`가 있으면 `/`에서 웹 앱도 서비스합니다.

```bash
uv run graphme serve                        # 127.0.0.1:8765 (--host, --port, --web DIR)
uv run graphme serve --openapi > api.json   # 계약만 JSON으로
```

```bash
B=http://127.0.0.1:8765
curl -s $B/health                                                   # 그래프 DB를 건드리지 않음
id=$(curl -s -F file=@memo.docx $B/v1/files | jq -r .id)            # 서버에 둠 (읽기는 아직)
job=$(curl -s -X POST $B/v1/jobs/build -H 'content-type: application/json' \
  -d "{\"files\": [\"$id\"]}" | jq -r .id)                          # 202: 읽기 → 넣기 → 잇기 → 임베딩
curl -s $B/v1/jobs/$job | jq .state                                 # reading … done
curl -s -X POST $B/v1/ask -H 'content-type: application/json' -d '{"question": "기밀 시험 압력은?"}'
curl -s -o bundle.zip $B/v1/export/bundle                           # neo4j-admin import 묶음
```

- 긴 일(`jobs/build` 적재, `jobs/read` 읽기만, `jobs/embed` 임베딩, `jobs/dump` .dump, `jobs/aura-upload` Aura 올리기)은 202와
  작업 id를 돌려주고 `GET /v1/jobs/{id}`로 진행을 봅니다. 적재·읽기·임베딩이 한 칸, 내보내기가 한 칸이며 칸이 차 있으면 409입니다.
  적재와 읽기는 `POST /v1/jobs/{id}/cancel`로 멈춥니다. 작업 기록은 메모리에만 있어 서버를 다시 띄우면 사라지고, 그래프와 올린
  파일은 `GRAPHME_DATA_DIR/server`에 남습니다.
- 오류 본문은 언제나 `{"detail": "..."}`입니다.
- `GRAPHME_API_KEY`를 두면 `/v1` 전부가 `Authorization: Bearer <키>`(또는 쿠키 `graphme_key`)를 요구합니다. 웹 앱은 401을 받으면
  키를 묻고, 이 브라우저를 닫을 때까지 `SameSite=Strict` 쿠키로 둡니다. `/health`, `/docs`, 웹 앱 파일은 키 없이 열립니다.
- 키가 없으면 이 컴퓨터에서만 열리고(127.0.0.1, 이 컴퓨터의 페이지만), 다른 주소(`--host 0.0.0.0`)로 열려면 키가 필요합니다.
- `{"folder": "<경로>"}` 적재는 서버 컴퓨터의 폴더를 서버의 권한으로 읽습니다. 키가 곧 그 권한입니다.
- 키와 모델 설정 변경(`PUT /v1/settings`)은 키와 상관없이 이 컴퓨터의 페이지에서만 받습니다.

## CLI

웹 앱과 같은 그래프 공간을 씁니다. 지금 폴더에 `.env`가 있으면(저장소) 그것을, 없으면 설치한 앱 폴더의
`.env`·그래프 데이터·뷰어를 씁니다. 전체 목록은 `uv run graphme --help`.

```text
graphme build-graph FOLDER          # 폴더의 문서를 읽어 그래프 공간에 넣고 잇기 (--again: 다시 읽기)
graphme embed-graph                 # 질문용 구절 임베딩, 중요도, 내용 유사 연결
graphme ask QUESTION                # 답과 근거
graphme delete-document ID...       # 문서 삭제
graphme reset-graph                 # 그래프 공간 비우기 (인덱스는 남김)
graphme export-corpus OUT           # Neo4j import 묶음 (--dump / --json / --cypher)
graphme xlsx-bundle WORKBOOK OUT    # XLSX를 페이지와 매핑 묶음으로 렌더
```

### 그래프 공간 내보내기

```bash
uv run graphme export-corpus OUT_DIR              # neo4j-admin import 묶음 (기본)
uv run graphme export-corpus out.json --json      # 같은 레코드를 파일 하나로
uv run graphme export-corpus out.cypher --cypher  # 구절·벡터 없이 그래프만
uv run graphme export-corpus neo4j.dump --dump    # .dump 하나: Aura Import Database, Neo4j Desktop
```

`OUT_DIR`는 Neo4j의 `neo4j-admin database import`가 그대로 읽는 묶음입니다: 라벨 조합별 노드 CSV, 관계 종류별 CSV(`COVERS` 포함), 청크(`Chunk`)와 벡터(`embedding:double[]`), 그리고 `schema.cypher`(제약·벡터 인덱스·전문 검색 인덱스), `import.sh`, 사용법을 적은 `README.md`. 멈춘 빈 데이터베이스에서 `./import.sh`를 실행한 뒤 시작하고 `cypher-shell -f schema.cypher`를 한 번 실행하면, neo4j-graphrag의 `VectorRetriever`·`HybridRetriever`·`HybridCypherRetriever`와 `GraphRAG`를 별도 코드 없이 바로 쓸 수 있습니다. `--dump`는 같은 묶음을 일회용 Neo4j 컨테이너(`GRAPHME_NEO4J_ADMIN_IMAGE`, 기본 `neo4j:5.26-community`)에서 적재하고 인덱스를 만든 뒤 `neo4j-admin database dump`로 파일 하나에 담습니다. 이 파일은 Aura 콘솔의 Import Database, Neo4j Desktop, `neo4j-admin database load`, 그리고 `neo4j-admin database upload`(Aura로 바로 올리기)가 그대로 받습니다. 데모의 "Neo4j로 내보내기"는 이 세 가지(묶음 .zip, .dump, Aura 업로드)를 화면에서 제공합니다. 벡터를 Cypher 스크립트에 리터럴로 넣으면 서버가 숫자를 모두 파싱하다 메모리가 부족해지므로, 청크와 벡터는 스크립트(`--cypher`)에 넣지 않습니다.

---

## 개발

화면은 `frontend/`의 React 앱(Vite, TypeScript, Tailwind, Radix, TanStack Query, vis-network)이고, 빌드한
`frontend/dist/`를 `graphme serve`(데모 `tools/hierarchy_demo/app.py`도 같은 서버)가 서비스합니다. 폰트와 라이브러리는 모두 번들에 들어가므로
화면을 여는 데 외부 네트워크가 필요 없습니다.

```bash
uv run python tools/hierarchy_demo/app.py      # API(/v1)와 샘플, :8765 (샘플 없이: uv run graphme serve)
cd frontend
npm run dev                                     # http://127.0.0.1:5173, /v1은 8765로 proxy
DEMO_URL=http://127.0.0.1:8766 npm run dev      # 서버를 다른 포트로 띄웠을 때
```

API의 요청·응답 형식은 `src/graphme/server/models.py` 한 곳에 있습니다. 바꾼 뒤 `npm run api`를 실행하면
`graphme serve --openapi`에서 `frontend/src/api/schema.d.ts`가 다시 만들어지고, 화면 코드는 그 타입으로 컴파일됩니다.
데모에만 있는 `POST /v1/samples`는 이 계약에 없어 `frontend/src/api/client.ts`의 `Samples`로 따로 적습니다.

### 검사

```bash
uv run ruff check . && uv run mypy src
uv run pytest tests/public    # 키·Neo4j·문서 없이. 나머지 tests/는 저장소에 없습니다
cd frontend && npm run typecheck && npm test && npm run e2e && npm run build   # e2e는 가짜 API로, 이 컴퓨터의 Chrome에서
```

설정은 기본값 < `.env` < `GRAPHME_CONFIG`가 가리키는 YAML(키는 넣을 수 없음) < 셸 환경변수 순으로 덮어씁니다.
`.env`, `.graphme*/`, `data/`, `test_dataset/`, `tests/`(`tests/public`·`tests/fakes` 제외), `frontend/dist/`는 git에 올라가지 않습니다.

기여하기: [CONTRIBUTING.md](CONTRIBUTING.md) — 개발 환경, 검사, 규칙, PR. 실행 예시는 [`examples/`](examples/).

---

## 보안 및 데이터 취급

- `.env`, 실제 문서, API key·OAuth 파일을 commit하지 않습니다.
- `graphme serve`를 이 컴퓨터 밖에 열 때는 `GRAPHME_API_KEY`를 둡니다. 키 없는 서버는 127.0.0.1 밖으로 뜨지 않습니다.
- Source crop과 generated interpretation을 구분합니다.
- Generated evidence는 citation authority로 사용하지 않습니다.
- 기밀 문서를 모델·임베딩 provider에 보내기 전에 그 provider의 데이터 처리 조건을 확인합니다.

---

## 알려진 제한사항

- 확인된 모델 제공자는 OpenAI뿐입니다. Anthropic·Gemini 경로는 코드와 가짜 응답 테스트는 있으나 실제 호출로 확인하지 않았습니다.
- Office rendering은 브라우저 WASM 뷰어의 layout에 의존합니다. DOCX pagination과 PPTX 효과 재현은 Word·PowerPoint와 픽셀 단위로 같지 않습니다.
- XLSX cell-to-rendered-pixel mapping은 모든 multi-page workbook에서 완전히 결정적이지 않을 수 있습니다.
- Formula 계산은 Excel calculation engine을 대체하지 않습니다.
- VLM segmentation 품질은 live provider/model에 따라 달라집니다.
- `frontend/dist`는 Git과 wheel에 포함되지 않습니다. 설치 프로그램은 릴리스의 `web.zip`으로 웹 앱을 함께 깔고, 저장소에서는 직접 빌드합니다.
- PDF scan-only 문서는 명시적으로 분류하지만 OCR/VLM 결과를 canonical text node로 자동 승격하지 않습니다. 두 단 읽기 순서는 띠·단 규칙으로 처리하지만, 단이 불규칙하게 섞인 레이아웃과 borderless table 해석은 제한적입니다.
- PPTX grouped shapes/SmartArt/external media의 재귀 해석과 렌더링 효과의 완전한 재현은 지원 범위가 제한적입니다.
- DOCX body node는 document root의 직접 자식입니다. 제목 스타일을 계층으로 읽지 않으며, header/footer/footnote/comment는 포괄적으로 추출하지 않습니다.
- 계층 패스는 페이지마다 VLM을 호출합니다. 모델이 쓸 수 있는 답을 주지 않은 페이지는 계층 없이 남고 `hierarchy_page_unread` 경고로 기록됩니다.
- Neo4j 내보내기: `.dump`를 빈 Neo4j 5.26에 `neo4j-admin database load`로 넣어 문서·구절·벡터와 벡터·전문 검색 인덱스가 살아나는 것까지 확인했습니다. Aura 업로드는 확인하지 않았습니다.
- 실제 datasets와 generated indexes는 저장소에 포함되지 않습니다.

## 라이선스

[PolyForm Noncommercial 1.0.0](LICENSE.md): 개인 공부·연구·교육·비영리 단체·공공기관은 쓰고 고치고 나눌 수 있습니다. 수익을 내는 일이나 기업의 업무에는 쓸 수 없습니다. 함께 배포하는 문서 뷰어는 MIT 라이선스입니다([THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)).
