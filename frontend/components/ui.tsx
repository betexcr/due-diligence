"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

export const FIELD_LABELS: Record<string, string> = {
  revenue: "Revenue",
  cost_of_goods_sold: "Cost of goods sold",
  gross_profit: "Gross profit",
  operating_expenses: "Operating expenses",
  operating_income: "Operating income",
  net_income: "Net income",
  total_assets: "Total assets",
  total_liabilities: "Total liabilities",
  total_equity: "Total equity",
  total_debt: "Total debt",
  cash: "Cash",
};

export function money(v: number | null | undefined, currency?: string | null) {
  if (v === null || v === undefined) return "—";
  const s = Math.abs(v).toLocaleString("en-US", { maximumFractionDigits: 0 });
  return `${currency ? currency + " " : ""}${v < 0 ? `(${s})` : s}`;
}

/** When a queued retry will run: "in 12s", or "due now" once its backoff has elapsed. */
export function retryIn(iso: string) {
  const s = (new Date(iso).getTime() - Date.now()) / 1000;
  return s > 0 ? `in ${Math.ceil(s)}s` : "due now";
}

export function ago(iso: string) {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 0) return `in ${Math.ceil(-s)}s`;
  if (s < 60) return `${Math.floor(s)}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  return new Date(iso).toLocaleString();
}

const TONES = {
  neutral: "bg-stone-100 text-stone-700 ring-stone-300",
  blue: "bg-sky-50 text-sky-800 ring-sky-300",
  green: "bg-emerald-50 text-emerald-800 ring-emerald-300",
  amber: "bg-amber-50 text-amber-900 ring-amber-300",
  red: "bg-rose-50 text-rose-800 ring-rose-300",
  violet: "bg-violet-50 text-violet-800 ring-violet-300",
} as const;

const STATUS_TONE: Record<string, keyof typeof TONES> = {
  uploaded: "neutral",
  queued: "neutral",
  processing: "blue",
  running: "blue",
  needs_review: "amber",
  flagged: "red",
  validated: "green",
  succeeded: "green",
  approved: "green",
  corrected: "violet",
  extracted: "blue",
  missing: "neutral",
  failed: "red",
  dead: "red",
  error: "red",
  warning: "amber",
  info: "neutral",
};

export function Badge({ children, tone = "neutral" }: { children: React.ReactNode; tone?: keyof typeof TONES }) {
  return (
    <span className={`inline-flex items-center rounded px-1.5 py-0.5 text-[11px] font-medium uppercase tracking-wide ring-1 ring-inset ${TONES[tone]}`}>
      {children}
    </span>
  );
}

export function StatusBadge({ status }: { status: string }) {
  return <Badge tone={STATUS_TONE[status] ?? "neutral"}>{status.replace("_", " ")}</Badge>;
}

export function Panel({ title, actions, children, className = "" }: {
  title?: React.ReactNode; actions?: React.ReactNode; children: React.ReactNode; className?: string;
}) {
  return (
    <section className={`rounded-md border border-stone-300 bg-white ${className}`}>
      {title && (
        <header className="flex items-center justify-between gap-3 border-b border-stone-200 px-4 py-2.5">
          <h2 className="text-[13px] font-semibold uppercase tracking-wider text-stone-600">{title}</h2>
          {actions}
        </header>
      )}
      {children}
    </section>
  );
}

export function Button({ variant = "secondary", className = "", ...props }:
  React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "danger" | "ghost" }) {
  const styles = {
    primary: "bg-stone-900 text-white hover:bg-stone-700 disabled:bg-stone-400",
    secondary: "bg-white text-stone-800 ring-1 ring-inset ring-stone-300 hover:bg-stone-50 disabled:text-stone-400",
    danger: "bg-rose-700 text-white hover:bg-rose-600",
    ghost: "text-stone-700 hover:bg-stone-100",
  }[variant];
  return <button {...props} className={`rounded px-3 py-1.5 text-sm font-medium transition-colors disabled:cursor-not-allowed ${styles} ${className}`} />;
}

export function ErrorNote({ error }: { error: string | null }) {
  if (!error) return null;
  return <p className="rounded border border-rose-300 bg-rose-50 px-3 py-2 text-sm text-rose-800">{error}</p>;
}

/** Fetch and re-poll while `shouldPoll(data)` is true. */
export function usePolled<T>(load: () => Promise<T>, shouldPoll: (d: T) => boolean, intervalMs = 1500) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const loadRef = useRef(load);
  const pollRef = useRef(shouldPoll);
  useLayoutEffect(() => {
    loadRef.current = load;
    pollRef.current = shouldPoll;
  });

  const refresh = useCallback(async () => {
    try {
      const d = await loadRef.current();
      setData(d);
      setError(null);
      return d;
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      return null;
    }
  }, []);

  // Bumping `generation` restarts the poll loop, e.g. after an upload creates new work.
  const [generation, setGeneration] = useState(0);
  const restart = useCallback(() => setGeneration((g) => g + 1), []);

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>;
    let alive = true;
    const tick = async () => {
      const d = await refresh();
      if (alive && (d === null || pollRef.current(d))) timer = setTimeout(tick, intervalMs);
    };
    tick();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [refresh, intervalMs, generation]);

  return { data, error, refresh, restart, setData };
}
