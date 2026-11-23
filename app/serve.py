"""
Production entry point:  python -m app.serve

Reads HOST / PORT from the environment (Railway injects PORT), trusts the platform's
reverse proxy for client addresses and HTTPS, and runs a single worker: the app keeps one
SQLite connection and loads the models once per process.
"""
import uvicorn

from . import config


def main():
    uvicorn.run(
        "app.main:app",
        host=config.HOST,
        port=config.PORT,
        workers=1,
        proxy_headers=True,
        forwarded_allow_ips="*",
        log_level=config.LOG_LEVEL.lower(),
        timeout_keep_alive=30,
    )


if __name__ == "__main__":
    main()
