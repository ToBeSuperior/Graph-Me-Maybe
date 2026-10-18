# 기여하기

이 저장소를 고치려는 분을 위한 안내입니다. 키·Neo4j·문서 없이 검사까지 마칠 수 있습니다.

## 개발 환경

[README의 0단계](README.md#0-준비물-설치)를 따릅니다. 검사만 돌리려면 Python 3.11과
[uv](https://docs.astral.sh/uv/)면 충분합니다. Node·Chrome·뷰어 번들(DOCX·PPTX·XLSX 렌더링),
Docker(Neo4j)는 그 기능을 고칠 때 설치합니다. 없으면 해당 테스트는 이유를 말하고 건너뜁니다.

```bash
uv sync --extra dev
uv run pytest tests/public
uv tool install pre-commit && pre-commit install   # 커밋할 때 ruff check가 돕니다
```

## 검사

PR을 열면 CI(`.github/workflows/ci.yml`)가 같은 세 가지를 돕니다.

```bash
uv run ruff check src tests/public
uv run mypy src
uv run pytest tests/public
```

릴리스 묶음: `tools/release/build.sh OUT`(태그 `v*`를 push하면 Release 워크플로가 같은 스크립트로 올림).

Neo4j가 있으면 `GRAPHME_NEO4J_TEST=1 uv run pytest tests/public`으로 그래프 공간 한 바퀴도 확인합니다.

## 테스트가 있는 곳

`tests/public/`이 이 저장소의 테스트이고, `tests/fakes/`는 그 테스트가 쓰는 가짜 임베더·생성기·문서입니다.
모델은 `httpx.MockTransport`가 대신 답하고, 문서는 테스트 안에서 PyMuPDF·python-docx·openpyxl·python-pptx로
만듭니다(`tests/public/test_read.py`). `tests/` 아래 나머지는 저장소에 올라가지 않습니다.

## 규칙

- 테스트에는 지어낸 데이터만 씁니다. 실제 문서, 그 문서에서 잰 값(페이지 수, 좌표, 시트 이름, 셀 범위,
  파일명, 사람 이름 등), 키를 커밋하지 않습니다. 커밋 메시지와 PR 설명도 마찬가지입니다.
- 동작을 바꾸면 그 차이로 실패하는 테스트를 먼저 쓰고(RED), 그다음 고칩니다(GREEN).
- 모델 호출이 드는 확인은 자기 키로 하고, 모델 응답이나 문서 내용은 PR에 붙이지 않습니다. 무엇을 확인했는지
  요약만 적습니다.
- 확인된 모델 제공자는 OpenAI뿐입니다. Anthropic·Gemini 경로는 코드와 가짜 응답 테스트
  (`tests/public/test_chat.py`)는 있으나 실제 호출로 확인하지 않았습니다.

## PR

1. 브랜치에서 고치고 위 세 검사를 통과시킵니다.
2. PR 템플릿의 확인란을 채웁니다. 실행 예시는 `examples/`에 있습니다.
3. PR은 머지하지 않고 유지보수자가 반영해 닫습니다.
