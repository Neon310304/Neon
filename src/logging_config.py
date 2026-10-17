import logging


def configure_logging(level: str) -> None:
    logging.basicConfig(level=level)
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
