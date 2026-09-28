"""Owner-only local terminal login; never copy development credentials."""
import os

from meeting_minutes.claude_provider import environment, isolation
from meeting_minutes.settings import Settings

settings = Settings()
if not settings.claude_cli.is_file():
    raise SystemExit('Run the approved optional setup-claude.py installer first')
for root in (settings.claude_home, settings.claude_user_home):
    if root.is_symlink():
        raise SystemExit('Unsafe runtime home')
    root.mkdir(parents=True,exist_ok=True,mode=0o700)
    root.chmod(0o700)
isolation(settings)
# Use a persistent empty working directory; no project customization is loaded.
cwd = settings.claude_user_home/'login'
cwd.mkdir(exist_ok=True,mode=0o700)
os.chdir(cwd)
os.execve(settings.claude_cli,[str(settings.claude_cli),'auth','login'],environment(settings))
