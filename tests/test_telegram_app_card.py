import json
import io
import unittest
from unittest.mock import Mock, patch

import app


class TelegramAppCardTests(unittest.TestCase):
    def setUp(self):
        with app.GOOGLE_PLAY_CARD_META_CACHE._lock:
            app.GOOGLE_PLAY_CARD_META_CACHE._items.clear()
        with app.TELEGRAM_APP_CARD_CACHE._lock:
            app.TELEGRAM_APP_CARD_CACHE._items.clear()

    def test_long_title_keeps_words_when_shrinking_and_marks_truncation(self):
        draw = app.ImageDraw.Draw(app.Image.new("RGBA", (1200, 800)))
        for title in (
            "Fast and Feather", "Ice Robot Fishing", "A Wonderful Fishing Adventure Game",
            "ExtremelyLongUnbrokenApplicationName" * 4,
            "A very long title with many words " * 8,
        ):
            with self.subTest(title=title):
                lines, font = app.fit_wrapped_card_text(draw, title, 560, 2, 72, 50, bold=True)
                self.assertLessEqual(len(lines), 2)
                self.assertGreaterEqual(font.size, 50)
                self.assertTrue(all(app.text_bbox_size(draw, line, font)[0] <= 560 for line in lines))
                self.assertTrue(" ".join(lines) == title.strip() or lines[-1].endswith("…"))

    def test_header_rows_stay_aligned_for_short_long_and_missing_metadata(self):
        layers = []
        for name in ("Game", "Ice Robot Fishing", "Very long application title " * 12, None):
            layer = app.render_telegram_card_header(
                {"name": name, "category": "Casual", "content_rating": "3+"}, "com.example.game",
            )
            self.assertEqual(layer.size, (580, 386))
            self.assertIsNone(layer.getchannel("A").crop((565, 0, 580, 386)).getbbox())
            for top, bottom in ((172, 181), (227, 240), (309, 321), (385, 386)):
                self.assertIsNone(layer.getchannel("A").crop((0, top, 580, bottom)).getbbox())
            layers.append(layer.crop((0, 180, 580, 386)).tobytes())
        self.assertTrue(all(layer == layers[0] for layer in layers))

    def test_header_limits_long_category_and_package_without_clipping(self):
        layer = app.render_telegram_card_header(
            {"name": "Test", "category": "Very long category " * 12, "content_rating": "18+"},
            "com." + "longpackage" * 24,
        )
        for box in ((565, 0, 580, 386), (0, 385, 580, 386)):
            self.assertIsNone(layer.getchannel("A").crop(box).getbbox())

    def test_supersampled_age_labels_remain_centered(self):
        for label in ("3+", "7+", "16+", "18+"):
            with self.subTest(label=label):
                layer = app.render_telegram_card_header({"content_rating": label}, "")
                badge = layer.crop((0, 244, 150, 304))
                bounds = badge.getchannel("G").point(lambda value: 255 if value > 128 else 0).getbbox()
                self.assertIsNotNone(bounds)
                self.assertAlmostEqual((bounds[0] + bounds[2]) / 2, 75, delta=2)
                self.assertAlmostEqual((bounds[1] + bounds[3]) / 2, 30, delta=2)

    @patch("app.TELEGRAM_SEND_APP_CARD", True)
    @patch("app.fetch_card_icon")
    @patch("app.fetch_card_media_image")
    @patch("app.sensor_tower_app_card_meta")
    def test_text_redesign_preserves_icon_screenshots_and_logo(self, meta, media, icon):
        meta.return_value = {"name": "Ice Robot Fishing", "category": "Casual", "content_rating": "3+", "screenshots": ["1", "2", "3"]}
        icon.return_value = app.Image.new("RGBA", (300, 300), (20, 200, 40, 255))
        media.side_effect = lambda url, size, radius: app.Image.new("RGBA", size, (40, 80, 180, 255))
        row = {"app_id": "com.example.game"}
        before = app.Image.open(io.BytesIO(app.build_telegram_app_card(row)))
        with app.TELEGRAM_APP_CARD_CACHE._lock:
            app.TELEGRAM_APP_CARD_CACHE._items.clear()
        with patch("app.render_telegram_card_header", return_value=app.Image.new("RGBA", (580, 386))):
            without_text = app.Image.open(io.BytesIO(app.build_telegram_app_card(row)))
        for box in ((0, 0, 420, 1134), (1000, 0, 1387, 1134), (0, 480, 1387, 1134)):
            self.assertEqual(before.crop(box).tobytes(), without_text.crop(box).tobytes())
        self.assertEqual(before.getpixel((200, 200)), (20, 200, 40))
        for x in (200, 650, 1100):
            self.assertEqual(before.getpixel((x, 700)), (40, 80, 180))

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

    @patch("app.session.get")
    def test_empty_preloaded_meta_falls_back_to_google_play_page(self, get):
        schema = {
            "@context": "https://schema.org",
            "@type": "SoftwareApplication",
            "name": "Big Win : Mega Jackpot",
            "applicationCategory": "GAME_CASINO",
            "image": "https://play-lh.googleusercontent.com/icon",
            "contentRating": "Teen",
        }
        response = Mock(status_code=200)
        response.text = "".join([
            '<html><head><script type="application/ld+json">',
            json.dumps(schema),
            '</script></head><body>',
            '<img alt="Screenshot image" src="https://play-lh.googleusercontent.com/shot-1">',
            '</body></html>',
        ])
        get.return_value = response

        meta = app.google_play_app_card_meta({
            "app_id": "com.big.win.mega.jackpot",
            "app_name": "com.big.win.mega.jackpot",
            "_google_play_card_meta": {},
        })

        get.assert_called_once()
        self.assertEqual(meta["name"], "Big Win : Mega Jackpot")
        self.assertEqual(meta["category"], "Game - Casino")
        self.assertEqual(meta["content_rating"], "16+")
        self.assertEqual(meta["icon_url"], "https://play-lh.googleusercontent.com/icon")
        self.assertEqual(meta["screenshots"], ["https://play-lh.googleusercontent.com/shot-1"])

    @patch("app.session.get")
    def test_empty_google_play_response_is_not_cached(self, get):
        response = Mock(status_code=200, text="<html></html>")
        get.return_value = response
        row = {"app_id": "com.example.empty", "_google_play_card_meta": {}}

        app.google_play_app_card_meta(row)
        app.google_play_app_card_meta(row)

        self.assertEqual(get.call_count, 2)

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

    @unittest.skipIf(app.Image is None, "Pillow is required for card rendering")
    def test_age_badge_visible_pixels_are_centered(self):
        for label in ("3+", "7+", "16+", "18+"):
            with self.subTest(label=label):
                image = app.Image.new("RGB", (300, 160), (0, 0, 0))
                width, height = app.draw_card_pill(
                    app.ImageDraw.Draw(image), (30, 25), label,
                    font=app.load_card_font(38, bold=True),
                    fill=(230, 0, 0), min_width=150, pad_x=34, pad_y=14,
                )
                # White text is the only content with a nonzero green channel.
                bounds = image.getchannel("G").point(lambda value: 255 if value > 128 else 0).getbbox()
                self.assertIsNotNone(bounds)
                self.assertAlmostEqual((bounds[0] + bounds[2]) / 2, 30 + width / 2, delta=2)
                self.assertAlmostEqual((bounds[1] + bounds[3]) / 2, 25 + height / 2, delta=2)

    @patch("app.send_telegram_message")
    @patch("app.send_telegram_photo")
    @patch("app.build_telegram_app_card", return_value=b"card")
    def test_long_geo_message_is_sent_in_full_after_card(self, build_card, send_photo, send_text):
        codes = sorted({code for code, _ in app.COUNTRIES_FULL.values()})
        row = {"app_id": "com.example.game", "app_name": "Example"}
        message = app.build_bot_message(
            "new_closed", row, {"total": len(codes), "closed_codes": codes}, ["SG"],
        )
        self.assertGreater(len(message), 950)
        send_photo.return_value = [{"photo": True}]
        send_text.return_value = [{"text": True}]

        result = app.send_telegram_event_message(message, row, event="new_closed")

        send_photo.assert_called_once_with(b"card", caption="")
        send_text.assert_called_once_with(message)
        self.assertEqual(result, [{"photo": True}, {"text": True}])

    @patch("app.session.get")
    def test_card_media_rejects_untrusted_or_oversized_downloads(self, get):
        for url in (
            "http://play-lh.googleusercontent.com/icon.png",
            "https://127.0.0.1/icon.png",
            "https://play-lh.googleusercontent.com.attacker.test/icon.png",
            "https://user:password@play-lh.googleusercontent.com/icon.png",
        ):
            with self.subTest(url=url):
                self.assertIsNone(app.fetch_card_media_bytes(url))
        get.assert_not_called()

        response = Mock(status_code=200)
        response.headers = {
            "Content-Type": "image/png",
            "Content-Length": str(app.CARD_MEDIA_MAX_BYTES + 1),
        }
        get.return_value = response
        self.assertIsNone(app.fetch_card_media_bytes("https://play-lh.googleusercontent.com/icon.png"))
        get.assert_called_once()

    @patch("app.session.get")
    def test_card_media_redirect_cannot_escape_google_cdn(self, get):
        response = Mock(status_code=302)
        response.headers = {"Location": "https://127.0.0.1/internal"}
        get.return_value = response

        self.assertIsNone(app.fetch_card_media_bytes("https://play-lh.googleusercontent.com/icon.png"))
        get.assert_called_once()

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
