"""Tests for SecretScanner service."""


from forge.api.services.secret_scanner import SecretFinding, SecretScanner


class TestSecretFinding:
    """Tests for the SecretFinding dataclass."""

    def test_fields(self):
        finding = SecretFinding(pattern_name="aws_key", match="AKIA1234567890AB...")
        assert finding.pattern_name == "aws_key"
        assert finding.match == "AKIA1234567890AB..."


class TestSecretScannerScanText:
    """Tests for SecretScanner.scan_text()."""

    def setup_method(self):
        self.scanner = SecretScanner()

    def test_detect_aws_access_key(self):
        text = "aws_access_key_id = AKIAIOSFODNN7EXAMPLE"
        findings = self.scanner.scan_text(text)
        assert len(findings) >= 1
        names = [f.pattern_name for f in findings]
        assert "aws_access_key" in names

    def test_detect_aws_secret_key(self):
        text = "aws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
        findings = self.scanner.scan_text(text)
        assert len(findings) >= 1
        names = [f.pattern_name for f in findings]
        assert "aws_secret_key" in names

    def test_detect_github_token(self):
        text = "GITHUB_TOKEN=ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij"
        findings = self.scanner.scan_text(text)
        assert len(findings) >= 1
        names = [f.pattern_name for f in findings]
        assert "github_token" in names

    def test_detect_generic_api_key(self):
        text = 'api_key = "sk-1234567890abcdef1234567890abcdef"'
        findings = self.scanner.scan_text(text)
        assert len(findings) >= 1
        names = [f.pattern_name for f in findings]
        assert "generic_api_key" in names

    def test_detect_private_key(self):
        text = "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAK..."
        findings = self.scanner.scan_text(text)
        assert len(findings) >= 1
        names = [f.pattern_name for f in findings]
        assert "private_key" in names

    def test_detect_password_in_url(self):
        text = "postgres://user:password123@localhost:5432/db"
        findings = self.scanner.scan_text(text)
        assert len(findings) >= 1
        names = [f.pattern_name for f in findings]
        assert "password_in_url" in names

    def test_no_false_positive_on_normal_code(self):
        text = """
def calculate_total(items):
    total = sum(item.price for item in items)
    return total

class UserService:
    def get_user(self, user_id: str):
        return self.db.query(user_id)
"""
        findings = self.scanner.scan_text(text)
        assert len(findings) == 0

    def test_match_is_truncated(self):
        """Matches longer than 20 chars should be truncated with '...'."""
        text = "GITHUB_TOKEN=ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij"
        findings = self.scanner.scan_text(text)
        assert len(findings) >= 1
        for finding in findings:
            # truncated to 20 chars + "..."
            assert len(finding.match) <= 23

    def test_multiple_findings_in_same_text(self):
        text = """
AKIAIOSFODNN7EXAMPLE
ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij
-----BEGIN RSA PRIVATE KEY-----
"""
        findings = self.scanner.scan_text(text)
        names = [f.pattern_name for f in findings]
        assert "aws_access_key" in names
        assert "github_token" in names
        assert "private_key" in names


class TestSecretScannerIsSensitiveFile:
    """Tests for SecretScanner.is_sensitive_file()."""

    def setup_method(self):
        self.scanner = SecretScanner()

    def test_env_file(self):
        assert self.scanner.is_sensitive_file(".env") is True

    def test_env_local(self):
        assert self.scanner.is_sensitive_file(".env.local") is True

    def test_pem_file(self):
        assert self.scanner.is_sensitive_file("server.pem") is True

    def test_key_file(self):
        assert self.scanner.is_sensitive_file("private.key") is True

    def test_credentials_json(self):
        assert self.scanner.is_sensitive_file("credentials.json") is True

    def test_id_rsa(self):
        assert self.scanner.is_sensitive_file("id_rsa") is True

    def test_normal_python_file(self):
        assert self.scanner.is_sensitive_file("main.py") is False

    def test_normal_json_file(self):
        assert self.scanner.is_sensitive_file("package.json") is False

    def test_htpasswd(self):
        assert self.scanner.is_sensitive_file(".htpasswd") is True

    def test_path_with_directory(self):
        assert self.scanner.is_sensitive_file("config/.env") is True

    def test_keystore_file(self):
        assert self.scanner.is_sensitive_file("app.keystore") is True
