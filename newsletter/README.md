# newsletter

`newsletter` carries material the London Project maintains here, such as the shared sections
of `AGENTS.md`, to each piece's repository as a pull request. The `[newsletter]` table of
`london.toml`, at the root of the checkout, names the sections; every piece is a subscriber.

```console
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[test]'
newsletter --help
```
