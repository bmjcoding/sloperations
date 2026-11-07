"""A bare-repository cache of each upstream, outside the working repository.

The cache holds the pinned commit and the tracked branch's history since a
little before the pin (shallow), so even a very large upstream stays cheap.
Files are read as raw blobs, never through a checkout, so upstream's
.gitattributes (export-ignore, eol, filters) can't change the bytes.
"""
from __future__ import annotations

import contextlib
import datetime as _dt
import hashlib
import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

from .config import SHA_RE, SkillSyncError, Upstream

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None

SHALLOW_MARGIN_DAYS = 3


def cache_root(explicit: Optional[str], repo_root: Path) -> Path:
    if explicit:
        base = Path(explicit)
    elif os.environ.get("SKILL_SYNC_CACHE"):
        base = Path(os.environ["SKILL_SYNC_CACHE"])
    else:
        xdg = os.environ.get("XDG_CACHE_HOME")
        base = (Path(xdg) if xdg else Path.home() / ".cache") / "skill-sync"
    base = base.expanduser().resolve()
    root = repo_root.resolve()
    if base == root or root in base.parents:
        raise SkillSyncError(f"cache {base} must be outside the repository {root}", 2)
    base.mkdir(parents=True, exist_ok=True)
    return base


def git_env() -> Dict[str, str]:
    env = dict(os.environ)
    env.update(GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="", SSH_ASKPASS="", LC_ALL="C")
    # Never read or write a repository other than the cache.
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
                "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE", "GIT_COMMON_DIR"):
        env.pop(key, None)
    return env


class Repo:
    def __init__(self, cache: Path, upstream: Upstream):
        self.upstream = upstream
        digest = hashlib.sha256(upstream.repo.encode()).hexdigest()[:16]
        label = re.sub(r"[^A-Za-z0-9._-]+", "-", upstream.repo.rstrip("/").split("/")[-1])[:40]
        self.path = cache / f"{label}-{digest}.git"

    # -------------------------------------------------------------- plumbing --
    def git(self, *args: str, check: bool = True, input: Optional[bytes] = None,
            text: bool = True, timeout: int = 1800) -> subprocess.CompletedProcess:
        cmd = ["git", "--git-dir", str(self.path), *args]
        try:
            result = subprocess.run(cmd, input=input, capture_output=True, env=git_env(), timeout=timeout)
        except FileNotFoundError:
            raise SkillSyncError("git is not installed") from None
        except subprocess.TimeoutExpired:
            raise SkillSyncError(f"git {' '.join(args[:2])} timed out after {timeout}s") from None
        if text:
            result.stdout = result.stdout.decode("utf-8", "replace")
            result.stderr = result.stderr.decode("utf-8", "replace")
        if check and result.returncode != 0:
            err = result.stderr if text else result.stderr.decode("utf-8", "replace")
            raise SkillSyncError(f"git {' '.join(args)} failed: {err.strip()[-2000:]}")
        return result

    @contextlib.contextmanager
    def locked(self) -> Iterator["Repo"]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = open(str(self.path) + ".lock", "w")
        try:
            if fcntl:
                fcntl.flock(lock, fcntl.LOCK_EX)
            yield self
        finally:
            lock.close()

    def _init(self) -> None:
        if not (self.path / "HEAD").exists():
            self.path.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "init", "-q", "--bare", str(self.path)], check=True, env=git_env(), capture_output=True)
        current = self.git("config", "--get", "remote.origin.url", check=False).stdout.strip()
        if current != self.upstream.repo:
            if current:
                self.git("remote", "set-url", "origin", self.upstream.repo)
            else:
                self.git("remote", "add", "origin", self.upstream.repo)
            self.git("config", "remote.origin.fetch", f"+refs/heads/{self.upstream.branch}:refs/remotes/origin/{self.upstream.branch}")

    def has_commit(self, sha: str) -> bool:
        return self.git("cat-file", "-e", f"{sha}^{{commit}}", check=False).returncode == 0

    def _fetch(self, *args: str) -> None:
        extra = ["--filter=" + self.upstream.filter] if self.upstream.filter else []
        self.git("fetch", "-q", "--no-tags", *extra, "origin", *args)

    def fetch_commit(self, sha: str) -> None:
        if not self.has_commit(sha):
            self._fetch("--depth=1", sha)
        if not self.has_commit(sha):
            raise SkillSyncError(f"upstream has no commit {sha}")

    @property
    def tracking_ref(self) -> str:
        return f"refs/remotes/origin/{self.upstream.branch}"

    def is_shallow(self) -> bool:
        return self.git("rev-parse", "--is-shallow-repository").stdout.strip() == "true"

    def ensure_commit(self, sha: str) -> None:
        """Just the one commit, for building at a known pin."""
        self._init()
        self.fetch_commit(sha)

    def boundaries(self) -> List[str]:
        """Shallow boundary commits: git sees them as roots that add every file, so log queries exclude them."""
        shallow = self.path / "shallow"
        if not shallow.exists():
            return []
        return [line.strip() for line in shallow.read_text().splitlines() if SHA_RE.match(line.strip())]

    def sync(self, pin: str) -> str:
        """Fetches the pin and the branch since shortly before it; returns the branch tip."""
        self._init()
        self.fetch_commit(pin)
        refspec = f"+refs/heads/{self.upstream.branch}:{self.tracking_ref}"
        if self.is_shallow():
            since = self.commit_time(pin) - _dt.timedelta(days=SHALLOW_MARGIN_DAYS)
            self._fetch(f"--shallow-since={since.strftime('%Y-%m-%d')}", refspec)
        else:
            self._fetch(refspec)
        tip = self.rev(self.tracking_ref)
        if not self.is_ancestor(pin, tip) and self.is_shallow():
            # Committer dates can run backwards across a rebase; get all of it once.
            self._fetch("--unshallow", refspec)
            tip = self.rev(self.tracking_ref)
        return tip

    def fetch_ref(self, ref: str) -> str:
        """A commit SHA for `ref`: a SHA (fetched if needed) or an upstream branch/tag name."""
        if SHA_RE.match(ref):
            self.fetch_commit(ref)
            return ref
        if re.match(r"^[0-9a-f]{7,39}$", ref):
            found = self.git("rev-parse", "--verify", "-q", f"{ref}^{{commit}}", check=False).stdout.strip()
            if found:
                return found
            raise SkillSyncError(f"{ref} is not in the fetched history; pass the full 40-character SHA", 2)
        if ref.startswith("-") or not re.match(r"^[A-Za-z0-9._/-]+$", ref):
            raise SkillSyncError(f"{ref!r} is not a SHA, branch or tag name", 2)
        self._fetch("--depth=1", ref)
        return self.rev("FETCH_HEAD")

    # --------------------------------------------------------------- queries --
    def rev(self, ref: str) -> str:
        return self.git("rev-parse", "--verify", f"{ref}^{{commit}}").stdout.strip()

    def verify_sha(self, sha: str) -> None:
        resolved = self.rev(sha)
        if resolved != sha:
            raise SkillSyncError(f"pinned SHA {sha} resolved to {resolved}")

    def commit_time(self, sha: str) -> _dt.datetime:
        stamp = self.git("show", "-s", "--format=%ct", sha).stdout.strip()
        return _dt.datetime.fromtimestamp(int(stamp), tz=_dt.timezone.utc)

    def describe(self, sha: str) -> Dict[str, str]:
        out = self.git("show", "-s", "--format=%H%x1f%cs%x1f%s", sha).stdout.strip()
        full, date, subject = (out.split("\x1f") + ["", ""])[:3]
        return {"sha": full, "date": date, "subject": subject}

    def is_ancestor(self, older: str, newer: str) -> bool:
        return self.git("merge-base", "--is-ancestor", older, newer, check=False).returncode == 0

    def newest_touching(self, tip: str, paths: List[str], exclude: Optional[str] = None) -> Optional[str]:
        """The newest commit on the branch's first-parent line whose change touches `paths`.

        --first-parent keeps to commits made on (or merged into) the branch itself: a merged
        topic branch shows up as its mainline merge, never as a commit from the topic,
        which isn't a descendant of a pin made on the mainline meanwhile.
        """
        excluded = ["^" + b for b in self.boundaries()] + (["^" + exclude] if exclude else [])
        out = self.git("log", "-1", "--first-parent", "--format=%H", tip, *excluded, "--", *paths).stdout.strip()
        return out or None

    def commits_between(self, old: str, new: str, paths: List[str], limit: int = 200) -> List[Dict[str, object]]:
        out = self.git("log", f"--max-count={limit}", "--format=%x1e%H%x1f%cs%x1f%s", "--name-only",
                       f"{old}..{new}", *("^" + b for b in self.boundaries() if b != old), "--", *paths).stdout
        commits = []
        for chunk in out.split("\x1e")[1:]:
            head, _, names = chunk.partition("\n")
            sha, date, subject = (head.split("\x1f") + ["", ""])[:3]
            commits.append({"sha": sha, "date": date, "subject": subject,
                            "files": [n for n in names.splitlines() if n.strip()]})
        return commits

    def diff(self, old: str, new: str, paths: List[str]) -> str:
        return self.git("diff", "--no-color", "--no-ext-diff", "--no-textconv", old, new, "--", *paths).stdout

    def ls_tree(self, sha: str, path: str) -> List[Tuple[str, str, str, str]]:
        """(mode, type, oid, path) for every entry at or under `path`."""
        out = self.git("ls-tree", "-r", "-z", "--full-tree", sha, "--", path).stdout
        entries = []
        for record in out.split("\x00"):
            if not record:
                continue
            meta, _, name = record.partition("\t")
            mode, otype, oid = meta.split(" ")
            if name == path or name.startswith(path + "/"):
                entries.append((mode, otype, oid, name))
        return entries

    def read_blobs(self, oids: List[str]) -> Dict[str, bytes]:
        if not oids:
            return {}
        unique = list(dict.fromkeys(oids))
        raw = self.git("cat-file", "--batch", input=("\n".join(unique) + "\n").encode(), text=False).stdout
        blobs, pos = {}, 0
        for oid in unique:
            end = raw.index(b"\n", pos)
            header = raw[pos:end].decode()
            parts = header.split(" ")
            if len(parts) != 3 or parts[1] != "blob":
                raise SkillSyncError(f"could not read blob {oid}: {header}")
            size = int(parts[2])
            blobs[oid] = raw[end + 1:end + 1 + size]
            pos = end + 1 + size + 1
        return blobs


def github_verified(owner: str, repo: str, sha: str) -> Tuple[bool, str]:
    """GitHub's own signature verification for a commit: (verified, reason)."""
    # SKILL_SYNC_GITHUB_API points at another API root (tests serve one locally).
    api = os.environ.get("SKILL_SYNC_GITHUB_API", "https://api.github.com").rstrip("/")
    if not re.match(r"^https?://", api):
        raise SkillSyncError(f"SKILL_SYNC_GITHUB_API must be an http(s) URL, not {api!r}", 2)
    url = f"{api}/repos/{owner}/{repo}/commits/{sha}"
    request = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json", "User-Agent": "skill-sync",
        "X-GitHub-Api-Version": "2022-11-28"})
    token = os.environ.get("SKILL_SYNC_GITHUB_TOKEN")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.load(response)
    except urllib.error.HTTPError as exc:
        raise SkillSyncError(f"GitHub signature lookup for {sha[:12]} failed: HTTP {exc.code}") from None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise SkillSyncError(f"GitHub signature lookup for {sha[:12]} failed: {exc}") from None
    verification = (data.get("commit") or {}).get("verification") or {}
    return bool(verification.get("verified")), str(verification.get("reason", "unknown"))
