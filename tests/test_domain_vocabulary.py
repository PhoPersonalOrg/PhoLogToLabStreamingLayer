"""Unit tests for PhoLog domain hotwords and post-decode corrections."""

import unittest

from phologtolabstreaminglayer.domain_vocabulary import (
    DOMAIN_HOTWORDS,
    apply_domain_corrections,
    apply_domain_corrections_to_emission,
    apply_domain_corrections_to_result,
    get_hotwords,
    get_initial_prompt,
)


class TestDomainVocabularyHelpers(unittest.TestCase):
    def test_get_hotwords_copy(self):
        words = get_hotwords()
        self.assertEqual(words, DOMAIN_HOTWORDS)
        words.append("extra")
        self.assertNotIn("extra", get_hotwords())

    def test_get_initial_prompt_mentions_terms(self):
        prompt = get_initial_prompt()
        for term in ("EEG", "AMPH", "1.0+", "0.5+", "log"):
            self.assertIn(term, prompt)


class TestApplyDomainCorrections(unittest.TestCase):
    def test_number_phrases(self):
        self.assertEqual(
            apply_domain_corrections("dose one point oh plus now"),
            "dose 1.0+ now",
        )
        self.assertEqual(
            apply_domain_corrections("took zero point five plus"),
            "took 0.5+",
        )
        self.assertEqual(apply_domain_corrections("1.0 plus"), "1.0+")
        self.assertEqual(apply_domain_corrections("0.5 plus"), "0.5+")
        self.assertEqual(apply_domain_corrections("1 .0  plus"), "1.0+")
        self.assertEqual(apply_domain_corrections("0 .5  plus"), "0.5+")
        self.assertEqual(
            apply_domain_corrections("dose 0 .5  plus then 1 .0  plus"),
            "dose 0.5+ then 1.0+",
        )

    def test_eeg_and_amph_variants(self):
        self.assertEqual(apply_domain_corrections("check the eeg"), "check the EEG")
        self.assertEqual(apply_domain_corrections("E.E.G. channel"), "EEG channel")
        self.assertEqual(apply_domain_corrections("amph dose"), "AMPH dose")
        self.assertEqual(apply_domain_corrections("AMP dose"), "AMPH dose")
        self.assertEqual(apply_domain_corrections("A-M-P-H"), "AMPH")
        self.assertEqual(apply_domain_corrections("amplify signal"), "amplify signal")

    def test_bare_log_not_rewritten(self):
        self.assertEqual(apply_domain_corrections("write a log entry"), "write a log entry")
        self.assertEqual(apply_domain_corrections("catalog the items"), "catalog the items")

    def test_unrelated_text_unchanged(self):
        text = "hello world temperature is fine"
        self.assertEqual(apply_domain_corrections(text), text)

    def test_result_dict_rewrites_segments_and_words(self):
        result = {
            "text": "check eeg then one point zero plus",
            "segments": [
                {
                    "text": "check eeg then one point zero plus",
                    "words": [
                        {"text": "check", "start": 0.0, "end": 0.2},
                        {"text": "eeg", "start": 0.2, "end": 0.4},
                        {"text": "then", "start": 0.4, "end": 0.5},
                        {"text": "one", "start": 0.5, "end": 0.6},
                        {"text": "point", "start": 0.6, "end": 0.7},
                        {"text": "zero", "start": 0.7, "end": 0.8},
                        {"text": "plus", "start": 0.8, "end": 1.0},
                    ],
                }
            ],
        }
        out = apply_domain_corrections_to_result(result)
        self.assertIn("EEG", out["text"])
        self.assertIn("1.0+", out["text"])
        words = out["segments"][0]["words"]
        texts = [w["text"] for w in words]
        self.assertIn("EEG", texts)
        self.assertIn("1.0+", texts)
        plus_word = next(w for w in words if w["text"] == "1.0+")
        self.assertEqual(plus_word["start"], 0.5)
        self.assertEqual(plus_word["end"], 1.0)

    def test_emission_corrections(self):
        emission = {
            "text": "amph at 0.5 plus",
            "words": [
                {"text": "amph", "start": 0.0, "end": 0.2},
                {"text": "at", "start": 0.2, "end": 0.3},
                {"text": "0.5", "start": 0.3, "end": 0.5},
                {"text": "plus", "start": 0.5, "end": 0.7},
            ],
        }
        out = apply_domain_corrections_to_emission(emission)
        self.assertIn("AMPH", out["text"])
        self.assertIn("0.5+", out["text"])

    def test_spaced_decimal_plus_tokens(self):
        emission = {
            "text": "0 .5  plus then 1 .0  plus",
            "words": [
                {"text": "0", "start": 0.0, "end": 0.1},
                {"text": ".5", "start": 0.1, "end": 0.2},
                {"text": "plus", "start": 0.2, "end": 0.3},
                {"text": "then", "start": 0.3, "end": 0.4},
                {"text": "1", "start": 0.4, "end": 0.5},
                {"text": ".0", "start": 0.5, "end": 0.6},
                {"text": "plus", "start": 0.6, "end": 0.7},
            ],
        }
        out = apply_domain_corrections_to_emission(emission)
        self.assertEqual(out["text"], "0.5+ then 1.0+")



if __name__ == "__main__":
    unittest.main()
