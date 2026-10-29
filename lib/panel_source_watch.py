"""Keep the cheap, validated source panel current during a serialized proof scan.

This owns no process, provider call, draw cache or prediction ledger. Its caller
must hold the statistics job lock for the entire context. Only the supplied
atomic base-panel publisher can change the page.
"""
from contextlib import contextmanager
import json
import math
import threading
import time


def _notice(status, started, result=None, error=None):
    try:
        row={'event':'base_source_refresh_v1','status':status,
             'elapsed_seconds':round(max(0.0,time.monotonic()-started),6)}
        if isinstance(result,dict):
            for name in ('reference','checked_at'):
                if isinstance(result.get(name),str):row[name]=result[name]
        if error is not None:row['error_type']=type(error).__name__
        print('PENTA5_BASE_SOURCE '+json.dumps(row,separators=(',',':')),flush=True)
    except Exception:
        pass


@contextmanager
def watch_source(refresh, source_key, *, interval=2.0):
    """Retry failed source changes; join any atomic publisher before unlocking."""
    if type(interval) not in (int,float) or not math.isfinite(interval) or interval<=0:
        raise ValueError('Invalid source watch interval')
    stopped=threading.Event()

    def run():
        previous=None
        while not stopped.is_set():
            started=time.monotonic()
            try:
                current=source_key()
                if current!=previous:
                    result=refresh()
                    previous=current
                    _notice('success',started,result)
            except Exception as error:
                # Do not acknowledge a failed read/render/fsync. No stale success
                # stamp is published, and the next iteration retries this key.
                _notice('failed',started,error=error)
            stopped.wait(interval)

    worker=threading.Thread(target=run,name='panel-base-source',daemon=True)
    worker.start()
    try:
        yield
    finally:
        stopped.set()
        worker.join()
