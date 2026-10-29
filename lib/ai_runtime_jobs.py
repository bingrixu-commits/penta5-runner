"""Stop only the fixed AI job trees; never database, draw or stats services."""
from pathlib import Path
import os,re,shlex,signal,subprocess

_ROOT_TEXT='/root/penta5-runner'
_ROOT=Path(_ROOT_TEXT)
_ENTRYPOINTS=frozenset(('scripts/run_ai_ready_job.py','scripts/run_ai_job.py',
    'scripts/run_he3_refine.py','scripts/run_selected_framework.py',
    'scripts/run_selected_six.py','scripts/run_cold_framework.py','cold_framework_model.py',
    'scripts/run_cold_fixed_framework.py','cold_fixed_framework_model.py',
    'selected_framework_model.py','selected_six_model.py',
    'auto_r1_killer.py','auto_r1_framework.py'))
_NAMES=frozenset(Path(name).name for name in _ENTRYPOINTS)

def _owned_entrypoint(argument,cwd):
    prefix=_ROOT_TEXT+'/'
    if argument.startswith(prefix):return argument[len(prefix):] in _ENTRYPOINTS
    relative=argument[2:] if argument.startswith('./') else argument
    return cwd==_ROOT and relative in _ENTRYPOINTS

def _direct_model(args,cwd):
    # A text editor, cat, or a Python program merely receiving a model filename
    # is not that model. Inspect only Python's executed script/module argument.
    if not args or re.fullmatch(r'python(?:[0-9]+(?:\.[0-9]+)*)?',Path(args[0]).name) is None:return False
    index=1
    while index<len(args):
        argument=args[index]
        if argument in ('-c','-V','--version','-h','--help') or argument.startswith('-c'):return False
        if argument=='-m':
            return (index+1<len(args) and cwd==_ROOT
                    and args[index+1].replace('.','/')+'.py' in _ENTRYPOINTS)
        if argument in ('-W','-X','--check-hash-based-pycs'):
            index+=2;continue
        if argument=='--':return index+1<len(args) and _owned_entrypoint(args[index+1],cwd)
        if argument in ('-u','-B','-E','-I','-s','-S','-q','-O','-OO','-b','-bb'):
            index+=1;continue
        if argument.startswith('-'):return False
        return _owned_entrypoint(argument,cwd)
    return False

def _cron_model_command(command):
    try:
        lexer=shlex.shlex(command,posix=True,punctuation_chars=';&|')
        lexer.whitespace_split=True
        words=list(lexer)
    except ValueError:return False
    cwd=None;segment=[]
    for word in words+[';']:
        if word not in (';','&&','||','&','|'):
            segment.append(word);continue
        if segment:
            if segment[0]=='cd' and len(segment)==2:
                cwd=Path(segment[1])
            elif _direct_model(segment,cwd):return True
            elif segment[0] in ('exec','timeout','flock','nice','ionice'):
                for index,value in enumerate(segment[1:],1):
                    if re.fullmatch(r'python(?:[0-9]+(?:\.[0-9]+)*)?',Path(value).name):
                        if _direct_model(segment[index:],cwd):return True
                        break
        segment=[]
    return False

def stop_model_jobs(*,proc_root=Path('/proc'),killer=os.kill,own_pid=None,cron_commands=None):
    own_pid=os.getpid() if own_pid is None else own_pid
    if cron_commands is None:
        try:
            lines=subprocess.check_output(['crontab','-l'],text=True).splitlines()
            cron_commands={line.split(None,5)[5] for line in lines if line and not line.lstrip().startswith('#')
                           and len(line.split(None,5))==6 and _cron_model_command(line.split(None,5)[5])}
        except (OSError,subprocess.SubprocessError):cron_commands=set()
    processes={}
    for directory in Path(proc_root).iterdir():
        if not directory.name.isdigit():continue
        try:
            args=[part.decode('utf-8') for part in (directory/'cmdline').read_bytes().split(b'\0') if part]
            parent=int(next(line.split()[1] for line in (directory/'status').read_text().splitlines() if line.startswith('PPid:')))
            cwd=(directory/'cwd').resolve()
            processes[int(directory.name)]=(parent,args,cwd)
        except (OSError,ValueError,UnicodeError,StopIteration):continue
    selected=set()
    for pid,(parent,args,cwd) in processes.items():
        if pid==own_pid or not args:continue
        direct=_direct_model(args,cwd)
        shell=(Path(args[0]).name in ('sh','dash','bash') and len(args)>=3 and args[1]=='-c'
               and args[2] in cron_commands)
        if direct or shell:selected.add(pid)
    while True:
        children={pid for pid,(parent,args,cwd) in processes.items() if parent in selected and pid!=own_pid}
        if children<=selected:break
        selected|=children
    stopped=0
    for pid in sorted(selected,reverse=True):
        try:killer(pid,signal.SIGTERM);stopped+=1
        except ProcessLookupError:pass
    return stopped
