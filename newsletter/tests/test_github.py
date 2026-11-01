"""Reading a pull request's readiness to merge from `gh pr view`."""

from newsletter.github import Readiness


def test_readiness_sorts_check_runs_and_commit_statuses_into_pending_and_failing():
    data = {
        "headRefOid": "a" * 40,
        "isDraft": False,
        "mergeable": "MERGEABLE",
        "statusCheckRollup": [
            {"__typename": "CheckRun", "name": "Build", "status": "COMPLETED", "conclusion": "SUCCESS"},
            {"__typename": "CheckRun", "name": "Files", "status": "COMPLETED", "conclusion": "SKIPPED"},
            {"__typename": "CheckRun", "name": "Note", "status": "COMPLETED", "conclusion": "NEUTRAL"},
            {"__typename": "CheckRun", "name": "Test", "status": "IN_PROGRESS", "conclusion": ""},
            {"__typename": "CheckRun", "name": "Lint", "status": "COMPLETED", "conclusion": "FAILURE"},
            {"__typename": "CheckRun", "name": "Old", "status": "COMPLETED", "conclusion": "CANCELLED"},
            {"__typename": "StatusContext", "context": "ci/signed", "state": "SUCCESS"},
            {"__typename": "StatusContext", "context": "ci/deploy", "state": "PENDING"},
            {"__typename": "StatusContext", "context": "ci/scan", "state": "ERROR"},
        ],
    }
    assert Readiness.from_gh(data) == Readiness(
        head="a" * 40, pending=("Test", "ci/deploy"), failing=("Lint", "Old", "ci/scan")
    )


def test_readiness_reads_a_draft_and_a_conflict():
    data = {"headRefOid": "b" * 40, "isDraft": True, "mergeable": "CONFLICTING", "statusCheckRollup": []}
    assert Readiness.from_gh(data) == Readiness(head="b" * 40, draft=True, conflicting=True)
