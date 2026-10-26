import unittest
from agenttxn import ActionSpec, EffectClass

class ModelValidationTests(unittest.TestCase):
    def test_non_finite_json_number_rejected(self):
        with self.assertRaises(ValueError):
            ActionSpec("demo.create", {"score": float("nan")}, EffectClass.REVERSIBLE, "action:nan")

    def test_non_string_json_key_rejected(self):
        with self.assertRaises(TypeError):
            ActionSpec("demo.create", {1: "not-a-json-object-key"}, EffectClass.REVERSIBLE, "action:key")

    def test_string_effect_is_normalized(self):
        action=ActionSpec("demo.create", {"name":"x"}, "reversible", "action:effect")
        self.assertEqual(action.effect,EffectClass.REVERSIBLE)

if __name__=="__main__": unittest.main()
