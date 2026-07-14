import type { Metadata } from "next";
import { RadarDashboard } from "./radar-dashboard";

export const metadata: Metadata = {
  title: "X Radar — Signal without the scroll",
  description: "A private, evidence-aware research feed distilled from X.",
};

export default function Home() {
  return (
    <RadarDashboard
      initialPosts={[]}
      initialStats={{ scanned: 0, observations: 0, kept: 0, candidates: 0, discarded: 0, authors: 0 }}
      initialReputation={[]}
    />
  );
}
