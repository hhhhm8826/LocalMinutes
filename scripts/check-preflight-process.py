"""모델 호출 없는 실제 시간 제한·자식 종료·공용 잠금 시험."""
import fcntl
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import psutil
from preflight_process import communicate_bounded

with tempfile.TemporaryDirectory(prefix='minutes-process-probe-') as temp:
    pid_file = Path(temp)/'child.pid'
    code = 'import subprocess,sys,time,pathlib; p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"]); pathlib.Path(sys.argv[1]).write_text(str(p.pid)); time.sleep(60)'
    process = subprocess.Popen([sys.executable,'-c',code,str(pid_file)],start_new_session=True,
                               stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    _, _, expired = communicate_bounded(process,'',2)
    child_pid = int(pid_file.read_text())
    child_live = psutil.pid_exists(child_pid) and psutil.Process(child_pid).status() != psutil.STATUS_ZOMBIE
    assert expired and process.poll() is not None and not child_live
    lock_path = Path.home()/'.cache/local-meeting-minutes/heavy.lock'
    lock = lock_path.open('a+')
    fcntl.flock(lock,fcntl.LOCK_EX | fcntl.LOCK_NB)
    contender = 'import fcntl,sys; f=open(sys.argv[1],"a+");\ntry: fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)\nexcept BlockingIOError: sys.exit(23)\nsys.exit(0)'
    result = subprocess.run([sys.executable,'-c',contender,str(lock_path)],capture_output=True)
    assert result.returncode == 23
    report = {'status':'PASS','timeout_seconds':2,'parent_terminated':True,'child_not_running':True,
              'shared_lock_exclusion':True,'model_calls':0}
    Path('.workflow/evidence/process-boundaries.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report))
