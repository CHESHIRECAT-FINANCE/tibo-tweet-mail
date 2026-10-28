import unittest

from tibo_tweet_mail import is_forwardable, is_reset_notice, subject_line

CONFIG = {
    "handle": "thsottiaux",
    "include_quotes": False,
    "include_replies": False,
    "include_reposts": False,
}


def post(**extra):
    item = {
        "type": "status",
        "id": "1",
        "text": "Day 8/ Reset",
        "author": {"screen_name": "thsottiaux"},
        "replying_to": None,
    }
    item.update(extra)
    return item


class FilterTest(unittest.TestCase):
    def test_plain_original(self):
        self.assertTrue(is_forwardable(post(), CONFIG))

    def test_quote_is_skipped(self):
        self.assertFalse(is_forwardable(post(quote={"id": "9"}), CONFIG))

    def test_quote_can_be_enabled(self):
        config = dict(CONFIG, include_quotes=True)
        self.assertTrue(is_forwardable(post(quote={"id": "9"}), config))

    def test_reply_to_someone_else(self):
        item = post(replying_to={"screen_name": "sama"})
        self.assertFalse(is_forwardable(item, CONFIG))

    def test_self_reply(self):
        item = post(replying_to={"screen_name": "thsottiaux"})
        self.assertFalse(is_forwardable(item, CONFIG))

    def test_other_author_is_repost(self):
        item = post(author={"screen_name": "OpenAIDevs"}, reposted_by={"screen_name": "thsottiaux"})
        self.assertFalse(is_forwardable(item, CONFIG))

    def test_repost_when_enabled(self):
        config = dict(CONFIG, include_reposts=True)
        item = post(
            text="You can finally close your laptop",
            author={"screen_name": "OpenAIDevs"},
            reposted_by={"screen_name": "thsottiaux"},
        )
        self.assertTrue(is_forwardable(item, config))
        self.assertEqual(
            subject_line("Tibo", item["text"], repost=True),
            "[Tibo 转发] You can finally close your laptop",
        )

    def test_subject_collapses_whitespace(self):
        self.assertEqual(subject_line("Tibo", "Day 8/\nReset"), "[Tibo] Day 8/ Reset")


class ResetNoticeTest(unittest.TestCase):
    def test_reset_day_announcement(self):
        text = "Day 17/ Bet you thought we forgot, but no, it’s reset day today."
        self.assertTrue(is_reset_notice(text))
        self.assertTrue(subject_line("Tibo", text, reset=True).startswith("[Tibo 重置] "))

    def test_limit_reset_and_cooking_reset(self):
        self.assertTrue(is_reset_notice("Day 13/ Le limit reset"))
        self.assertTrue(is_reset_notice("Day 15/ Nevermind, we're still cooking... and reset. Enjoy."))
        self.assertTrue(is_reset_notice("用量今晚重置"))

    def test_ordinary_posts_stay_quiet(self):
        self.assertFalse(is_reset_notice("Day 8/ Reset"))
        self.assertFalse(is_reset_notice("Day 16/ You can now cache for 12h in the API."))
        self.assertFalse(is_reset_notice("People are asking me for resets on the street now."))
        self.assertFalse(is_reset_notice("Lands tomorrow before 10am PST."))


if __name__ == "__main__":
    unittest.main()
