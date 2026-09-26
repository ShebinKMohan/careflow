import { Suspense } from "react";

import { VitalsPage } from "@/components/VitalsPage";

export default function VitalsRoute() {
  return (
    <Suspense>
      <VitalsPage />
    </Suspense>
  );
}
