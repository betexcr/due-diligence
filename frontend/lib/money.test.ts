import assert from "node:assert/strict";
import { test } from "node:test";
import { formatMinor, minorToInput, parseMoneyToMinor } from "./money.ts";

test("formatMinor renders whole units, cents only when present, negatives in parentheses", () => {
  assert.equal(formatMinor(1_250_000_000, "USD"), "USD 12,500,000");
  assert.equal(formatMinor(-120_050), "(1,200.50)");
  assert.equal(formatMinor(5), "0.05");
  assert.equal(formatMinor(null), "—");
});

test("parseMoneyToMinor is exact and strict", () => {
  assert.deepEqual(parseMoneyToMinor("3,500,000"), { ok: true, minor: 350_000_000 });
  assert.deepEqual(parseMoneyToMinor(" 1234.57 "), { ok: true, minor: 123_457 });
  assert.deepEqual(parseMoneyToMinor("0.1"), { ok: true, minor: 10 });
  assert.deepEqual(parseMoneyToMinor("(1,200)"), { ok: true, minor: -120_000 });
  assert.deepEqual(parseMoneyToMinor("-2.5"), { ok: true, minor: -250 });
  for (const bad of ["", "abc", "1.234", "1e6", "12,5x", "--1", "99999999999999999"]) {
    assert.equal(parseMoneyToMinor(bad).ok, false, bad);
  }
});

test("minorToInput round-trips through parseMoneyToMinor", () => {
  for (const v of [0, 5, 350_000_000, -120_050, 123_457]) {
    const parsed = parseMoneyToMinor(minorToInput(v));
    assert.deepEqual(parsed, { ok: true, minor: v });
  }
});
