from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile

from tools.verify_review_package import verify


class ReviewPackageTests(unittest.TestCase):
    def test_counterexample_device_versions_must_match_target(self):
        base = b"test APK input"
        candidate = b"test APK output"
        identity = {"input_sha256": sha256(base).hexdigest(),
                    "candidate_sha256": sha256(candidate).hexdigest()}
        visible = {"action": "expect", "value": "Ready"}
        hidden = {"action": "absent", "value": "Ready"}
        success = {**visible, "status": "passed", "match_count": 1}
        failure = {**hidden, "status": "failed", "match_count": 1}

        def acceptance(row_api, device_api, *, counterexample=False):
            status = "failed" if counterexample else "behavior-verified"
            return {
                **identity, "targets": [36],
                "status": "incomplete" if counterexample else status,
                "steps": [visible, hidden] if counterexample else [visible],
                "rows": [{
                    "api": row_api, "status": status, "candidate": {
                        "install": "passed", "launch": "passed",
                        "function": "failed" if counterexample else "passed",
                        "steps": [success, failure] if counterexample else [success],
                        "device": {"api": device_api, "installed_sha256": identity["candidate_sha256"]},
                    },
                }],
            }

        with TemporaryDirectory() as directory:
            for row_api, device_api in ((36, 36), (35, 36), (36, 35)):
                with self.subTest(row_api=row_api, device_api=device_api):
                    archive = Path(directory) / f"{row_api}-{device_api}.zip"
                    files = {"复核状态.json": b"{}"}
                    for folder, report in (
                        ("可分发样本/P01", acceptance(36, 36)),
                        ("反例样本/N03", acceptance(row_api, device_api, counterexample=True)),
                    ):
                        files[f"{folder}/base.apk"] = base
                        files[f"{folder}/candidate.apk"] = candidate
                        files[f"{folder}/agent-acceptance.json"] = json.dumps(report).encode()
                    files["反例样本/N03/expected-outcome.json"] = json.dumps({
                        "exit_code": 1, "steps": [success, failure],
                    }).encode()
                    with ZipFile(archive, "w") as output:
                        for name, data in files.items():
                            output.writestr(name, data)
                        output.writestr("SHA256SUMS.txt", "".join(
                            f"{sha256(data).hexdigest()}  {name}\n" for name, data in files.items()))
                    if row_api == device_api == 36:
                        self.assertEqual(len(verify(archive)["counterexamples"]), 1)
                    else:
                        with self.assertRaisesRegex(ValueError, "设备版本"):
                            verify(archive)
