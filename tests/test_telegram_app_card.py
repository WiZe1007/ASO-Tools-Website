import json
import unittest
from unittest.mock import Mock, patch

import app


class TelegramAppCardTests(unittest.TestCase):
    def setUp(self):
        with app.GOOGLE_PLAY_CARD_META_CACHE._lock:
            app.GOOGLE_PLAY_CARD_META_CACHE._items.clear()

    @patch("app.session.get")
    def test_google_play_meta_reads_icon_and_screenshots(self, get):
        schema = {
            "@context": "https://schema.org",
            "@type": "SoftwareApplication",
            "name": "Royal Jewels Game",
            "applicationCategory": "GAME_CASUAL",
            "image": "https://play-lh.googleusercontent.com/icon",
            "contentRating": "Teen",
        }
        response = Mock(status_code=200)
        response.text = "".join([
            '<html><head><script type="application/ld+json">',
            json.dumps(schema),
            '</script></head><body>',
            '<img alt="Screenshot image" src="https://play-lh.googleusercontent.com/shot-1">',
            '<img alt="Screenshot image" src="https://play-lh.googleusercontent.com/shot-2">',
            '<img alt="Screenshot image" src="https://play-lh.googleusercontent.com/shot-3">',
            '</body></html>',
        ])
        get.return_value = response

        meta = app.google_play_app_card_meta({
            "app_id": "com.atknsyl.we",
            "app_name": "Fallback",
        })

        self.assertEqual(meta["name"], "Royal Jewels Game")
        self.assertEqual(meta["category"], "Game - Casual")
        self.assertEqual(meta["content_rating"], "16+")
        self.assertEqual(meta["icon_url"], "https://play-lh.googleusercontent.com/icon")
        self.assertEqual(len(meta["screenshots"]), 3)

    def test_content_rating_labels_are_normalized_for_card_badges(self):
        cases = {
            "Everyone": "3+",
            "Rated for 3+": "3+",
            "Everyone 10+": "7+",
            "Parental guidance": "7+",
            "Teen": "16+",
            "Teenagers": "16+",
            "PEGI 12": "16+",
            "16+": "16+",
            "Mature 17+": "18+",
            "Adults only 18+": "18+",
            "Unrated": "—",
            "Unknown label": "—",
        }

        for source, expected in cases.items():
            with self.subTest(source=source):
                self.assertEqual(app.normalize_content_rating_label(source), expected)

    @patch("app.send_telegram_photo")
    @patch("app.build_telegram_app_card", return_value=b"card")
    def test_live_event_is_forwarded_to_card_builder(self, build_card, send_photo):
        send_photo.return_value = [{"ok": True}]
        row = {"app_id": "com.atknsyl.we", "app_name": "Royal Jewels Game"}

        app.send_telegram_event_message("Live", row, event="new_live")

        build_card.assert_called_once_with(row, event="new_live")
        send_photo.assert_called_once_with(b"card", caption="Live")


if __name__ == "__main__":
    unittest.main()
