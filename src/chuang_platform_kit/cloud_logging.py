"""Structured logging for the Cloud Functions: one JSON line per record, so
Cloud Logging sees a SEVERITY and Error Reporting sees an ERROR.

WHY. Every function set `logging.basicConfig(level=INFO, force=True)`, which
writes `ERROR:shared.player_data:Failed to get full player data: EOF ...` as a
plain line on stdout. Cloud Run ingests that as textPayload with NO severity.
So on 2026-09-11 9:28 PM PT a real ERROR was invisible to a `severity>=ERROR`
filter, invisible to the Error Reporting API that `error-reporting.yml` sweeps
hourly, and therefore never became a GitHub issue for the alert investigator
routines to pick up. The whole chain -- log, sweep, issue, routine -- was
intact and idle, because the first link never fired (Gene: "the whole routines
and GH Issue watch and proactive investigation isn't working??").

Cloud Run parses a JSON line on stdout: `severity` becomes the entry's
severity, `message` the payload, and an entry carrying
`@type: ...ReportedErrorEvent` with `serviceContext` is ingested by Error
Reporting whether or not it has a stack trace. `logger.error(..., exc_info=True)`
puts the traceback in the message, so a grouped error reads like one.

Stdlib only -- this module is copied into all four functions at deploy time
and must not add a package to any of them (CLAUDE.md, "Adding a Dependency to
webhook/shared/"). Local scripts keep plain basicConfig; only the functions'
main.py call setup_logging().
"""
import json
import logging
import os
import sys

from .redact import redact

ERROR_EVENT_TYPE = 'type.googleapis.com/google.devtools.clouderrorreporting.v1beta1.ReportedErrorEvent'


class JsonFormatter(logging.Formatter):
    """One JSON object per record: severity, message (+ traceback), logger."""

    def __init__(self, service=''):
        super().__init__()
        self.service = service

    def format(self, record):
        message = record.getMessage()
        if record.exc_info:
            message = message + '\n' + self.formatException(record.exc_info)
        # Every line, the traceback included, passes the scrub: the
        # 2026-09-25 PT GREEN-API token reached Error Reporting through
        # Flask's own "Exception on / [POST]" line, not one of ours.
        message = redact(message)
        entry = {
            'severity': record.levelname,
            'message': message,
            'logger': record.name,
        }
        if record.levelno >= logging.ERROR:
            entry['@type'] = ERROR_EVENT_TYPE
            entry['serviceContext'] = {'service': self.service or 'unknown'}
        return json.dumps(entry, ensure_ascii=False)


def setup_logging(level=logging.INFO, service=None, stream=None):
    """Install the JSON formatter on the root logger, replacing whatever
    functions-framework installed first (the reason basicConfig needed
    force=True). `service` defaults to Cloud Run's K_SERVICE."""
    service = service if service is not None else os.environ.get('K_SERVICE', '')
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(JsonFormatter(service))
    logging.basicConfig(level=level, force=True, handlers=[handler])
    return handler


class RedactFilter(logging.Filter):
    """The scrub for plain (non-JSON) handlers: the CLI and the scripts a
    workflow runs, whose output becomes the step log that report-failure
    quotes in an alert issue. Rewrites the message and pre-formats the
    traceback (a Formatter uses exc_text when it is set), both redacted."""

    def filter(self, record):
        try:
            record.msg = redact(record.getMessage())
            record.args = None
            if record.exc_info and not record.exc_text:
                record.exc_text = logging.Formatter().formatException(record.exc_info)
            if record.exc_text:
                record.exc_text = redact(record.exc_text)
        except Exception:
            pass
        return True


def install_redaction(logger: logging.Logger = None) -> None:
    """Attach RedactFilter to every handler of `logger` (the root by
    default). Call it after logging.basicConfig() in a script."""
    for h in (logger or logging.getLogger()).handlers:
        if not any(isinstance(f, RedactFilter) for f in h.filters):
            h.addFilter(RedactFilter())
