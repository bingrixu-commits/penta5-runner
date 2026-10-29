"""Fixed, non-shell entry used by the authenticated manual-control endpoint."""
from pathlib import Path
import argparse,json,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ai_call_control import AICallsDisabled,read_control,set_enabled
from ai_runtime_jobs import stop_model_jobs

def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=('status','start','stop'))
    parser.add_argument('--expected-generation',type=int)
    args=parser.parse_args(argv)
    try:
        if args.action=='status':state=read_control()
        else:
            if args.expected_generation is None or args.expected_generation<0:raise ValueError('invalid-generation')
            state=set_enabled(args.action=='start',args.expected_generation)
            if args.action=='stop':stop_model_jobs()
        print(json.dumps(state,ensure_ascii=True),flush=True)
        return 0
    except ValueError as error:
        if str(error)=='ai-api-control-generation-conflict':
            print(json.dumps({'ok':False,'error':'generation-conflict','state':read_control()}),flush=True)
            return 3
        print(json.dumps({'ok':False,'error':'control-unavailable'}),flush=True)
        return 2
    except Exception:
        print(json.dumps({'ok':False,'error':'control-unavailable'}),flush=True)
        return 2

if __name__=='__main__':raise SystemExit(main())
