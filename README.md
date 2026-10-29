# Omarchy M pull request board

One row per open pull request. Each row names the first thing that blocks it.

The page is at https://joshuaswarren.github.io/omarchy-m-pr-board/. A workflow refreshes it every hour.

Repos: omacom/linux-aurora, omacom/mesa, omacom/omarchy-mac-installer, omacom/omarchy-mac-pkgs and chriskearney/maclab. Also aurora-silicon/aurora-installer. To change the list, edit `REPOS` in `board.py`. The board only reads public repos: it refuses a repo that is private, or that does not say it is public, and does not name it.

Columns: merge state, checks, approvals, maclab qualification, the blocking item, the owner and the RC blocker flag. The owner is the author. The RC blocker flag is yes when the PR has a label named rc, rc-blocker, release blocker or blocker. Maclab qualification is always "unknown", because maclab's job records are not public.

The page has three sections, in this order: the Omarchy M team, existing contributors, and everyone else. The team is the GitHub logins listed in `team.json`, a plain list. An existing contributor is anyone with an earlier merged PR in the same repo. A team member who is also a contributor shows as team. A test checks the file.

The blocking item is the first match in this list: draft, conflicts, changes requested, failing check, a workflow run that waits for a maintainer, a missing review, checks running, branch behind, branch rules. A failing check named `approve` counts as a missing review. "Can merge now" means GitHub reports the PR clean; a repo that requires no review can show a PR that nobody has read.

Limits: the case of a workflow run that waits for a maintainer is tested on made-up data only, because no open PR had one when this was written. A PR with more than 40 checks or 30 reviews shows only the last ones.

Run it:

    python3 -m unittest discover -s tests
    python3 board.py --out docs --url https://example.invalid/
    python3 board.py --fixture data.json --out /tmp/board

The first command runs the tests. The second reads GitHub and needs the gh CLI. The third reads saved data and needs no network. Each run also writes `summary.txt`, the short text for a daily message.
