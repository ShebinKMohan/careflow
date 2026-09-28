import { getTranslations } from "next-intl/server";

import { BoardView } from "@/components/BoardView";

const IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$/;

export default async function BoardPage({
  searchParams,
}: {
  searchParams: Promise<{ department?: string | string[] }>;
}) {
  const { department: requested } = await searchParams;
  const department = typeof requested === "string" && IDENTIFIER.test(requested) ? requested : "ed";
  const t = await getTranslations("board");
  const workspace = await getTranslations("workspace");
  return (
    <>
      <div className="staff-page-heading">
        <p className="staff-eyebrow">{workspace("workspace")}</p>
      <h1 className="text-display font-bold">{t("title", { department })}</h1>
        <p>{workspace("boardDescription")}</p>
      </div>
      <BoardView department={department} />
    </>
  );
}
