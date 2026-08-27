import json
import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
import sys


CRAWLER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CRAWLER_DIR))

from naver_lounge_crawler import (  # noqa: E402
    _validate_args,
    assign_title_folder_names,
    build_parser,
    write_outputs,
)


class MetadataOnlyOptionTests(unittest.TestCase):
    def test_metadata_only_is_explicit_and_does_not_enable_downloads(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["--metadata-only"])

        _validate_args(parser, args)

        self.assertTrue(args.metadata_only)
        self.assertFalse(args.download_images)
        self.assertFalse(args.images_only)

    def test_metadata_only_rejects_image_download_mode(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["--metadata-only", "--download-images"])

        with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            _validate_args(parser, args)

    def test_metadata_output_contains_existing_title_folder_name(self) -> None:
        posts = [
            {
                "feed_id": 24,
                "title": "[웹툰] 제목: 테스트",
                "images": [{"url": "https://example.com/1.jpg"}],
                "links": [],
            }
        ]
        assign_title_folder_names(posts)

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = write_outputs(
                posts,
                Path(temp_dir),
                lounge_id="COUNTERSIDE",
                output_format="json",
                metadata={"metadata_only": True},
            )
            payload = json.loads(paths[0].read_text(encoding="utf-8"))

        self.assertEqual(payload["posts"][0]["title_folder"], "[웹툰] 제목_ 테스트")


if __name__ == "__main__":
    unittest.main()
