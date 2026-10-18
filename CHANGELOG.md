# 변경 기록

[Keep a Changelog](https://keepachangelog.com/ko/1.1.0/) 형식을 따르고, 버전은
[Semantic Versioning](https://semver.org/lang/ko/)을 따릅니다.

## [0.1.11] - 2026-10-19

### 고침

- 화면이 스스로 최신 상태가 되지 않던 것.
  - Docker·그래프 DB가 준비되는 동안 화면이 503 세 번 뒤 다시 묻기를 멈추고 "연결할 수 없습니다"에 머물던 것. 이제 서버가 "준비 중"이라고 답하는 동안 3초마다 계속 묻고, 그동안 오류가 아닌 진행 상태를 보여 주다가 준비되면 바로 바뀝니다. 오류와 "다시 시도"는 진짜 실패(Docker 꺼짐, 3분 넘게 준비 안 됨)일 때만 나옵니다.
  - 문서 목록과 연결 지도를 탭에 들어올 때와 창으로 돌아올 때마다 다시 묻습니다. 터미널의 `graphme build-graph`로 바뀐 그래프도 반영됩니다.
- Docker가 켜진 뒤 Neo4j 컨테이너를 띄우는 동안 화면 요청이 20초 넘게 막히던 것. 그래프를 여는 일은 뒤에서 하고, 화면 요청은 2초 안에 "그래프 DB를 준비하는 중"으로 답합니다.
- 한국어 Windows에서 `.dump`를 만들 때 docker 출력을 cp949로 읽다 `UnicodeDecodeError`가 찍히던 것. 앱이 실행한 프로그램의 출력은 UTF-8로 읽습니다.
- `graphme export-corpus OUT --dump`가 확장자 없는 파일을 만들던 것. `.dump`를 붙이고, 폴더를 주면 그 안에 `graph.dump`를 만듭니다.

## [0.1.10] - 2026-10-19

### 고침

- Windows에서 `.dump` 내보내기가 실패하던 것. 내보내기 묶음의 `import.sh`·`schema.cypher`·`README.md`가 CRLF로 저장돼 컨테이너의 `sh`가 `set -e`를 거부했습니다. 이제 어느 OS에서나 LF로 씁니다.
- 내보내기 실패 안내가 같은 말만 되풀이하던 것. 오류의 첫 줄을 보여 줍니다(Aura 비밀번호는 가림).
- 그래프를 비운 뒤 읽은 문서가 질문 화면과 연결 지도에 나오지 않던 것. 비우기가 빌드 상태를 캐시에서 지워 화면이 다음 빌드의 끝을 보지 못했습니다.
- 그래프 DB를 처음 여는 동안(Docker 켜기, Neo4j 이미지 받기) 화면 요청이 몇 분씩 막히던 것. 2초 안에 열리지 않으면 "그래프 DB를 준비하는 중"으로 답하고 화면이 저절로 다시 묻습니다. 빌드와 내보내기는 그대로 기다립니다.

### 바뀜

- `graphme build-graph`가 읽은 뒤 질문용 임베딩까지 합니다(앱과 같음). 건너뛰려면 `--no-embed`.
- macOS에서 Docker Desktop을 자동으로 켤 때 창을 띄우지 않습니다(`open -g -j`).
- 앱 창에서 F5·Ctrl+R(macOS Cmd+R)로 새로 고칩니다.
- 문서 연결 지도의 문서 이름 글자를 키웠습니다.

## [0.1.9] - 2026-10-18

### 고침

- 0.1.5 이전에 만든 Windows 바로가기가 `app` 인수를 그대로 가진 채 `graphme-app.exe`를 가리켜, 앱 폴더를 `…\Graph-Me-Maybe\app`으로 잡던 것(화면 대신 API 문서, 키·그래프·뷰어 없음, 열 때마다 한 단계씩 깊어짐). 설치 프로그램이 바로가기 인수를 비우고, `graphme-app`은 전체 경로만 앱 폴더로 받습니다.
- Windows에서 Docker Desktop 자동 켜기가 동작하지 않던 것. `docker desktop start`는 꺼져 있어도 "already running"으로 성공을 알릴 수 있어, `Docker Desktop.exe`를 직접 실행합니다.
- 앱 창을 닫아도 남은 스레드 때문에 프로세스가 끝나지 않을 수 있던 것. 창이 닫히면 앱이 바로 끝납니다.

## [0.1.8] - 2026-10-18

### 추가

- 그래프가 필요한데 Docker Desktop이 꺼져 있으면 앱이 켭니다(Windows `docker desktop start`, 없으면 `Docker Desktop.exe`; macOS `open -a Docker`). 켜지는 동안 화면은 "Docker를 켜는 중"을 보여 주고 저절로 다시 묻습니다(서버는 503과 `Retry-After`). 3분이 지나도 준비되지 않으면 약관 동의 같은 확인이 떠 있는지 보라고 말합니다. `GRAPHME_DOCKER_AUTOSTART=0`으로 끕니다.

### 고침

- Docker가 꺼져 있을 때 요청마다 `docker`를 다시 실행하던 것. 꺼져 있다는 결과를 5초 동안 기억해, 한 화면의 요청들이 한 번의 확인을 나눠 씁니다.
- README: WSL에 깔았던 앱을 키·그래프까지 완전히 지우는 방법.

## [0.1.7] - 2026-10-18

### 고침

- Windows 앱 창에서 Docker·페이지 그리기·내보내기를 실행할 때마다 검은 콘솔 창이 잠깐 뜨던 것. 앱이 실행하는 프로그램은 모두 창 없이 실행됩니다(`graphme/processes.py` 한 곳).

### 바뀜

- WSL에서 `install.sh`를 실행하면 설치하지 않고 Windows 설치(PowerShell의 `install.ps1`)를 안내합니다. 한 PC에 앱이 두 벌 생기지 않게 하려는 것입니다. 그래도 WSL에 설치하려면 `GRAPHME_WSL=1`. 이미 깔린 WSL 앱 제거(`--uninstall`)는 그대로 됩니다.

## [0.1.6] - 2026-10-18

### 추가

- 앱 창: 시작 메뉴(Windows)와 응용 프로그램 폴더(macOS)의 Graph-Me-Maybe가 브라우저 탭과 검은 콘솔 창 대신 주소창 없는 앱 창으로 열립니다(새 명령 `graphme-app`, pywebview). 서버는 창 뒤에서 돌고, 창을 닫으면 함께 꺼집니다. 앱 창을 띄울 수 없으면 브라우저로 엽니다. `graphme app`은 그대로 브라우저로 엽니다.

### 고침

- 터미널의 `graphme` 명령이 지금 폴더의 `.env`만 읽어, 설치한 앱의 키·그래프 데이터를 쓰지 못하던 것. 지금 폴더에 `.env`가 없으면 앱 폴더의 것을 씁니다.
- 명령이 실행될 수 없을 때(키 없음, Docker 꺼짐 등) 긴 traceback 대신 `오류: …` 한 줄로 말하고, 키 오류는 어디서 넣는지도 말합니다.
- 한 PC의 WSL 앱과 Windows 앱이 서로를 자기 것으로 여기던 것. `/health`가 앱 폴더를 알려 주고, 앱 실행과 설치 프로그램은 같은 폴더의 앱만 "실행 중"으로 봅니다. Docker가 꺼져 있거나 앱의 Neo4j 컨테이너가 꺼져 있는데 그 포트에서 다른 Neo4j가 답하면, 그것을 쓰지 않고 이유를 말합니다.
- `GRAPHME_NEO4J_PASSWORD`를 적었다면 앱이 Neo4j 컨테이너를 띄우지 않습니다(직접 띄운 Neo4j를 일부러 쓰는 것).

## [0.1.5] - 2026-10-18

### 제거

- 문서 하나를 SQLite 색인으로 다루던 예전 경로와 그 CLI 명령 `ingest`·`query`·`inspect`·`evaluate`·`export-neo4j`를 지웠습니다. 문서는 `build-graph`로 그래프 공간에 올리고 `ask`로 묻고 `export-corpus`로 내보냅니다. 그 경로만 읽던 설정 `GRAPHME_HIERARCHY_MODE`·`GRAPHME_UNIT_GROUPING`·`GRAPHME_NODE_BUDGET`·`GRAPHME_TOKEN_BUDGET`·`GRAPHME_MAX_HOPS`도 더는 읽지 않습니다.
- 쓰이지 않던 코드(호출하는 곳이 없는 grounding 경로, VLM 페이지 분할, 읽히지 않는 필드)와 중복 코드를 정리했습니다.

### 고침

- 설치 프로그램으로 깐 뒤 `graphme build-graph` 등 CLI·Python API가 앱 폴더의 뷰어를 찾지 못해 DOCX·PPTX·XLSX를 그리지 못하던 것. 이제 모든 명령이 설치된 뷰어를 씁니다. 뷰어가 없으면 그렇다고, 어떻게 만드는지 말합니다.
- 엑셀: 두 시트의 같은 셀이 같은 표를 읽는 수식에서 두 번째 연결이 빠지던 것. JPEG 그림의 형식을 `image/jpg`가 아니라 `image/jpeg`로 적습니다.
- 첨부 파일 이름의 내부 구분자(`.emb…`)가 Python API 예제와 HTTP 예제의 근거 목록에 나오던 것(`graphme.shown_name`).
- colima의 기본 포트 전달(ssh)에서 처음 띄운 Neo4j에 연결하지 못하던 것: README와 오류 메시지가 `--port-forwarder grpc`를 안내합니다.

### 바뀜

- 엑셀·PDF 추출이 같은 영역·페이지를 여러 번 읽지 않습니다(결과는 같고 더 빠름).

## [0.1.4] - 2026-10-18

### 고침

- 설치 프로그램이 최신 릴리스를 GitHub API로 찾습니다. 릴리스 직후 `releases/latest` 주소가 한동안 이전 버전을 가리켜 한 버전 낮은 것이 설치됐습니다(API 한도가 찼을 때만 그 주소를 씀).
- Linux·WSL: DOCX·PPTX·XLSX 페이지의 한글을 앱에 든 글꼴(Pretendard)로 그립니다. 시스템에 한글 글꼴이 없어도 네모로 깨지지 않고, `fonts-noto-cjk` 설치가 필요 없습니다.
- 앱을 끄자마자 다시 띄우면 8765가 아니라 8766으로 뜨던 것: 막 닫힌 연결(TIME_WAIT)이 남은 포트를 사용 중으로 보지 않습니다.

## [0.1.3] - 2026-10-18

### 고침

- Linux·WSL: 한글 글꼴이 없으면 설치 프로그램이 `fonts-noto-cjk` 설치를 안내합니다(0.1.4에서 앱 글꼴로 대신해 안내는 빠짐).
- README: Linux·WSL 설치 경로, Node 24, 공개 저장소 주소, 설치 프로그램이 웹 앱을 함께 까는 것을 반영했습니다.

## [0.1.2] - 2026-10-18

### 고침

- WSL: `graphme app`이 화면을 WSL 안의 리눅스 Chrome이 아니라 Windows 기본 브라우저로 엽니다(`BROWSER`로 바꿀 수 있음).

## [0.1.1] - 2026-10-18

### 고침

- 표·차트 미리보기의 한글이 네모(□)로 그려지던 것: 한글 글꼴 Pretendard(SIL OFL)를 패키지에 넣어 그 글꼴로 그립니다.
- Windows: 텍스트 파일을 UTF-8로 읽고 써서 한글이 든 문서의 렌더링이 멈추지 않습니다. `xlsx-bundle`이 열린 파일을 바꾸려다 "Access is denied"로 실패하던 것도 고쳤습니다.

## [0.1.0] - 2026-10-18

### 추가

- One-line installers for macOS and Windows (`install.sh`, `install.ps1`) and `graphme app`; pages
  drawn through Playwright for Python (no Node needed to run).
- Python API: `graphme.read`(문서 하나 읽기)와 `GraphMe`(add·embed·search·ask·documents·delete·reset·export).
- 모델 제공자: 채팅은 OpenAI·Anthropic·Gemini(`GRAPHME_PROVIDER`), 임베딩은 OpenAI·Gemini
  (`GRAPHME_EMBEDDING_PROVIDER`). 실제 호출로 확인한 것은 OpenAI뿐입니다.
- HTTP 서버: `graphme serve` — `/v1`(파일, 작업 폴링·취소, 검색, 답변, 내보내기), `/health`, `/docs`,
  `GRAPHME_API_KEY`로 잠그기.
