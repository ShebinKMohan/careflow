import { getTranslations } from "next-intl/server";

import { BriefingView } from "@/components/BriefingView";

export default async function DoctorBriefingPage({ params }: { params: Promise<{ sessionId: string }> }) {
  const { sessionId } = await params;
  const t = await getTranslations("doctor");
  return (
    <>
      <h1 className="text-display font-bold">{t("title")}</h1>
      <p className="text-ink-muted">{t.rich("session", { id: sessionId, ltr: (chunks) => <bdi dir="ltr">{chunks}</bdi> })}</p>
      <BriefingView sessionId={sessionId} />
    </>
  );
}
