# Omarchy M pull request board

One row per open pull request. Each row names the first thing that blocks it.

The page is at https://joshuaswarren.github.io/omarchy-m-pr-board/. A workflow refreshes it every hour.

Repos: omacom/linux-aurora, omacom/mesa, omacom/omarchy-mac-installer, omacom/omarchy-mac-pkgs and chriskearney/maclab. Also four aurora-silicon repos: linux-aurora, mesa-aurora, m1n1-aurora and aurora-installer. To change the list, edit `REPOS` in `board.py`.

Columns: merge state, checks, approvals, maclab qualification, the blocking item, the owner and the RC blocker flag. The owner is the author. The RC blocker flag is yes when the PR has a label named rc, rc-blocker, release blocker or blocker. Maclab qualification is always "unknown", because maclab's job records are not public.

The blocking item is the first match in this list: draft, conflicts, changes requested, failing check, a workflow run that waits for a maintainer, a missing review, checks running, branch behind, branch rules. "Can merge now" means GitHub reports the PR clean.

Run it:

    python3 -m unittest discover -s tests
    python3 board.py --out docs --url https://example.invalid/
    python3 board.py --fixture data.json --out /tmp/board

The first command runs the tests. The second reads GitHub and needs the gh CLI. The third reads saved data and needs no network. Each run also writes `summary.txt`, the short text for a daily message.
