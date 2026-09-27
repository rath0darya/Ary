import unittest

from ary_episode_watcher import (
    choose_best_variant,
    parse_hls_master,
    safe_filename,
    _series_from_html,
    _internal_links,
    _extract_video_sources,
    _extract_episode_catalogue,
    normalise_episode,
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

    def test_series_discovery_parser(self):
        html = '<a href="/title/abc123">Example Drama</a><span>Series42 Ep</span>'
        result = _series_from_html(html, "https://aryplus.tv/browse/genre/test")
        self.assertEqual(result[0]["id"], "abc123")
        self.assertEqual(result[0]["content_type"], "Series")

    def test_telefilm_discovery_parser(self):
        html = '<a href="/title/tf123">Example Telefilm</a><span>Telefilm</span><span>Movie</span>'
        result = _series_from_html(html, "https://aryplus.tv/browse/genre/test", "Telefilms")
        self.assertEqual(result[0]["id"], "tf123")
        self.assertEqual(result[0]["content_type"], "Telefilm")


    def test_movie_and_show_card_types_do_not_leak_from_neighbouring_cards(self):
        html = '''
        <a href="/title/movie123">13+A-One Travels Movie</a>
        <a href="/title/show123">13+Tamasha Season 5 Series49 Ep</a>
        <a href="/title/drama123">13+Mera Yaar Miladay Series22 Ep</a>
        '''
        result = _series_from_html(html, "https://aryplus.tv/browse/genre/test")
        self.assertEqual({x["id"]: x["content_type"] for x in result}, {
            "movie123": "Movie",
            "show123": "Series",
            "drama123": "Series",
        })
        shows = _series_from_html(
            '<a href="/title/show123">13+Tamasha Season 5 Series49 Ep</a>',
            "https://aryplus.tv/browse/genre/684848223e08d31efd33fbcc",
            "TV Shows",
        )
        self.assertEqual(shows[0]["content_type"], "Show")

    def test_internal_catalogue_links(self):
        html = '''
        <a href="/browse/genre/abc123">Drama</a>
        <a href="https://aryplus.tv/browse/genre/def456">Shows</a>
        <a href="/title/title123">Example</a>
        <a href="https://example.com/title/nope">Ignore</a>
        '''
        genres, titles = _internal_links(html, "https://aryplus.tv/")
        self.assertEqual(genres, {
            "https://aryplus.tv/browse/genre/abc123",
            "https://aryplus.tv/browse/genre/def456",
        })
        self.assertEqual(titles, {"https://aryplus.tv/title/title123"})

    def test_extract_video_sources_from_escaped_page_data(self):
        html = r'''{"videoSource":"https:\/\/vod.example.test\/movie\/master.m3u8","other":"x"}'''
        result = _extract_video_sources(html)
        self.assertEqual(result, ["https://vod.example.test/movie/master.m3u8"])


    def test_episode_catalogue_nested_and_escaped(self):
        html = r'''<script type="application/json">{"props":{"pageProps":{"episodes":[
          {"_id":"ep2","seriesId":"abc","videoEpNumber":2,"title":"Episode 2",
           "videoSource":"https://vod.example/2.m3u8",
           "thumbnail":{"url":"https://images.example/ep2.webp"}},
          {"_id":"ep1","seriesId":"abc","videoEpNumber":1,"title":"Episode 1",
           "videoSource":"https:\/\/vod.example\/1.m3u8",
           "thumbnailUrl":"https://images.example/ep1.webp"}
        ]}}}</script>'''
        result = _extract_episode_catalogue(html, "abc")
        self.assertEqual([x["_id"] for x in result], ["ep2", "ep1"])
        self.assertEqual(normalise_episode(result[0])["thumbnail"], "https://images.example/ep2.webp")

    def test_discovery_keeps_untyped_title_cards(self):
        html = '<a href="/title/unknown123">A Drama Without Marker</a>'
        result = _series_from_html(html, "https://aryplus.tv/browse/genre/test")
        self.assertEqual(result[0]["id"], "unknown123")
        self.assertEqual(result[0]["content_type"], "Series")

    def test_safe_filename(self):
        self.assertEqual(
            safe_filename('Episode 16: Dar/Nijaat?'),
            "Episode-16-Dar-Nijaat",
        )


if __name__ == "__main__":
    unittest.main()
