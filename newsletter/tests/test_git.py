"""Reading the version git reports."""

from newsletter.git import MINIMUM_VERSION, version


def test_reads_the_version_git_reports():
    assert version("git version 2.49.0") == (2, 49, 0)
    assert version("git version 2.39.5 (Apple Git-154)") == (2, 39, 5)
    assert version("git version 2.34.1.windows.1") == (2, 34, 1)


def test_versions_before_the_minimum_are_refused():
    assert version("git version 2.33.8") < MINIMUM_VERSION <= version("git version 2.34.0")
    assert version("not git") < MINIMUM_VERSION
