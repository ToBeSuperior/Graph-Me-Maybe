# 변경 기록

[Keep a Changelog](https://keepachangelog.com/ko/1.1.0/) 형식을 따르고, 버전은
[Semantic Versioning](https://semver.org/lang/ko/)을 따릅니다.

## [0.1.3] - 2026-10-18

### 고침

- Linux·WSL: 한글 글꼴이 없으면 설치 프로그램이 `fonts-noto-cjk` 설치를 안내합니다(없으면 Office 페이지의 한글이 네모로 그려짐).
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
