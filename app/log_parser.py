import re
from typing import List


# each tuple is (flag_name, regex_pattern)
# keeping this as a simple list makes it easy to add new patterns
LOG_PATTERNS = [
    ("expired_token",      r"(token|jwt|session).{0,20}(expired|invalid|revoked)"),
    ("timeout",            r"(timeout|timed.?out|deadline.exceeded|connection.reset)"),
    ("missing_field",      r"missing.{0,20}(field|param|key|property)"),
    ("connection_refused", r"connection.refused|ECONNREFUSED|no.route.to.host"),
    ("permission_denied",  r"(permission|access).denied|forbidden"),
    ("not_found",          r"(route|endpoint|path|resource).not.found"),
    ("rate_limited",       r"rate.lim|too.many.requests|429"),
    ("db_error",           r"(database|db|sql|query).error|connection.pool"),
    ("null_pointer",       r"null(pointer|reference)|NoneType|AttributeError"),
    ("invalid_json",       r"invalid.json|json.parse|unexpected.token|malformed"),
]

NEGATION_WINDOW_CHARS = 40
NEGATION_WORD_RE = re.compile(r"\b(?:not|no|without|never)\b")


def parse_logs(log_text: str) -> List[str]:
    """
    Scan log lines for known error patterns and return a list of flags.
    Simple heuristic — not perfect but catches the common cases.
    """
    if not log_text:
        return []

    flags = []
    text = log_text.lower()

    for flag_name, pattern in LOG_PATTERNS:
        m = re.search(pattern, text, re.IGNORECASE)
        if not m:
            continue

        # Basic negation awareness: if the match is preceded by a negation
        # word within a short window, assume it's not the failure we care about.
        # Example: "not expired" should not trigger "expired_token".
        start = m.start()
        window_start = max(0, start - NEGATION_WINDOW_CHARS)
        window = text[window_start:start]
        if NEGATION_WORD_RE.search(window):
            continue

        flags.append(flag_name)

    return flags
