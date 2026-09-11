import json
import unittest
from pathlib import Path


class ReferenceDataTest(unittest.TestCase):
    def test_stable_identity_is_used_by_events(self):
        data = json.loads((Path(__file__).parents[1] / "reference" / "domain.json").read_text(encoding="utf-8"))
        self.assertEqual(data["domain"], "dairy-contact-tracing")
        animals = {item["animal_key"] for item in data["animals"]}
        self.assertTrue(all(event["animal_key"] in animals for event in data["resource_events"]))
        self.assertTrue(all(result["animal_key"] in animals for result in data["lab_results"]))


if __name__ == "__main__":
    unittest.main()
