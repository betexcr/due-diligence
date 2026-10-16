import { Suspense } from "react";
import { ProjectView } from "./project-view";

export default function Page() {
  return (
    <Suspense fallback={<p className="text-sm text-stone-500">Loading project…</p>}>
      <ProjectView />
    </Suspense>
  );
}
