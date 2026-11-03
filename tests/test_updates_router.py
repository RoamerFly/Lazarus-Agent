import unittest

from fastapi.testclient import TestClient

import workbench


class UpdatesRouterTests(unittest.TestCase):
    def test_about_route_is_mounted_on_loopback_workbench(self):
        client = TestClient(workbench.app, base_url="http://127.0.0.1:8767")
        response = client.get("/api/updates/about")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["developer"], "RoamerFly")
        self.assertEqual(response.json()["version"], "0.2.0")


if __name__ == "__main__":
    unittest.main()
