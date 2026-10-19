# Security policy

## Reporting a vulnerability

Please do **not** open a public issue. Report it privately through GitHub's
[security advisory form](../../security/advisories/new) for this repository, with:

- what is affected and how it can be triggered (an invented document if one is needed — never a
  real one),
- what an attacker could do with it,
- the version (the release tag, or `python -c "import graphme; print(graphme.__version__)"`)
  and your platform.

You will get an answer within a week. A fix is released as soon as it is ready, and the advisory is
published afterwards, crediting you if you wish.

## Supported versions

Only the latest release receives security fixes. The installers always install it; run the install
line again to update.

## How the app is meant to run

- `graphme serve` without `GRAPHME_API_KEY` listens on 127.0.0.1 only and answers only this machine's
  pages. Opening it to a network requires a key.
- The app's Neo4j has no password and listens on 127.0.0.1 only.
- Documents are sent only to the model and embedding providers you configure; check their data terms
  before using confidential documents.
