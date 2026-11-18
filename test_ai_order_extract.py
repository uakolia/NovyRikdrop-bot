"""Structured OpenAI response parsing tests without network requests."""
import json
import unittest

from ai_order_extract import AIOrderError, SCHEMA, parse_response


class ExtractTests(unittest.TestCase):
    def test_strict_schema(self):
        self.assertEqual(set(SCHEMA["required"]), set(SCHEMA["properties"]))
        self.assertFalse(SCHEMA["additionalProperties"])
        item = SCHEMA["properties"]["items"]["items"]
        self.assertEqual(set(item["required"]), set(item["properties"]))

    def test_good_response(self):
        data = {"items": [], "payment": "", "prepaid": 0,
                "fio": "", "phone": "", "city": "", "warehouse_number": ""}
        response = {"status": "completed", "output": [{
            "type": "message",
            "content": [{"type": "output_text", "text": json.dumps(data)}],
        }]}
        self.assertEqual(data, parse_response(response))

    def test_invalid_json(self):
        with self.assertRaises(AIOrderError):
            parse_response({"status": "completed", "output": [{
                "type": "message", "content": [{"type": "output_text", "text": "invalid"}],
            }]})

    def test_incomplete_response(self):
        with self.assertRaises(AIOrderError):
            parse_response({"status": "incomplete", "output": []})

    def test_missing_keys(self):
        with self.assertRaises(AIOrderError):
            parse_response({"status": "completed", "output": [{
                "type": "message", "content": [{"type": "output_text", "text": "{}"}],
            }]})


if __name__ == "__main__":
    unittest.main()
