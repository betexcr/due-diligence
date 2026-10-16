// Shared class strings. Kept out of components/ui.tsx ("use client") so server
// components such as the root layout can import them as plain strings.

/** Visible keyboard focus for every interactive element we style ourselves. */
export const FOCUS_RING = "outline-none focus-visible:ring-2 focus-visible:ring-sky-600 focus-visible:ring-offset-1";

export const INPUT_CLASS = `rounded border border-stone-300 bg-white px-2 py-1.5 text-sm ${FOCUS_RING}`;
