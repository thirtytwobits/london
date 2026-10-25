"""Serve RFC 7662 token introspection on loopback for the tokens in development-grants.json.

The server stands in for an application's authorisation server during development. It verifies
the resource server's credentials and answers for each development token with that token's
grant, bound to the issuer and audience in configuration.json.
"""

import argparse
import base64
import http.server
import json
import threading
import urllib.parse
from pathlib import Path

EXAMPLE = Path(__file__).resolve().parent


GRANTS = json.loads((EXAMPLE / "development-grants.json").read_text())


def resource_server():
    """The client ID and secret the resource server presents to the introspection endpoint."""
    client = GRANTS["resource_server"]
    return client["client_id"], client["client_secret"]


def responses():
    """The introspection response for each development token, and the expected client credentials."""
    oauth = json.loads((EXAMPLE / "configuration.json").read_text())["oauth"]
    bound = {"active": True, "iss": oauth["issuer"], "aud": [oauth["audience"]]}
    pair = ":".join(urllib.parse.quote(part) for part in resource_server())
    basic = "Basic " + base64.b64encode(pair.encode()).decode()
    return {token: bound | grant for token, grant in GRANTS["tokens"].items()}, basic


class Introspection(http.server.BaseHTTPRequestHandler):
    """Answers POST /introspect for the development tokens."""

    grants, basic = responses()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        form = urllib.parse.parse_qs(self.rfile.read(length).decode())
        if self.path != "/introspect":
            self.reply(404, {"error": "not_found"})
        elif self.headers.get("Authorization") != self.basic:
            self.reply(401, {"error": "invalid_client"})
        else:
            token = form.get("token", [""])[0]
            self.reply(200, self.grants.get(token, {"active": False}))

    def reply(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format, *args):
        pass


def start(port=0):
    """Serve introspection on a background thread, returning the server and its endpoint URL."""
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Introspection)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/introspect"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8400, help="Loopback port to listen on (default: 8400)")
    args = parser.parse_args()
    server, url = start(args.port)
    print(f"Introspection endpoint: {url}. Press Ctrl-C to stop.", flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        server.shutdown()
