import unittest

from compatibility_matrix import finish_matrix, new_matrix, normalize_targets, set_row


class CompatibilityMatrixTests(unittest.TestCase):
    def test_omitted_targets_preserve_single_target_behavior(self):
        self.assertEqual(normalize_targets(None, {34, 35, 36}, 35), [35])

    def test_sparse_explicit_targets_are_valid_and_ordered(self):
        self.assertEqual(normalize_targets([36, 34], {34, 35, 36}, 36), [36, 34])

    def test_duplicate_or_unknown_target_is_rejected(self):
        with self.assertRaises(ValueError):
            normalize_targets([34, 34], {34, 35, 36}, 34)
        with self.assertRaises(ValueError):
            normalize_targets([33], {34, 35, 36}, 34)

    def test_missing_environment_never_produces_exportable_matrix(self):
        matrix = new_matrix([34, 36], 36)
        set_row(matrix, 34, status="passed", installed_sha256="a" * 64)
        set_row(matrix, 36, status="unavailable", failure="缺少 API 36 模拟器镜像")
        self.assertFalse(finish_matrix(matrix, {"sha256": "a" * 64}))
        self.assertEqual(matrix["status"], "failed")
        self.assertIsNone(matrix["artifact"])

    def test_all_requested_targets_are_required(self):
        matrix = new_matrix([34, 36], 36)
        for api in (34, 36):
            set_row(matrix, api, status="passed", installed_sha256="b" * 64)
        self.assertTrue(finish_matrix(matrix, {"sha256": "b" * 64}))
        self.assertEqual(matrix["artifact"]["sha256"], "b" * 64)

    def test_hash_mismatch_revokes_an_already_passed_row(self):
        matrix = new_matrix([36], 36)
        set_row(matrix, 36, status="passed", installed_sha256="c" * 64)
        self.assertFalse(finish_matrix(matrix, {"sha256": "d" * 64}))
        self.assertEqual(matrix["rows"][0]["status"], "failed")
        self.assertIn("哈希", matrix["rows"][0]["failure"])


if __name__ == "__main__":
    unittest.main()
