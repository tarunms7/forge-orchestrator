"""Secret scanner service for detecting credentials and sensitive data."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

# Maximum characters to keep in a match before truncating
_TRUNCATE_LENGTH = 20


@dataclass
class SecretFinding:
    """A single secret finding from a scan."""

    pattern_name: str
    match: str


# Each pattern: (name, compiled regex)
_SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("aws_secret_key", re.compile(r"(?i)aws_secret_access_key\s*=\s*\S{20,}")),
    ("github_token", re.compile(r"ghp_[A-Za-z0-9]{36,}")),
    ("generic_api_key", re.compile(r'(?i)api[_-]?key\s*[=:]\s*["\']?\S{20,}')),
    ("private_key", re.compile(r"-----BEGIN\s+\w*\s*PRIVATE KEY-----")),
    ("password_in_url", re.compile(r"://[^/\s]+:[^/\s]+@[^/\s]+")),
]

# File patterns that indicate sensitive files
_SENSITIVE_FILE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(^|/)\.env(\..*)?$"),          # .env, .env.local, .env.production
    re.compile(r"\.pem$"),                       # TLS/SSL certificates
    re.compile(r"\.key$"),                       # Private key files
    re.compile(r"\.keystore$"),                  # Java/Android keystores
    re.compile(r"(^|/)credentials\.json$"),      # GCP/generic credentials
    re.compile(r"(^|/)id_rsa$"),                 # SSH private keys
    re.compile(r"(^|/)id_ed25519$"),             # SSH private keys (ed25519)
    re.compile(r"(^|/)\.htpasswd$"),             # Apache password files
    re.compile(r"(^|/)\.netrc$"),                # Network credentials
    re.compile(r"\.p12$"),                       # PKCS#12 certificates
    re.compile(r"\.pfx$"),                       # PFX certificates
    re.compile(r"(^|/)token\.json$"),            # OAuth token files
]


def _truncate_match(text: str) -> str:
    """Truncate a matched string to ``_TRUNCATE_LENGTH`` chars + '...'."""
    if len(text) <= _TRUNCATE_LENGTH:
        return text
    return text[:_TRUNCATE_LENGTH] + "..."


class SecretScanner:
    """Scans text and filenames for potential secrets and sensitive data."""

    def scan_text(self, text: str) -> list[SecretFinding]:
        """Scan *text* for patterns that look like secrets.

        Returns a list of :class:`SecretFinding` instances, one per match.
        Matched strings are truncated to 20 characters.
        """
        findings: list[SecretFinding] = []
        for name, pattern in _SECRET_PATTERNS:
            for m in pattern.finditer(text):
                findings.append(
                    SecretFinding(
                        pattern_name=name,
                        match=_truncate_match(m.group()),
                    )
                )
        return findings

    def is_sensitive_file(self, filename: str) -> bool:
        """Return True if *filename* matches a known sensitive-file pattern.

        Accepts both bare filenames and paths (e.g. ``config/.env``).
        """
        # Normalise path separators
        normalised = filename.replace("\\", "/")
        for pattern in _SENSITIVE_FILE_PATTERNS:
            if pattern.search(normalised):
                return True
        return False
