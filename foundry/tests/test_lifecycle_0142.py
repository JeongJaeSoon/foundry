"""0.1.42: the language each sync target is written in comes from prefs `sync.languages`."""

import json
import os
import sys

import test_foundry as tf
from test_sync import SyncBase

DEFAULTS = {"jira": "ja", "linear": "ja", "github": "en", "obs": "ko"}  # github since 0.1.75


def lib(name):
    sys.path.insert(0, tf.SCRIPTS)
    return __import__(name)


class Languages(SyncBase):
    def set_sync(self, **sync):
        with open(os.path.join(self.dir, "prefs.json"), "w") as f:
            json.dump({"sync": sync}, f)

    def items(self):
        self.unit("u1")
        self.foundry("sync", "enqueue", "u1", "--target", "jira", "--text", "merged")
        self.foundry("unit", "report", "u1", "--text", "보고: done")
        return {i["target"]: i for i in self.drain()}

    def test_override_jira_en_keeps_obs_korean(self):
        self.set_sync(languages={"jira": "en"})
        items = self.items()
        self.assertEqual(items["jira"]["language"], "English")
        self.assertIn("1-3 English lines of outcome", items["jira"]["instructions"])
        self.assertIn("Write the whole comment in English", items["jira"]["instructions"])
        self.assertNotIn("Japanese", items["jira"]["instructions"])
        self.assertEqual(items["obs"]["language"], "Korean")
        self.assertIn("Write the note in Korean", items["obs"]["instructions"])

    def test_invalid_prefs_file_falls_back_to_defaults(self):
        self.set_sync(languages={"jira": ""})
        items = self.items()
        self.assertEqual(items["jira"]["language"], "Japanese")
        self.assertEqual(items["obs"]["language"], "Korean")

    def test_language_falls_back_per_value(self):
        sync = lib("sync")
        for bad in ("ja", ["ja"], {"jira": ""}, {"jira": 3}, None):
            p = {"sync": {"languages": bad}}
            self.assertEqual(sync.language("jira", p), "Japanese", bad)
            self.assertEqual(sync.language("obs", p), "Korean", bad)
        self.assertEqual(sync.language("linear", {"sync": {"languages": {"jira": "en"}}}), "Japanese")
        self.assertEqual(sync.language("jira", {"sync": {"languages": {"jira": "de"}}}), "de")

    def test_validate_rejects_non_object_and_blank_codes(self):
        prefs = lib("prefs")
        msg = "sync.languages: an object of non-empty language codes per target (jira, linear, github, obs)"
        self.assertIn(msg, prefs.validate({"sync": {"languages": "ja"}}))
        self.assertIn(msg, prefs.validate({"sync": {"languages": {"jira": " "}}}))
        self.assertNotIn(msg, prefs.validate({"sync": {"languages": {"jira": "en"}}}))

    def test_defaults_unchanged_from_0141(self):
        prefs = lib("prefs")
        sync = lib("sync")
        d = prefs.defaults()["sync"]
        self.assertEqual(d["languages"], DEFAULTS)
        self.assertEqual(sync.DEFAULT_LANGUAGES, DEFAULTS)
        self.assertEqual(list(d)[-2:], ["comment_footer", "languages"])
        items = self.items()
        self.assertIn("1-3 Japanese lines of outcome", items["jira"]["instructions"])
        self.assertIn("1-3 Korean lines of outcome", items["obs"]["instructions"])
