import unittest
from handle_email import app


class TestHandleEmailApp(unittest.TestCase):
    
    def setUp(self):
        app.EMAIL_DOM = "source.com"
    
    def test_transform_address(self):
        test_cases = [
            ("Name One <name1@example.com>", "Name One <name1_example.com@source.com>", "name1_example.com"),
            ("Name Two <name2.extra@example.com>", "Name Two <name2.extra_example.com@source.com>", "name2.extra_example.com"),
            ("<name3a@example.com>", "name3a_example.com@source.com", "name3a_example.com"),
            ("name3@example.com", "name3_example.com@source.com", "name3_example.com"),
        ]
        for case in test_cases:
            self.assertEqual(app.transform_address(case[0]), case[1])
            self.assertEqual(app.transform_address(case[0], user_only=True), case[2])
