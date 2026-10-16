"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

export function ExtractorStatus() {
  const [label, setLabel] = useState<string>("connecting…");
  const [ok, setOk] = useState<boolean | null>(null);

  useEffect(() => {
    api.health()
      .then((h) => {
        setOk(true);
        setLabel(h.extractor === "claude" ? `Claude · ${h.model}` : "Offline extractor (no API key)");
      })
      .catch(() => {
        setOk(false);
        setLabel("API unreachable");
      });
  }, []);

  return (
    <span className="flex items-center gap-2 text-xs text-stone-600">
      <span className={`h-2 w-2 rounded-full ${ok === null ? "bg-stone-300" : ok ? "bg-emerald-500" : "bg-rose-500"}`} />
      {label}
    </span>
  );
}
