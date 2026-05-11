import os
import requests
import urllib.parse
from typing import Dict, Any
from github import Github

class PullRequestManager:
    def create_pr(self, repo_url: str, branch: str, base: str, title: str, body: str, token: str = None) -> Dict[str, Any]:
        if "github.com" in repo_url:
            return self._create_github_pr(repo_url, branch, base, title, body, token)
        elif "gitlab.com" in repo_url or "gitlab" in repo_url:
            return self._create_gitlab_mr(repo_url, branch, base, title, body, token)
        else:
            return {"error": "Unsupported git provider"}

    def _create_github_pr(self, repo_url: str, branch: str, base: str, title: str, body: str, token: str = None) -> Dict[str, Any]:
        final_token = token if token else os.getenv("GITHUB_TOKEN")
        if not final_token:
            return {"error": "GITHUB_TOKEN not set"}
        try:
            gh = Github(final_token)
            # Extraer owner/name de la URL
            # https://github.com/owner/name.git -> owner/name
            path = repo_url.split("github.com/")[-1].replace(".git", "").rstrip("/")
            repo = gh.get_repo(path)
            pr = repo.create_pull(title=title, body=body, head=branch, base=base)
            return {"number": pr.number, "url": pr.html_url, "provider": "github"}
        except Exception as e:
            return {"error": f"GitHub PR error: {str(e)}"}

    def _create_gitlab_mr(self, repo_url: str, branch: str, base: str, title: str, body: str, token: str = None) -> Dict[str, Any]:
        final_token = token if token else os.getenv("GITLAB_TOKEN")
        if not final_token:
            return {"error": "GITLAB_TOKEN not set"}
        
        try:
            # Extraer el project path de la URL
            # https://gitlab.com/group/subgroup/name.git -> group/subgroup/name
            if "gitlab.com" in repo_url:
                base_url = "https://gitlab.com"
                project_path = repo_url.split("gitlab.com/")[-1].replace(".git", "").rstrip("/")
            else:
                # Caso para GitLab self-hosted (heurística simple)
                parts = repo_url.split("/")
                base_url = f"{parts[0]}//{parts[2]}"
                project_path = "/".join(parts[3:]).replace(".git", "").rstrip("/")

            project_id = urllib.parse.quote_oneplus(project_path)
            api_url = f"{base_url}/api/v4/projects/{project_id}/merge_requests"
            
            headers = {"Private-Token": final_token}
            data = {
                "source_branch": branch,
                "target_branch": base,
                "title": title,
                "description": body,
                "remove_source_branch": True
            }
            
            res = requests.post(api_url, headers=headers, json=data)
            if res.status_code in (200, 201):
                mr = res.json()
                return {"number": mr["iid"], "url": mr["web_url"], "provider": "gitlab"}
            else:
                return {"error": f"GitLab MR error: {res.text}"}
        except Exception as e:
            return {"error": f"GitLab process error: {str(e)}"}
