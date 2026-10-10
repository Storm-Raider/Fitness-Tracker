"""scripts/auto-deploy-user.sh against a throwaway repo.

Every side effect is redirected: ZENKAI_REPO points at a temp clone,
ZENKAI_RESTART_CMD records restarts instead of killing uvicorn, a stub `pgrep`
on PATH finds nothing (belt and braces: the live server is never touched),
ZENKAI_TEST_CMD stands in for the test suite, and the health URL is a file.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "auto-deploy-user.sh"

pytestmark = pytest.mark.skipif(
    not (shutil.which("bash") and shutil.which("git") and shutil.which("flock") and shutil.which("curl")),
    reason="needs bash, git, flock and curl",
)


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})


def _head(cwd):
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=cwd, capture_output=True,
                          text=True, check=True).stdout.strip()


@pytest.fixture
def env(tmp_path):
    origin, repo, upstream = tmp_path / "origin.git", tmp_path / "repo", tmp_path / "upstream"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    _git(tmp_path, "clone", "-q", str(origin), str(upstream))
    (upstream / "app").mkdir()
    (upstream / "app" / "main.py").write_text("v = 1\n")
    (upstream / ".gitignore").write_text("logs/\n")
    _git(upstream, "add", "-A"); _git(upstream, "commit", "-q", "-m", "init"); _git(upstream, "push", "-q", "origin", "main")
    _git(tmp_path, "clone", "-q", str(origin), str(repo))
    (repo / "logs").mkdir()
    (repo / "logs" / "deployed-commit").write_text(_head(repo) + "\n")

    stubs = tmp_path / "stubs"
    stubs.mkdir()
    (stubs / "pgrep").write_text("#!/bin/sh\nexit 1\n")
    (stubs / "pgrep").chmod(0o755)
    (tmp_path / "health").write_text("ok")

    def push(path="app/main.py", text="v = 2\n"):
        target = upstream / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        _git(upstream, "add", "-A"); _git(upstream, "commit", "-q", "-m", f"change {path}")
        _git(upstream, "push", "-q", "origin", "main")
        return _head(upstream)

    def run(tests_pass=True):
        e = {
            **os.environ,
            "PATH": f"{stubs}:{os.environ['PATH']}",
            "ZENKAI_REPO": str(repo),
            "ZENKAI_DEPLOY_LOCK": str(tmp_path / "deploy.lock"),
            "ZENKAI_HEALTH_URL": f"file://{tmp_path / 'health'}",
            "ZENKAI_RESTART_CMD": f"echo restart >> {tmp_path / 'restarts'}",
            "ZENKAI_TEST_CMD": f"echo run >> {tmp_path / 'test-runs'}; {'true' if tests_pass else 'false'}",
        }
        return subprocess.run(["bash", str(SCRIPT)], env=e, capture_output=True, text=True, timeout=60)

    def count(name):
        f = tmp_path / name
        return len(f.read_text().splitlines()) if f.exists() else 0

    return type("Env", (), {"repo": repo, "push": staticmethod(push), "run": staticmethod(run),
                            "count": staticmethod(count),
                            "marker": lambda self=None: (repo / "logs" / "deployed-commit").read_text().strip()})


def test_server_change_with_passing_tests_restarts(env):
    new = env.push()
    out = env.run()
    assert out.returncode == 0, out.stdout + out.stderr
    assert env.count("test-runs") == 1
    assert env.count("restarts") == 1
    assert env.marker() == new


def test_failing_tests_block_the_restart_and_are_not_rerun(env):
    old = env.marker()
    new = env.push()
    first = env.run(tests_pass=False)
    assert "tests failed" in first.stdout
    assert env.count("restarts") == 0
    assert env.marker() == old                 # the running commit is unchanged

    env.run(tests_pass=False)                  # next cron tick, same commit
    assert env.count("test-runs") == 1         # not re-tested every 2 minutes
    assert env.count("restarts") == 0
    assert _head(env.repo) == new


def test_a_fix_after_a_failure_deploys(env):
    env.push()
    env.run(tests_pass=False)
    fix = env.push(text="v = 3\n")
    env.run(tests_pass=True)
    assert env.count("restarts") == 1
    assert env.marker() == fix


def test_a_feature_branch_checkout_is_never_deployed(env):
    _git(env.repo, "checkout", "-q", "-b", "feature")
    (env.repo / "app" / "main.py").write_text("v = 'feature'\n")
    _git(env.repo, "commit", "-q", "-am", "feature work")
    feature_head = _head(env.repo)
    env.push()                                  # origin/main moves too

    out = env.run()
    assert "not main" in out.stdout
    assert env.count("restarts") == 0
    assert env.count("test-runs") == 0
    assert _head(env.repo) == feature_head      # origin/main was not merged into the branch


def test_doc_only_change_skips_tests_and_restart(env):
    new = env.push(path="README.md", text="docs\n")
    env.run()
    assert env.count("test-runs") == 0
    assert env.count("restarts") == 0
    assert env.marker() == new
