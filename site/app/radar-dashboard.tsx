"use client";
/* eslint-disable @next/next/no-img-element -- collector media uses arbitrary remote origins */

import { CSSProperties, FormEvent, useEffect, useMemo, useState } from "react";
import {
  ArchiveIcon,
  BookmarkSimpleIcon,
  ChartBarIcon,
  GearSixIcon,
  HouseIcon,
  MagnifyingGlassIcon,
  SlidersHorizontalIcon,
  XIcon,
} from "@phosphor-icons/react";

export type RadarPost = {
  postId: string;
  url: string;
  handle: string;
  author: string | null;
  profileImageUrl: string | null;
  text: string;
  postedAt: string | null;
  capturedAt: string;
  firstSeenAt?: string;
  lastSeenAt?: string;
  score: number;
  decision: "keep" | "candidate" | "discard";
  isAd?: boolean;
  isReply?: boolean;
  isQuote?: boolean;
  reasons: string[];
  engagement: Record<string, number>;
  accountDisposition?: string;
  xcancelUrl: string;
  externalLinks: Array<{ url: string; title?: string; domain?: string; description?: string }>;
  media: Array<{
    type: "image" | "video" | "gif"; url: string; previewUrl?: string;
    alt?: string; width?: number; height?: number;
  }>;
  article: {
    id?: string; url?: string; title: string; description?: string;
    publisher?: string; imageUrl?: string;
  } | null;
  saved?: boolean;
  pinned?: boolean;
  dismissed?: boolean;
};

type Stats = { scanned: number; observations?: number; kept: number; candidates: number; discarded: number; authors: number };
type Reputation = { handle: string; disposition: string; strikePoints: number; confidence: number; notes?: string | null };
type FetchRequest = { id: string; handle: string; targetKind?: "mixed" | "account"; includeReplies: boolean; status: string; requestedAt: string; preferenceVersion?: number; bootstrapTopics?: string[]; resultCount?: number | null; error?: string | null };
type Range = "day" | "week" | "month" | "year" | "all";
type Surface = "briefing" | "signal" | "saved" | "history";
type SortMode = "signal" | "newest";
type RadarRun = {
  scanId: string; host: string; source: string; target?: string | null; requestId?: string | null;
  capturedAt: string; ingestedAt: string; postsSeen: number; postsKept: number; postsAdded: number;
  duplicates: number; candidates: number; discarded: number; signalsCount: number; mediaCount: number;
  linksCount: number; durationSeconds: number; status: string;
  schemaVersion?: number; periodId?: string | null; preferenceVersion?: number; targetUnique?: number;
};
type RunAcquisition = { acquisitionId: string; kind: string; target: string; topicKey?: string | null; topicLabel?: string | null; plannedQuota: number; observedCount: number; uniqueCount: number; status: string; error?: string | null; durationSeconds: number };
type PostObservation = { scanId: string; capturedAt: string; score: number; decision: string; source?: string; target?: string };
type CollectorStatus = {
  lastRun: null | { capturedAt: string; ingestedAt: string; postsSeen: number; postsKept: number };
  counts?: { uniquePosts: number; observations: number; runs: number };
  activity?: { phase: string; target?: string | null; observed?: number; targetUnique?: number; preferenceVersion?: number; sourceProgress?: Record<string, { kind: string; topic?: string | null; planned: number; observed: number; unique: number; status: string }>; updatedAt?: string; pendingSync?: number; lastSyncAt?: string; lastBackupAt?: string };
  warnings?: Array<{ code: string; level: "warning" | "critical"; message: string }>;
  reliability?: { successfulSlots: number; expectedSlots: number; percent: number };
};
type CuratorPreferences = { instructions: string; topics: string[]; preferenceVersion?: number; updatedAt?: string | null };
type UndoToast = { message: string; undo: () => Promise<void> };

const ranges: Array<{ id: Range; label: string; short: string }> = [
  { id: "day", label: "Past day", short: "D" },
  { id: "week", label: "Past week", short: "W" },
  { id: "month", label: "Past month", short: "M" },
  { id: "year", label: "Past year", short: "Y" },
  { id: "all", label: "All time", short: "∞" },
];

const dashboardTimeZone = "Australia/Melbourne";
const monthNumbers: Record<string, number> = {
  jan: 0, feb: 1, mar: 2, apr: 3, may: 4, jun: 5,
  jul: 6, aug: 7, sep: 8, oct: 9, nov: 10, dec: 11,
};

function resolvePublishedAt(postedAt: string | null, capturedAt: string) {
  if (!postedAt) return null;
  const captured = new Date(capturedAt);
  if (Number.isNaN(captured.getTime())) return null;

  if (/\d{4}|T\d{2}:\d{2}/.test(postedAt)) {
    const exact = new Date(postedAt);
    if (!Number.isNaN(exact.getTime())) return exact;
  }

  const relative = postedAt.trim().match(/^(\d+)\s*([smhd])$/i);
  if (relative) {
    const unitMs = { s: 1_000, m: 60_000, h: 3_600_000, d: 86_400_000 }[relative[2].toLowerCase()];
    return new Date(captured.getTime() - Number(relative[1]) * unitMs);
  }

  const shortDate = postedAt.trim().match(/^([A-Za-z]{3})\s+(\d{1,2})$/);
  const month = shortDate ? monthNumbers[shortDate[1].toLowerCase()] : undefined;
  if (shortDate && month !== undefined) {
    const inferred = new Date(captured);
    inferred.setUTCFullYear(captured.getUTCFullYear(), month, Number(shortDate[2]));
    inferred.setUTCHours(12, 0, 0, 0);
    if (inferred.getTime() > captured.getTime() + 86_400_000) inferred.setUTCFullYear(inferred.getUTCFullYear() - 1);
    return inferred;
  }
  return null;
}

function formatDateTime(value: Date, includeZone = false) {
  return new Intl.DateTimeFormat("en-AU", {
    timeZone: dashboardTimeZone,
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
    ...(includeZone ? { timeZoneName: "short" as const } : {}),
  }).format(value);
}

function publishedLabel(post: Pick<RadarPost, "postedAt" | "capturedAt">) {
  const published = resolvePublishedAt(post.postedAt, post.capturedAt);
  return published ? `published ${formatDateTime(published)}` : `published ${post.postedAt ?? "date unavailable"}`;
}

function fetchedLabel(capturedAt: string) {
  const fetched = new Date(capturedAt);
  return Number.isNaN(fetched.getTime()) ? "fetched date unavailable" : `fetched ${formatDateTime(fetched, true)}`;
}

function compact(value?: number) {
  if (!value) return "0";
  return Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 }).format(value);
}

function safeUrl(value?: string | null) {
  if (!value) return null;
  try {
    const url = new URL(value);
    return url.protocol === "https:" || url.protocol === "http:" ? url.toString() : null;
  } catch { return null; }
}

function displayDomain(link: RadarPost["externalLinks"][number]) {
  if (link.domain) return link.domain.replace(/^www\./, "");
  try { return new URL(link.url).hostname.replace(/^www\./, ""); } catch { return "external link"; }
}

function initials(post: Pick<RadarPost, "author" | "handle">) {
  const words = (post.author ?? "").trim().split(/\s+/).filter(Boolean);
  if (words.length > 1) return `${words[0][0]}${words.at(-1)![0]}`.toUpperCase();
  return (words[0]?.slice(0, 2) ?? post.handle.replace(/^@/, "").slice(0, 2)).toUpperCase();
}

function avatarHue(handle: string) {
  return [...handle].reduce((value, character) => (value * 31 + character.charCodeAt(0)) % 360, 0);
}

function effectiveScore(post: RadarPost) {
  const adjustment = post.accountDisposition === "allow" ? .05
    : post.accountDisposition === "watch" ? -.15
      : post.accountDisposition === "downrank" ? -.40 : 0;
  return post.score + adjustment;
}

function ProfileAvatar({ post }: { post: RadarPost }) {
  const [failed, setFailed] = useState(false);
  const image = safeUrl(post.profileImageUrl);
  const style = { "--avatar-hue": `${avatarHue(post.handle)}deg` } as CSSProperties;
  return (
    <span className="profile-avatar" style={style} aria-hidden="true">
      {image && !failed
        ? <img src={image} alt="" loading="lazy" onError={() => setFailed(true)} />
        : <b>{initials(post)}</b>}
    </span>
  );
}

function RichAttachments({ post }: { post: RadarPost }) {
  const articleUrl = safeUrl(post.article?.url);
  return (
    <>
      {post.article && (
        <a className="article-card" href={articleUrl ?? post.xcancelUrl} target="_blank" rel="noreferrer">
          {safeUrl(post.article.imageUrl) && <img src={safeUrl(post.article.imageUrl)!} alt="" loading="lazy" />}
          <span className="article-copy">
            <small>{post.article.publisher ?? "X ARTICLE"}</small>
            <strong>{post.article.title}</strong>
            {post.article.description && <span>{post.article.description}</span>}
          </span>
          <b aria-hidden="true">↗</b>
        </a>
      )}
      {post.media.length > 0 && (
        <div className={`media-grid media-count-${Math.min(post.media.length, 4)}`}>
          {post.media.slice(0, 4).map((item, index) => {
            const image = safeUrl(item.previewUrl ?? item.url);
            if (!image) return null;
            return (
              <a href={post.xcancelUrl} target="_blank" rel="noreferrer" key={`${image}-${index}`}>
                <img src={image} alt={item.alt ?? ""} loading="lazy" />
                {item.type !== "image" && <span>{item.type === "gif" ? "GIF" : "PLAY"} ↗</span>}
              </a>
            );
          })}
        </div>
      )}
      {post.externalLinks.length > 0 && (
        <div className="link-stack" aria-label="Referenced websites">
          {post.externalLinks.slice(0, 4).map((link, index) => {
            const href = safeUrl(link.url);
            if (!href) return null;
            return (
              <a href={href} target="_blank" rel="noreferrer" key={`${href}-${index}`}>
                <small>{displayDomain(link)}</small>
                <strong>{link.title ?? displayDomain(link)}</strong>
                {link.description && <span>{link.description}</span>}
                <b aria-hidden="true">↗</b>
              </a>
            );
          })}
        </div>
      )}
    </>
  );
}

function AccountControl({ account, onSaved }: { account: Reputation; onSaved: (account: Reputation, previous: Reputation) => void }) {
  const [disposition, setDisposition] = useState(account.disposition);
  const [notes, setNotes] = useState(account.notes ?? "");
  const [saving, setSaving] = useState(false);
  async function save() {
    setSaving(true);
    const response = await fetch(`/api/accounts/${account.handle.replace(/^@/, "")}`, {
      method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify({ disposition, notes }),
    });
    setSaving(false);
    if (response.ok) onSaved({ ...account, disposition, notes }, account);
  }
  return (
    <div className="account-control">
      <div><b>{account.handle}</b><span>{account.strikePoints} evidence points</span></div>
      <select aria-label={`Disposition for ${account.handle}`} value={disposition} onChange={(event) => setDisposition(event.target.value)}>
        {['allow', 'normal', 'watch', 'downrank', 'blocked'].map((value) => <option value={value} key={value}>{value}</option>)}
      </select>
      <input aria-label={`Notes for ${account.handle}`} value={notes} onChange={(event) => setNotes(event.target.value)} placeholder="Private note" />
      <button onClick={save} disabled={saving}>{saving ? "…" : "Save"}</button>
    </div>
  );
}

export function RadarDashboard({
  initialPosts,
  initialStats,
  initialReputation,
}: {
  initialPosts: RadarPost[];
  initialStats: Stats;
  initialReputation: Reputation[];
}) {
  const [range, setRange] = useState<Range>("week");
  const [surface, setSurface] = useState<Surface>("briefing");
  const [decision, setDecision] = useState<"keep" | "all">("keep");
  const [sort, setSort] = useState<SortMode>("signal");
  const [posts, setPosts] = useState(initialPosts);
  const [stats, setStats] = useState(initialStats);
  const [reputation, setReputation] = useState(initialReputation);
  const [requests, setRequests] = useState<FetchRequest[]>([]);
  const [handle, setHandle] = useState("");
  const [includeReplies, setIncludeReplies] = useState(true);
  const [requestState, setRequestState] = useState<"idle" | "sending" | "sent" | "error">("idle");
  const [panel, setPanel] = useState<"queue" | "reputation">("queue");
  const [nextOffset, setNextOffset] = useState<number | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [query, setQuery] = useState("");
  const [accountFilter, setAccountFilter] = useState("");
  const [submittedSearch, setSubmittedSearch] = useState<{ query: string; handle: string } | null>(null);
  const [collectorStatus, setCollectorStatus] = useState<CollectorStatus | null>(null);
  const [runs, setRuns] = useState<RadarRun[]>([]);
  const [selectedRun, setSelectedRun] = useState<{ run: RadarRun; acquisitions?: RunAcquisition[]; observations: RadarPost[] } | null>(null);
  const [observationHistory, setObservationHistory] = useState<Record<string, PostObservation[] | null>>({});
  const [toast, setToast] = useState<UndoToast | null>(null);
  const [homeRequestState, setHomeRequestState] = useState<"idle" | "sending" | "sent" | "error">("idle");
  const [curation, setCuration] = useState<CuratorPreferences>({ instructions: "", topics: [] });
  const [topicDraft, setTopicDraft] = useState("");
  const [curationState, setCurationState] = useState<"loading" | "idle" | "saving" | "saved" | "error">("loading");
  const [deskOpen, setDeskOpen] = useState(false);
  const [deskSection, setDeskSection] = useState<"config" | "settings">("config");
  const [searchOpen, setSearchOpen] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    const feedUrl = submittedSearch && surface === "signal"
      ? `/api/posts/search?${new URLSearchParams({ q: submittedSearch.query, handle: submittedSearch.handle, sort, limit: "100" })}`
      : surface === "briefing"
      ? `/api/briefing?${new URLSearchParams({ timezone: dashboardTimeZone, sort })}`
      : surface === "saved"
        ? "/api/posts/search?surface=saved&limit=100"
        : `/api/feed?range=${range}&decision=${decision}&sort=${sort}&view=${surface}&limit=${surface === "history" ? 100 : 300}&offset=0`;
    fetch(feedUrl, { signal: controller.signal }).then((r) => r.ok ? r.json() : Promise.reject()).then((feed) => {
      setPosts(feed.posts);
      if (feed.stats) setStats(feed.stats);
      if (feed.reputation) setReputation(feed.reputation);
      setNextOffset(feed.nextOffset ?? null);
    }).catch(() => undefined);
    return () => controller.abort();
  }, [range, decision, sort, submittedSearch, surface]);

  useEffect(() => {
    let alive = true;
    async function refreshOperational() {
      const [statusResponse, queueResponse, runsResponse] = await Promise.all([
        fetch("/api/status"), fetch("/api/fetch-requests"), fetch("/api/runs?limit=24"),
      ]);
      if (!alive) return;
      if (statusResponse.ok) setCollectorStatus(await statusResponse.json());
      if (queueResponse.ok) setRequests((await queueResponse.json()).requests);
      if (runsResponse.ok) setRuns((await runsResponse.json()).runs);
    }
    refreshOperational().catch(() => undefined);
    const timer = window.setInterval(() => refreshOperational().catch(() => undefined), 15_000);
    return () => { alive = false; window.clearInterval(timer); };
  }, []);

  useEffect(() => {
    fetch("/api/curation").then((response) => response.ok ? response.json() : Promise.reject()).then((preferences) => {
      setCuration(preferences);
      setCurationState("idle");
    }).catch(() => setCurationState("error"));
  }, []);

  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(null), 8_000);
    return () => window.clearTimeout(timer);
  }, [toast]);

  useEffect(() => {
    if (!deskOpen) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setDeskOpen(false);
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", closeOnEscape);
    };
  }, [deskOpen]);

  async function runSearch(event: FormEvent) {
    event.preventDefault();
    setSurface("signal");
    setSubmittedSearch({ query: query.trim(), handle: accountFilter.trim() });
  }

  function changeSurface(next: Surface) {
    setSurface(next);
    setSubmittedSearch(null);
  }

  function openDesk(section: "config" | "settings") {
    setDeskSection(section);
    setDeskOpen(true);
  }

  async function updatePostState(post: RadarPost, change: { saved?: boolean; pinned?: boolean; dismissed?: boolean }) {
    const previousPosts = posts;
    const previous = { saved: Boolean(post.saved), pinned: Boolean(post.pinned), dismissed: Boolean(post.dismissed) };
    const optimistic = { ...post, ...change, saved: change.pinned ? true : (change.saved ?? post.saved) };
    if (change.dismissed && surface === "briefing") setPosts((current) => current.filter((item) => item.postId !== post.postId));
    else setPosts((current) => current.map((item) => item.postId === post.postId ? optimistic : item));
    const response = await fetch(`/api/posts/${post.postId}/state`, {
      method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify(change),
    });
    if (!response.ok) {
      setPosts(previousPosts);
      return;
    }
    setToast({
      message: change.dismissed ? "Post dismissed" : change.pinned !== undefined ? (change.pinned ? "Post pinned" : "Post unpinned") : change.saved ? "Post saved" : "Post removed from Saved",
      undo: async () => {
        await fetch(`/api/posts/${post.postId}/state`, { method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify(previous) });
        setPosts(previousPosts);
      },
    });
  }

  async function loadObservationHistory(postId: string) {
    if (observationHistory[postId] !== undefined) {
      setObservationHistory((current) => { const next = { ...current }; delete next[postId]; return next; });
      return;
    }
    setObservationHistory((current) => ({ ...current, [postId]: null }));
    const response = await fetch(`/api/posts/${postId}/observations`);
    const data = response.ok ? await response.json() : { observations: [] };
    setObservationHistory((current) => ({ ...current, [postId]: data.observations }));
  }

  async function openRun(scanId: string) {
    const response = await fetch(`/api/runs/${scanId}`);
    if (response.ok) setSelectedRun(await response.json());
  }

  async function queueHome() {
    setHomeRequestState("sending");
    const response = await fetch("/api/fetch-requests", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ kind: "mixed" }) });
    if (response.ok) {
      const data = await response.json();
      setRequests((current) => [data.request, ...current]);
      setHomeRequestState("sent");
      setToast({ message: "150-post discovery period requested. The Pi checks the queue every minute." });
      window.setTimeout(() => setHomeRequestState("idle"), 3000);
    } else {
      setHomeRequestState("error");
    }
  }

  function addTopic() {
    const topic = topicDraft.trim();
    if (!topic || curation.topics.some((item) => item.toLowerCase() === topic.toLowerCase()) || curation.topics.length >= 24) return;
    setCuration((current) => ({ ...current, topics: [...current.topics, topic] }));
    setTopicDraft("");
    setCurationState("idle");
  }

  async function saveCuration() {
    setCurationState("saving");
    const response = await fetch("/api/curation", {
      method: "PUT", headers: { "content-type": "application/json" },
      body: JSON.stringify({ instructions: curation.instructions, topics: curation.topics }),
    });
    if (!response.ok) { setCurationState("error"); return; }
    const saved = await response.json();
    setCuration(saved);
    setCurationState("saved");
    if (saved.bootstrapQueued) setToast({ message: `Saved. Discovery queued for ${saved.addedTopics.join(", ")}.` });
    window.setTimeout(() => setCurationState("idle"), 2500);
  }

  async function undoAccount(updated: Reputation, previous: Reputation) {
    setReputation((current) => current.map((item) => item.handle === updated.handle ? updated : item));
    setToast({
      message: `${updated.handle} set to ${updated.disposition}`,
      undo: async () => {
        await fetch(`/api/accounts/${previous.handle.replace(/^@/, "")}`, { method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify({ disposition: previous.disposition, notes: previous.notes ?? null }) });
        setReputation((current) => current.map((item) => item.handle === previous.handle ? previous : item));
      },
    });
  }

  const orderedPosts = useMemo(() => [...posts].sort((a, b) => {
    if (surface === "history") return Date.parse(b.capturedAt) - Date.parse(a.capturedAt);
    if (sort === "signal") return effectiveScore(b) - effectiveScore(a);
    const bDate = resolvePublishedAt(b.postedAt, b.capturedAt)?.getTime() ?? Date.parse(b.capturedAt);
    const aDate = resolvePublishedAt(a.postedAt, a.capturedAt)?.getTime() ?? Date.parse(a.capturedAt);
    return bDate - aDate;
  }), [posts, sort, surface]);

  const grouped = useMemo(() => {
    const groups = new Map<string, RadarPost[]>();
    for (const post of orderedPosts) {
      const published = resolvePublishedAt(post.postedAt, post.capturedAt) ?? new Date(post.capturedAt);
      const key = surface === "history"
        ? `Scan · ${formatDateTime(new Date(post.capturedAt), true)}`
        : surface === "signal" && sort === "signal"
          ? submittedSearch ? "Search results · strongest first" : "Ranked signal · strongest first"
        : published.toLocaleDateString("en-AU", {
          timeZone: dashboardTimeZone, weekday: "long", day: "numeric", month: "long", year: "numeric",
        });
      groups.set(key, [...(groups.get(key) ?? []), post]);
    }
    return [...groups.entries()];
  }, [orderedPosts, sort, submittedSearch, surface]);

  async function loadMoreHistory() {
    if (nextOffset === null || loadingMore) return;
    setLoadingMore(true);
    try {
      const response = await fetch(`/api/feed?range=${range}&view=history&limit=100&offset=${nextOffset}`);
      if (!response.ok) return;
      const feed = await response.json();
      setPosts((current) => [...current, ...feed.posts]);
      setNextOffset(feed.nextOffset ?? null);
    } finally {
      setLoadingMore(false);
    }
  }

  async function queueAccount(event: FormEvent) {
    event.preventDefault();
    setRequestState("sending");
    const response = await fetch("/api/fetch-requests", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ handle, includeReplies }),
    });
    if (!response.ok) {
      setRequestState("error");
      return;
    }
    const data = await response.json();
    setRequests((current) => [data.request, ...current]);
    setHandle("");
    setRequestState("sent");
    setToast({ message: "Account scan requested. The Pi checks the queue every minute." });
  }

  return (
    <main className="radar-shell">
      <header className="masthead">
        <div className="brand-lockup">
          <span className="brand-mark" aria-hidden="true">
            <img src="/x-radar-mark.png" alt="" />
          </span>
          <div>
            <p className="eyebrow">PRIVATE SIGNAL DESK</p>
            <h1>X RADAR</h1>
          </div>
        </div>
        <div className="masthead-actions">
          <div className="run-status"><span className={collectorStatus?.warnings?.some((warning) => warning.level === "critical") ? "stale" : ""} />
            {collectorStatus?.activity && ["collecting", "ranking", "syncing"].includes(collectorStatus.activity.phase)
              ? `${collectorStatus.activity.phase} · ${collectorStatus.activity.observed ?? 0}/${collectorStatus.activity.targetUnique ?? 150} observed`
              : collectorStatus?.lastRun
                ? `Last scan ${fetchedLabel(collectorStatus.lastRun.capturedAt).replace("fetched ", "")} · ${collectorStatus.lastRun.postsSeen} seen`
                : "Waiting for collector status"}
          </div>
          <div className="header-tools">
            <button className="mobile-search" aria-label="Search archive" aria-expanded={searchOpen} onClick={() => setSearchOpen((open) => !open)}><MagnifyingGlassIcon size={20} /></button>
            <button aria-label="Open collector configuration" aria-expanded={deskOpen && deskSection === "config"} onClick={() => openDesk("config")}><SlidersHorizontalIcon size={20} /><span>Config</span></button>
            <button aria-label="Open curator settings and topics" aria-expanded={deskOpen && deskSection === "settings"} onClick={() => openDesk("settings")}><GearSixIcon size={20} /><span>Settings</span></button>
          </div>
        </div>
      </header>

      <section className="control-deck" aria-label="Feed controls">
        <div className="range-control">
          <p className="control-label">TIME LENS</p>
          <div className="range-row">
            {ranges.map((item) => (
              <button
                key={item.id}
                className={range === item.id ? "active" : ""}
                onClick={() => setRange(item.id)}
                aria-label={item.label}
                aria-pressed={range === item.id}
              >
                <span>{item.short}</span>{item.label}
              </button>
            ))}
          </div>
        </div>
        <div className="metric-strip">
          <div><b>{stats.scanned}</b><span>unique posts</span></div>
          <div><b>{stats.observations ?? collectorStatus?.counts?.observations ?? stats.scanned}</b><span>observations</span></div>
          <div><b>{stats.kept}</b><span>kept</span></div>
          <div><b>{stats.authors}</b><span>sources</span></div>
        </div>
      </section>

      <div className="workspace-grid">
        <section className="stream" aria-label={surface === "history" ? "Complete scan history" : "Curated research feed"}>
          <div className="surface-tabs" aria-label="Feed view">
            <button className={surface === "briefing" ? "active" : ""} onClick={() => changeSurface("briefing")}>
              <span>01</span><b>Today</b><small>Top 20 signal briefing</small>
            </button>
            <button className={surface === "signal" ? "active" : ""} onClick={() => changeSurface("signal")}>
              <span>02</span><b>Signal</b><small>Interesting post archive</small>
            </button>
            <button className={surface === "saved" ? "active" : ""} onClick={() => changeSurface("saved")}>
              <span>03</span><b>Saved</b><small>Pinned first</small>
            </button>
            <button className={surface === "history" ? "active" : ""} onClick={() => changeSurface("history")}>
              <span>04</span><b>All seen</b><small>Complete scan history</small>
            </button>
          </div>
          <form className={`search-deck ${searchOpen ? "mobile-open" : ""}`} onSubmit={runSearch}>
            <input aria-label="Search post text" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search posts, authors, research…" />
            <input aria-label="Filter by account" value={accountFilter} onChange={(event) => setAccountFilter(event.target.value)} placeholder="@account" />
            <button>Search archive →</button>
            <button type="button" className="clear-search" disabled={!submittedSearch} onClick={() => { setSubmittedSearch(null); setQuery(""); setAccountFilter(""); }}>Reset</button>
          </form>
          <div className="stream-heading">
            <div>
              <p className="eyebrow">{surface === "history" ? "OBSERVATION LEDGER" : surface === "briefing" ? "DAILY BRIEFING" : surface === "saved" ? "READING DESK" : "STACKED FEED"} / {ranges.find((r) => r.id === range)?.label.toUpperCase()}</p>
              <h2>{surface === "history" ? "Everything the radar saw." : surface === "briefing" ? sort === "signal" ? "Today’s strongest signals." : "Today’s newest signals." : surface === "saved" ? "Saved for deeper reading." : submittedSearch ? "Matching posts, ordered." : sort === "signal" ? "Signal, ranked." : "Newest signal first."}</h2>
            </div>
            {surface === "briefing" || surface === "signal" ? (
              <div className="feed-toggles">
                <div className="decision-toggle" aria-label="Sort posts">
                  <button type="button" className={sort === "signal" ? "active" : ""} onClick={() => setSort("signal")}>Signal</button>
                  <button type="button" className={sort === "newest" ? "active" : ""} onClick={() => setSort("newest")}>Newest</button>
                </div>
                {surface === "signal" ? (
                  <div className="decision-toggle" aria-label="Feed density">
                    <button type="button" className={decision === "keep" ? "active" : ""} onClick={() => setDecision("keep")}>Curated</button>
                    <button type="button" className={decision === "all" ? "active" : ""} onClick={() => setDecision("all")}>+ candidates</button>
                  </div>
                ) : null}
              </div>
            ) : surface === "saved" ? (
              <div className="export-links" aria-label="Export saved posts">
                <a href="/api/export?format=markdown">Markdown</a><a href="/api/export?format=csv">CSV</a><a href="/api/export?format=json">JSON</a>
              </div>
            ) : <p className="archive-count">{posts.length} observations loaded</p>}
          </div>

          {grouped.length === 0 ? (
            <div className="empty-state"><span>○</span><p>No retained posts in this time lens yet.</p></div>
          ) : grouped.map(([date, datePosts]) => (
            <div className="day-group" key={date}>
              <div className="day-label"><span>{date}</span><i /></div>
              {datePosts.map((post, index) => (
                <article className={`signal-card decision-${post.decision}`} key={`${post.capturedAt}-${post.postId}`}>
                  <div className="timeline-node"><span>{String(index + 1).padStart(2, "0")}</span></div>
                  <div className="signal-body">
                    <div className="signal-meta">
                      <div className="author-lockup">
                        <ProfileAvatar post={post} />
                        <div className="author-copy"><strong>{post.author ?? post.handle}</strong><span>{post.handle}</span><small>{publishedLabel(post).replace("published ", "")} · {fetchedLabel(post.capturedAt).replace("fetched ", "fetched ")}</small></div>
                      </div>
                      <div className="score">
                        {surface === "history" && (
                          <span className={`decision-badge badge-${post.decision}`}>{post.decision}</span>
                        )}
                        {surface === "history" && post.isAd && <span className="observation-badge">promoted</span>}
                        {surface === "history" && post.isReply && <span className="observation-badge">reply</span>}
                        {surface === "history" && post.isQuote && <span className="observation-badge">quote</span>}
                        <span>signal</span><b>{post.score.toFixed(2)}</b>
                      </div>
                    </div>
                    <p className="signal-text">{post.text}</p>
                    <RichAttachments post={post} />
                    <div className="reason-row">
                      {post.reasons.slice(0, 3).map((reason) => <span key={reason}>{reason}</span>)}
                    </div>
                    <div className="feedback-row" aria-label="Post actions">
                      <button className={post.saved ? "active" : ""} onClick={() => updatePostState(post, { saved: !post.saved })}>{post.saved ? "Saved" : "Save"}</button>
                      <button className={post.pinned ? "active" : ""} onClick={() => updatePostState(post, { pinned: !post.pinned })}>{post.pinned ? "Pinned" : "Pin"}</button>
                      <button onClick={() => updatePostState(post, { dismissed: !post.dismissed })}>{post.dismissed ? "Restore" : "Dismiss"}</button>
                      <button className={observationHistory[post.postId] !== undefined ? "active" : ""} onClick={() => loadObservationHistory(post.postId)}>Seen history</button>
                    </div>
                    {observationHistory[post.postId] !== undefined && (
                      <div className="observation-history">
                        {observationHistory[post.postId] === null ? <span>Loading observations…</span> : observationHistory[post.postId]!.length === 0 ? <span>No observation history.</span> : observationHistory[post.postId]!.map((observation) => (
                          <button key={`${observation.scanId}-${observation.capturedAt}`} onClick={() => openRun(observation.scanId)}>
                            <time>{formatDateTime(new Date(observation.capturedAt), true)}</time><b>{observation.decision} · {observation.score.toFixed(2)}</b>
                          </button>
                        ))}
                      </div>
                    )}
                    <div className="signal-footer">
                      <div className="engagement">
                        <span>{compact(post.engagement.likes)} likes</span>
                        <span>{compact(post.engagement.bookmarks)} saves</span>
                        <time className="footer-time" dateTime={resolvePublishedAt(post.postedAt, post.capturedAt)?.toISOString()}>{publishedLabel(post)}</time>
                        <time className="footer-time" dateTime={post.capturedAt}>{fetchedLabel(post.capturedAt)}</time>
                      </div>
                      <div className="reading-links">
                        <a className="xcancel-link" href={post.xcancelUrl} target="_blank" rel="noreferrer">Read on XCancel <span>↗</span></a>
                        <a className="original-link" href={post.url} target="_blank" rel="noreferrer">Original X</a>
                      </div>
                    </div>
                  </div>
                </article>
              ))}
            </div>
          ))}
          {surface === "history" && nextOffset !== null && (
            <button className="load-more" onClick={loadMoreHistory} disabled={loadingMore}>
              {loadingMore ? "Loading observations…" : "Load 100 more observations"}<span>↓</span>
            </button>
          )}
        </section>

        {deskOpen && <>
        <button className="desk-backdrop visible" onClick={() => setDeskOpen(false)} aria-label="Close operations desk" />
        <aside className="sidecar desk-open" aria-label="Operations desk" role="dialog" aria-modal="true">
          <div className="sidecar-mobile-head"><div><p className="eyebrow">OPERATIONS</p><h2>{deskSection === "config" ? "Configuration" : "Settings & topics"}</h2></div><button onClick={() => setDeskOpen(false)} aria-label="Close desk"><XIcon size={20} /></button></div>
          <div className="desk-scroll-region">
          <section className={`health-card ${deskOpen && deskSection !== "config" ? "drawer-hidden" : ""}`} id="desk-overview">
            <div className="health-heading"><div><p className="eyebrow">24-HOUR VERIFICATION</p><h2>{collectorStatus?.reliability?.successfulSlots ?? 0}<span>/24</span></h2></div><em>{collectorStatus?.reliability?.percent ?? 0}%</em></div>
            <div className="reliability-track"><i style={{ width: `${Math.min(100, collectorStatus?.reliability?.percent ?? 0)}%` }} /></div>
            <div className="phase-line"><span className={`phase-${collectorStatus?.activity?.phase ?? "idle"}`} />
              <b>{collectorStatus?.activity?.phase ?? "idle"}</b><small>{collectorStatus?.activity?.target?.replace("https://x.com/", "") ?? "waiting for next cycle"}</small>
            </div>
            {collectorStatus?.activity?.preferenceVersion != null && <div className="period-meta"><span>Preference v{collectorStatus.activity.preferenceVersion}</span><b>{collectorStatus.activity.observed ?? 0}/{collectorStatus.activity.targetUnique ?? 150}</b></div>}
            {Object.values(collectorStatus?.activity?.sourceProgress ?? {}).length > 0 && <div className="source-progress" aria-label="Discovery source progress">
              {Object.entries(collectorStatus?.activity?.sourceProgress ?? {}).map(([id, item]) => <div key={id}>
                <span>{item.topic ? `${item.kind.replaceAll("_", " ")} · ${item.topic}` : item.kind.replaceAll("_", " ")}</span>
                <b>{item.unique}/{item.planned}</b>
              </div>)}
            </div>}
            {(collectorStatus?.warnings?.length ?? 0) > 0 ? <div className="warning-list">{collectorStatus!.warnings!.map((warning) => <p className={warning.level} key={warning.code}>{warning.message}</p>)}</div> : <p className="all-clear">No reliability warnings.</p>}
            <div className="run-list">
              <div className="run-list-title"><b>Recent runs</b><span>{runs.length} loaded</span></div>
              {runs.slice(0, 6).map((run) => <button key={run.scanId} onClick={() => openRun(run.scanId)}>
                <span><b>{run.source === "x-account" ? run.target?.split("/")[3] ?? "account" : run.source === "x-mixed" ? "discovery" : "home"}</b><small>{formatDateTime(new Date(run.ingestedAt))}</small></span>
                <em>{run.postsSeen} seen</em>
              </button>)}
            </div>
          </section>
          <section className={`curator-card ${deskOpen && deskSection !== "settings" ? "drawer-hidden" : ""}`} id="desk-curator">
            <div className="curator-heading"><div><p className="eyebrow">LUNA CURATOR</p><h2>Shape the signal</h2></div><span>{curation.topics.length}/24</span></div>
            <p className="subcopy">Topics shape future discovery and ranking. New topics queue a bootstrap scan; existing history is never recategorized.</p>
            <div className="topic-list" aria-label="Curator topics">
              {curation.topics.map((topic) => <span key={topic}>{topic}<button aria-label={`Remove ${topic}`} onClick={() => { setCuration((current) => ({ ...current, topics: current.topics.filter((item) => item !== topic) })); setCurationState("idle"); }}>−</button></span>)}
              {curation.topics.length === 0 && <small>No topic priorities yet.</small>}
            </div>
            <div className="topic-add"><input value={topicDraft} maxLength={60} onChange={(event) => setTopicDraft(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); addTopic(); } }} placeholder="Add a topic" aria-label="New curator topic" /><button onClick={addTopic} disabled={!topicDraft.trim() || curation.topics.length >= 24} aria-label="Add topic">+</button></div>
            <label htmlFor="curator-instructions">Custom instructions</label>
            <textarea id="curator-instructions" maxLength={4000} value={curation.instructions} onChange={(event) => { setCuration((current) => ({ ...current, instructions: event.target.value })); setCurationState("idle"); }} placeholder="Prefer primary research, concrete results, and novel technical detail…" />
            <div className="curator-save"><small>{curationState === "saved" ? "Saved for future scans" : curationState === "error" ? "Couldn’t save changes" : curation.updatedAt ? `v${curation.preferenceVersion ?? 0} · ${formatDateTime(new Date(curation.updatedAt))}` : "Not synchronized yet"}</small><button onClick={saveCuration} disabled={curationState === "saving" || curationState === "loading"}>{curationState === "saving" ? "Saving…" : "Save brief"}</button></div>
          </section>
          <section className={`fetch-card ${deskOpen && deskSection !== "config" ? "drawer-hidden" : ""}`} id="desk-scans">
            <div className="fetch-title-row"><p className="eyebrow">DIRECTED SCAN</p><button className="home-now" onClick={queueHome} disabled={homeRequestState === "sending"}>{homeRequestState === "sending" ? "Requesting…" : homeRequestState === "sent" ? "Requested ✓" : "Run discovery now"}</button></div>
            {homeRequestState === "error" && <p className="form-note error">Couldn’t request a discovery period.</p>}
            <p className="subcopy">Collect up to 150 unique posts: 60 Home, 45 topic search, 30 known accounts, and 15 exploration.</p>
            <h2>Fetch an account</h2>
            <p className="subcopy">Queue a focused pass through an account’s original posts and conversation replies.</p>
            <form onSubmit={queueAccount}>
              <label htmlFor="account-handle">X handle</label>
              <div className="handle-input"><span>@</span><input id="account-handle" value={handle.replace(/^@/, "")} onChange={(e) => setHandle(e.target.value)} placeholder="researcher" required pattern="[A-Za-z0-9_]{1,15}" /></div>
              <label className="check-row"><input type="checkbox" checked={includeReplies} onChange={(e) => setIncludeReplies(e.target.checked)} /><span>Include replies and threads</span></label>
              <button className="queue-button" disabled={requestState === "sending"}>{requestState === "sending" ? "Requesting…" : requestState === "sent" ? "Requested ✓" : "Run account now"}<span>→</span></button>
              {requestState === "sent" && <p className="form-note success">Requested. The Pi checks the queue every minute; scans run in order.</p>}
              {requestState === "error" && <p className="form-note error">Couldn’t queue this scan.</p>}
            </form>
          </section>

          <section className={`ledger-card ${deskOpen && deskSection !== "config" ? "drawer-hidden" : ""}`}>
            <div className="tab-row">
              <button className={panel === "queue" ? "active" : ""} onClick={() => setPanel("queue")}>Queue</button>
              <button className={panel === "reputation" ? "active" : ""} onClick={() => setPanel("reputation")}>Accounts</button>
            </div>
            {panel === "queue" ? (
              <div className="ledger-list">
                {requests.length === 0 ? <p className="quiet">No directed scans queued.</p> : requests.slice(0, 6).map((request) => (
                  <div className="ledger-item" key={request.id}>
                    <div><b>{request.targetKind === "mixed" || request.handle === "@home" || request.handle === "@mixed" ? "Discovery period" : request.handle}</b><span>{request.status === "complete" && request.resultCount != null ? `${request.resultCount} posts collected` : request.status === "collecting" ? "Browser is collecting sources" : request.status === "ranking" ? "Luna is ranking" : request.status === "syncing" ? "Synchronizing dashboard" : request.bootstrapTopics?.length ? `bootstrap · ${request.bootstrapTopics.join(", ")}` : request.targetKind === "mixed" ? "150-post mixed collection" : request.includeReplies ? "posts + replies" : "posts only"}</span></div>
                    <em className={`status-${request.status}`}>{request.status}</em>
                  </div>
                ))}
              </div>
            ) : (
              <div className="ledger-list">
                {reputation.map((account) => <AccountControl key={`${account.handle}:${account.disposition}:${account.notes ?? ""}`} account={account} onSaved={undoAccount} />)}
              </div>
            )}
          </section>
          </div>
        </aside>
        </>}
      </div>
      <nav className="mobile-nav" aria-label="Primary feed views">
        <button className={surface === "briefing" ? "active" : ""} onClick={() => changeSurface("briefing")}><HouseIcon size={22} weight={surface === "briefing" ? "fill" : "regular"} /><span>Today</span></button>
        <button className={surface === "signal" ? "active" : ""} onClick={() => changeSurface("signal")}><ChartBarIcon size={22} weight={surface === "signal" ? "fill" : "regular"} /><span>Signal</span></button>
        <button className={surface === "saved" ? "active" : ""} onClick={() => changeSurface("saved")}><BookmarkSimpleIcon size={22} weight={surface === "saved" ? "fill" : "regular"} /><span>Saved</span></button>
        <button className={surface === "history" ? "active" : ""} onClick={() => changeSurface("history")}><ArchiveIcon size={22} weight={surface === "history" ? "fill" : "regular"} /><span>All seen</span></button>
      </nav>
      <footer><span>X RADAR / PRIVATE SIGNAL INDEX</span><span>{collectorStatus?.counts?.runs ?? 0} scans synchronized</span></footer>
      {selectedRun && <div className="detail-overlay" role="dialog" aria-modal="true" aria-label="Scan detail" onMouseDown={(event) => { if (event.target === event.currentTarget) setSelectedRun(null); }}>
        <section className="run-detail">
          <div className="detail-head"><div><p className="eyebrow">SCAN DETAIL</p><h2>{selectedRun.run.source === "x-account" ? selectedRun.run.target?.split("/")[3] ?? "Account" : selectedRun.run.source === "x-mixed" ? "Discovery period" : "Home feed"}</h2><span>{formatDateTime(new Date(selectedRun.run.ingestedAt), true)} · {Math.round(selectedRun.run.durationSeconds / 60)} min{selectedRun.run.preferenceVersion != null ? ` · preference v${selectedRun.run.preferenceVersion}` : ""}</span></div><button onClick={() => setSelectedRun(null)} aria-label="Close scan detail">×</button></div>
          <div className="run-metrics"><div><b>{selectedRun.run.postsSeen}</b><span>seen</span></div><div><b>{selectedRun.run.postsAdded}</b><span>new</span></div><div><b>{selectedRun.run.duplicates}</b><span>duplicates</span></div><div><b>{selectedRun.run.postsKept}</b><span>kept</span></div><div><b>{selectedRun.run.candidates}</b><span>candidate</span></div><div><b>{selectedRun.run.discarded}</b><span>discarded</span></div></div>
          <div className="run-attachments"><span>{selectedRun.run.mediaCount} media</span><span>{selectedRun.run.linksCount} links</span><span>{selectedRun.run.signalsCount} account signals</span></div>
          {(selectedRun.acquisitions?.length ?? 0) > 0 && <div className="run-sources">{selectedRun.acquisitions!.map((item) => <div key={item.acquisitionId}><span>{item.topicLabel ? `${item.kind.replaceAll("_", " ")} · ${item.topicLabel}` : item.kind.replaceAll("_", " ")}</span><b>{item.uniqueCount}/{item.plannedQuota}</b></div>)}</div>}
          <div className="run-observations">{selectedRun.observations.map((post, index) => <article key={`${post.postId}-${index}`}><span>{String(index + 1).padStart(2, "0")}</span><div><b>{post.author ?? post.handle}</b><small>{post.handle} · {post.decision} · {post.score.toFixed(2)}</small><p>{post.text}</p></div><a href={post.xcancelUrl} target="_blank" rel="noreferrer">↗</a></article>)}</div>
        </section>
      </div>}
      {toast && <div className="undo-toast" role="status"><span>{toast.message}</span><button onClick={async () => { const action = toast.undo; setToast(null); await action(); }}>Undo</button><button aria-label="Dismiss notification" onClick={() => setToast(null)}>×</button></div>}
    </main>
  );
}
