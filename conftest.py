"""Shared pytest fixtures.

``tls_cert`` generates a throwaway self-signed certificate for the TLS tests
(both the in-process test in ``test_tls.py`` and the real-emulator smoke test in
``test_smoke_ws3270.py``). It uses the ``openssl`` CLI so the test suite gains no
Python dependency; if ``openssl`` is absent the dependent tests skip.
"""
import shutil
import subprocess

import pytest

OPENSSL = shutil.which("openssl")


@pytest.fixture(scope="session")
def tls_cert(tmp_path_factory):
    """A ``(certfile, keyfile)`` pair for a self-signed ``CN=localhost`` cert.

    Skips the requesting test when ``openssl`` isn't installed."""
    if OPENSSL is None:
        pytest.skip("openssl not installed; cannot generate a TLS test cert")
    d = tmp_path_factory.mktemp("tls")
    cert = d / "cert.pem"
    key = d / "key.pem"
    subprocess.run(
        [OPENSSL, "req", "-x509", "-newkey", "rsa:2048",
         "-keyout", str(key), "-out", str(cert), "-days", "2", "-nodes",
         "-subj", "/CN=localhost"],
        check=True, capture_output=True,
    )
    return str(cert), str(key)
