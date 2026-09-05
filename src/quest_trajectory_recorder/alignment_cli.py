"""Inspect or advance the source-owned direction alignment over local ZMQ."""

from __future__ import annotations

import argparse
import json
import uuid

import zmq


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["status", "start", "forward", "finish"])
    parser.add_argument("--endpoint", default="tcp://127.0.0.1:8133")
    args = parser.parse_args()
    try:
        with zmq.Context() as context, context.socket(zmq.REQ) as socket:
            socket.setsockopt(zmq.LINGER, 0)
            socket.connect(args.endpoint)

            def request(action: str, revision: str = "") -> dict:
                socket.send_json(
                    {"action": action, "revision": revision, "request_id": str(uuid.uuid4())}
                )
                if not socket.poll(2000):
                    raise TimeoutError("Source unavailable; start the source and check its log")
                return socket.recv_json()

            result = request("status")
            if args.action != "status" and result.get("applied"):
                result = request(args.action, result["alignment"]["revision"])
    except (OSError, ValueError, zmq.ZMQError) as exc:
        result = {"applied": False, "message": str(exc)}
    print(json.dumps(result, indent=2))
    return 0 if result.get("applied") else 1


if __name__ == "__main__":
    raise SystemExit(main())
