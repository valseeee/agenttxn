# Security Policy

## Reporting a vulnerability

Do not publish exploitable vulnerabilities in public issues. Use GitHub's private
vulnerability reporting feature if enabled, or contact the maintainer privately.
Include affected version, Python version, a minimal reproduction, expected and actual
behavior, and whether an external side effect or secret exposure is possible.

## Security boundaries

AgentTxn is a coordinator, not a sandbox. It cannot protect the database from an
attacker with full filesystem access, and it cannot guarantee that a provider is
truthful or that compensation restores the original history. Use least-privilege
credentials and test adapters against sandbox resources.
