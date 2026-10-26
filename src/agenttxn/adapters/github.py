from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from http.client import HTTPException
from urllib.parse import quote
from urllib.request import Request, urlopen
import hashlib, json, socket, re
from ..errors import ActionExecutionError, OutcomeUnknownError, PreflightError
from ..models import ActionSpec, ReconcileOutcome
from .base import AdapterContext

API_BASE = "https://api.github.com"
API_VERSION = "2022-11-28"

class GitHubIssuesAdapter:
    """Small stdlib-only GitHub Issues adapter. Tokens are held in memory only."""
    kinds = frozenset({"github.issue.create", "github.issue.add_labels"})

    def __init__(self, token: str, timeout: float = 10.0, *, allow_label_compensation: bool = False):
        if not token: raise ValueError("GitHub token is required")
        self._token, self.timeout = token, timeout
        self.allow_label_compensation = allow_label_compensation

    @staticmethod
    def _repo(payload):
        repo = payload.get("repository")
        if not isinstance(repo, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
            raise PreflightError("repository must be a valid owner/repo identifier")
        owner, name = repo.split("/", 1)
        if owner in {".", ".."} or name in {".", ".."}:
            raise PreflightError("invalid repository name")
        return repo

    def validate(self, action: ActionSpec):
        if action.kind not in self.kinds: raise PreflightError(f"unsupported GitHub action: {action.kind}")
        self._repo(action.payload)
        if action.effect.value == "read_only":
            raise PreflightError("GitHub issue actions change remote state and cannot be marked read_only")
        if action.kind == "github.issue.create":
            if not isinstance(action.payload.get("title"), str) or not action.payload["title"].strip():
                raise PreflightError("issue title is required")
            if not isinstance(action.payload.get("body", ""), str): raise PreflightError("issue body must be text")
            labels = action.payload.get("labels", [])
            if not isinstance(labels, list) or not all(isinstance(x, str) and x.strip() for x in labels):
                raise PreflightError("labels must be a list of non-empty strings")
        else:
            number=action.payload.get("issue_number")
            if not isinstance(number, int) or isinstance(number, bool) or number <= 0:
                raise PreflightError("positive integer issue_number required")
            labels = action.payload.get("labels")
            if not isinstance(labels, list) or not labels or not all(isinstance(x, str) and x.strip() for x in labels):
                raise PreflightError("non-empty labels list required")

    def _request(self, method, path, body=None, write=False):
        data = json.dumps(body, allow_nan=False).encode() if body is not None else None
        req = Request(API_BASE + path, data=data, method=method, headers={
            "Accept":"application/vnd.github+json", "Authorization":f"Bearer {self._token}",
            "X-GitHub-Api-Version":API_VERSION, "User-Agent":"agenttxn/0.4.0",
            "Content-Type":"application/json"})
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                if not raw:
                    return {}
                try:
                    return json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    if write:
                        raise OutcomeUnknownError("GitHub write response was unreadable; outcome may be uncertain") from None
                    raise ActionExecutionError("GitHub returned an unreadable response") from None
        except HTTPError as e:
            if write and (e.code >= 500 or e.code in {408,429}):
                raise OutcomeUnknownError(f"GitHub HTTP {e.code}; write outcome may be uncertain") from None
            raise ActionExecutionError(f"GitHub API returned HTTP {e.code}") from None
        except (URLError, TimeoutError, socket.timeout, ConnectionError, OSError, HTTPException) as e:
            if write: raise OutcomeUnknownError(f"GitHub write outcome unknown ({type(e).__name__})") from None
            raise ActionExecutionError(f"GitHub read failed ({type(e).__name__})") from None

    @staticmethod
    def _issue_path(repo, number=None):
        return f"/repos/{repo}/issues" if number is None else f"/repos/{repo}/issues/{int(number)}"

    @staticmethod
    def _marker(action):
        h = hashlib.sha256(action.idempotency_key.encode()).hexdigest()[:24]
        return f"<!-- agenttxn:idempotency={h} -->"

    def preflight(self, context: AdapterContext, action: ActionSpec):
        self.validate(action); repo = self._repo(action.payload)
        if action.kind == "github.issue.create":
            # Read access is checked during prepare, before any write is attempted.
            repository=self._request("GET", f"/repos/{repo}")
            return {"marker":self._marker(action), "repository_id":repository.get("id"),
                    "checked_at":datetime.now(timezone.utc).isoformat()}
        number = action.payload["issue_number"]
        issue = self._request("GET", self._issue_path(repo, number))
        labels = self._request("GET", f"{self._issue_path(repo, number)}/labels")
        return {"issue_state":issue.get("state"), "previous_labels":sorted(x["name"] for x in labels)}

    def execute(self, context: AdapterContext, action: ActionSpec, preflight: dict[str, Any]):
        repo = self._repo(action.payload)
        if action.kind == "github.issue.create":
            repository=self._request("GET",f"/repos/{repo}")
            if repository.get("id") != preflight.get("repository_id"):
                raise ActionExecutionError("repository identity changed after preflight")
            marker = preflight["marker"]; body = action.payload.get("body", "")
            body = f"{body.rstrip()}\n\n{marker}" if body.strip() else marker
            payload = {"title":action.payload["title"], "body":body}
            if action.payload.get("labels"): payload["labels"] = action.payload["labels"]
            issue = self._request("POST", self._issue_path(repo), payload, write=True)
            if not isinstance(issue, dict) or not isinstance(issue.get("number"), int):
                raise OutcomeUnknownError("GitHub accepted or may have accepted the issue write but returned no usable issue number")
            return {"issue_number":issue["number"], "html_url":issue.get("html_url"),
                    "marker":marker, "title":issue.get("title"),
                    "semantic_compensation":"close_issue", "restored_original_state":False}
        number = action.payload["issue_number"]
        previous = set((preflight or {}).get("previous_labels", []))
        current = {x["name"] for x in self._request("GET", f"{self._issue_path(repo, number)}/labels")}
        if current != previous:
            raise ActionExecutionError("issue labels changed since preflight; refusing stale write")
        requested = list(dict.fromkeys(action.payload["labels"]))
        response = self._request("POST", f"{self._issue_path(repo, number)}/labels",
                                  {"labels":requested}, write=True)
        if not isinstance(response, list) or any(not isinstance(x, dict) or not isinstance(x.get("name"), str) for x in response):
            raise OutcomeUnknownError("GitHub label write may have succeeded but returned an unusable label list")
        return {"issue_number":number, "previous_labels":sorted(previous),
                "added_labels":sorted(set(requested)-previous),
                "current_labels":sorted(x["name"] for x in response)}

    def compensate(self, context, action, result, preflight):
        repo = self._repo(action.payload)
        if action.kind == "github.issue.create":
            number = result.get("issue_number")
            if not number: raise ActionExecutionError("cannot close issue without issue_number")
            self._request("PATCH", self._issue_path(repo, number),
                          {"state":"closed", "state_reason":"not_planned"}, write=True)
            return {"action":"close_issue", "issue_number":number, "restored_original_state":False}
        if not self.allow_label_compensation:
            raise ActionExecutionError("label removal is unsafe by default; explicit opt-in required")
        number = action.payload["issue_number"]; removed=[]
        for label in result.get("added_labels", []):
            self._request("DELETE", f"{self._issue_path(repo, number)}/labels/{quote(label,safe='')}", write=True)
            removed.append(label)
        return {"action":"remove_owned_labels", "removed_labels":removed}

    def reconcile(self, context, action, phase, result, preflight):
        repo = self._repo(action.payload)
        if phase == "forward":
            if action.kind == "github.issue.create":
                marker = (preflight or {}).get("marker") or self._marker(action)
                issues = self._request("GET", f"/repos/{repo}/issues?state=all&sort=created&direction=desc&per_page=100")
                matches = [x for x in issues if marker in (x.get("body") or "")]
                if len(matches) == 1:
                    x=matches[0]
                    return ReconcileOutcome.SUCCEEDED, {"issue_number":x["number"],"html_url":x.get("html_url"),
                        "marker":marker,"title":x.get("title"),"semantic_compensation":"close_issue","restored_original_state":False}
                return ReconcileOutcome.UNKNOWN, None
            number=action.payload["issue_number"]
            current={x["name"] for x in self._request("GET",f"{self._issue_path(repo,number)}/labels")}
            previous=set((preflight or {}).get("previous_labels",[])); requested=set(action.payload["labels"])
            if requested.issubset(current):
                return ReconcileOutcome.SUCCEEDED, {"issue_number":number,"previous_labels":sorted(previous),
                    "added_labels":sorted(requested-previous),"current_labels":sorted(current)}
            # A collaborator may have removed labels after our write; absence is not proof.
            return ReconcileOutcome.UNKNOWN, None
        result = result or {}
        if action.kind == "github.issue.create":
            number=result.get("issue_number")
            if not number: return ReconcileOutcome.UNKNOWN, None
            issue=self._request("GET",self._issue_path(repo,number))
            if issue.get("state")=="closed": return ReconcileOutcome.SUCCEEDED, {"action":"close_issue","issue_number":number}
            # The issue may have been closed and reopened; do not repeat compensation.
            return ReconcileOutcome.UNKNOWN, None
        number=action.payload["issue_number"]
        names={x["name"] for x in self._request("GET",f"{self._issue_path(repo,number)}/labels")}
        added=set(result.get("added_labels",[]))
        if names.isdisjoint(added): return ReconcileOutcome.SUCCEEDED, {"removed_labels":sorted(added)}
        # Labels may have been re-added by a collaborator after removal.
        return ReconcileOutcome.UNKNOWN, None

    def can_compensate(self, action):
        return action.effect.value == "reversible" and (action.kind == "github.issue.create" or (action.kind == "github.issue.add_labels" and self.allow_label_compensation))
