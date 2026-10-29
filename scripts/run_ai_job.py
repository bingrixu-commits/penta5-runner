#!/usr/bin/env python3
"""Run one fixed local AI job and report verified publication state."""
from pathlib import Path
import argparse
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ai_job_status import JOBS, clean_error, run_job


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kind', choices=tuple(JOBS))
    arguments = parser.parse_args()
    try:
        result = run_job(arguments.kind)
    except Exception as error:
        print(clean_error(error), file=sys.stderr)
        return 1
    print(arguments.kind + ': ' + result['status'])
    return result['exit_code']


if __name__ == '__main__':
    raise SystemExit(main())

