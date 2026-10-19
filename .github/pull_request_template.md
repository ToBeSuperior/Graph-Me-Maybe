## What and why

<!-- What does this change, and why? Link the issue it closes: "Closes #123". -->

## How it was checked

- [ ] `uv run ruff check src tests/public && uv run mypy src`
- [ ] `uv run pytest tests/public`
- [ ] For web app changes: `cd frontend && npm run typecheck && npm test && npm run e2e`
- [ ] A change in behaviour comes with a test that failed before it
- [ ] User-visible changes have a line under `## [Unreleased]` in `CHANGELOG.md`

<!-- If you checked something with a real model call, summarise what you checked.
     Do not paste model responses or document content. -->

## Data

- [ ] No real document, nothing measured from one, and no key — in code, tests, commit messages or
      this description
