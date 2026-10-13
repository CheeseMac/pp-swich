"""pp switch policy engine.

Deny-by-default authorization and input validation. The engine never
resolves identity from caller-supplied JSON: the caller passes already
verified facts (docs/05-SECURITY.md section 2).
"""

from ppswitch_policy.decisions import PolicyDecision, evaluate_invocation
from ppswitch_policy.reserved import RESERVED_ARGUMENT_NAMES, validate_arguments

__all__ = [
    "RESERVED_ARGUMENT_NAMES",
    "PolicyDecision",
    "evaluate_invocation",
    "validate_arguments",
]
