from __future__ import annotations

import argparse
import io
import os
from pathlib import Path
from typing import Any


class PushError(RuntimeError):
    pass


def _has_values(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_has_values(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_has_values(item) for item in value)
    return bool(value)


class DulwichBackend:
    def __init__(self) -> None:
        from dulwich import porcelain
        from dulwich.repo import Repo

        self.porcelain = porcelain
        self.repo_type = Repo

    def is_clean(self, repo: Path) -> bool:
        status = self.porcelain.status(str(repo))
        return not any(
            _has_values(value)
            for value in (status.staged, status.unstaged, status.untracked)
        )

    def local_sha(self, repo: Path, branch: str) -> str:
        instance = self.repo_type(str(repo))
        ref = f"refs/heads/{branch}".encode("utf-8")
        try:
            return instance.refs[ref].decode("ascii")
        except KeyError as exc:
            raise PushError("local branch does not exist") from exc

    def push(
        self,
        repo: Path,
        branch: str,
        remote_branch: str,
        remote: str,
        *,
        username: str,
        password: str,
    ) -> None:
        refspec = f"refs/heads/{branch}:refs/heads/{remote_branch}"
        self.porcelain.push(
            str(repo),
            remote,
            refspecs=[refspec],
            outstream=io.BytesIO(),
            errstream=io.BytesIO(),
            username=username,
            password=password,
        )

    def remote_sha(
        self,
        remote: str,
        branch: str,
        *,
        username: str,
        password: str,
    ) -> str:
        refs = self.porcelain.ls_remote(
            remote, username=username, password=password
        ).refs
        ref = f"refs/heads/{branch}".encode("utf-8")
        try:
            return refs[ref].decode("ascii")
        except KeyError as exc:
            raise PushError("remote branch was not created") from exc


def _remote_url(repo_path: Path, remote: str) -> str:
    if "://" in remote or remote.startswith("git@"):
        return remote
    from dulwich.repo import Repo

    config = Repo(str(repo_path)).get_config()
    try:
        return config.get((b"remote", remote.encode("utf-8")), b"url").decode(
            "utf-8"
        )
    except KeyError as exc:
        raise PushError("configured remote does not exist") from exc


def push_branch(
    repo: Path,
    branch: str,
    remote: str,
    *,
    username: str,
    password: str,
    remote_branch: str | None = None,
    backend: Any | None = None,
) -> str:
    repo = Path(repo).resolve(strict=True)
    branch = str(branch or "").strip()
    remote_branch = str(remote_branch or branch).strip()
    if not branch or not remote_branch:
        raise PushError("local and remote branches are required")
    if not username or not password:
        raise PushError("dulwich credentials are unavailable")
    backend = backend or DulwichBackend()
    if not backend.is_clean(repo):
        raise PushError("worktree is not clean")
    local_sha = backend.local_sha(repo, branch)
    backend.push(
        repo,
        branch,
        remote_branch,
        remote,
        username=username,
        password=password,
    )
    remote_sha = backend.remote_sha(
        remote,
        remote_branch,
        username=username,
        password=password,
    )
    if local_sha != remote_sha:
        raise PushError("remote SHA does not match local SHA")
    return local_sha


def _environment_credentials() -> tuple[str, str]:
    username = os.getenv("DULWICH_USERNAME", "").strip()
    password = os.getenv("DULWICH_PASSWORD", "")
    return username, password


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Push one clean branch with Python dulwich"
    )
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--remote-branch", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    username, password = _environment_credentials()
    remote = _remote_url(args.repo.resolve(strict=True), args.remote)
    sha = push_branch(
        args.repo,
        args.branch,
        remote,
        username=username,
        password=password,
        remote_branch=args.remote_branch or args.branch,
    )
    print(
        f"dulwich_push_ok local={args.branch} "
        f"remote={args.remote_branch or args.branch} sha={sha}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
