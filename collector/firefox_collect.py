#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

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
    parser.add_argument("--plan", help="Version 2 multi-source collection plan")
    parser.add_argument("--output", required=True)
    parser.add_argument("--profile", default="var/firefox-profile")
    parser.add_argument("--geckodriver", default=str(Path.home() / ".local/bin/geckodriver"))
    parser.add_argument("--firefox", default="/usr/bin/firefox")
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
    driver.get(url)
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
            envelope = {
                **plan, "captured_at": captured_at, "host": os.environ.get("XRADAR_HOST", "pi"),
                "source": "x-mixed", "target": None, "request_id": plan.get("request_id") or args.request_id,
                "collector": {"targets": len(acquisitions), "limit": plan["target_unique"], "max_minutes": max_minutes},
                "acquisitions": acquisitions, "posts": list(posts.values()), "account_signals": [],
            }
            summary = {"output": str(output), "observed": len(posts), "targetUnique": plan["target_unique"], "periodId": plan["period_id"], "acquisitions": len(acquisitions)}
        else:
            kind = legacy_kind(args.target)
            target = {"id": str(uuid.uuid4()), "kind": kind, "url": args.target, "quota": args.limit}
            acquisition = collect_target(driver, target, posts, captured_at, deadline, args.limit, args.max_scrolls)
            envelope = {
                "scan_id": str(uuid.uuid4()), "captured_at": captured_at,
                "host": os.environ.get("XRADAR_HOST", "pi"),
                "source": "x-account" if kind == "account" else "x-home",
                "target": args.target, "request_id": args.request_id,
                "collector": {"target": args.target, "scrolls": acquisition["scrolls"], "limit": args.limit,
                              "max_scrolls": args.max_scrolls, "max_minutes": args.max_minutes},
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
