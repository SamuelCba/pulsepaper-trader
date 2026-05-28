from __future__ import annotations

import os

from webapp import serve_web


def main() -> None:
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    serve_web(host=host, port=port)


if __name__ == "__main__":
    main()
