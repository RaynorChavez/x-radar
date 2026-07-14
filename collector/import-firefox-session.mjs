#!/usr/bin/env node

import { chromium } from "playwright-core";
import { spawnSync } from "node:child_process";
import process from "node:process";

function arg(name, fallback) {
  const index = process.argv.indexOf(name);
  return index >= 0 ? process.argv[index + 1] : fallback;
}

const firefoxCookies = arg("--firefox-cookies", "var/firefox-profile/cookies.sqlite");
const chromiumProfile = arg("--chromium-profile", "var/browser-profile");
const executable = arg("--chromium", process.env.CHROMIUM_PATH || "/usr/bin/chromium");

const extractor = String.raw`
import json, sqlite3, sys
db = sqlite3.connect(sys.argv[1])
rows = db.execute("""
  SELECT name, value, host, path, expiry, isSecure, isHttpOnly, sameSite
  FROM moz_cookies
  WHERE host LIKE '%x.com' OR host LIKE '%twitter.com'
""").fetchall()
same_site = {0: "None", 1: "Lax", 2: "Strict"}
cookies = []
for name, value, host, path, expiry, secure, http_only, same in rows:
    cookies.append({
        "name": name,
        "value": value,
        "domain": host,
        "path": path or "/",
        "expires": int(expiry) if expiry and 0 < expiry <= 253402300799 else -1,
        "httpOnly": bool(http_only),
        "secure": bool(secure),
        "sameSite": same_site.get(same, "Lax"),
    })
print(json.dumps(cookies))
`;

const extracted = spawnSync("python3", ["-c", extractor, firefoxCookies], {
  encoding: "utf8",
  maxBuffer: 2 * 1024 * 1024,
});
if (extracted.status !== 0) throw new Error("Could not read the Firefox X session");

const cookies = JSON.parse(extracted.stdout);
if (!cookies.some((cookie) => cookie.name === "auth_token")) {
  throw new Error("AUTH_REQUIRED: Firefox has no X auth_token cookie");
}

const context = await chromium.launchPersistentContext(chromiumProfile, {
  executablePath: executable,
  headless: true,
  viewport: { width: 1280, height: 900 },
  args: ["--disable-dev-shm-usage"],
});

try {
  await context.addCookies(cookies);
  const page = context.pages()[0] || await context.newPage();
  await page.goto("https://x.com/home", { waitUntil: "domcontentloaded", timeout: 60_000 });
  await page.waitForTimeout(5_000);
  const authenticated = await page.locator(
    '[data-testid="SideNav_AccountSwitcher_Button"], article[data-testid="tweet"]',
  ).count();
  if (!authenticated || /\/i\/flow\/login|\/login/.test(page.url())) {
    throw new Error("SESSION_TRANSFER_FAILED: Chromium did not accept the Firefox X session");
  }
  console.log(JSON.stringify({ imported: cookies.length, authenticated: true }));
} finally {
  await context.close();
}
