"""Best-effort actual HTTP boundary logs; no I/O or provider content."""
from datetime import datetime, timezone
import json
import re

from ai_prediction_guard import PublicationRejected


def emit_http_phase(log, reference, attempt, phase, *, status=None):
    """Diagnostics must not affect a provider request or its accepted result.

    Use an independent wall clock, without consuming the caller's deadline clock.
    Only these six fixed public fields can reach the supplied logger.
    """
    try:
        if (not isinstance(reference, str) or not re.fullmatch(r'[0-9]{11}', reference)
                or type(attempt) is not int or not 1 <= attempt <= 3
                or not isinstance(phase, str) or phase not in ('start', 'complete')):
            return
        now = datetime.now(timezone.utc)
        record = {
            'ref': reference, 'attempt': attempt, 'phase': phase, 'at': now.isoformat(),
            'status': status if type(status) is int and 100 <= status <= 599 else None,
        }
        log('AI_HTTP_PHASE ' + json.dumps(record, ensure_ascii=True, allow_nan=False, separators=(',', ':')))
    except PublicationRejected:
        # SIGALRM deadline rejection can arrive while formatting or logging.
        # It must keep its original request-abort semantics.
        raise
    except Exception:
        pass
