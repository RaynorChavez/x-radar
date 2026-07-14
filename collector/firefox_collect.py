#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

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
const body = text('[data-testid="tweetText"]');
const fullText = root.innerText || "";

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

const articleAnchor = [...root.querySelectorAll('a[href*="/i/article/"]')][0];
const articleMatch = articleAnchor?.href?.match(/\/i\/article\/(\d+)/);
const article = articleMatch ? {
  id: articleMatch[1], url: articleAnchor.href,
  title: articleAnchor.innerText?.trim() || body.slice(0, 160) || "X Article",
  description: null, publisher: author,
  imageUrl: root.querySelector('[data-testid="card.layoutLarge.media"] img, [data-testid="card.layoutSmall.media"] img')?.src || null,
} : null;

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
  xcancel_url: articleMatch ? `https://xcancel.com/i/article/${articleMatch[1]}` : `https://xcancel.com/${rawHandle}/status/${postId}`,
  external_links: externalLinks, media, article,
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
    parser.add_argument("--output", required=True)
    parser.add_argument("--profile", default="var/firefox-profile")
    parser.add_argument("--geckodriver", default=str(Path.home() / ".local/bin/geckodriver"))
    parser.add_argument("--firefox", default="/usr/bin/firefox")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--max-scrolls", type=int, default=30)
    parser.add_argument("--max-minutes", type=int, default=25)
    parser.add_argument("--request-id")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    captured_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    deadline = time.monotonic() + args.max_minutes * 60
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
    scrolls = 0
    stagnant = 0
    try:
        driver.get(args.target)
        try:
            WebDriverWait(driver, 30).until(
                lambda d: d.find_elements(By.CSS_SELECTOR, 'article[data-testid="tweet"]')
                or d.find_elements(By.CSS_SELECTOR, '[data-testid="SideNav_AccountSwitcher_Button"]')
            )
        except TimeoutException as error:
            if "/login" in driver.current_url or "/i/flow/login" in driver.current_url:
                raise RuntimeError("AUTH_REQUIRED: the persistent Pi Firefox profile is not signed in to X") from error
            raise RuntimeError(f"TIMELINE_UNAVAILABLE: no authenticated X surface at {driver.current_url}") from error

        while len(posts) < args.limit and scrolls <= args.max_scrolls and time.monotonic() < deadline:
            try:
                WebDriverWait(driver, 30).until(
                    lambda d: d.find_elements(By.CSS_SELECTOR, 'article[data-testid="tweet"]')
                )
            except TimeoutException as error:
                raise RuntimeError(f"TIMELINE_UNAVAILABLE: no posts appeared at {driver.current_url}") from error

            before = len(posts)
            for element in driver.find_elements(By.CSS_SELECTOR, 'article[data-testid="tweet"]'):
                if len(posts) >= args.limit:
                    break
                try:
                    post = driver.execute_script(EXTRACT_POST, element, captured_at)
                    if post and post.get("url") not in posts:
                        posts[post["url"]] = post
                except (JavascriptException, StaleElementReferenceException):
                    continue

            if len(posts) >= args.limit or scrolls == args.max_scrolls or time.monotonic() >= deadline:
                break
            stagnant = stagnant + 1 if len(posts) == before else 0
            driver.execute_script("window.scrollBy(0, Math.max(window.innerHeight * 0.82, 700))")
            scrolls += 1
            time.sleep(3 if stagnant >= 2 else 1.5)

        envelope = {
            "scan_id": str(uuid.uuid4()),
            "captured_at": captured_at,
            "host": os.environ.get("XRADAR_HOST", "pi"),
            "source": "x-account" if "/with_replies" in args.target else "x-home",
            "target": args.target,
            "request_id": args.request_id,
            "collector": {
                "target": args.target, "scrolls": scrolls, "limit": args.limit,
                "max_scrolls": args.max_scrolls, "max_minutes": args.max_minutes,
            },
            "posts": list(posts.values()),
            "account_signals": [],
        }
        output.write_text(json.dumps(envelope, indent=2) + "\n")
        print(json.dumps({"output": str(output), "observed": len(posts), "scrolls": scrolls, "target": args.target}))
        return 0
    finally:
        driver.quit()


if __name__ == "__main__":
    raise SystemExit(main())
