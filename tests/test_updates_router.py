import hashlib
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import workbench
import updates_router
from updater import UpdateAsset, UpdateManifest


class UpdatesRouterTests(unittest.TestCase):
    def test_about_route_is_mounted_on_loopback_workbench(self):
        client = TestClient(workbench.app, base_url="http://127.0.0.1:8767")
        response = client.get("/api/updates/about")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["developer"], "RoamerFly")
        self.assertEqual(response.json()["version"], "0.2.0")

    def test_post_update_flow_requires_token_and_works_with_loopback_middleware(self):
        client = TestClient(workbench.app, base_url="http://127.0.0.1:8767")
        token = client.get("/api/config").json()["token"]
        content = b"signed update fixture"
        manifest = UpdateManifest("0.2.1", "now", "fixture", {
            "windows-x64-portable-exe": UpdateAsset("fixture.exe", "https://fixture.invalid/fixture.exe", hashlib.sha256(content).hexdigest()),
        })
        with tempfile.TemporaryDirectory() as raw:
            artifact = Path(raw).resolve() / "fixture.exe"
            def fake_download(*_args, **_kwargs):
                artifact.write_bytes(content)
                return artifact
            updates_router._cached.clear(); updates_router._downloads.clear()
            with patch("updates_router.check_for_update", return_value=manifest), patch("updates_router.download_asset", side_effect=fake_download):
                self.assertEqual(client.post("/api/updates/check").status_code, 403)
                checked = client.post("/api/updates/check", headers={"x-reviver-token": token})
                self.assertEqual(checked.status_code, 200)
                started = client.post("/api/updates/download?kind=windows-x64-portable-exe", headers={"x-reviver-token": token})
                self.assertEqual(started.status_code, 200)
                job_id = started.json()["id"]
                for _ in range(30):
                    state = client.get(f"/api/updates/download/{job_id}")
                    if state.json()["ready"]:
                        break
                    time.sleep(.01)
                self.assertTrue(state.json()["ready"])
                self.assertEqual(client.post(f"/api/updates/download/{job_id}/use", headers={"x-reviver-token": token}).status_code, 200)

    def test_install_copies_a_separate_helper_before_starting_it(self):
        content = b"trusted artifact"
        manifest = UpdateManifest("0.2.1", "now", "fixture", {
            "windows-x64-portable-exe": UpdateAsset("fixture.exe", "https://fixture.invalid/fixture.exe", hashlib.sha256(content).hexdigest()),
        })
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            artifact, target = root / "fixture.exe", root / "Lazarus-Agent.exe"
            artifact.write_bytes(content); target.write_bytes(b"running image")
            class Paths: temp = root / "temp"
            updates_router._cached.clear()
            updates_router._cached.update(manifest=manifest, artifact=artifact, kind="windows-x64-portable-exe")
            with patch.object(updates_router.sys, "frozen", True, create=True), patch.object(updates_router.sys, "executable", str(target)), patch("updates_router.configure_environment", return_value=Paths()), patch("updates_router.subprocess.Popen") as launched:
                result = updates_router.install()
            self.assertTrue(result["close_app"])
            command = launched.call_args.args[0]
            helper = Path(command[0])
            self.assertNotEqual(helper, target)
            self.assertTrue(helper.is_file())
            self.assertEqual(command[command.index("--source-sha256") + 1], hashlib.sha256(content).hexdigest())


if __name__ == "__main__":
    unittest.main()
