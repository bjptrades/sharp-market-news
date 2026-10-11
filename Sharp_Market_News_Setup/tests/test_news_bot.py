import calendar
import unittest
from unittest.mock import Mock
import news_bot as n

class NewsTests(unittest.TestCase):
    def test_parse_feed(self):
        now = calendar.timegm((2026,10,11,18,0,0))
        xml = b'''<?xml version="1.0"?><rss version="2.0"><channel><title>News</title><item><title>Fed raises rates</title><link>https://example.com/a</link><pubDate>Sun, 11 Oct 2026 17:30:00 GMT</pubDate></item></channel></rss>'''
        session = Mock()
        session.get.return_value.content = xml
        session.get.return_value.raise_for_status.return_value = None
        items = n.fetch_feed(session, "MarketWatch", "https://example.org/rss", now)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "Fed raises rates")
        self.assertTrue(n.is_important(items[0]))

    def test_no_ping_on_routine(self):
        item = n.Headline("Big tech stocks climb", "https://example.com/1", "MarketWatch", 1)
        p = n.discord_payload(item, '12345', 'important')
        self.assertNotIn('<@&', p['content'])
        self.assertEqual(p['allowed_mentions']['roles'], [])

    def test_important_ping(self):
        item = n.Headline("Federal Reserve raises rates", "https://example.com/2", "Federal Reserve", 1)
        p = n.discord_payload(item, '12345', 'important')
        self.assertIn('<@&12345>', p['content'])
        self.assertEqual(p['allowed_mentions']['roles'], ['12345'])

    def test_duplication_across_sources(self):
        a = n.Headline("Fed holds rates at current levels", "https://example.com/1", "MarketWatch", 1)
        b = n.Headline("Fed holds rates at current levels", "https://example.com/2", "Yahoo", 2)
        self.assertEqual(n.fingerprint(a), n.fingerprint(b))

if __name__ == '__main__':
    unittest.main()
