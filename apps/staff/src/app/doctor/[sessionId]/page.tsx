import Link from "next/link";
import { getTranslations } from "next-intl/server";

import { BriefingView } from "@/components/BriefingView";

export default async function DoctorBriefingPage({ params }: { params: Promise<{ sessionId: string }> }) {
  const { sessionId } = await params;
  const t = await getTranslations("doctor");
  const workspace = await getTranslations("workspace");
  return (
    <>
      <Link href="/doctor" className="staff-back-link"><span aria-hidden="true">←</span> {workspace("backBriefings")}</Link>
      <div className="staff-page-heading">
      <h1 className="text-display font-bold">{t("title")}</h1>
      <p className="text-ink-muted">{t.rich("session", { id: sessionId, ltr: (chunks) => <bdi dir="ltr">{chunks}</bdi> })}</p>
      </div>
      <BriefingView sessionId={sessionId} />
    </>
  );
}
