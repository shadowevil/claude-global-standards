"""Rule modules, grouped by the filesystem action they judge (write/, delete/, read/, execute/...).
write/ and delete/ run under write_guard.py, which runs modules per the action(s) it classifies a
tool call into; execute/ (W0, the size cap) runs first in write_guard.py, before any of them; size/ (S1, the .claude size flag) runs under size_guard.py."""
