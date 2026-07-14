// Synthetic fixtures only. Production starts empty and is populated by the collector.
import type { RadarPost } from "../app/radar-dashboard";

export const initialPosts: RadarPost[] = [];
export const initialStats = { scanned: 0, observations: 0, kept: 0, candidates: 0, discarded: 0, authors: 0 };
export const initialReputation: Array<{ handle: string; disposition: string; strikePoints: number; confidence: number }> = [];
