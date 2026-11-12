"""Plain backend used by the proxy tests: answers with its own tag and what it received."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from daemon import StreapRous

app = StreapRous()
TAG = sys.argv[2]


@app.route('/', methods=['GET'])
def root(headers, body):
    return {"backend": TAG, "seen_host_header": headers.get("x-seen-host"),
            "forwarded_for": headers.get("x-forwarded-for")}


@app.route('/echo', methods=['POST'])
def echo(headers, body):
    return {"backend": TAG, "body_len": len(body)}


if __name__ == "__main__":
    app.prepare_address("127.0.0.1", int(sys.argv[1]), "threading")
    app.run()
