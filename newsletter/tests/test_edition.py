"""An edition sent from a local London Project repository to local bare subscriber remotes."""

import asyncio
import dataclasses
import io
import subprocess
from pathlib import Path

import pytest
from rich.console import Console

from newsletter import pipeline
from newsletter.config import load
from newsletter.git import Edition
from newsletter.github import NewPullRequest, PullRequest
from newsletter.process import Log
from newsletter.ui import Outcome, State

LONDON_AGENTS = """# London

London's own rules.

<!-- newsletter:begin shared -->
## Shared

Shared rules.
<!-- newsletter:end shared -->
"""

PIECE_AGENTS = "# Piece\n\nPiece rules.\n"


def git(cwd: Path, *args: str) -> str:
    """Run git in `cwd` and return its standard output, stripped.

    Args:
        cwd: The directory git runs in.
        *args: The arguments to git.
    """
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class ScriptedPullRequests:
    """Holds pull requests in memory, numbered from 1."""

    def __init__(self) -> None:
        self.opened: list[NewPullRequest] = []

    async def find(self, repository: str, head: str, _log: Log) -> PullRequest | None:
        """The pull request opened from `head` in `repository`, if there is one.

        Args:
            repository: The repository as `owner/name`.
            head: The branch the pull request comes from.
        """
        for number, pr in enumerate(self.opened, start=1):
            if pr.repository == repository and pr.head == head:
                return PullRequest(number, f"https://example.invalid/{repository}/pull/{number}")
        return None

    async def open(self, request: NewPullRequest, _log: Log) -> PullRequest:
        """Record `request` and return its pull request, numbered in the order opened.

        Args:
            request: The pull request to open.
        """
        self.opened.append(request)
        number = len(self.opened)
        return PullRequest(number, f"https://example.invalid/{request.repository}/pull/{number}")

    async def edit(self, pull_request: PullRequest, request: NewPullRequest, _log: Log) -> None:
        """Give the recorded pull request the title and body of `request`.

        Args:
            pull_request: The pull request, as `find` returned it.
            request: Supplies the new title and body.
        """
        opened = self.opened[pull_request.number - 1]
        self.opened[pull_request.number - 1] = dataclasses.replace(opened, title=request.title, body=request.body)


@pytest.fixture(autouse=True)
def isolated_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run git with no global or system configuration and a fixed identity.

    Args:
        tmp_path: The test's temporary directory, which holds an empty global configuration.
        monkeypatch: Sets the environment for the test.
    """
    empty = tmp_path / "gitconfig"
    empty.write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "Tester")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "tester@example.invalid")


def make_remote(tmp_path: Path, name: str, files: dict[str, str]) -> Path:
    """Make a bare repository holding `files` in one commit on `main`, serving partial clones.

    Args:
        tmp_path: The test's temporary directory.
        name: The repository's name.
        files: The contents of each file, by path.
    """
    work = tmp_path / "seed" / name
    work.mkdir(parents=True)
    git(work, "init", "--quiet", "--initial-branch=main")
    for path, text in files.items():
        (work / path).parent.mkdir(parents=True, exist_ok=True)
        (work / path).write_text(text)
    git(work, "add", ".")
    git(work, "commit", "--quiet", "-m", "Seed")
    bare = tmp_path / "remotes" / f"{name}.git"
    git(tmp_path, "clone", "--quiet", "--bare", str(work), str(bare))
    git(bare, "config", "uploadpack.allowFilter", "true")
    return bare


@pytest.fixture(name="workspace")
def fixture_workspace(tmp_path: Path) -> tuple[pipeline.Workspace, dict[str, Path]]:
    """A London Project repository with a `shared` block, and two subscriber remotes: `piece`, whose
    `AGENTS.md` lacks the block, and `other`, which has no `AGENTS.md`.

    Args:
        tmp_path: The test's temporary directory.
    """
    london = tmp_path / "london"
    london.mkdir()
    git(london, "init", "--quiet", "--initial-branch=main")
    (london / "AGENTS.md").write_text(LONDON_AGENTS)
    (london / ".gitignore").write_text("/.newsletter/\n")
    remotes = {
        "piece": make_remote(tmp_path, "piece", {"AGENTS.md": PIECE_AGENTS, "README.md": "Piece.\n"}),
        "other": make_remote(tmp_path, "other", {"README.md": "Other.\n"}),
    }
    pieces = "".join(
        f'[piece.{name}]\nrepository = "owner/{name}"\nurl = "{bare.as_uri()}"\n\n' for name, bare in remotes.items()
    )
    (london / "london.toml").write_text(
        '[london]\nrepository = "owner/london"\n\n'
        + pieces
        + '[newsletter]\nref = "main"\n\n'
        + '[[newsletter.section]]\nname = "agents"\nkind = "block"\nsubject = "Update the shared rules"\n'
        + 'path = "AGENTS.md"\nblock = "shared"\n'
    )
    git(london, "add", ".")
    git(london, "commit", "--quiet", "-m", "London")
    config = load(london / "london.toml")
    ws = pipeline.Workspace(
        london,
        config,
        Console(file=io.StringIO(), width=120),
        ScriptedPullRequests(),
        config.pieces,
        animate=False,
    )
    return ws, remotes


def phase(ws: pipeline.Workspace, name: str, stage: pipeline.Stage) -> dict[str, Outcome]:
    """Run `stage` for every subscriber as phase `name`.

    Args:
        ws: The workspace.
        name: The phase's name.
        stage: The work done for each subscriber.
    """
    return asyncio.run(pipeline.run_phase(ws, name, stage))


def states(outcomes: dict[str, Outcome]) -> dict[str, State]:
    """Each outcome's state, by name.

    Args:
        outcomes: A phase's outcomes.
    """
    return {name: outcome.state for name, outcome in outcomes.items()}


def send(ws: pipeline.Workspace) -> dict[str, Outcome]:
    """Run every phase, asserting that clone, sync and commit succeed, and return the push outcomes.

    Args:
        ws: The workspace.
    """
    assert states(phase(ws, "clone", pipeline.clone)) == {"piece": State.DONE, "other": State.DONE}
    assert not pipeline.failed(asyncio.run(pipeline.sync(ws)))
    assert states(phase(ws, "commit", pipeline.commit)) == {"piece": State.DONE, "other": State.DONE}
    return phase(ws, "push", pipeline.make_push(draft=False, force=False))


def test_an_edition_reaches_every_subscriber_as_a_pull_request(workspace):
    ws, remotes = workspace
    sha = git(ws.root, "rev-parse", "HEAD")
    branch = f"newsletter/{sha[:12]}"

    assert states(send(ws)) == {"piece": State.DONE, "other": State.DONE}

    piece = git(remotes["piece"], "show", f"{branch}:AGENTS.md")
    assert piece.startswith(PIECE_AGENTS)
    assert "<!-- newsletter:begin shared -->\n## Shared\n\nShared rules.\n<!-- newsletter:end shared -->" in piece
    assert "London's own rules" not in piece
    assert git(remotes["other"], "show", f"{branch}:AGENTS.md") == LONDON_AGENTS[LONDON_AGENTS.index("<!--") :].strip()

    message = git(remotes["piece"], "log", "-1", "--format=%B", branch)
    assert message.splitlines()[0] == "Update the shared rules"
    assert f"Newsletter-Source: owner/london@{sha}" in message
    assert "Newsletter-Section: agents" in message
    assert "appended" in message

    opened = ws.pull_requests.opened
    assert {(pr.repository, pr.base, pr.head) for pr in opened} == {
        ("owner/piece", "main", branch),
        ("owner/other", "main", branch),
    }
    piece_pr = next(pr for pr in opened if pr.repository == "owner/piece")
    assert piece_pr.title == "Update the shared rules"
    assert "## Notes" in piece_pr.body
    assert not piece_pr.draft


def test_clones_hold_one_commit_and_only_the_sections_files(workspace):
    ws, _ = workspace
    phase(ws, "clone", pipeline.clone)
    clone = ws.clone(ws.subscribers[0])
    assert git(clone, "rev-parse", "--is-shallow-repository") == "true"
    assert sorted(p.name for p in clone.iterdir() if p.name != ".git") == ["AGENTS.md"]


def test_sending_the_same_edition_again_changes_nothing(workspace):
    ws, remotes = workspace
    send(ws)
    pushed = {name: git(bare, "rev-parse", "--all") for name, bare in remotes.items()}
    assert states(send(ws)) == {"piece": State.SAME, "other": State.SAME}
    assert {name: git(bare, "rev-parse", "--all") for name, bare in remotes.items()} == pushed
    assert len(ws.pull_requests.opened) == 2


def test_a_new_source_commit_opens_a_new_edition(workspace):
    ws, remotes = workspace
    send(ws)
    agents = ws.root / "AGENTS.md"
    agents.write_text(agents.read_text().replace("Shared rules.", "Shared rules, revised."))
    git(ws.root, "commit", "--quiet", "-am", "Revise")
    sha = git(ws.root, "rev-parse", "HEAD")

    assert states(send(ws)) == {"piece": State.DONE, "other": State.DONE}
    assert "revised" in git(remotes["piece"], "show", f"newsletter/{sha[:12]}:AGENTS.md")
    assert len(ws.pull_requests.opened) == 4


def test_push_refuses_to_replace_commits_it_did_not_push(workspace, tmp_path):
    ws, remotes = workspace
    send(ws)
    branch = f"newsletter/{git(ws.root, 'rev-parse', 'HEAD')[:12]}"
    other = tmp_path / "reviewer"
    git(tmp_path, "clone", "--quiet", "--branch", branch, remotes["piece"].as_uri(), str(other))
    (other / "README.md").write_text("Reviewed.\n")
    git(other, "commit", "--quiet", "-am", "Review")
    git(other, "push", "--quiet", "origin", branch)
    reviewed = git(other, "rev-parse", "HEAD")

    asyncio.run(pipeline.sync(ws))
    agents = ws.clone(ws.subscribers[0]) / "AGENTS.md"
    agents.write_text(agents.read_text() + "\nLocal edit.\n")
    phase(ws, "commit", pipeline.commit)

    refused = phase(ws, "push", pipeline.make_push(draft=False, force=False))
    assert refused["piece"].state is State.FAIL
    assert git(remotes["piece"], "rev-parse", branch) == reviewed

    forced = phase(ws, "push", pipeline.make_push(draft=False, force=True))
    assert forced["piece"].state is State.DONE
    assert git(remotes["piece"], "rev-parse", branch) != reviewed


def test_a_failing_subscriber_does_not_stop_the_others(workspace):
    ws, remotes = workspace
    git(remotes["other"], "config", "uploadpack.allowFilter", "false")
    remotes["other"].rename(remotes["other"].with_suffix(".gone"))
    outcomes = phase(ws, "clone", pipeline.clone)
    assert states(outcomes) == {"piece": State.DONE, "other": State.FAIL}
    assert outcomes["other"].tail
    assert outcomes["other"].log is not None and outcomes["other"].log.exists()


def test_a_source_without_a_sections_block_fails_once_before_any_subscriber(workspace):
    ws, _ = workspace
    (ws.root / "AGENTS.md").write_text("# London\n")
    git(ws.root, "commit", "--quiet", "-am", "Drop the block")
    phase(ws, "clone", pipeline.clone)
    outcomes = asyncio.run(pipeline.sync(ws))
    assert states(outcomes) == {"london": State.FAIL}
    assert "shared" in outcomes["london"].summary


def test_draft_pull_requests_are_opened_as_drafts(workspace):
    ws, _ = workspace
    phase(ws, "clone", pipeline.clone)
    asyncio.run(pipeline.sync(ws))
    phase(ws, "commit", pipeline.commit)
    phase(ws, "push", pipeline.make_push(draft=True, force=False))
    assert [pr.draft for pr in ws.pull_requests.opened] == [True, True]


def test_preparing_stops_after_a_phase_in_which_a_subscriber_fails(workspace):
    ws, remotes = workspace
    remotes["other"].rename(remotes["other"].with_suffix(".gone"))
    outcomes = asyncio.run(pipeline.prepare(ws))
    assert states(outcomes) == {"piece": State.DONE, "other": State.FAIL}
    assert Edition.load(ws.clone(ws.subscribers[0])) is None


def reconfigure(ws: pipeline.Workspace, old: str, new: str) -> None:
    """Replace `old` with `new` in the London Project's `london.toml`, commit it and reload it.

    Args:
        ws: The workspace, whose configuration and subscribers are replaced.
        old: Text in the configuration.
        new: The text that replaces it.
    """
    path = ws.root / "london.toml"
    path.write_text(path.read_text().replace(old, new))
    git(ws.root, "commit", "--quiet", "-am", "Reconfigure")
    ws.config = load(path)
    ws.subscribers = ws.config.pieces


def test_each_section_commits_only_its_own_paths_whatever_is_staged(workspace):
    ws, _ = workspace
    (ws.root / "SHARED.txt").write_text("Shared.\n")
    git(ws.root, "add", "SHARED.txt")
    second = (
        '[[newsletter.section]]\nname = "shared-file"\nkind = "file"\nsubject = "Update the shared file"\n'
        'path = "SHARED.txt"\n'
    )
    reconfigure(ws, "[[newsletter.section]]\n", second + "\n[[newsletter.section]]\n")
    phase(ws, "clone", pipeline.clone)
    asyncio.run(pipeline.sync(ws))
    clone = ws.clone(ws.subscribers[0])
    git(clone, "add", "--sparse", "AGENTS.md", "SHARED.txt")

    assert states(phase(ws, "commit", pipeline.commit))["piece"] is State.DONE
    log = git(clone, "log", "-2", "--name-only", "--format=%x00%s%n%(trailers:key=Newsletter-Section,valueonly)")
    commits = [[line for line in chunk.splitlines() if line] for chunk in log.split("\0") if chunk]
    by_subject = {subject: (trailer, sorted(files)) for subject, trailer, *files in commits}
    assert by_subject == {
        "Update the shared file": ("shared-file", ["SHARED.txt"]),
        "Update the shared rules": ("agents", ["AGENTS.md"]),
    }


def test_a_clone_follows_a_change_of_base_branch(workspace, tmp_path):
    ws, remotes = workspace
    phase(ws, "clone", pipeline.clone)
    release = tmp_path / "release"
    git(tmp_path, "clone", "--quiet", remotes["piece"].as_uri(), str(release))
    git(release, "switch", "--quiet", "-c", "release")
    (release / "AGENTS.md").write_text("# Release\n")
    git(release, "commit", "--quiet", "-am", "Release")
    git(release, "push", "--quiet", "origin", "release")
    reconfigure(ws, 'repository = "owner/piece"\n', 'repository = "owner/piece"\nbase = "release"\n')

    assert states(phase(ws, "clone", pipeline.clone)) == {"piece": State.DONE, "other": State.DONE}
    assert not pipeline.failed(asyncio.run(pipeline.sync(ws)))
    agents = (ws.clone(ws.subscribers[0]) / "AGENTS.md").read_text()
    assert agents.startswith("# Release\n")


def test_open_opens_each_edition_pull_request_and_skips_the_rest(workspace):
    ws, _ = workspace
    send(ws)
    (ws.clone(ws.subscribers[1]) / ".git" / Edition.FILE).unlink()
    opened: list[str] = []

    def browse(url: str) -> bool:
        opened.append(url)
        return True

    outcomes = phase(ws, "open", pipeline.make_open(browse))
    assert states(outcomes) == {"piece": State.DONE, "other": State.SKIP}
    piece = next(i for i, pr in enumerate(ws.pull_requests.opened, start=1) if pr.repository == "owner/piece")
    assert opened == [f"https://example.invalid/owner/piece/pull/{piece}"]


def test_open_fails_a_subscriber_whose_url_no_browser_takes(workspace):
    ws, _ = workspace
    send(ws)
    outcomes = phase(ws, "open", pipeline.make_open(lambda url: False))
    assert states(outcomes) == {"piece": State.FAIL, "other": State.FAIL}
    assert "BROWSER" in outcomes["piece"].summary


def test_sync_after_the_edition_is_merged_finds_nothing_to_send(workspace, tmp_path):
    ws, remotes = workspace
    send(ws)
    branch = f"newsletter/{git(ws.root, 'rev-parse', 'HEAD')[:12]}"
    for name, bare in remotes.items():
        merger = tmp_path / "merge" / name
        git(tmp_path, "clone", "--quiet", bare.as_uri(), str(merger))
        git(merger, "merge", "--quiet", "--no-edit", f"origin/{branch}")
        git(merger, "push", "--quiet", "origin", "main")

    assert states(asyncio.run(pipeline.sync(ws))) == {"london": State.DONE, "piece": State.SAME, "other": State.SAME}


def test_a_structured_section_merges_settings_and_keeps_the_subscribers_comments(workspace, tmp_path):
    ws, remotes = workspace
    settings = ws.root / ".vscode" / "settings.json"
    settings.parent.mkdir()
    settings.write_text('{\n    "london.only": true,\n    "cSpell.language": "en-GB",\n}\n')
    git(ws.root, "add", ".vscode")
    structured = (
        '[[newsletter.section]]\nname = "editor-settings"\nkind = "structured"\nsubject = "Update the settings"\n'
        'path = ".vscode/settings.json"\nselect = ["/cSpell.language"]\n\n'
    )
    reconfigure(ws, "[[newsletter.section]]\n", structured + "[[newsletter.section]]\n")
    seeded = tmp_path / "seeded"
    git(tmp_path, "clone", "--quiet", remotes["piece"].as_uri(), str(seeded))
    (seeded / ".vscode").mkdir()
    (seeded / ".vscode" / "settings.json").write_text('{\n    // Why.\n    "piece.own": 1\n}\n')
    git(seeded, "add", ".vscode")
    git(seeded, "commit", "--quiet", "-m", "Settings")
    git(seeded, "push", "--quiet", "origin", "main")

    phase(ws, "clone", pipeline.clone)
    asyncio.run(pipeline.sync(ws))
    assert ".vscode/settings.json" in git(ws.clone(ws.subscribers[1]), "diff", "--name-only", "origin/main")

    assert states(send(ws)) == {"piece": State.DONE, "other": State.DONE}
    branch = f"newsletter/{git(ws.root, 'rev-parse', 'HEAD')[:12]}"
    assert git(remotes["piece"], "show", f"{branch}:.vscode/settings.json") == (
        '{\n    // Why.\n    "piece.own": 1,\n    "cSpell.language": "en-GB"\n}'
    )
    assert git(remotes["other"], "show", f"{branch}:.vscode/settings.json") == '{\n    "cSpell.language": "en-GB"\n}'
    subjects = git(remotes["piece"], "log", "--format=%s", f"main..{branch}").splitlines()
    assert "Update the settings" in subjects


def test_a_pull_request_is_titled_by_its_commit_subjects():
    assert pipeline.title(["Update the rules"]) == "Update the rules"
    assert pipeline.title(["Update the rules", "Update the settings"]) == "Update the rules; Update the settings"


def test_a_title_past_githubs_limit_counts_the_subjects_left_out():
    subjects = [f"Update section {n:03} of the shared material" for n in range(10)]
    titled = pipeline.title(subjects)
    assert len(titled) <= pipeline.TITLE_LIMIT
    assert titled.startswith("Update section 000 of the shared material; ")
    shown = titled.count("Update section")
    assert titled.endswith(f"; and {10 - shown} more")
    assert len(pipeline.title(["x" * 300])) == pipeline.TITLE_LIMIT


def test_pushing_again_retitles_an_open_pull_request_by_its_commits(workspace):
    ws, _ = workspace
    send(ws)
    clone = ws.clone(ws.subscribers[0])
    (clone / "AGENTS.md").write_text((clone / "AGENTS.md").read_text().replace("Piece rules.", "Piece rules, tidied."))
    git(clone, "commit", "--quiet", "-am", "Tidy the piece's rules")

    outcomes = phase(ws, "push", pipeline.make_push(draft=False, force=False))
    assert states(outcomes) == {"piece": State.DONE, "other": State.SAME}
    assert len(ws.pull_requests.opened) == 2
    piece = next(pr for pr in ws.pull_requests.opened if pr.repository == "owner/piece")
    assert piece.title == "Update the shared rules; Tidy the piece's rules"
    assert "- Tidy the piece's rules" in piece.body


def test_a_section_reaches_only_the_pieces_it_names(workspace):
    ws, remotes = workspace
    (ws.root / "SHARED.txt").write_text("Shared.\n")
    git(ws.root, "add", "SHARED.txt")
    only = (
        '[[newsletter.section]]\nname = "shared-file"\nkind = "file"\nsubject = "Update the shared file"\n'
        'path = "SHARED.txt"\npieces = ["other"]\n\n'
    )
    reconfigure(ws, "[[newsletter.section]]\n", only + "[[newsletter.section]]\n")

    assert states(send(ws)) == {"piece": State.DONE, "other": State.DONE}
    branch = f"newsletter/{git(ws.root, 'rev-parse', 'HEAD')[:12]}"
    assert git(remotes["other"], "show", f"{branch}:SHARED.txt") == "Shared."
    assert git(remotes["piece"], "ls-tree", "--name-only", branch) == "AGENTS.md\nREADME.md"
    assert git(remotes["piece"], "log", "--format=%s", f"main..{branch}") == "Update the shared rules"


def test_a_subscriber_sent_no_section_is_skipped(workspace):
    ws, _ = workspace
    reconfigure(ws, 'block = "shared"\n', 'block = "shared"\npieces = ["piece"]\n')
    phase(ws, "clone", pipeline.clone)
    assert states(asyncio.run(pipeline.sync(ws))) == {"london": State.DONE, "piece": State.DONE, "other": State.SKIP}


def test_a_rendering_section_sends_each_subscriber_its_own_values(workspace):
    ws, remotes = workspace
    agents = ws.root / "AGENTS.md"
    agents.write_text(
        agents.read_text().replace("Shared rules.", "Rules for {{london:piece.title}} on {{london:vars.os}}.")
    )
    git(ws.root, "add", "AGENTS.md")
    reconfigure(ws, 'repository = "owner/piece"\n', 'repository = "owner/piece"\ntitle = "The Piece"\n')
    reconfigure(ws, "[newsletter]\n", '[newsletter]\nvars = { os = "linux" }\n')
    reconfigure(ws, 'block = "shared"\n', 'block = "shared"\nrender = true\n')

    assert states(send(ws)) == {"piece": State.DONE, "other": State.DONE}
    branch = f"newsletter/{git(ws.root, 'rev-parse', 'HEAD')[:12]}"
    assert "Rules for The Piece on linux." in git(remotes["piece"], "show", f"{branch}:AGENTS.md")
    assert "Rules for Other on linux." in git(remotes["other"], "show", f"{branch}:AGENTS.md")


def test_a_token_without_a_value_for_a_subscriber_fails_once_before_any_subscriber(workspace):
    ws, _ = workspace
    agents = ws.root / "AGENTS.md"
    agents.write_text(agents.read_text().replace("Shared rules.", "Released as {{london:piece.tag}}."))
    git(ws.root, "add", "AGENTS.md")
    reconfigure(ws, 'block = "shared"\n', 'block = "shared"\nrender = true\n')
    phase(ws, "clone", pipeline.clone)

    outcomes = asyncio.run(pipeline.sync(ws))
    assert states(outcomes) == {"london": State.FAIL}
    assert "piece.tag" in outcomes["london"].summary
    assert Edition.load(ws.clone(ws.subscribers[0])) is None
