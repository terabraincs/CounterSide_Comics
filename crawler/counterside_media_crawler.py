#!/usr/bin/env python3
"""카운터사이드 공식 홈페이지의 공개 미디어와 원본 이미지를 저장한다."""

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
from typing import Any, Iterable, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urljoin, urlparse
from urllib.request import Request, urlopen


BASE_URL = "https://www.counterside.com"
DEFAULT_LIST_URL = f"{BASE_URL}/media/lists/ct/jp/tbl/media/cate/guiwt"
SCRIPT_DIR = Path(__file__).resolve().parent


def find_comics_root(script_dir: Path) -> Path:
    """스크립트가 CounterSide_Comics 안팎 어디에 있어도 같은 저장 기준을 반환한다."""

    if script_dir.name.casefold() == "counterside_comics":
        return script_dir
    return script_dir / "CounterSide_Comics"


COMICS_ROOT = find_comics_root(SCRIPT_DIR)
DEFAULT_OUTPUT_DIR = Path("output")
KST = timezone(timedelta(hours=9), name="KST")

SOURCE_PATTERN = re.compile(
    r"/media/lists/ct/(?P<locale>[^/]+)/tbl/media/cate/(?P<category>[^/]+)"
)

DEFAULT_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ja,en;q=0.8,ko;q=0.7",
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

VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}


class CrawlError(RuntimeError):
    """사용자에게 설명할 수 있는 크롤링 오류."""


@dataclass(frozen=True)
class FetchResult:
    url: str
    data: bytes
    content_type: str


@dataclass(frozen=True)
class MediaSource:
    locale: str
    category: str
    base_name: str


@dataclass(frozen=True)
class ParsedMediaList:
    items: list[dict[str, Any]]
    page_urls: list[str]


def infer_media_source(list_url: str) -> MediaSource:
    """목록 주소에서 언어와 분류를 읽어 결과 파일명을 만든다."""

    match = SOURCE_PATTERN.search(urlparse(list_url).path)
    locale = (match.group("locale") if match else "unknown").lower()
    category = (match.group("category") if match else "media").lower()
    if category == "guiwt":
        base_name = f"counterside_{locale}_guide_webtoons"
    else:
        safe_category = re.sub(r"[^a-z0-9_-]+", "_", category).strip("_") or "media"
        base_name = f"counterside_{locale}_{safe_category}_media"
    return MediaSource(locale, category, base_name)


def resolve_output_dir(value: Path) -> Path:
    """상대 출력 경로를 CounterSide_Comics 폴더 아래의 절대 경로로 바꾼다."""

    if value.is_absolute():
        return value.resolve()

    resolved = (COMICS_ROOT / value).resolve()
    if not resolved.is_relative_to(COMICS_ROOT):
        raise CrawlError("상대 --output-dir은 CounterSide_Comics 폴더 밖을 가리킬 수 없습니다.")
    return resolved


def _unique(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        value = value.strip()
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _normalise_inline(chunks: Iterable[str]) -> str:
    return re.sub(r"\s+", " ", "".join(chunks)).strip()


def _normalise_multiline(chunks: Iterable[str]) -> str:
    raw = "".join(chunks).replace("\u200b", "").replace("\ufeff", "")
    lines = []
    for line in raw.splitlines():
        cleaned = re.sub(r"[ \t\r\f\v]+", " ", line).strip()
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def _safe_name(value: str, fallback: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", value).strip(" .")
    return cleaned[:120] or fallback


def _image_extension(url: str, content_type: str = "") -> str:
    suffix = Path(unquote(urlparse(url).path)).suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return ".jpg" if suffix == ".jpeg" else suffix

    clean_type = content_type.split(";", 1)[0].strip().lower()
    return {
        "image/avif": ".avif",
        "image/bmp": ".bmp",
        "image/gif": ".gif",
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/svg+xml": ".svg",
        "image/webp": ".webp",
    }.get(clean_type, ".bin")


def image_filename(url: str, index: int, total: int, content_type: str = "") -> str:
    """이미지 개수와 관계없이 1, 2, 3 형식의 파일명을 만든다."""

    return f"{index}{_image_extension(url, content_type)}"


class MediaClient:
    def __init__(self, *, timeout: float = 20.0, retries: int = 3) -> None:
        self.timeout = timeout
        self.retries = retries

    def fetch(self, url: str, *, referer: str | None = None) -> FetchResult:
        headers = dict(DEFAULT_HEADERS)
        headers["Referer"] = referer or BASE_URL + "/"
        last_error: Exception | None = None

        for attempt in range(self.retries + 1):
            request = Request(url, headers=headers)
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    data = response.read()
                    content_type = response.headers.get("Content-Type", "")
                    final_url = response.geturl()
                return FetchResult(final_url, data, content_type)
            except HTTPError as error:
                last_error = error
                retryable = error.code == 429 or 500 <= error.code < 600
                if not retryable or attempt >= self.retries:
                    break
                retry_after = error.headers.get("Retry-After")
                try:
                    wait = float(retry_after) if retry_after else float(2**attempt)
                except ValueError:
                    wait = float(2**attempt)
                time.sleep(wait)
            except (URLError, TimeoutError, OSError) as error:
                last_error = error
                if attempt >= self.retries:
                    break
                time.sleep(2**attempt)

        raise CrawlError(f"요청 실패: {url}\n원인: {last_error}") from last_error

    def fetch_text(self, url: str, *, referer: str | None = None) -> str:
        result = self.fetch(url, referer=referer)
        charset_match = re.search(r"charset=([^;\s]+)", result.content_type, re.I)
        charset = charset_match.group(1).strip('"\'') if charset_match else "utf-8"
        try:
            return result.data.decode(charset)
        except (LookupError, UnicodeDecodeError):
            return result.data.decode("utf-8-sig", errors="replace")


class _MediaListParser(HTMLParser):
    ITEM_PATTERN = re.compile(r"/media/item/.+?/idx/(\d+)/P\d+$")
    PAGE_PATTERN = re.compile(r"^(?P<root>/media/lists/.+?/cate/[^/]+)(?:/P\d+)?/?$")

    def __init__(self, page_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.page_url = page_url
        self.items: list[dict[str, Any]] = []
        self.page_urls: list[str] = []
        self._current: dict[str, Any] | None = None
        self._text_chunks: list[str] = []
        parsed_page_url = urlparse(page_url)
        self._page_host = parsed_page_url.netloc.lower()
        page_match = self.PAGE_PATTERN.match(parsed_page_url.path)
        self._page_root = page_match.group("root") if page_match else parsed_page_url.path

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attributes = dict(attrs)

        if tag == "a" and self._current is None:
            href = attributes.get("href") or ""
            match = self.ITEM_PATTERN.search(urlparse(href).path)
            if match:
                self._current = {
                    "media_id": int(match.group(1)),
                    "url": urljoin(self.page_url, href),
                    "thumbnail_url": "",
                }
                self._text_chunks = []
            elif href and href != "#":
                absolute = urljoin(self.page_url, href)
                parsed = urlparse(absolute)
                page_match = self.PAGE_PATTERN.match(parsed.path)
                if (
                    parsed.netloc.lower() == self._page_host
                    and page_match
                    and page_match.group("root") == self._page_root
                    and re.search(r"/P\d+/?$", parsed.path)
                ):
                    self.page_urls.append(absolute)
            return

        if tag == "img" and self._current is not None:
            source = attributes.get("src") or attributes.get("data-src") or ""
            if source:
                self._current["thumbnail_url"] = urljoin(self.page_url, source)

    def handle_data(self, data: str) -> None:
        if self._current is not None:
            self._text_chunks.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self._current is None:
            return
        self._current["title"] = _normalise_inline(self._text_chunks)
        self.items.append(self._current)
        self._current = None
        self._text_chunks = []


class _MediaDetailParser(HTMLParser):
    def __init__(self, page_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.page_url = page_url
        self.subject_chunks: list[str] = []
        self.info_chunks: list[str] = []
        self.content_chunks: list[str] = []
        self.content_images: list[str] = []
        self.article_images: list[str] = []
        self._depth = 0
        self._article_depth: int | None = None
        self._subject_depth: int | None = None
        self._info_depth: int | None = None
        self._content_depth: int | None = None

    @staticmethod
    def _classes(attrs: list[tuple[str, str | None]]) -> set[str]:
        value = dict(attrs).get("class") or ""
        return set(value.split())

    def _inside_article(self) -> bool:
        return self._article_depth is not None and self._depth >= self._article_depth

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        next_depth = self._depth + 1
        classes = self._classes(attrs)

        if tag == "article" and self._article_depth is None:
            self._article_depth = next_depth

        if self._article_depth is not None and next_depth >= self._article_depth:
            if tag == "div" and "subject" in classes:
                self._subject_depth = next_depth
            elif tag == "div" and "info" in classes:
                self._info_depth = next_depth
            elif tag == "div" and "content" in classes:
                self._content_depth = next_depth

            if tag == "img":
                attributes = dict(attrs)
                source = (
                    attributes.get("src")
                    or attributes.get("data-src")
                    or attributes.get("data-original")
                    or ""
                )
                if source:
                    absolute = urljoin(self.page_url, source)
                    self.article_images.append(absolute)
                    if self._content_depth is not None:
                        self.content_images.append(absolute)

            if tag == "br":
                if self._subject_depth is not None:
                    self.subject_chunks.append("\n")
                if self._info_depth is not None:
                    self.info_chunks.append("\n")
                if self._content_depth is not None:
                    self.content_chunks.append("\n")

        if tag not in VOID_TAGS:
            self._depth = next_depth

    def handle_data(self, data: str) -> None:
        if not self._inside_article():
            return
        if self._subject_depth is not None:
            self.subject_chunks.append(data)
        if self._info_depth is not None:
            self.info_chunks.append(data)
        if self._content_depth is not None:
            self.content_chunks.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in VOID_TAGS:
            return

        if self._subject_depth == self._depth:
            self._subject_depth = None
        if self._info_depth == self._depth:
            self._info_depth = None
        if self._content_depth == self._depth:
            self._content_depth = None
        if self._article_depth == self._depth and tag == "article":
            self._article_depth = None

        self._depth = max(0, self._depth - 1)


def parse_media_list_page(
    html: str,
    page_url: str = DEFAULT_LIST_URL,
) -> ParsedMediaList:
    """목록 HTML에서 상세 링크, 썸네일, 실제 페이지 이동 링크를 추출한다."""

    parser = _MediaListParser(page_url)
    parser.feed(html)
    parser.close()

    result: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    for item in parser.items:
        media_id = int(item["media_id"])
        if media_id not in seen_ids:
            seen_ids.add(media_id)
            result.append(item)
    return ParsedMediaList(result, _unique(parser.page_urls))


def parse_media_list(html: str, page_url: str = DEFAULT_LIST_URL) -> list[dict[str, Any]]:
    """목록 HTML에서 상세 페이지 링크와 썸네일을 추출한다."""

    return parse_media_list_page(html, page_url).items


def collect_listing_items(
    client: MediaClient,
    list_url: str,
    *,
    max_items: int = 0,
    delay: float = 0.5,
) -> tuple[list[dict[str, Any]], int]:
    """목록에 노출된 페이지 링크를 따라가며 항목을 중복 없이 모은다."""

    def canonical_page_url(value: str) -> str:
        parsed = urlparse(value)
        path = re.sub(r"/P1/?$", "", parsed.path)
        return parsed._replace(path=path).geturl()

    initial_url = canonical_page_url(list_url)
    pending = [initial_url]
    queued = {initial_url}
    visited: set[str] = set()
    items: list[dict[str, Any]] = []
    seen_ids: set[int] = set()

    while pending:
        page_url = pending.pop(0)
        queued.discard(page_url)
        if page_url in visited:
            continue
        if visited and delay:
            time.sleep(delay)

        html = client.fetch_text(page_url, referer=initial_url)
        visited.add(page_url)
        page = parse_media_list_page(html, page_url)
        for item in page.items:
            media_id = int(item["media_id"])
            if media_id in seen_ids:
                continue
            seen_ids.add(media_id)
            items.append(item)
            if max_items and len(items) >= max_items:
                return items, len(visited)

        for discovered_url in page.page_urls:
            discovered_url = canonical_page_url(discovered_url)
            if discovered_url not in visited and discovered_url not in queued:
                pending.append(discovered_url)
                queued.add(discovered_url)

    return items, len(visited)


def parse_media_detail(
    html: str,
    *,
    page_url: str,
    listing_item: dict[str, Any],
) -> dict[str, Any]:
    """상세 HTML을 저장하기 쉬운 공통 형식으로 변환한다."""

    parser = _MediaDetailParser(page_url)
    parser.feed(html)
    parser.close()

    title = _normalise_inline(parser.subject_chunks) or str(listing_item.get("title") or "")
    info = _normalise_inline(parser.info_chunks)
    date_match = re.search(r"\b(\d{4})\.(\d{2})\.(\d{2})\b", info)

    author = ""
    published_date = ""
    published_time = ""
    if date_match:
        author = info[: date_match.start()].strip()
        published_date = "-".join(date_match.groups())
        published_time = info[date_match.end() :].strip()
    elif info:
        author = info

    images = _unique(parser.content_images or parser.article_images)
    return {
        "media_id": int(listing_item["media_id"]),
        "title": title,
        "author": author,
        "published_date": published_date,
        "published_time": published_time,
        "info_raw": info,
        "content_text": _normalise_multiline(parser.content_chunks),
        "thumbnail_url": str(listing_item.get("thumbnail_url") or ""),
        "images": [{"url": url} for url in images],
        "url": page_url,
    }


def build_title_folder_names(items: Sequence[dict[str, Any]]) -> dict[int, str]:
    """제목을 Windows에서 사용할 수 있는 중복 없는 폴더명으로 바꾼다."""

    names: dict[int, str] = {}
    used: set[str] = set()
    for item in items:
        media_id = int(item["media_id"])
        base = _safe_name(str(item.get("title") or ""), str(media_id))
        candidate = base
        if candidate.casefold() in used:
            candidate = _safe_name(f"{base} ({media_id})", str(media_id))
        suffix = 2
        while candidate.casefold() in used:
            candidate = _safe_name(f"{base} ({media_id}-{suffix})", str(media_id))
            suffix += 1
        used.add(candidate.casefold())
        names[media_id] = candidate
    return names


def download_images(
    items: list[dict[str, Any]],
    output_dir: Path,
    *,
    client: MediaClient,
    delay: float,
    overwrite: bool = False,
    title_folders: bool = False,
) -> tuple[int, int, int]:
    """상세 이미지를 선택한 폴더 구조로 저장하고 성공, 기존, 실패 개수를 반환한다."""

    downloaded = 0
    existing = 0
    failed = 0
    folder_names = build_title_folder_names(items) if title_folders else {}

    for item in items:
        media_id = int(item["media_id"])
        target_dir = (
            output_dir / folder_names[media_id]
            if title_folders
            else output_dir / "images" / str(media_id)
        )
        images = item["images"]
        for index, image in enumerate(images, start=1):
            url = image["url"]
            expected_target = target_dir / image_filename(url, index, len(images))
            if expected_target.is_file() and expected_target.stat().st_size > 0 and not overwrite:
                image["local_path"] = expected_target.relative_to(output_dir).as_posix()
                existing += 1
                continue

            try:
                result = client.fetch(url, referer=item["url"])
                target = target_dir / image_filename(
                    result.url,
                    index,
                    len(images),
                    result.content_type,
                )
                if target.is_file() and target.stat().st_size > 0 and not overwrite:
                    image["local_path"] = target.relative_to(output_dir).as_posix()
                    existing += 1
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(result.data)
                image["local_path"] = target.relative_to(output_dir).as_posix()
                image.pop("download_error", None)
                downloaded += 1
            except (CrawlError, OSError) as error:
                image["download_error"] = str(error)
                failed += 1
            finally:
                if delay:
                    time.sleep(delay)

    return downloaded, existing, failed


CSV_FIELDS = [
    "media_id",
    "title",
    "author",
    "published_date",
    "published_time",
    "thumbnail_url",
    "image_urls",
    "local_paths",
    "url",
]


def write_outputs(
    items: Sequence[dict[str, Any]],
    output_dir: Path,
    *,
    output_format: str,
    metadata: dict[str, Any],
    base_name: str = "counterside_jp_guide_webtoons",
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    if output_format in {"json", "both"}:
        json_path = output_dir / f"{base_name}.json"
        json_path.write_text(
            json.dumps({"metadata": metadata, "items": list(items)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        written.append(json_path)

    if output_format in {"csv", "both"}:
        csv_path = output_dir / f"{base_name}.csv"
        with csv_path.open("w", encoding="utf-8-sig", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=CSV_FIELDS)
            writer.writeheader()
            for item in items:
                row = {key: item.get(key, "") for key in CSV_FIELDS}
                row["image_urls"] = "\n".join(image["url"] for image in item["images"])
                row["local_paths"] = "\n".join(
                    image.get("local_path", "") for image in item["images"]
                )
                writer.writerow(row)
        written.append(csv_path)

    return written


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="카운터사이드 공식 홈페이지의 공개 미디어와 원본 이미지를 저장합니다.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--list-url", default=DEFAULT_LIST_URL, help="수집할 미디어 목록 주소")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="저장 폴더(CounterSide_Comics 기준 상대 경로 또는 절대 경로)",
    )
    parser.add_argument("--max-items", type=int, default=0, help="최대 항목 수(0이면 전체)")
    parser.add_argument(
        "--format",
        choices=("json", "csv", "both"),
        default="both",
        dest="output_format",
        help="목록 저장 형식",
    )
    parser.add_argument("--metadata-only", action="store_true", help="이미지는 받지 않고 목록만 저장")
    parser.add_argument(
        "--title-folders",
        action="store_true",
        help="미디어 제목 폴더 아래에 1, 2, 3 이름으로 이미지 저장",
    )
    parser.add_argument("--overwrite", action="store_true", help="이미 저장된 이미지도 다시 받기")
    parser.add_argument("--delay", type=float, default=0.5, help="요청 사이 대기 시간(초)")
    parser.add_argument("--timeout", type=float, default=20.0, help="요청 제한 시간(초)")
    parser.add_argument("--retries", type=int, default=3, help="일시적 실패 재시도 횟수")
    parser.add_argument("--dry-run", action="store_true", help="파일 저장 없이 목록과 상세 파싱만 확인")
    return parser


def _validate_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    parsed = urlparse(args.list_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        parser.error("--list-url은 http 또는 https 주소여야 합니다.")
    if args.max_items < 0:
        parser.error("--max-items는 0 이상이어야 합니다.")
    if args.delay < 0:
        parser.error("--delay는 0 이상이어야 합니다.")
    if args.timeout <= 0:
        parser.error("--timeout은 0보다 커야 합니다.")
    if args.retries < 0:
        parser.error("--retries는 0 이상이어야 합니다.")


def run(args: argparse.Namespace) -> int:
    client = MediaClient(timeout=args.timeout, retries=args.retries)
    source = infer_media_source(args.list_url)
    output_dir = resolve_output_dir(args.output_dir)
    print(f"목록 확인: {args.list_url}")
    listing_items, page_count = collect_listing_items(
        client,
        args.list_url,
        max_items=args.max_items,
        delay=args.delay,
    )
    if not listing_items:
        raise CrawlError("목록에서 미디어 상세 링크를 찾지 못했습니다.")
    print(f"목록 발견: {len(listing_items)}개 (목록 페이지 {page_count}개)")

    items: list[dict[str, Any]] = []
    for index, listing_item in enumerate(listing_items, start=1):
        if args.delay:
            time.sleep(args.delay)
        detail_url = listing_item["url"]
        detail_html = client.fetch_text(detail_url, referer=args.list_url)
        item = parse_media_detail(
            detail_html,
            page_url=detail_url,
            listing_item=listing_item,
        )
        items.append(item)
        print(f"[{index}/{len(listing_items)}] {item['title']} | 이미지 {len(item['images'])}개")

    image_count = sum(len(item["images"]) for item in items)
    print(f"상세 수집 완료: 항목 {len(items)}개, 원본 이미지 {image_count}개")
    if args.dry_run:
        print("드라이런이므로 파일은 저장하지 않았습니다.")
        return 0

    downloaded = existing = failed = 0
    if not args.metadata_only:
        downloaded, existing, failed = download_images(
            items,
            output_dir,
            client=client,
            delay=args.delay,
            overwrite=args.overwrite,
            title_folders=args.title_folders,
        )
        print(f"이미지 저장: 신규 {downloaded}개, 기존 {existing}개, 실패 {failed}개")

    metadata = {
        "source": args.list_url,
        "site": "CounterSide official website",
        "locale": source.locale,
        "category": source.category,
        "listing_page_count": page_count,
        "collected_at": datetime.now(KST).isoformat(),
        "collected_count": len(items),
        "image_count": image_count,
        "downloaded_images": downloaded,
        "existing_images": existing,
        "failed_images": failed,
        "title_folders": args.title_folders,
    }
    written = write_outputs(
        items,
        output_dir,
        output_format=args.output_format,
        metadata=metadata,
        base_name=source.base_name,
    )
    for path in written:
        print(f"목록 저장: {path.resolve()}")
    print(f"저장 폴더: {output_dir.resolve()}")
    return 1 if failed else 0


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
