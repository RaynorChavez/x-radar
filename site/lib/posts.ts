export function parseJson<T>(value: unknown, fallback: T): T {
  try { return JSON.parse(String(value ?? "")) as T; } catch { return fallback; }
}

export function publicPost(row: Record<string, unknown>) {
  return {
    postId: row.post_id, url: row.url, handle: row.handle, author: row.author,
    profileImageUrl: row.profile_image_url, text: row.text,
    postedAt: row.posted_at, capturedAt: row.observation_captured_at ?? row.captured_at,
    firstSeenAt: row.first_seen_at ?? row.captured_at, lastSeenAt: row.last_seen_at ?? row.captured_at,
    score: row.observation_score ?? row.score, decision: row.observation_decision ?? row.decision,
    scoreComponents: parseJson(row.observation_score_components_json ?? row.score_components_json, null),
    isAd: Boolean(row.observation_is_ad ?? row.is_ad),
    isReply: Boolean(row.observation_is_reply ?? row.is_reply),
    isQuote: Boolean(row.observation_is_quote ?? row.is_quote),
    reasons: parseJson(row.reasons_json, []), engagement: parseJson(row.engagement_json, {}),
    xcancelUrl: row.xcancel_url ?? `https://xcancel.com/${String(row.handle).replace(/^@/, "")}/status/${row.post_id}`,
    externalLinks: parseJson(row.external_links_json, []), media: parseJson(row.media_json, []),
    article: parseJson(row.article_json, null), accountDisposition: row.account_disposition ?? "normal",
    saved: Boolean(row.saved_at), pinned: Boolean(row.pinned_at), dismissed: Boolean(row.dismissed_at),
  };
}

export function dateInZone(value: string, timeZone: string) {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone, year: "numeric", month: "2-digit", day: "2-digit",
  }).format(new Date(value));
}
