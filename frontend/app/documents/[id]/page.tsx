import { Suspense } from "react";
import { ReviewView } from "./review-view";

export default function Page() {
  return (
    <Suspense fallback={<p className="text-sm text-stone-500">Loading document…</p>}>
      <ReviewView />
    </Suspense>
  );
}
