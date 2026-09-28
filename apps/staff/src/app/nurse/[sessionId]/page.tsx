import Link from "next/link";
import { getTranslations } from "next-intl/server";

import { NurseSession } from "@/components/nurse/NurseSession";

export default async function NurseSessionPage({ params }: { params: Promise<{ sessionId: string }> }) {
  const { sessionId } = await params;
  const t = await getTranslations("nurse");
  const workspace = await getTranslations("workspace");
  return (
    <>
      <Link href="/nurse" className="staff-back-link"><span aria-hidden="true">←</span> {workspace("backWorklist")}</Link>
      <div className="staff-page-heading">
      <h1 className="text-display font-bold">{t("sessionTitle")}</h1>
      <p className="text-ink-muted">{t.rich("session", { id: sessionId, ltr: (chunks) => <bdi dir="ltr">{chunks}</bdi> })}</p>
      </div>
      <NurseSession sessionId={sessionId} />
    </>
  );
}
