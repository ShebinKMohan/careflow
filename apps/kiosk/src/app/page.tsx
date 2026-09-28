import { getLocale } from "next-intl/server";

import { StartFresh } from "@/components/StartFresh";
import { StartChoices } from "@/components/StartChoices";
import { isCloudPreview } from "@/lib/server/preview";

export default async function HomePage() {
  const ar = await getLocale() === "ar";
  return (
    <>
      <StartFresh />
      <section className="entry-intro"><p className="kiosk-eyebrow">{ar ? "أهلاً بك في نورث ستار" : "WELCOME TO NORTHSTAR"}</p><h1>{ar ? "رعايتك تبدأ من هنا." : "Your care starts here."}</h1><p>{ar ? "أخبرنا بسبب زيارتك. سنساعدك في الخطوة التالية." : "Tell us what brings you in. We’ll help with the next step."}</p><div className="entry-service-list"><span>{ar ? "الاستشارات" : "Consultations"}</span><span>{ar ? "المتابعة" : "Follow-up visits"}</span><span>{ar ? "الفحوص والنتائج" : "Tests & results"}</span><span>{ar ? "غير متأكد؟ نساعدك" : "Not sure? We’ll help"}</span></div></section>
      <StartChoices preview={isCloudPreview()} />
    </>
  );
}
