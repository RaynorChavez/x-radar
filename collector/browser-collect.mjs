#!/usr/bin/env node

import { chromium } from "playwright-core";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";

function arg(name, fallback = null) {
  const index = process.argv.indexOf(name);
  return index >= 0 ? process.argv[index + 1] : fallback;
}

function has(name) {
  return process.argv.includes(name);
}

const target = arg("--target", "https://x.com/home");
const output = arg("--output", `var/inbox/raw-${Date.now()}.json`);
const profile = arg("--profile", "var/browser-profile");
const executable = arg("--chromium", process.env.CHROMIUM_PATH || "/usr/bin/chromium");
const wanted = Number(arg("--limit", "100"));
const maxScrolls = Number(arg("--max-scrolls", "30"));
const maxMinutes = Number(arg("--max-minutes", "25"));
const headed = has("--headed");
const loginOnly = has("--login-only");
const capturedAt = new Date().toISOString();
const deadline = Date.now() + maxMinutes * 60_000;

await mkdir(path.dirname(output), { recursive: true });
await mkdir(profile, { recursive: true });

const context = await chromium.launchPersistentContext(profile, {
  executablePath: executable,
  headless: !headed,
  viewport: { width: 1440, height: 1100 },
  locale: "en-AU",
  args: [
    "--disable-blink-features=AutomationControlled",
    "--disable-dev-shm-usage",
    "--no-first-run",
    "--no-default-browser-check",
  ],
});

const page = context.pages()[0] || await context.newPage();
page.setDefaultTimeout(15_000);

try {
  await page.goto(target, { waitUntil: "domcontentloaded", timeout: 60_000 });

  if (loginOnly) {
    console.log("LOGIN_BROWSER_READY");
    console.log("Sign in to X in this Chromium window, then close the browser.");
    await new Promise((resolve) => context.on("close", resolve));
    process.exit(0);
  }

  await page.waitForTimeout(5_000);
  const url = page.url();
  const loginVisible = await page.locator('input[autocomplete="username"], a[href="/login"], a[href*="/i/flow/login"]').count();
  if (/\/i\/flow\/login|\/login/.test(url) || loginVisible) {
    throw new Error("AUTH_REQUIRED: the persistent Pi Chromium profile is not signed in to X");
  }

  const posts = new Map();
  let scrolls = 0;
  let stagnant = 0;

  while (posts.size < wanted && scrolls <= maxScrolls && Date.now() < deadline) {
    try {
      await page.waitForSelector('article[data-testid="tweet"]', { timeout: 30_000 });
    } catch (error) {
      const currentUrl = page.url();
      const pageText = (await page.locator("body").innerText().catch(() => "")).slice(0, 2_000);
      const title = await page.title().catch(() => "");
      await mkdir("var/log", { recursive: true });
      await page.screenshot({ path: "var/log/browser-diagnostic.png", fullPage: false }).catch(() => {});
      if (/log in|sign in|create account|join today|happening now|continue with google/i.test(pageText) || /\/i\/flow\/login|\/login/.test(currentUrl)) {
        throw new Error("AUTH_REQUIRED: the persistent Pi Chromium profile is not signed in to X");
      }
      throw new Error(`TIMELINE_UNAVAILABLE: no posts appeared at ${currentUrl} (title=${JSON.stringify(title)}, body_chars=${pageText.length}): ${error.message}`);
    }
    const articles = page.locator('article[data-testid="tweet"]');
    const count = await articles.count();
    const before = posts.size;

    for (let i = 0; i < count && posts.size < wanted; i += 1) {
      const article = articles.nth(i);
      try {
        const post = await article.evaluate((root, capturedAtValue) => {
          const text = (selector) => root.querySelector(selector)?.innerText?.trim() || "";
          const statusLinks = [...root.querySelectorAll('a[href*="/status/"]')]
            .map((a) => a.href)
            .filter((href) => /\/status\/\d+/.test(href));
          const timeLink = root.querySelector('time')?.closest('a')?.href;
          const primary = timeLink && /\/status\/\d+/.test(timeLink) ? timeLink : statusLinks[0];
          if (!primary) return null;
          const match = primary.match(/x\.com\/([^/]+)\/status\/(\d+)/i);
          if (!match) return null;
          const [, rawHandle, postId] = match;
          const canonical = `https://x.com/${rawHandle}/status/${postId}`;

          const userName = text('[data-testid="User-Name"]');
          const userLines = userName.split("\n").map((s) => s.trim()).filter(Boolean);
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

          const media = [];
          const seenMedia = new Set();
          for (const img of root.querySelectorAll('[data-testid="tweetPhoto"] img')) {
            if (img.src && !seenMedia.has(img.src)) {
              seenMedia.add(img.src);
              media.push({ type: "image", url: img.src, preview_url: null, alt: img.alt || null });
            }
          }
          for (const video of root.querySelectorAll("video")) {
            const rawVideoUrl = video.currentSrc || video.src;
            const mediaUrl = /^https?:/i.test(rawVideoUrl || "") ? rawVideoUrl : video.poster;
            if (mediaUrl && !seenMedia.has(mediaUrl)) {
              seenMedia.add(mediaUrl);
              media.push({ type: "video", url: mediaUrl, preview_url: video.poster || null, alt: null });
            }
          }

          const externalLinks = [];
          const seenLinks = new Set();
          for (const anchor of root.querySelectorAll("a[href]")) {
            const href = anchor.href;
            if (!/^https?:/i.test(href) || /(^|\.)x\.com\//i.test(new URL(href).hostname + "/") || /twitter\.com\//i.test(href)) continue;
            if (seenLinks.has(href)) continue;
            seenLinks.add(href);
            let domain = null;
            try { domain = new URL(href).hostname.replace(/^www\./, ""); } catch {}
            externalLinks.push({ url: href, title: anchor.innerText?.trim() || null, domain, description: null });
          }

          const articleAnchor = [...root.querySelectorAll('a[href*="/i/article/"]')][0];
          const articleMatch = articleAnchor?.href?.match(/\/i\/article\/(\d+)/);
          const article = articleMatch ? {
            id: articleMatch[1],
            url: articleAnchor.href,
            title: articleAnchor.innerText?.trim() || body.slice(0, 160) || "X Article",
            description: null,
            publisher: author,
            image_url: root.querySelector('[data-testid="card.layoutLarge.media"] img, [data-testid="card.layoutSmall.media"] img')?.src || null,
          } : null;

          const quotedStatus = statusLinks.find((href) => {
            const m = href.match(/\/status\/(\d+)/);
            return m && m[1] !== postId;
          });
          const isReply = /Replying to\s+@/i.test(fullText);
          const isAd = /(^|\n)(Ad|Promoted)(\n|$)/i.test(fullText);
          const visibleTime = root.querySelector("time")?.getAttribute("datetime") || root.querySelector("time")?.innerText || null;

          return {
            post_id: postId,
            url: canonical,
            handle: `@${rawHandle}`,
            author,
            profile_image_url: avatar && /^https:\/\//.test(avatar) ? avatar : null,
            text: body,
            posted_at: visibleTime,
            captured_at: capturedAtValue,
            is_ad: isAd,
            is_reply: isReply,
            is_quote: Boolean(quotedStatus),
            source_url: externalLinks[0]?.url || null,
            xcancel_url: articleMatch
              ? `https://xcancel.com/i/article/${articleMatch[1]}`
              : `https://xcancel.com/${rawHandle}/status/${postId}`,
            external_links: externalLinks,
            media,
            article,
            engagement: {
              replies: metric("reply", "repl"),
              reposts: metric("retweet", "repost"),
              likes: metric("like", "like"),
              bookmarks: metric("bookmark", "bookmark"),
              views: numberFrom(root.querySelector('a[href$="/analytics"]')?.getAttribute("aria-label") || root.querySelector('a[href$="/analytics"]')?.innerText || ""),
            },
          };
        }, capturedAt);
        if (post && !posts.has(post.url)) posts.set(post.url, post);
      } catch {
        // A virtualized tweet may disappear while it is being read; the next pass catches it.
      }
    }

    if (posts.size >= wanted || scrolls === maxScrolls || Date.now() >= deadline) break;
    stagnant = posts.size === before ? stagnant + 1 : 0;
    await page.evaluate(() => window.scrollBy(0, Math.max(window.innerHeight * 0.82, 700)));
    scrolls += 1;
    await page.waitForTimeout(stagnant >= 2 ? 3_000 : 1_500);
  }

  const envelope = {
    captured_at: capturedAt,
    host: process.env.XRADAR_HOST || "pi",
    source: { target, scrolls, limit: wanted, max_scrolls: maxScrolls, max_minutes: maxMinutes },
    posts: [...posts.values()],
    account_signals: [],
  };
  await writeFile(output, `${JSON.stringify(envelope, null, 2)}\n`, "utf8");
  console.log(JSON.stringify({ output, observed: posts.size, scrolls, target }));
} finally {
  if (!loginOnly) await context.close();
}
