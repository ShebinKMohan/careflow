"use client";

import { Card } from "@careflow/ui";

import { api } from "@/lib/api";
import { useApiData } from "@/lib/useApiData";

import { ApiStatus } from "./ApiStatus";

/** GET /v1/doctor/sessions/{session_id}/briefing. Every item shows its provenance (safety rule 4). */
export function BriefingView({ sessionId }: { sessionId: string }) {
  const state = useApiData(
    () => api.GET("/v1/doctor/sessions/{session_id}/briefing", { params: { path: { session_id: sessionId } } }),
    `briefing:${sessionId}`,
  );
  return (
    <ApiStatus state={state}>
      {(briefing) => (
        <div className="flex flex-col gap-4">
          {briefing.sections.map((section) => (
            <Card key={section.title} title={section.title}>
              <ul className="flex flex-col gap-3">
                {section.items.map((item) => (
                  <li key={item.fact_id ?? item.text} className="flex flex-wrap items-baseline gap-3">
                    <span className="rounded-control bg-secondary px-2 font-mono text-label text-on-secondary">
                      {item.provenance}
                    </span>
                    <span>{item.text}</span>
                  </li>
                ))}
              </ul>
            </Card>
          ))}
        </div>
      )}
    </ApiStatus>
  );
}
