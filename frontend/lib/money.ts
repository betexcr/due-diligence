// Money is integer minor units (hundredths of the currency unit) end to end,
// matching backend/app/money.py. Parsing works on the digit string, never via
// floating point, so "1234.57" is exactly 123457.

const MINOR_PER_UNIT = 100;

export function formatMinor(minor: number | null | undefined, currency?: string | null): string {
  if (minor === null || minor === undefined) return "—";
  const abs = Math.abs(minor);
  const units = Math.trunc(abs / MINOR_PER_UNIT);
  const cents = abs % MINOR_PER_UNIT;
  let s = units.toLocaleString("en-US") + (cents ? `.${String(cents).padStart(2, "0")}` : "");
  if (minor < 0) s = `(${s})`;
  return currency ? `${currency} ${s}` : s;
}

export type ParseResult = { ok: true; minor: number } | { ok: false; error: string };

/** Accepts "3,500,000", "3500000.5", "-1,200.25", "(1,200)". Rejects more than 2 decimals. */
export function parseMoneyToMinor(input: string): ParseResult {
  let s = input.trim().replace(/[,\s]/g, "");
  let negative = false;
  if (/^\(.*\)$/.test(s)) {
    negative = true;
    s = s.slice(1, -1);
  }
  if (s.startsWith("-")) {
    negative = !negative;
    s = s.slice(1);
  }
  const m = /^(\d+)(?:\.(\d{1,2}))?$/.exec(s);
  if (!m) return { ok: false, error: "Enter an amount such as 3,500,000 or 1200.50" };
  const minor = Number(m[1]) * MINOR_PER_UNIT + Number((m[2] ?? "").padEnd(2, "0"));
  if (!Number.isSafeInteger(minor)) return { ok: false, error: "Amount is too large" };
  return { ok: true, minor: negative ? -minor : minor };
}

/** Value for pre-filling an input from minor units, without thousands separators. */
export function minorToInput(minor: number | null | undefined): string {
  if (minor === null || minor === undefined) return "";
  const abs = Math.abs(minor);
  const cents = abs % MINOR_PER_UNIT;
  const s = `${Math.trunc(abs / MINOR_PER_UNIT)}${cents ? `.${String(cents).padStart(2, "0")}` : ""}`;
  return minor < 0 ? `-${s}` : s;
}
