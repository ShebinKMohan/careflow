import { getTranslations } from "next-intl/server";

import { NurseSession } from "@/components/nurse/NurseSession";

export default async function NurseSessionPage({ params }: { params: Promise<{ sessionId: string }> }) {
  const { sessionId } = await params;
  const t = await getTranslations("nurse");
  return (
    <>
      <h1 className="text-display font-bold">{t("sessionTitle")}</h1>
      <p className="text-ink-muted">{t.rich("session", { id: sessionId, ltr: (chunks) => <bdi dir="ltr">{chunks}</bdi> })}</p>
      <NurseSession sessionId={sessionId} />
    </>
  );
}
