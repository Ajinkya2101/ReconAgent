# Repository history: branches and versions

## 10 Nov 2026: `main` overwritten with the v3 data model; one branch left

**Before.** The repository (github.com/Ajinkya2101/ReconAgent) had three branches:

| Branch | Last commit | What it held |
|---|---|---|
| `main` | `aa4afa9` (9 Nov) | The v2 data model: the five client extracts of 12 Sep 2026, rules R01-R19 |
| `data-model-9nov` | `43883eb` on GitHub (9 Nov), then the 10 Nov commits | The v3 data model: the business team's 9 Nov mapping, rules R01-R13. The demo VM ran this branch from 9 Nov |
| `chore/deployment-readiness` | `f3fffa8` (4 Nov) | Reset simulation and the v2 simulation docs. Already merged into `main` on 4 Nov |

So the code on `main` and the code on the demo VM used two different data schemas.

**What was done.**
1. The 10 Nov work was committed on `data-model-9nov`: the workbook's price revision (event contract v4),
   the worklist changes, the removal of the v2 carry-forward code, the docs reorganisation, and the
   0.3.0 release commit.
2. `main` was moved to the tip of `data-model-9nov` and tagged `v0.3.0`. `data-model-9nov` was built on top
   of `main` (`aa4afa9`), so this was a **fast-forward**: no commit was rewritten or lost, and no force-push
   was needed. The v2 code is still in the history, at `aa4afa9` and before.
3. `data-model-9nov` and `chore/deployment-readiness` were deleted, locally and on GitHub. Both were fully
   contained in `main`: `data-model-9nov` is the commit tagged `v0.3.0`, and `f3fffa8` is an ancestor of
   `aa4afa9`.
4. The demo VM (worklist :8003, simulator :8103) was redeployed from `v0.3.0`.

**After.** One branch, `main`, with one data schema: v3 (event contract v4). The v2 documents are in
[docs/archive/v2/](../archive/v2/).

**To look at the v2 code again:** `git switch -c v2-reference aa4afa9` (a local branch only; do not push
it back as a long-lived branch).

## Branch policy

- `main` is the only long-lived branch. It holds the latest release plus finished work under
  `[Unreleased]` in the CHANGELOG.
- Work in progress goes on a short-lived branch, which is merged to `main` and then deleted.
- Releases follow [DEPLOYMENT.md](DEPLOYMENT.md), section 6: a CHANGELOG entry, `__version__`, and an
  annotated tag `vX.Y.Z` on `main`. The tag starts the Release workflow (tests, image, GitHub release).
- Servers run a release tag, never an untagged branch.

## Versions

| Version | Date | Commit | Tag | Data model |
|---|---|---|---|---|
| 0.1.0 | 2026-10-29 | Before this repository (its first push is 3 Nov) | none | v2 |
| 0.2.0 | 2026-11-03 | `8786987` | none: it was not tagged when released | v2 |
| 0.3.0 | 2026-11-10 | the commit tagged `v0.3.0` | `v0.3.0` | v3, event contract v4 |
| 0.3.1 | 2026-11-10 | the commit tagged `v0.3.1` | `v0.3.1` | v3, event contract v4 |
| 0.4.0 | 2026-11-10 | the commit tagged `v0.4.0` | `v0.4.0` | v3, event contract v4; ML layer with history |

0.2.0 is left untagged on purpose: pushing a `v0.2.0` tag now would start the Release workflow and
publish an old image as `latest`.
