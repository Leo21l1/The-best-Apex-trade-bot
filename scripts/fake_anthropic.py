"""Scripted stand-in for the Anthropic Messages API, for end-to-end tests only.

Round 1: asks for two read-only tools. Round 2: answers using numbers from the tool results, so the
test can confirm the UI shows data that really came from the application.
Usage: python scripts/fake_anthropic.py [port]
"""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def message(content: list[dict], stop: str) -> dict:
    return {"id": "msg_fake", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
            "content": content, "stop_reason": stop, "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 10}}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: object) -> None:  # quiet
        pass

    def _send(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # health check
        self._send(200, {"ok": True})

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))))
        key = self.headers.get("x-api-key") or self.headers.get("authorization", "").removeprefix("Bearer ")
        if not self.path.startswith("/v1/messages") or key != "e2e-test-key":
            self._send(401, {"type": "error", "error": {"type": "authentication_error", "message": "bad key"}})
            return
        if not body.get("tools"):  # trade-coaching request (no tools): answer from the supplied record
            record = json.loads(body["messages"][-1]["content"])
            t = record["trade"]
            text = (f"**What went well**\n- Trade #{t['id']} on {t['symbol']} had a recorded stop at {t['planned_stop']}.\n\n"
                    f"**What to improve**\n- Net result {t['net_pnl']:+.2f} is reviewed against the plan, not judged alone.\n\n"
                    "**Next learning objective**\n- Keep recording plans before entry.")
            self._send(200, message([{"type": "text", "text": text}], "end_turn"))
            return
        last = body["messages"][-1]["content"]
        results = [b for b in last if isinstance(b, dict) and b.get("type") == "tool_result"] if isinstance(last, list) else []
        if not results:
            self._send(200, message([
                {"type": "text", "text": "Checking your rules and account."},
                {"type": "tool_use", "id": "toolu_1", "name": "get_rule_profile", "input": {}},
                {"type": "tool_use", "id": "toolu_2", "name": "get_account_risk", "input": {}},
            ], "tool_use"))
            return
        rules = json.loads(results[0]["content"])
        unverified = sum(1 for r in rules.get("rules", []) if not r["verified"])
        account = json.loads(results[1]["content"])
        acct_line = (f"- Risk budget: ${account['account']['risk_budget']:,.2f} (get_account_risk)"
                     if account.get("data_class") != "unavailable" else "- Account risk: unknown")
        text = (
            "**Verified data**\n"
            f"- Unverified Apex rules: {unverified} (get_rule_profile)\n{acct_line}\n\n"
            "**Missing information**\n- No validated strategy, so no historical expectancy exists."
        )
        self._send(200, message([{"type": "text", "text": text}], "end_turn"))


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8020
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
