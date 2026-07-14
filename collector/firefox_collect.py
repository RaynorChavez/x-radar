#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from selenium import webdriver
from selenium.common.exceptions import JavascriptException, StaleElementReferenceException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.firefox.service import Service
from selenium.webdriver.support.ui import WebDriverWait


EXTRACT_POST = r"""
const root = arguments[0], capturedAtValue = arguments[1];
const text = (selector) => root.querySelector(selector)?.innerText?.trim() || "";
const statusLinks = [...root.querySelectorAll('a[href*="/status/"]')]
  .map((a) => a.href).filter((href) => /\/status\/\d+/.test(href));
const timeLink = root.querySelector('time')?.closest('a')?.href;
const primary = timeLink && /\/status\/\d+/.test(timeLink) ? timeLink : statusLinks[0];
if (!primary) return null;
const match = primary.match(/x\.com\/([^/]+)\/status\/(\d+)/i);
if (!match) return null;
const rawHandle = match[1], postId = match[2];
const canonical = `https://x.com/${rawHandle}/status/${postId}`;

const userLines = text('[data-testid="User-Name"]').split("\n").map((s) => s.trim()).filter(Boolean);
const author = userLines.find((line) => !line.startsWith("@") && !/^·$/.test(line)) || rawHandle;
const avatar = root.querySelector('[data-testid="Tweet-User-Avatar"] img')?.src || null;
const tweetBody = text('[data-testid="tweetText"]');
const fullText = root.innerText || "";
const needsTextHydration = Boolean(root.querySelector('[data-testid="tweet-text-show-more-link"]'))
  || /(^|\n)Show more(\n|$)/i.test(fullText);

const numberFrom = (value) => {
  if (!value) return 0;
  const token = String(value).match(/[\d,.]+[KMB]?/i)?.[0];
  if (!token) return 0;
  const suffix = token.slice(-1).toUpperCase();
  const multiplier = suffix === "K" ? 1e3 : suffix === "M" ? 1e6 : suffix === "B" ? 1e9 : 1;
  const numeric = Number(token.replace(/[KMB,]/gi, ""));
  return Number.isFinite(numeric) ? Math.round(numeric * multiplier) : 0;
};
const metric = (testid, word) => {
  const node = root.querySelector(`[data-testid="${testid}"]`);
  const label = node?.getAttribute("aria-label") || node?.innerText || "";
  const wordMatch = label.match(new RegExp(`([\\d,.]+[KMB]?)\\s+${word}`, "i"));
  return numberFrom(wordMatch?.[1] || label);
};

const media = [], seenMedia = new Set();
for (const img of root.querySelectorAll('[data-testid="tweetPhoto"] img')) {
  if (img.src && !seenMedia.has(img.src)) {
    seenMedia.add(img.src);
    media.push({ type: "image", url: img.src, previewUrl: null, alt: img.alt || null });
  }
}
for (const video of root.querySelectorAll("video")) {
  const rawVideoUrl = video.currentSrc || video.src;
  const mediaUrl = /^https?:/i.test(rawVideoUrl || "") ? rawVideoUrl : video.poster;
  if (mediaUrl && !seenMedia.has(mediaUrl)) {
    seenMedia.add(mediaUrl);
    media.push({ type: "video", url: mediaUrl, previewUrl: video.poster || null, alt: null });
  }
}

const externalLinks = [], seenLinks = new Set();
for (const anchor of root.querySelectorAll("a[href]")) {
  const href = anchor.href;
  if (!/^https?:/i.test(href)) continue;
  let parsed;
  try { parsed = new URL(href); } catch { continue; }
  if (/(^|\.)x\.com$/i.test(parsed.hostname) || /(^|\.)twitter\.com$/i.test(parsed.hostname)) continue;
  if (seenLinks.has(href)) continue;
  seenLinks.add(href);
  externalLinks.push({
    url: href,
    title: anchor.innerText?.trim() || null,
    domain: parsed.hostname.replace(/^www\./, ""),
    description: null,
  });
}

const legacyArticleAnchor = [...root.querySelectorAll('a[href*="/i/article/"]')][0];
const legacyArticleMatch = legacyArticleAnchor?.href?.match(/\/i\/article\/(\d+)/);
const articleView = root.querySelector('[data-testid="twitterArticleReadView"]');
const articlePreview = root.querySelector('[data-testid="article-cover-image"]');
const articleId = legacyArticleMatch?.[1] || (articleView ? postId : null);
const articleTitle = text('[data-testid="twitter-article-title"]')
  || legacyArticleAnchor?.innerText?.trim() || "X Article";
const rawArticleContent = text('[data-testid="twitterArticleRichTextView"]')
  || text('[data-testid="longformRichTextComponent"]');
// X's article reader commonly separates semantic blocks with a single newline.
// Persist those blocks explicitly so downstream readers do not flatten an
// entire essay into one paragraph.
const articleContent = rawArticleContent.split(/\n+/)
  .map((value) => value.trim()).filter(Boolean).join("\n\n");
const articleExcerpt = articleContent.slice(0, 1200);
const articleUrl = legacyArticleAnchor?.href || (articleId ? `https://x.com/${rawHandle}/article/${articleId}` : null);
const articleXcancelUrl = articleId
  ? `https://xcancel.com/i/article/${articleId}`
  : `https://xcancel.com/${rawHandle}/status/${postId}`;
const article = articleId ? {
  id: articleId, url: articleUrl, xcancelUrl: articleXcancelUrl,
  title: articleTitle,
  description: articleExcerpt.slice(0, 500) || null,
  content: articleContent.slice(0, 12000) || null,
  publisher: author,
  imageUrl: root.querySelector('[data-testid="twitterArticleReadView"] [data-testid="tweetPhoto"] img, [data-testid="card.layoutLarge.media"] img, [data-testid="card.layoutSmall.media"] img')?.src || null,
} : null;
const body = tweetBody || (article ? [articleTitle, articleExcerpt].filter(Boolean).join("\n\n") : "");

const quotedStatus = statusLinks.find((href) => {
  const found = href.match(/\/status\/(\d+)/);
  return found && found[1] !== postId;
});
const visibleTime = root.querySelector("time")?.getAttribute("datetime") || root.querySelector("time")?.innerText || null;
return {
  post_id: postId, url: canonical, handle: `@${rawHandle}`, author,
  profile_image_url: avatar && /^https:\/\//.test(avatar) ? avatar : null,
  text: body, posted_at: visibleTime, captured_at: capturedAtValue,
  is_ad: /(^|\n)(Ad|Promoted)(\n|$)/i.test(fullText),
  is_reply: /Replying to\s+@/i.test(fullText), is_quote: Boolean(quotedStatus),
  source_url: externalLinks[0]?.url || null,
  xcancel_url: article?.xcancelUrl || `https://xcancel.com/${rawHandle}/status/${postId}`,
  external_links: externalLinks, media, article,
  _needs_article_hydration: Boolean(articlePreview && !articleView),
  _needs_text_hydration: needsTextHydration,
  // Timeline virtualization occasionally yields a shell with a valid status
  // ID but none of the body/article content. Revisit that exact status page
  // before allowing the observation to be ranked.
  _needs_content_hydration: !tweetBody && !articleContent,
  engagement: {
    replies: metric("reply", "repl"), reposts: metric("retweet", "repost"),
    likes: metric("like", "like"), bookmarks: metric("bookmark", "bookmark"),
    views: numberFrom(root.querySelector('a[href$="/analytics"]')?.getAttribute("aria-label") || root.querySelector('a[href$="/analytics"]')?.innerText || ""),
  },
};
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only X timeline capture through Firefox")
    parser.add_argument("--target", default="https://x.com/home")
    parser.add_argument("--plan", help="Version 2 multi-source collection plan")
    parser.add_argument("--output", required=True)
    parser.add_argument("--profile", default="var/firefox-profile")
    parser.add_argument(
        "--geckodriver",
        default=os.environ.get("XRADAR_GECKODRIVER", str(Path.home() / ".local/bin/geckodriver")),
    )
    parser.add_argument("--firefox", default=os.environ.get("XRADAR_FIREFOX", "/usr/bin/firefox"))
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--max-scrolls", type=int, default=30)
    parser.add_argument("--max-minutes", type=int, default=25)
    parser.add_argument("--request-id")
    return parser.parse_args()


def validate_target(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.hostname not in {"x.com", "www.x.com"}:
        raise ValueError("collector targets must use https://x.com")
    path = parsed.path.rstrip("/") or "/"
    parts = [part for part in path.split("/") if part]
    allowed = path in {"/home", "/search"}
    allowed = allowed or (len(parts) == 1 and parts[0].replace("_", "").isalnum() and len(parts[0]) <= 15)
    allowed = allowed or (
        len(parts) == 2 and parts[1] == "with_replies"
        and parts[0].replace("_", "").isalnum() and len(parts[0]) <= 15
    )
    if not allowed:
        raise ValueError(f"unsupported X collection target: {value}")
    return value


def validate_post_detail_target(value: str, expected_post_id: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.hostname not in {"x.com", "www.x.com"}:
        raise ValueError("article hydration targets must use https://x.com")
    parts = [part for part in parsed.path.split("/") if part]
    valid_handle = len(parts) == 3 and parts[0].replace("_", "").isalnum() and len(parts[0]) <= 15
    if not valid_handle or parts[1] != "status" or not parts[2].isdigit() or parts[2] != str(expected_post_id):
        raise ValueError("article hydration target must match its captured post ID")
    return f"https://x.com/{parts[0]}/status/{parts[2]}"


def validate_xcancel_article_target(article_id: str) -> str:
    value = str(article_id)
    if not value.isdigit():
        raise ValueError("XCancel article IDs must be numeric")
    return f"https://xcancel.com/i/article/{value}"


class _XCancelArticleParser(HTMLParser):
    """Extract inert long-form text from the one allowlisted XCancel article page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.article_depth = 0
        self.ignore_depth = 0
        self.capture_tag: str | None = None
        self.capture_depth = 0
        self.buffer: list[str] = []
        self.title = ""
        self.blocks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())
        if tag == "article" and "article-body" in classes:
            self.article_depth = 1
            return
        if not self.article_depth:
            return
        self.article_depth += 1
        if self.ignore_depth:
            return
        if "article-author" in classes:
            self.ignore_depth = self.article_depth
            return
        if self.capture_tag is None and tag in {"h1", "h2", "h3", "p", "li", "blockquote"}:
            self.capture_tag = tag
            self.capture_depth = self.article_depth
            self.buffer = []

    def handle_endtag(self, tag: str) -> None:
        if not self.article_depth:
            return
        if self.ignore_depth:
            if self.article_depth == self.ignore_depth:
                self.ignore_depth = 0
            self.article_depth -= 1
            return
        if self.capture_tag == tag and self.capture_depth == self.article_depth:
            value = " ".join("".join(self.buffer).split())
            if value:
                if tag == "h1" and not self.title:
                    self.title = value
                elif tag != "h1":
                    self.blocks.append(value)
            self.capture_tag = None
            self.capture_depth = 0
            self.buffer = []
        self.article_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.article_depth and self.capture_tag is not None:
            self.buffer.append(data)


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        raise ValueError("XCancel article fallback redirects are not allowed")


def fetch_xcancel_article(article_id: str, timeout: float = 15.0) -> dict[str, str] | None:
    """Bounded fallback used only when X did not expose a captured article body."""
    target = validate_xcancel_article_target(article_id)
    request = Request(target, headers={
        "Accept": "text/html,application/xhtml+xml",
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0",
    })
    with build_opener(_RejectRedirects).open(request, timeout=timeout) as response:
        final = urlparse(response.geturl())
        if final.scheme != "https" or final.hostname != "xcancel.com" or final.path != f"/i/article/{article_id}":
            raise ValueError("XCancel article fallback redirected outside its allowlisted article")
        raw = response.read(262145)
        if len(raw) > 262144:
            raise ValueError("XCancel article fallback exceeded 256 KiB")
        content_type = response.headers.get_content_type()
        if content_type not in {"text/html", "application/xhtml+xml"}:
            raise ValueError("XCancel article fallback returned a non-HTML document")
    parser = _XCancelArticleParser()
    parser.feed(raw.decode("utf-8", errors="replace"))
    content = "\n\n".join(parser.blocks).strip()
    if not parser.title or not content:
        return None
    return {"title": parser.title[:500], "content": content[:12000]}


def legacy_kind(value: str) -> str:
    path = urlparse(value).path.rstrip("/") or "/"
    return "home" if path == "/home" else "account"


def load_plan(path: str) -> dict:
    plan = json.loads(Path(path).read_text())
    if int(plan.get("schema_version", 0)) != 2 or plan.get("mode") != "mixed":
        raise ValueError("collection plan must be a version 2 mixed plan")
    if int(plan.get("target_unique", 0)) != 150:
        raise ValueError("mixed collection plans must target exactly 150 unique posts")
    for target in [*plan.get("targets", []), *plan.get("backfill_targets", [])]:
        validate_target(str(target.get("url", "")))
        quota = int(target.get("quota", 0))
        if quota < 0 or quota > 150:
            raise ValueError("target quota is outside the allowed range")
    if sum(int(item.get("quota", 0)) for item in plan.get("targets", [])) != 150:
        raise ValueError("planned target quotas must sum to 150")
    return plan


def _source_ref(target: dict) -> dict:
    return {
        key: target[key] for key in ("id", "kind", "topic_key", "topic", "handle", "query")
        if target.get(key) is not None
    } | {"acquisition_id": target["id"]}


def collect_target(driver, target: dict, posts: dict[str, dict], captured_at: str,
                   global_deadline: float, global_target: int, max_scrolls: int) -> dict:
    started = time.monotonic()
    url = validate_target(str(target["url"]))
    quota = int(target.get("quota", 0))
    if target.get("backfill"):
        quota = max(0, global_target - len(posts))
    result = {**target, "observed": 0, "unique": 0, "scrolls": 0, "status": "complete", "error": None}
    if quota <= 0 or len(posts) >= global_target:
        result["duration_seconds"] = 0
        return result
    per_target_deadline = min(global_deadline, time.monotonic() + 6 * 60)
    seen_on_target: set[str] = set()
    source = _source_ref(target)
    stagnant = 0
    try:
        driver.get(url)
    except TimeoutException as error:
        # A single slow search/account page must not abort the complete mixed
        # period. Stop the partial navigation and let later/backfill targets run.
        try:
            driver.execute_script("window.stop()")
        except JavascriptException:
            pass
        current_url = str(getattr(driver, "current_url", ""))
        if "/login" in current_url or "/i/flow/login" in current_url:
            raise RuntimeError("AUTH_REQUIRED: the persistent Pi Firefox profile is not signed in to X") from error
        result.update(status="error", error=f"NAVIGATION_TIMEOUT: page load exceeded 60 seconds at {url}")
        result["duration_seconds"] = round(time.monotonic() - started)
        return result
    try:
        WebDriverWait(driver, 20).until(
            lambda d: d.find_elements(By.CSS_SELECTOR, 'article[data-testid="tweet"]')
            or d.find_elements(By.CSS_SELECTOR, '[data-testid="SideNav_AccountSwitcher_Button"]')
        )
    except TimeoutException as error:
        if "/login" in driver.current_url or "/i/flow/login" in driver.current_url:
            raise RuntimeError("AUTH_REQUIRED: the persistent Pi Firefox profile is not signed in to X") from error
        result.update(status="error", error=f"TIMELINE_UNAVAILABLE: no authenticated X surface at {driver.current_url}")
        result["duration_seconds"] = round(time.monotonic() - started)
        return result

    while result["unique"] < quota and len(posts) < global_target and result["scrolls"] <= max_scrolls and time.monotonic() < per_target_deadline:
        try:
            elements = WebDriverWait(driver, 30).until(
                lambda d: d.find_elements(By.CSS_SELECTOR, 'article[data-testid="tweet"]')
            )
        except TimeoutException:
            result.update(status="error", error=f"TIMELINE_UNAVAILABLE: no posts appeared at {driver.current_url}")
            break
        before = len(seen_on_target)
        for element in elements:
            if result["unique"] >= quota or len(posts) >= global_target:
                break
            try:
                post = driver.execute_script(EXTRACT_POST, element, captured_at)
            except (JavascriptException, StaleElementReferenceException):
                continue
            if not post or not post.get("post_id"):
                continue
            post_id = str(post["post_id"])
            if post_id in seen_on_target:
                continue
            seen_on_target.add(post_id)
            result["observed"] += 1
            if post_id not in posts:
                post["discovery_sources"] = [source]
                posts[post_id] = post
                result["unique"] += 1
            elif not any(item.get("acquisition_id") == target["id"] for item in posts[post_id].get("discovery_sources", [])):
                posts[post_id].setdefault("discovery_sources", []).append(source)
        stagnant = stagnant + 1 if len(seen_on_target) == before else 0
        if result["unique"] >= quota or len(posts) >= global_target or result["scrolls"] == max_scrolls or time.monotonic() >= per_target_deadline:
            break
        driver.execute_script("window.scrollBy(0, Math.max(window.innerHeight * 0.82, 700))")
        result["scrolls"] += 1
        time.sleep(2.5 if stagnant >= 2 else 1.2)
    result["duration_seconds"] = round(time.monotonic() - started)
    return result


def report_mixed_progress(plan: dict, acquisitions: list[dict], observed: int) -> None:
    """Best-effort progress only; collection remains authoritative when Sites is offline."""
    try:
        source_root = str(Path(__file__).resolve().parents[1] / "src")
        if source_root not in sys.path:
            sys.path.insert(0, source_root)
        from xradar.site_client import report_progress
        progress = {
            item["id"]: {
                "kind": item["kind"], "topic": item.get("topic"),
                "planned": item.get("quota", 0), "observed": item.get("observed", 0),
                "unique": item.get("unique", 0), "status": item.get("status", "complete"),
            }
            for item in acquisitions
        }
        report_progress({
            "phase": "collecting", "target": "mixed", "requestId": plan.get("request_id"),
            "scanId": plan["scan_id"], "observed": observed, "targetUnique": plan["target_unique"],
            "preferenceVersion": plan.get("preference_version", 0), "sourceProgress": progress,
        })
    except Exception:
        pass


def hydrate_post_details(driver, posts: dict[str, dict], captured_at: str,
                         global_deadline: float, limit: int = 24) -> int:
    candidates: list[tuple[int, dict]] = []
    for post in posts.values():
        needs_article = bool(post.pop("_needs_article_hydration", False))
        needs_text = bool(post.pop("_needs_text_hydration", False))
        needs_content = bool(post.pop("_needs_content_hydration", False))
        if needs_article or needs_text or needs_content:
            # Article previews first, then entirely empty shells, then ordinary
            # truncated posts. The bound and shared deadline prevent unbounded
            # status-page fan-out.
            priority = 0 if needs_article else 1 if needs_content else 2
            candidates.append((priority, post))
    candidates = sorted(candidates, key=lambda item: item[0])[:limit]
    hydrated = 0
    for _, post in candidates:
        remaining = global_deadline - time.monotonic()
        if remaining <= 1:
            break
        enriched = None
        try:
            url = validate_post_detail_target(str(post["url"]), str(post["post_id"]))
            driver.get(url)
            elements = WebDriverWait(driver, min(15, max(1, remaining))).until(
                lambda d: d.find_elements(By.CSS_SELECTOR, 'article[data-testid="tweet"]')
            )
            for element in elements:
                try:
                    candidate = driver.execute_script(EXTRACT_POST, element, captured_at)
                except (JavascriptException, StaleElementReferenceException):
                    continue
                if candidate and str(candidate.get("post_id")) == str(post["post_id"]):
                    enriched = candidate
                    break
        except (TimeoutException, ValueError):
            pass
        has_complete_article = bool(enriched and (enriched.get("article") or {}).get("content"))
        has_longer_text = bool(enriched and len(str(enriched.get("text") or "")) > len(str(post.get("text") or "")))
        if enriched and (has_complete_article or has_longer_text):
            discovery_sources = post.get("discovery_sources", [])
            post.update(enriched)
            post["discovery_sources"] = discovery_sources
            post.pop("_needs_article_hydration", None)
            post.pop("_needs_text_hydration", None)
            post.pop("_needs_content_hydration", None)
            hydrated += 1
            continue

        article = post.get("article") if isinstance(post.get("article"), dict) else {}
        article_id = str(article.get("id") or "")
        if not article_id or global_deadline - time.monotonic() <= 1:
            continue
        try:
            fallback = fetch_xcancel_article(article_id, timeout=min(15, max(1, global_deadline - time.monotonic())))
        except (OSError, TimeoutError, ValueError):
            fallback = None
        if fallback:
            article.update({
                "title": fallback["title"],
                "content": fallback["content"],
                "description": fallback["content"][:500],
                "xcancelUrl": validate_xcancel_article_target(article_id),
            })
            post["article"] = article
            if not post.get("text"):
                post["text"] = f'{fallback["title"]}\n\n{fallback["content"][:1200]}'
            hydrated += 1
    return hydrated


def main() -> int:
    args = parse_args()
    captured_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    plan = load_plan(args.plan) if args.plan else None
    max_minutes = 30 if plan else args.max_minutes
    deadline = time.monotonic() + max_minutes * 60
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    options = Options()
    options.add_argument("-headless")
    options.binary_location = args.firefox
    options.profile = str(Path(args.profile).resolve())
    options.set_preference("dom.webnotifications.enabled", False)
    options.set_preference("media.autoplay.default", 5)
    driver = webdriver.Firefox(service=Service(args.geckodriver), options=options)
    driver.set_page_load_timeout(60)

    posts: dict[str, dict] = {}
    try:
        if plan:
            acquisitions = []
            for target in plan.get("targets", []):
                if time.monotonic() >= deadline or len(posts) >= plan["target_unique"]:
                    break
                acquisitions.append(collect_target(driver, target, posts, captured_at, deadline, plan["target_unique"], 8))
                report_mixed_progress(plan, acquisitions, len(posts))
            for target in plan.get("backfill_targets", []):
                if time.monotonic() >= deadline or len(posts) >= plan["target_unique"]:
                    break
                acquisitions.append(collect_target(driver, target, posts, captured_at, deadline, plan["target_unique"], 6))
                report_mixed_progress(plan, acquisitions, len(posts))
            articles_hydrated = hydrate_post_details(driver, posts, captured_at, deadline)
            envelope = {
                **plan, "captured_at": captured_at, "host": os.environ.get("XRADAR_HOST", "pi"),
                "source": "x-mixed", "target": None, "request_id": plan.get("request_id") or args.request_id,
                "collector": {"targets": len(acquisitions), "limit": plan["target_unique"], "max_minutes": max_minutes,
                              "articles_hydrated": articles_hydrated},
                "acquisitions": acquisitions, "posts": list(posts.values()), "account_signals": [],
            }
            summary = {"output": str(output), "observed": len(posts), "targetUnique": plan["target_unique"], "periodId": plan["period_id"], "acquisitions": len(acquisitions)}
        else:
            kind = legacy_kind(args.target)
            target = {"id": str(uuid.uuid4()), "kind": kind, "url": args.target, "quota": args.limit}
            acquisition = collect_target(driver, target, posts, captured_at, deadline, args.limit, args.max_scrolls)
            articles_hydrated = hydrate_post_details(driver, posts, captured_at, deadline)
            envelope = {
                "scan_id": str(uuid.uuid4()), "captured_at": captured_at,
                "host": os.environ.get("XRADAR_HOST", "pi"),
                "source": "x-account" if kind == "account" else "x-home",
                "target": args.target, "request_id": args.request_id,
                "collector": {"target": args.target, "scrolls": acquisition["scrolls"], "limit": args.limit,
                              "max_scrolls": args.max_scrolls, "max_minutes": args.max_minutes,
                              "articles_hydrated": articles_hydrated},
                # Detail hydration preserves each post's discovery source. Keep
                # the matching acquisition in legacy single-target envelopes so
                # provenance foreign keys remain valid during ingest.
                "acquisitions": [acquisition],
                "posts": list(posts.values()), "account_signals": [],
            }
            summary = {"output": str(output), "observed": len(posts), "scrolls": acquisition["scrolls"], "target": args.target}
        output.write_text(json.dumps(envelope, indent=2) + "\n")
        print(json.dumps(summary))
        return 0
    finally:
        driver.quit()


if __name__ == "__main__":
    raise SystemExit(main())
