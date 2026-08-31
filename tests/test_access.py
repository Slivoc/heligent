from __future__ import annotations

import unittest

from adsb_ingest.access import AccessUser, normalize_display_name, normalize_email, normalize_role


class AccessTests(unittest.TestCase):
    def test_identity_values_are_normalized(self) -> None:
        self.assertEqual(normalize_email(" Admin@Example.COM "), "admin@example.com")
        self.assertEqual(normalize_role(" analyst "), "ANALYST")
        self.assertEqual(normalize_display_name(" Test   User "), "Test User")

    def test_role_hierarchy(self) -> None:
        viewer = AccessUser("viewer@example.com", None, "VIEWER", True)
        analyst = AccessUser("analyst@example.com", None, "ANALYST", True)
        admin = AccessUser("admin@example.com", None, "ADMIN", True)
        self.assertTrue(viewer.permits("VIEWER"))
        self.assertFalse(viewer.permits("ANALYST"))
        self.assertTrue(analyst.permits("VIEWER"))
        self.assertTrue(admin.permits("ADMIN"))
        self.assertFalse(AccessUser("off@example.com", None, "ADMIN", False).permits("VIEWER"))

    def test_invalid_identity_values_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            normalize_email("not-an-email")
        with self.assertRaises(ValueError):
            normalize_role("owner")


if __name__ == "__main__":
    unittest.main()
