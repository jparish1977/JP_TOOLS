#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_ci_image_ids.py
ci-image/fleet-ci-ids.sh, called as build.sh calls it (set -euo pipefail),
with stand-ins for sudo (answering docker info, or failing) and id (a host
fleet-ci account, or none). Each case of its header:

    - the daemon lists name=userns: uid 994 gid 981, account or not.
    - it does not: the host account's ids; with no account, exit 1 and how
      to make one.
    - it cannot be asked: exit 2, "nothing built", account or not; never
      read as "does not remap".
    - name=userns among other options, and an option that only contains
      the word, are told apart.

    python3 tests/test_ci_image_ids.py
"""

import subprocess
import sys

from _harness import ROOT, check, check_true, run
from fake_tools import calls, env, fake_tools

SCRIPT = ROOT / "ci-image" / "fleet-ci-ids.sh"
SUDO = """\
if os.environ.get("FAKE_SUDO_RC", "0") != "0":
    sys.stderr.write("docker: template parsing error\\n")
    sys.exit(int(os.environ["FAKE_SUDO_RC"]))
print(os.environ.get("FAKE_INFO", ""))
"""
ID = """\
if not os.environ.get("FAKE_ACCOUNT"):
    sys.exit(1)
print({"-u": 1500, "-g": 1501}[sys.argv[1]])
"""
USERNS = "name=apparmor name=seccomp,profile=builtin name=userns "
PLAIN = "name=apparmor name=seccomp,profile=builtin "


def _ids(info: str = "", sudo_rc: str = "0", account: str = "") -> tuple[int, str, str]:
    """(exit, stdout, stderr) of fleet-ci-ids.sh then printing uid and gid."""
    with fake_tools(sudo=SUDO, id=ID) as (work, log), \
            env(FAKE_INFO=info, FAKE_SUDO_RC=sudo_rc, FAKE_ACCOUNT=account):
        # As build.sh calls it.
        script = (f'set -euo pipefail; ids=$(/bin/bash "{SCRIPT}" dind-x); '
                  'read -r uid gid <<<"$ids"; echo "ids $uid $gid"')
        p = subprocess.run(["/bin/bash", "-c", script],
                           cwd=work, capture_output=True, text=True, check=False)
        sudo_argv = [c for c in calls(log) if c[0] == "sudo"]
    check_true("  asked the named daemon", sudo_argv[:1] == [[
        "sudo", "-n", "docker", "exec", "dind-x", "docker", "info", "-f",
        "{{range .SecurityOptions}}{{.}} {{end}}"]])
    return p.returncode, p.stdout, p.stderr


def test_a_remapping_daemon_needs_no_account() -> None:
    for account in ("", "1"):
        rc, out, err = _ids(USERNS, account=account)
        check(f"remaps, account={bool(account)}: exit 0", rc, 0)
        check_true("  994 981, not the host's", "ids 994 981" in out)
        check_true("  and says why", "remaps users, so no host account is needed" in err)


def test_no_remap_takes_the_host_account() -> None:
    rc, out, _ = _ids(PLAIN, account="1")
    check("no remap, account: exit 0", rc, 0)
    check_true("  the host account's ids", "ids 1500 1501" in out)


def test_no_remap_and_no_account_stops() -> None:
    rc, out, err = _ids(PLAIN)
    check("no remap, no account: exit 1", rc, 1)
    check_true("  says how to make the account", "useradd --system" in err)
    check_true("  and goes no further", "ids" not in out)


def test_a_probe_that_cannot_answer_is_not_no_remap() -> None:
    for account in ("1", ""):
        rc, out, err = _ids(sudo_rc="1", account=account)
        check(f"probe failed, account={bool(account)}: exit 2", rc, 2)
        check_true("  says it could not ask", "could not ask dind-x" in err and "nothing built" in err)
        check_true("  and claims nothing about remapping", "does not remap" not in err)
        check_true("  nor builds with any ids", "ids" not in out)


def test_the_option_is_matched_whole() -> None:
    rc, out, _ = _ids("name=userns-ish name=seccomp ", account="1")
    check("an option only containing the word is not it: exit 0", rc, 0)
    check_true("  the host account's ids", "ids 1500 1501" in out)


if __name__ == "__main__":
    sys.exit(run(globals(), "fleet-ci-ids.sh"))
