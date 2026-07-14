# Responsive dashboard QA

The dashboard is checked at desktop, tablet, and narrow mobile widths against
the following public release criteria:

- the reading stream remains the primary surface;
- Config and Settings open one scrollable drawer and never duplicate inline;
- Today and Signal expose signal/date sorting without requiring a search;
- published and fetched timestamps remain distinguishable;
- post media, articles, external links, profile images, and XCancel links render
  without horizontal overflow;
- Search, Save, Pin, Dismiss, directed scans, curator topics, and queue status
  remain keyboard accessible;
- reduced-motion preferences and visible focus states are preserved; and
- the mobile navigation respects safe-area insets.

Release builds must pass lint, the rendered-surface test, and a manual smoke
check of the closed drawer, Config drawer, Settings drawer, and directed scan
form. Production screenshots and captured post data are deliberately not stored
in the public repository.
