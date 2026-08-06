#!/usr/bin/env python3
"""Unit tests for omni SpeakQueue drain merge strategies."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from omni_exp_speak_queue import SpeakItem, SpeakQueue  # noqa: E402


def _item(n: int) -> SpeakItem:
    return SpeakItem(
        reason="work_result",
        prompt_text=f"result-{n}",
        task_id=f"task-{n}",
        preview=f"p{n}",
    )


class TestSpeakQueue(unittest.TestCase):
    def test_serial_one_at_a_time(self):
        q = SpeakQueue(merge="serial")
        q.push(_item(1))
        q.push(_item(2))
        a = q.take()
        self.assertEqual(a.task_id, "task-1")
        self.assertEqual(len(q), 1)
        b = q.take()
        self.assertEqual(b.task_id, "task-2")
        self.assertEqual(len(q), 0)
        self.assertIsNone(q.take())

    def test_latest_drops_older(self):
        q = SpeakQueue(merge="latest")
        q.push(_item(1))
        q.push(_item(2))
        q.push(_item(3))
        a = q.take()
        self.assertEqual(a.task_id, "task-3")
        self.assertEqual(a.meta.get("dropped"), 2)
        self.assertEqual(len(q), 0)

    def test_concat_merges(self):
        q = SpeakQueue(merge="concat")
        q.push(_item(1))
        q.push(_item(2))
        a = q.take()
        self.assertIn("2 core results", a.prompt_text)
        self.assertIn("result-1", a.prompt_text)
        self.assertIn("result-2", a.prompt_text)
        self.assertEqual(a.meta.get("merged"), 2)
        self.assertEqual(len(q), 0)

    def test_push_front_requeues(self):
        q = SpeakQueue(merge="serial")
        q.push(_item(2))
        q.push_front(_item(1))
        self.assertEqual(q.take().task_id, "task-1")

    def test_max_items_drops_oldest(self):
        q = SpeakQueue(merge="serial", max_items=2)
        q.push(_item(1))
        q.push(_item(2))
        q.push(_item(3))
        self.assertEqual(len(q), 2)
        self.assertEqual(q.take().task_id, "task-2")


if __name__ == "__main__":
    unittest.main()
