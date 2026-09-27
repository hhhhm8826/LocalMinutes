"""원문·토큰 없이 코드와 식별자만 기록하는 회전 로그."""
import json
import logging
from logging.handlers import RotatingFileHandler


def configure_log(directory):
    logger = logging.getLogger("meeting_minutes")
    for handler in logger.handlers[:]:
        handler.close()
        logger.removeHandler(handler)
    handler = RotatingFileHandler(directory / "events.jsonl", maxBytes=2_000_000,
                                  backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter('%(message)s'))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


def event(logger, code, **fields):
    allowed = {"job_id", "attempt_id", "stage", "error_code", "elapsed_seconds"}
    if not fields.keys() <= allowed:
        raise ValueError("허용되지 않은 로그 필드")
    logger.info(json.dumps({"code": code, **fields}, ensure_ascii=False))
