import os

import uvicorn


def main() -> None:
    port = int(os.getenv("DATABRICKS_APP_PORT") or os.getenv("UVICORN_PORT") or "8000")
    host = os.getenv("UVICORN_HOST", "0.0.0.0")
    uvicorn.run("backend.main:app", host=host, port=port)


if __name__ == "__main__":
    main()
