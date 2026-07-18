#!/usr/bin/env python3
"""네이버 게임 라운지의 공개 게시글과 이미지를 저장하는 크롤러."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlparse, urlunparse
from urllib.request import Request, urlopen


API_BASE = "https://comm-api.game.naver.com/nng_main/v1"
WEB_BASE = "https://game.naver.com"
DEFAULT_LOUNGE_ID = "COUNTERSIDE"
KST = timezone(timedelta(hours=9), name="KST")

DEFAULT_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Origin": WEB_BASE,
    "Referer": f"{WEB_BASE}/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
}

IMAGE_SUFFIXES = {
    ".avif",
    ".bmp",
    ".gif",
    ".jpeg",
    ".jpg",
    ".png",
    ".svg",
    ".webp",
}


class CrawlError(RuntimeError):
    """사용자에게 설명할 수 있는 크롤링 오류."""


def _unique(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        value = value.strip()
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _normalise_text(chunks: Iterable[str]) -> str:
    joined = "".join(chunks).replace("\u200b", "").replace("\ufeff", "")
    lines = []
    for line in joined.splitlines():
        cleaned = re.sub(r"[ \t\r\f\v]+", " ", line).strip()
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def _looks_like_image(url: str) -> bool:
    parsed = urlparse(url)
    suffix = Path(unquote(parsed.path)).suffix.lower()
    return suffix in IMAGE_SUFFIXES or "-phinf.pstatic.net" in parsed.netloc


class _BodyHTMLParser(HTMLParser):
    """게시글 HTML에서 본문 텍스트, 이미지, 링크만 추출한다."""

    BLOCK_TAGS = {
        "article",
        "blockquote",
        "div",
        "figcaption",
        "figure",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "li",
        "p",
        "pre",
        "section",
        "table",
        "td",
        "th",
        "tr",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text_chunks: list[str] = []
        self.images: list[str] = []
        self.links: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in {"script", "style"}:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return

        attributes = dict(attrs)
        if tag == "img" and attributes.get("src"):
            self.images.append(urljoin(WEB_BASE, attributes["src"] or ""))
        elif tag == "a" and attributes.get("href"):
            href = attributes["href"] or ""
            if href and href != "#" and not href.lower().startswith("javascript:"):
                self.links.append(urljoin(WEB_BASE, href))

        if tag == "br" or tag in self.BLOCK_TAGS:
            self.text_chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style"}:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if not self._skip_depth and tag in self.BLOCK_TAGS:
            self.text_chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.text_chunks.append(data)


@dataclass(frozen=True)
class ParsedContent:
    content_type: str
    text: str
    images: list[str]
    links: list[str]


def _parse_json_content(data: Any) -> ParsedContent:
    text_chunks: list[str] = []
    images: list[str] = []
    links: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, list):
            for child in node:
                walk(child)
            return
        if not isinstance(node, dict):
            return

        content_type = node.get("@ctype") or node.get("ctype")
        if content_type == "textNode":
            value = node.get("value")
            if isinstance(value, str):
                text_chunks.append(value)
            return

        source = node.get("src")
        if isinstance(source, str) and source.startswith(("http://", "https://")):
            if content_type == "image" or _looks_like_image(source):
                images.append(source)
            else:
                links.append(source)

        for key in ("link", "url", "href"):
            value = node.get(key)
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                if _looks_like_image(value):
                    images.append(value)
                else:
                    links.append(value)

        if content_type == "paragraph":
            walk(node.get("nodes", []))
            text_chunks.append("\n")
            return

        for key, value in node.items():
            if key not in {"src", "link", "url", "href", "value", "nodes"}:
                walk(value)
        if "value" in node:
            walk(node["value"])
        if "nodes" in node:
            walk(node["nodes"])

    walk(data)
    return ParsedContent(
        content_type="json",
        text=_normalise_text(text_chunks),
        images=_unique(images),
        links=_unique(links),
    )


def parse_content(raw_content: Any) -> ParsedContent:
    """JSON형/HTML형 스마트에디터 본문을 공통 형식으로 변환한다."""

    if raw_content is None:
        return ParsedContent("empty", "", [], [])

    if isinstance(raw_content, (dict, list)):
        return _parse_json_content(raw_content)

    raw_text = str(raw_content).strip()
    if not raw_text:
        return ParsedContent("empty", "", [], [])

    if raw_text[0] in "[{":
        try:
            return _parse_json_content(json.loads(raw_text))
        except json.JSONDecodeError:
            pass

    parser = _BodyHTMLParser()
    parser.feed(raw_text)
    parser.close()
    return ParsedContent(
        content_type="html",
        text=_normalise_text(parser.text_chunks),
        images=_unique(parser.images),
        links=_unique(parser.links),
    )


def parse_naver_datetime(value: Any) -> str | None:
    if not value:
        return None
    raw = str(value)
    try:
        parsed = datetime.strptime(raw, "%Y%m%d%H%M%S").replace(tzinfo=KST)
    except ValueError:
        return raw
    return parsed.isoformat()


def _raw_content_as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def normalise_feed(item: dict[str, Any], *, pinned: bool = False) -> dict[str, Any]:
    """네이버 API의 글 한 건을 저장하기 쉬운 평면 구조로 바꾼다."""

    feed = item.get("feed") or item
    user = item.get("user") or {}
    comment = item.get("comment") or {}
    lounge = item.get("lounge") or {}
    board = item.get("board") or {}
    buff = item.get("buff") or {}
    feed_link = item.get("feedLink") or {}
    raw_content = feed.get("contents", "")
    parsed = parse_content(raw_content)

    feed_id = int(feed.get("feedId") or item.get("feedId") or 0)
    lounge_id = str(feed.get("loungeId") or feed.get("originalLoungeId") or "")
    post_url = feed_link.get("pc") or (
        f"{WEB_BASE}/lounge/{lounge_id}/board/detail/{feed_id}"
    )

    return {
        "feed_id": feed_id,
        "lounge_id": lounge_id,
        "lounge_name": lounge.get("loungeName", ""),
        "board_id": board.get("boardId"),
        "board_name": board.get("boardName", ""),
        "title": feed.get("title", ""),
        "author": user.get("nickname", ""),
        "author_level": user.get("level"),
        "author_id_hash": user.get("userIdHash", ""),
        "created_at": parse_naver_datetime(feed.get("createdDate")),
        "updated_at": parse_naver_datetime(feed.get("updatedDate")),
        "view_count": item.get("readCount", 0),
        "comment_count": comment.get("totalCount", comment.get("commentCount", 0)),
        "buff_count": buff.get("buffCount", feed.get("buff", 0)),
        "nerf_count": buff.get("nerfCount", feed.get("nerf", 0)),
        "is_pinned": bool(pinned or feed.get("pinned")),
        "is_hidden_by_cleanbot": bool(feed.get("hideByCleanBot")),
        "representative_image_url": feed.get("repImageUrl", ""),
        "content_type": parsed.content_type,
        "content_text": parsed.text,
        "content_raw": _raw_content_as_text(raw_content),
        "images": [{"url": url} for url in parsed.images],
        "links": parsed.links,
        "url": post_url,
    }


class LoungeClient:
    def __init__(
        self,
        *,
        timeout: float = 20.0,
        retries: int = 3,
        delay: float = 0.5,
    ) -> None:
        self.timeout = timeout
        self.retries = retries
        self.delay = delay
        self.last_total_count: int | None = None

    def _fetch_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{API_BASE}{path}"
        if params:
            clean_params = {key: value for key, value in params.items() if value is not None}
            url = f"{url}?{urlencode(clean_params)}"

        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            request = Request(url, headers=DEFAULT_HEADERS)
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    payload = json.loads(response.read().decode("utf-8-sig"))
                if payload.get("code") != 200:
                    raise CrawlError(
                        f"네이버 API 오류: code={payload.get('code')}, "
                        f"message={payload.get('message')}"
                    )
                return payload.get("content")
            except HTTPError as error:
                last_error = error
                retryable = error.code == 429 or 500 <= error.code < 600
                if not retryable or attempt >= self.retries:
                    break
                retry_after = error.headers.get("Retry-After")
                wait = float(retry_after) if retry_after and retry_after.isdigit() else 2**attempt
                time.sleep(wait)
            except (URLError, TimeoutError, json.JSONDecodeError) as error:
                last_error = error
                if attempt >= self.retries:
                    break
                time.sleep(2**attempt)

        raise CrawlError(f"요청 실패: {url}\n원인: {last_error}") from last_error

    def fetch_pins(self, lounge_id: str, board_id: int | None = None) -> list[dict[str, Any]]:
        params = {"boardId": board_id} if board_id is not None else None
        content = self._fetch_json(
            f"/community/lounge/{lounge_id}/feed/pins",
            params,
        )
        return content if isinstance(content, list) else []

    def iter_feeds(
        self,
        lounge_id: str,
        *,
        board_id: int | None = None,
        order: str = "NEW",
        page_size: int = 30,
    ) -> Iterator[dict[str, Any]]:
        page = 0
        collected = 0
        while True:
            content = self._fetch_json(
                f"/community/lounge/{lounge_id}/feed",
                {
                    "offset": page,
                    "limit": page_size,
                    "order": order.upper(),
                    "boardId": board_id,
                    "buffFilteringYN": "N",
                },
            )
            if not isinstance(content, dict):
                raise CrawlError("게시글 목록 응답 형식이 예상과 다릅니다.")

            feeds = content.get("feeds") or []
            self.last_total_count = content.get("totalCount")
            if not feeds:
                return

            yield from feeds
            count = int(content.get("count") or len(feeds))
            collected += count
            if count <= 0 or (
                self.last_total_count is not None and collected >= self.last_total_count
            ):
                return
            page += 1
            if self.delay:
                time.sleep(self.delay)

    def crawl(
        self,
        lounge_id: str,
        *,
        board_id: int | None = None,
        order: str = "NEW",
        max_posts: int | None = 30,
        page_size: int = 30,
        include_pins: bool = True,
    ) -> list[dict[str, Any]]:
        posts: list[dict[str, Any]] = []
        seen_ids: set[int] = set()

        def add(item: dict[str, Any], *, pinned: bool) -> bool:
            post = normalise_feed(item, pinned=pinned)
            feed_id = post["feed_id"]
            if not feed_id or feed_id in seen_ids:
                return False
            seen_ids.add(feed_id)
            posts.append(post)
            return True

        if include_pins:
            for item in self.fetch_pins(lounge_id, board_id):
                add(item, pinned=True)
                if max_posts is not None and len(posts) >= max_posts:
                    return posts[:max_posts]

        for item in self.iter_feeds(
            lounge_id,
            board_id=board_id,
            order=order,
            page_size=page_size,
        ):
            add(item, pinned=False)
            if max_posts is not None and len(posts) >= max_posts:
                break
        return posts


def _safe_name(value: str, fallback: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", value).strip(" .")
    return cleaned[:120] or fallback


def _download_extension(url: str, content_type: str) -> str:
    suffix = Path(unquote(urlparse(url).path)).suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return ".jpg" if suffix == ".jpeg" else suffix
    content_type = content_type.split(";", 1)[0].lower()
    return {
        "image/avif": ".avif",
        "image/gif": ".gif",
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/svg+xml": ".svg",
        "image/webp": ".webp",
    }.get(content_type, ".bin")


def build_title_folder_names(posts: Sequence[dict[str, Any]]) -> dict[int, str]:
    """글 제목을 Windows에서 사용할 수 있는 중복 없는 폴더명으로 바꾼다."""

    names: dict[int, str] = {}
    used: set[str] = set()
    for post in posts:
        feed_id = int(post["feed_id"])
        base = _safe_name(str(post.get("title") or ""), str(feed_id))
        candidate = base
        if candidate.casefold() in used:
            candidate = _safe_name(f"{base} ({feed_id})", str(feed_id))
        suffix = 2
        while candidate.casefold() in used:
            candidate = _safe_name(f"{base} ({feed_id}-{suffix})", str(feed_id))
            suffix += 1
        used.add(candidate.casefold())
        names[feed_id] = candidate
    return names


def image_filename(url: str, index: int, total: int, content_type: str = "") -> str:
    """이미지 수와 관계없이 1, 2, 3 형식의 파일명을 만든다."""

    extension = _download_extension(url, content_type)
    return f"{index}{extension}"


def _without_resize_query(url: str) -> str:
    """pstatic 이미지의 표시 크기(type)만 제거해 원본 주소를 만든다."""

    parsed = urlparse(url)
    if "pstatic.net" not in parsed.netloc:
        return url
    query = urlencode([(key, value) for key, value in parse_qsl(parsed.query) if key != "type"])
    return urlunparse(parsed._replace(query=query))


def download_images(
    posts: list[dict[str, Any]],
    output_dir: Path,
    *,
    timeout: float,
    delay: float,
    retries: int = 3,
    title_folders: bool = False,
) -> tuple[int, int]:
    downloaded = 0
    failed = 0
    image_root = output_dir / "images"
    title_folder_names = build_title_folder_names(posts) if title_folders else {}

    for post in posts:
        feed_id = post["feed_id"]
        total_images = len(post["images"])
        for index, image in enumerate(post["images"], start=1):
            url = image["url"]
            download_url = _without_resize_query(url)
            parsed_name = Path(unquote(urlparse(download_url).path)).name
            stem = _safe_name(Path(parsed_name).stem, f"image_{index:03d}")
            target_dir = (
                output_dir / title_folder_names[feed_id]
                if title_folders
                else image_root / str(feed_id)
            )

            last_error: Exception | None = None
            for attempt in range(retries + 1):
                try:
                    request = Request(download_url, headers=DEFAULT_HEADERS)
                    with urlopen(request, timeout=timeout) as response:
                        data = response.read()
                        content_type = response.headers.get("Content-Type", "")
                    target_dir.mkdir(parents=True, exist_ok=True)
                    if title_folders:
                        filename = image_filename(download_url, index, total_images, content_type)
                    else:
                        suffix = _download_extension(download_url, content_type)
                        filename = f"{index:03d}_{stem}{suffix}"
                    target = target_dir / filename
                    target.write_bytes(data)
                    image["original_url"] = download_url
                    image["local_path"] = target.relative_to(output_dir).as_posix()
                    image.pop("download_error", None)
                    downloaded += 1
                    last_error = None
                    break
                except (HTTPError, URLError, TimeoutError, OSError) as error:
                    last_error = error
                    if attempt < retries:
                        time.sleep(2**attempt)
            if last_error is not None:
                image["download_error"] = str(last_error)
                failed += 1
            if delay:
                time.sleep(delay)

    return downloaded, failed


CSV_FIELDS = [
    "feed_id",
    "lounge_id",
    "lounge_name",
    "board_id",
    "board_name",
    "title",
    "author",
    "author_level",
    "created_at",
    "updated_at",
    "view_count",
    "comment_count",
    "buff_count",
    "nerf_count",
    "is_pinned",
    "content_type",
    "content_text",
    "image_urls",
    "links",
    "url",
]


def write_outputs(
    posts: Sequence[dict[str, Any]],
    output_dir: Path,
    *,
    lounge_id: str,
    output_format: str,
    metadata: dict[str, Any],
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    base_name = f"{_safe_name(lounge_id, 'lounge')}_posts"
    written: list[Path] = []

    if output_format in {"json", "both"}:
        json_path = output_dir / f"{base_name}.json"
        payload = {"metadata": metadata, "posts": list(posts)}
        json_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        written.append(json_path)

    if output_format in {"csv", "both"}:
        csv_path = output_dir / f"{base_name}.csv"
        with csv_path.open("w", encoding="utf-8-sig", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=CSV_FIELDS)
            writer.writeheader()
            for post in posts:
                row = {key: post.get(key, "") for key in CSV_FIELDS}
                row["image_urls"] = "\n".join(image["url"] for image in post["images"])
                row["links"] = "\n".join(post["links"])
                writer.writerow(row)
        written.append(csv_path)

    return written


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="네이버 게임 라운지의 공개 게시글을 JSON/CSV로 저장합니다.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--lounge", default=DEFAULT_LOUNGE_ID, help="라운지 ID")
    parser.add_argument("--board-id", type=int, help="특정 게시판 ID만 수집")
    parser.add_argument("--max-posts", type=int, default=30, help="고정글 포함 최대 글 수")
    parser.add_argument("--all", action="store_true", help="모든 공개 글 수집")
    parser.add_argument("--order", default="NEW", help="정렬 방식(API 값, 예: NEW)")
    parser.add_argument("--page-size", type=int, default=30, help="API 요청당 글 수(1~30)")
    parser.add_argument("--no-pins", action="store_true", help="상단 고정글 제외")
    parser.add_argument("--output-dir", type=Path, default=Path("output"), help="저장 폴더")
    parser.add_argument(
        "--format",
        choices=("json", "csv", "both"),
        default="both",
        dest="output_format",
        help="저장 형식",
    )
    parser.add_argument("--download-images", action="store_true", help="본문 이미지를 원본 크기로 저장")
    parser.add_argument(
        "--title-folders",
        action="store_true",
        help="게시글 제목 폴더 안에 1, 2, 3 이름으로 이미지 저장",
    )
    parser.add_argument(
        "--images-only",
        action="store_true",
        help="JSON/CSV 없이 이미지 폴더만 저장",
    )
    parser.add_argument("--delay", type=float, default=0.5, help="요청 사이 대기 시간(초)")
    parser.add_argument("--timeout", type=float, default=20.0, help="요청 제한 시간(초)")
    parser.add_argument("--retries", type=int, default=3, help="일시적 실패 재시도 횟수")
    parser.add_argument("--dry-run", action="store_true", help="파일 저장 없이 수집과 파싱만 확인")
    return parser


def _validate_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if args.max_posts <= 0:
        parser.error("--max-posts는 1 이상이어야 합니다.")
    if not 1 <= args.page_size <= 30:
        parser.error("--page-size는 1~30 사이여야 합니다.")
    if args.delay < 0:
        parser.error("--delay는 0 이상이어야 합니다.")
    if args.timeout <= 0:
        parser.error("--timeout은 0보다 커야 합니다.")
    if args.retries < 0:
        parser.error("--retries는 0 이상이어야 합니다.")
    if args.title_folders and not args.download_images:
        parser.error("--title-folders는 --download-images와 함께 사용해야 합니다.")
    if args.images_only and not args.download_images:
        parser.error("--images-only는 --download-images와 함께 사용해야 합니다.")


def run(args: argparse.Namespace) -> int:
    client = LoungeClient(timeout=args.timeout, retries=args.retries, delay=args.delay)
    max_posts = None if args.all else args.max_posts

    print(
        f"수집 시작: 라운지={args.lounge}, "
        f"게시판={args.board_id if args.board_id is not None else '전체'}"
    )
    posts = client.crawl(
        args.lounge,
        board_id=args.board_id,
        order=args.order,
        max_posts=max_posts,
        page_size=args.page_size,
        include_pins=not args.no_pins,
    )
    image_count = sum(len(post["images"]) for post in posts)
    print(f"수집 완료: 게시글 {len(posts)}개, 본문 이미지 {image_count}개")

    for post in posts[:5]:
        pin = "[고정] " if post["is_pinned"] else ""
        print(f"- {pin}{post['feed_id']} | {post['title']} | {post['author']}")

    if args.dry_run:
        print("드라이런이므로 파일은 저장하지 않았습니다.")
        return 0

    downloaded = failed = 0
    if args.download_images:
        downloaded, failed = download_images(
            posts,
            args.output_dir,
            timeout=args.timeout,
            delay=args.delay,
            retries=args.retries,
            title_folders=args.title_folders,
        )
        print(f"이미지 저장: 성공 {downloaded}개, 실패 {failed}개")

    metadata = {
        "source": f"{WEB_BASE}/lounge/{args.lounge}/board",
        "api_base": API_BASE,
        "lounge_id": args.lounge,
        "board_id": args.board_id,
        "order": args.order.upper(),
        "include_pins": not args.no_pins,
        "collected_at": datetime.now(KST).isoformat(),
        "collected_count": len(posts),
        "available_regular_posts": client.last_total_count,
        "downloaded_images": downloaded,
        "failed_images": failed,
    }
    written = []
    if not args.images_only:
        written = write_outputs(
            posts,
            args.output_dir,
            lounge_id=args.lounge,
            output_format=args.output_format,
            metadata=metadata,
        )
    for path in written:
        print(f"저장 완료: {path.resolve()}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
    parser = build_parser()
    args = parser.parse_args(argv)
    _validate_args(parser, args)
    try:
        return run(args)
    except KeyboardInterrupt:
        print("\n사용자가 작업을 중단했습니다.", file=sys.stderr)
        return 130
    except CrawlError as error:
        print(f"오류: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
