import threading
import time
import unittest
from unittest.mock import patch

import telegram_bot


class TelegramSchedulerTests(unittest.TestCase):
    def setUp(self):
        telegram_bot.last_scheduled_key = ""
        telegram_bot.last_live_status_key = ""
        telegram_bot.scheduled_check_pending = False
        telegram_bot.live_status_check_pending = False

    def tearDown(self):
        telegram_bot.last_scheduled_key = ""
        telegram_bot.last_live_status_key = ""
        telegram_bot.scheduled_check_pending = False
        telegram_bot.live_status_check_pending = False

    @patch("telegram_bot.run_live_status_bot_check")
    def test_live_status_waits_for_an_active_check_instead_of_skipping(self, run_check):
        run_check.return_value = {
            "apps_checked": 1,
            "apps_total": 1,
            "notifications": [],
            "errors": [],
            "full_confirmations": 0,
        }
        telegram_bot.live_status_check_pending = True
        telegram_bot.check_lock.acquire()
        worker = threading.Thread(target=telegram_bot.run_scheduled_live_status_check)
        worker.start()
        try:
            time.sleep(0.05)
            run_check.assert_not_called()
            self.assertTrue(worker.is_alive())
        finally:
            telegram_bot.check_lock.release()

        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        run_check.assert_called_once()
        self.assertFalse(telegram_bot.live_status_check_pending)

    @patch("telegram_bot.threading.Thread")
    def test_live_status_slot_is_only_queued_once(self, thread_cls):
        telegram_bot.maybe_run_live_status_schedule()
        telegram_bot.maybe_run_live_status_schedule()

        thread_cls.assert_called_once_with(
            target=telegram_bot.run_scheduled_live_status_check,
            daemon=True,
        )
        thread_cls.return_value.start.assert_called_once()
        self.assertTrue(telegram_bot.live_status_check_pending)


if __name__ == "__main__":
    unittest.main()
