# 변경 기록

[Keep a Changelog](https://keepachangelog.com/ko/1.1.0/) 형식을 따르고, 버전은
[Semantic Versioning](https://semver.org/lang/ko/)을 따릅니다.

## [0.1.0] - Unreleased

### 추가

- One-line installers for macOS and Windows (`install.sh`, `install.ps1`) and `graphme app`; pages
  drawn through Playwright for Python (no Node needed to run).
- Python API: `graphme.read`(문서 하나 읽기)와 `GraphMe`(add·embed·search·ask·documents·delete·reset·export).
- 모델 제공자: 채팅은 OpenAI·Anthropic·Gemini(`GRAPHME_PROVIDER`), 임베딩은 OpenAI·Gemini
  (`GRAPHME_EMBEDDING_PROVIDER`). 실제 호출로 확인한 것은 OpenAI뿐입니다.
- HTTP 서버: `graphme serve` — `/v1`(파일, 작업 폴링·취소, 검색, 답변, 내보내기), `/health`, `/docs`,
  `GRAPHME_API_KEY`로 잠그기.
