import unittest

from ary_episode_watcher import (
    choose_best_variant,
    parse_hls_master,
    safe_filename,
)


class CoreTests(unittest.TestCase):
    def test_parse_hls_master(self):
        text = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=100000,AVERAGE-BANDWIDTH=80000,CODECS="avc1.640015,mp4a.40.2",RESOLUTION=426x240
240.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=6800000,AVERAGE-BANDWIDTH=4800000,CODECS="avc1.640033,mp4a.40.2",RESOLUTION=3840x2160
2160.m3u8
"""
        result = parse_hls_master(text, "https://example.test/path/master.m3u8")
        self.assertEqual(len(result["variants"]), 2)
        self.assertEqual(result["variants"][1]["height"], 2160)
        self.assertTrue(result["variants"][1]["uri"].endswith("/2160.m3u8"))

    def test_choose_best_variant(self):
        variants = [
            {"width": 1920, "height": 1080, "bandwidth": 500000, "uri": "1080"},
            {"width": 3840, "height": 2160, "bandwidth": 1000000, "uri": "2160"},
        ]
        self.assertEqual(choose_best_variant(variants)["height"], 2160)

    def test_safe_filename(self):
        self.assertEqual(
            safe_filename('Episode 16: Dar/Nijaat?'),
            "Episode-16-Dar-Nijaat",
        )


if __name__ == "__main__":
    unittest.main()
