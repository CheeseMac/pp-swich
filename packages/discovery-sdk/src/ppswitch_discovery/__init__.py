"""pp switch discovery SDK.

Pure, side-effect-free helpers that turn sanitized traffic evidence (HAR,
OpenAPI) into candidate API operations. The pipeline:

    raw request shape -> sanitize_observation -> normalize_path
                      -> fingerprint_operation -> infer_json_schema
                      -> candidate Operation (+ versioned contract)

Security invariants (docs/05-SECURITY.md, docs/11-TEST-PLAN.md):
- credential-shaped values are never returned, logged or stored;
- an input that still carries raw sensitive values is reported as
  ``unsanitized`` so the importer can reject the whole upload.
"""

from ppswitch_discovery.fingerprint import fingerprint_operation
from ppswitch_discovery.normalizer import NormalizedPath, normalize_path
from ppswitch_discovery.sanitizer import (
    CANARY_API_KEY,
    CANARY_COOKIE,
    CANARY_PASSWORD,
    SanitizationReport,
    sanitize_observation,
)
from ppswitch_discovery.schema_inference import infer_json_schema

__all__ = [
    "CANARY_API_KEY",
    "CANARY_COOKIE",
    "CANARY_PASSWORD",
    "NormalizedPath",
    "SanitizationReport",
    "fingerprint_operation",
    "infer_json_schema",
    "normalize_path",
    "sanitize_observation",
]
