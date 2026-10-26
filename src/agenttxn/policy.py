from dataclasses import dataclass, field
import hashlib
from .errors import PolicyDeniedError
from .models import EffectClass, canonical_json

@dataclass(frozen=True)
class Policy:
    version: str = "default-safe/v1"
    max_steps: int = 20
    allow_irreversible: bool = False
    require_approval_for_reversible: bool = True
    allowed_kinds: frozenset[str] | None = None
    denied_kinds: frozenset[str] = field(default_factory=frozenset)
    allowed_github_repositories: frozenset[str] | None = None

    def __post_init__(self):
        if not isinstance(self.version, str) or not self.version.strip():
            raise ValueError("policy version must be a non-empty string")
        if not isinstance(self.max_steps, int) or isinstance(self.max_steps, bool) or self.max_steps < 1:
            raise ValueError("max_steps must be a positive integer")
        if self.allowed_kinds is not None:
            allowed=frozenset(self.allowed_kinds)
            if not all(isinstance(x,str) and x.strip() for x in allowed):
                raise ValueError("allowed_kinds must contain non-empty strings")
            object.__setattr__(self, "allowed_kinds", allowed)
        denied=frozenset(self.denied_kinds)
        if not all(isinstance(x,str) and x.strip() for x in denied):
            raise ValueError("denied_kinds must contain non-empty strings")
        object.__setattr__(self, "denied_kinds", denied)
        if self.allowed_github_repositories is not None:
            repos=frozenset(self.allowed_github_repositories)
            if not all(isinstance(x,str) and x.strip() for x in repos):
                raise ValueError("allowed_github_repositories must contain non-empty strings")
            object.__setattr__(self, "allowed_github_repositories", repos)

    @classmethod
    def default_safe(cls):
        return cls()

    @property
    def identity(self) -> str:
        """Stable identity for the full policy configuration, not just its label."""
        payload = {
            "version": self.version,
            "max_steps": self.max_steps,
            "allow_irreversible": self.allow_irreversible,
            "require_approval_for_reversible": self.require_approval_for_reversible,
            "allowed_kinds": sorted(self.allowed_kinds) if self.allowed_kinds is not None else None,
            "denied_kinds": sorted(self.denied_kinds),
            "allowed_github_repositories": sorted(self.allowed_github_repositories) if self.allowed_github_repositories is not None else None,
        }
        digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
        return f"{self.version}:{digest}"

    @property
    def legacy_identity(self) -> str:
        """Read-only compatibility with v0.2 policy fingerprint truncation."""
        return self.identity[:-48]

    def validate(self, actions):
        actions = list(actions)
        if not actions:
            raise PolicyDeniedError("transaction must contain at least one action")
        if len(actions) > self.max_steps:
            raise PolicyDeniedError(f"step limit exceeded ({len(actions)} > {self.max_steps})")
        for a in actions:
            if a.kind in self.denied_kinds:
                raise PolicyDeniedError(f"action kind denied: {a.kind}")
            if self.allowed_kinds is not None and a.kind not in self.allowed_kinds:
                raise PolicyDeniedError(f"action kind not allowlisted: {a.kind}")
            if a.effect == EffectClass.IRREVERSIBLE and not self.allow_irreversible:
                raise PolicyDeniedError(f"irreversible action denied: {a.kind}")
            if a.kind.startswith("github.") and self.allowed_github_repositories is not None:
                if a.payload.get("repository") not in self.allowed_github_repositories:
                    raise PolicyDeniedError("GitHub repository is not allowlisted")

    def approval_required(self, actions):
        return any(a.effect == EffectClass.IRREVERSIBLE or
                   (a.effect == EffectClass.REVERSIBLE and self.require_approval_for_reversible)
                   for a in actions)
