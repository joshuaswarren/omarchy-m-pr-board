# Omarchy M pull request board

One row per open pull request. Each row names the first thing that blocks it.

The page is at https://joshuaswarren.github.io/omarchy-m-pr-board/. A workflow refreshes it every hour.

Repos: omacom/linux-aurora, omacom/mesa, omacom/omarchy-mac-installer, omacom/omarchy-mac-pkgs and chriskearney/maclab. Also aurora-silicon/aurora-installer. To change the list, edit `REPOS` in `board.py`. The board only reads public repos: it refuses a repo that is private, or that does not say it is public, and does not name it.

Each PR is one row: a small repo label and the title, the number and author, one line that says who the PR waits on, and chips: its one state, an evidence link when the PR body gives one, the maclab result, and the days since the last update. The state is one of five: ready to merge, needs review, needs fixes, waiting or held (a draft). A green bar marks a PR that can merge now. A PR with an rc, rc-blocker, release blocker or blocker label gets an RC blocker chip. The page needs no network beyond itself and fits a phone.

The page opens on what needs action now: the PRs that wait on a reviewer or a maintainer (ready to merge, needs review). A menu under the title shows everything, filters by repo and shows drafts, which stay hidden until asked for.

Waiting on: the author for conflicts, failing checks, requested changes, a branch behind and drafts; CI while checks run; GitHub while it has not answered; otherwise the reviewers GitHub asked (code owners included, by login or team slug), else a maintainer.

Evidence: the first https link on a PR body line that starts with Evidence, Proof, Receipt, Test report or Results.

Maclab: `maclab.json` maps a commit to how many Macs passed and failed it. `maclab_export.py` writes it from maclab's job list, run where maclab can be reached (MACLAB_URL and MACLAB_TOKEN in the environment); the file holds commits and counts, not Mac names or job ids. A PR whose head commit is in the file shows passed or failed; a kernel PR (omacom/linux-aurora) whose head is not shows not run; other repos show no chip. maclab keeps few jobs, so the export keeps older answers. The export commits `maclab.json` to this repo with a write deploy key that works for this repo only and lives on one machine. To revoke it: repo Settings, Deploy keys, delete `pr-board-main (maclab.json export)`. Until then a leaked key can push to this repo, including the workflow, so delete it at the first doubt. The report itself is not linked: maclab is not reachable from the public page.

The page has three sections, in this order: the Omarchy M team, existing contributors, and everyone else. The team is the GitHub logins listed in `team.json`, a plain list. An existing contributor is anyone with an earlier merged PR in the same repo. A team member who is also a contributor shows as team. A test checks the file.

The blocking item is the first match in this list: draft, conflicts, changes requested, failing check, a workflow run that waits for a maintainer, a missing review, checks running, branch behind, branch rules. A failing check named `approve` counts as a missing review. "Can merge now" means GitHub reports the PR clean; a repo that requires no review can show a PR that nobody has read.

Limits: the case of a workflow run that waits for a maintainer is tested on made-up data only, because no open PR had one when this was written. A PR with more than 30 checks or 20 reviews shows only some of them.

Run it:

    python3 -m unittest discover -s tests
    python3 board.py --out docs --url https://example.invalid/
    python3 board.py --fixture data.json --out /tmp/board

The first command runs the tests. The second reads GitHub and needs the gh CLI. The third reads saved data and needs no network. Each run also writes `summary.txt`, the short text for a daily message.
