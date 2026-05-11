import os
import tempfile
from typing import List, Dict, Any
from git import Repo, GitCommandError
from core.models import FileChange
from core.audit import Auditor

class RepoManager:

    def clone(self, repo_url: str, auditor: Auditor) -> str:
        tmpdir = tempfile.mkdtemp(prefix="ai-repo-")
        auditor.record("repo_clone_start", {"repo_url": repo_url, "dir": tmpdir})

        try:
            repo = Repo.clone_from(repo_url, tmpdir)
        except GitCommandError as e:
            auditor.record("repo_clone_error", {"error": str(e)})
            raise

        auditor.record("repo_clone_done", {
            "head": str(repo.head.commit),
            "dir": tmpdir
        })
        return tmpdir

    def create_branch(self, repo_dir: str, branch: str, auditor: Auditor) -> None:
        repo = Repo(repo_dir)
        auditor.record("branch_create_start", {"branch": branch})

        try:
            repo.git.checkout("-b", branch)
        except GitCommandError:
            # Si ya existe, checkout normal
            repo.git.checkout(branch)

        auditor.record("branch_create_done", {"branch": branch})

    def apply_changes(self, repo_dir: str, changes: List[FileChange], auditor: Auditor) -> List[str]:
        applied = []
        for ch in changes:

            # Validación: no aplicar cambios fuera del repo
            if ".." in ch.path or ch.path.startswith("/"):
                auditor.record("file_change_rejected", {
                    "path": ch.path,
                    "reason": "unsafe_path"
                })
                continue

            abs_path = os.path.join(repo_dir, ch.path)
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)

            if ch.mode in ["add", "update"]:
                with open(abs_path, "w", encoding="utf-8") as f:
                    f.write(ch.content)

                applied.append(ch.path)
                auditor.record("file_change_applied", {
                    "path": ch.path,
                    "mode": ch.mode
                })

        return applied

    def commit_and_push(self, repo_dir: str, branch: str, message: str, auditor: Auditor) -> Dict[str, Any]:
        repo = Repo(repo_dir)

        # Agregar sólo archivos modificados
        diff_files = [item.a_path for item in repo.index.diff(None)]
        repo.index.add(diff_files)

        auditor.record("commit_start", {"message": message})
        commit = repo.index.commit(message)
        auditor.record("commit_done", {"commit": str(commit)})

        origin = repo.remotes.origin

        auditor.record("push_start", {"remote": "origin", "branch": branch})

        try:
            origin.push(branch)
        except GitCommandError as e:
            auditor.record("push_error", {"error": str(e)})
            raise

        auditor.record("push_done", {"branch": branch})
        return {"commit": str(commit)}
