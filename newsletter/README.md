# newsletter

`newsletter` carries material the London Project maintains here, such as the shared sections
of `AGENTS.md`, to each piece's repository as a pull request. `newsletter.toml` names the
subscribers and the sections.

```console
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[test]'
newsletter --help
```
