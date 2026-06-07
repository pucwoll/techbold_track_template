from __future__ import annotations

import unittest

from app.evidence_detector import detect_inspected_sources


class EvidenceDetectorTest(unittest.TestCase):
    def test_detects_all_spec_source_types(self) -> None:
        examples = [
            ("tail -n 20 /var/log/nginx/error.log", "nginx bind() failed", "file"),
            ("journalctl -u nginx --no-pager -n 80", "bind() failed", "journal"),
            ("systemctl status nginx --no-pager", "nginx.service failed", "service_status"),
            ("cat /etc/nginx/nginx.conf", "listen 80;", "config"),
            ("stat /srv/app/uploads", "Access: 0755", "metadata"),
            ("curl -I http://localhost:8080/health", "HTTP/1.1 200 OK", "endpoint"),
            ("df -h /", "/dev/root 100% /", "other"),
        ]

        detected_types = [
            detect_inspected_sources(
                command=command,
                sanitized_stdout=stdout,
                sanitized_stderr="",
                purpose="Inspect source.",
                phase="diagnostic",
                redacted=False,
            )[0].source_type
            for command, stdout, _source_type in examples
        ]

        self.assertEqual(detected_types, [source_type for _command, _stdout, source_type in examples])


if __name__ == "__main__":
    unittest.main()
