/**
 * The doctor briefing, the public board and the admin status (M1-17), against fixtures built from
 * the contract's examples (tests/fixtures/api). These are mock-backed component tests: the same
 * screens on the real API and a persisted briefing are the journeys' job (M1-19, M2-12).
 */
import type { Schema } from "@careflow/contracts";
import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AdminStatusView } from "@/components/AdminStatusView";
import { BoardView, BOARD_POLL_MS } from "@/components/BoardView";
import { BriefingView } from "@/components/BriefingView";
import { ProvenanceChip } from "@/components/ProvenanceChip";
import { FHIR_BASE_URL, SourcePopover } from "@/components/SourcePopover";
import { MESSAGES } from "@/i18n/messages";
import { api } from "@/lib/api";
import { highlight } from "@/lib/highlight";

import providers from "./fixtures/api/admin-providers.json";
import adminStatus from "./fixtures/api/admin-status.json";
import leakedBoard from "./fixtures/api/board-with-leaked-fields.json";
import chestPain from "./fixtures/api/briefing-chest-pain.json";
import dropped from "./fixtures/api/briefing-dropped.json";

vi.mock("@/lib/api", () => ({ api: { GET: vi.fn() } }));
vi.mock("next/navigation", () => ({ usePathname: () => "/doctor/ses_chest_pain" }));

const get = vi.mocked(api.GET);
type Answer = { data?: unknown; response: Response };
const ok = (data: unknown): Answer => ({ data, response: new Response(null, { status: 200 }) });

function withIntl(children: ReactNode) {
  return (
    <NextIntlClientProvider locale="en" messages={MESSAGES.en} timeZone="Asia/Dubai">
      {children}
    </NextIntlClientProvider>
  );
}

const PROVENANCES = ["EHR", "PATIENT_STATED", "DEVICE_MEASURED", "NURSE_CONFIRMED", "MODEL_INFERRED"] as const;
const LABELS = { EHR: "EHR", PATIENT_STATED: "Patient said", DEVICE_MEASURED: "Device", NURSE_CONFIRMED: "Nurse", MODEL_INFERRED: "Model inferred" };
const briefing = chestPain as unknown as Schema<"Briefing">;
const items: Schema<"BriefingItem">[] = briefing.sections.flatMap((section) => section.items);
const firstOf = (provenance: string) => items.find((item) => item.provenance === provenance && !item.source_missing)!;

afterEach(() => {
  get.mockReset();
  vi.useRealTimers();
});

describe("highlight (code points, as the extractor counts them)", () => {
  it("marks an ASCII span", () => {
    expect(highlight("chest pain since 7", 0, 10)).toEqual({ before: "", marked: "chest pain", after: " since 7" });
  });
  it("counts an emoji as one character", () => {
    expect(highlight("😷 my chest hurts", 2, 4)).toEqual({ before: "😷 ", marked: "my", after: " chest hurts" });
  });
  it("marks spans at both edges and clamps out-of-range ends", () => {
    expect(highlight("pain", 0, 4)).toEqual({ before: "", marked: "pain", after: "" });
    expect(highlight("pain", 3, 99)).toEqual({ before: "pai", marked: "n", after: "" });
    expect(highlight("pain", 5, 9)).toEqual({ before: "pain", marked: "", after: "" });
  });
  it("marks Arabic text by characters", () => {
    expect(highlight("ألم في الصدر", 7, 12).marked).toBe("الصدر");
  });
});

describe("ProvenanceChip", () => {
  it.each(PROVENANCES)("labels %s with text, never colour alone", (provenance) => {
    render(withIntl(<ProvenanceChip provenance={provenance} />));
    const chip = screen.getByText(LABELS[provenance]);
    expect(chip).toHaveAttribute("data-provenance", provenance);
    expect(chip.className).toContain(provenance === "MODEL_INFERRED" ? "border-dashed" : "border-solid");
  });
  it("says 'Source missing' in red when a source failed the check", () => {
    render(withIntl(<ProvenanceChip provenance="DEVICE_MEASURED" missing />));
    const chip = screen.getByText("Source missing");
    expect(chip).toHaveAttribute("data-missing", "true");
    expect(chip.className).toContain("text-prov-missing");
  });
});

describe("SourcePopover", () => {
  it("opens with Tab and Enter and closes with Escape, returning focus", async () => {
    const user = userEvent.setup();
    render(withIntl(<SourcePopover item={firstOf("PATIENT_STATED")} triage={briefing.triage} />));
    await user.tab();
    const chip = screen.getByRole("button", { name: /Patient said/ });
    expect(chip).toHaveFocus();
    expect(chip).toHaveAttribute("aria-expanded", "false");
    await user.keyboard("{Enter}");
    expect(chip).toHaveAttribute("aria-expanded", "true");
    const dialog = screen.getByRole("dialog", { name: "Source" });
    const quote = (firstOf("PATIENT_STATED").source as { quote: string }).quote;
    expect(within(dialog).getByText(quote, { selector: "mark" })).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(chip).toHaveAttribute("aria-expanded", "false");
    expect(chip).toHaveFocus();
  });

  it("shows each kind of source", async () => {
    const user = userEvent.setup();
    const cases: [string, (dialog: HTMLElement) => void][] = [
      [
        "EHR",
        (dialog) => {
          const source = firstOf("EHR").source as { resource_type: string; id: string };
          expect(within(dialog).getByRole("link", { name: "Open in the EHR" })).toHaveAttribute(
            "href",
            `${FHIR_BASE_URL}/${source.resource_type}/${source.id}`,
          );
        },
      ],
      ["DEVICE_MEASURED", (dialog) => expect(dialog).toHaveTextContent("Simulated device.")],
      [
        "NURSE_CONFIRMED",
        (dialog) => expect(dialog).toHaveTextContent((firstOf("NURSE_CONFIRMED").source as { staff_user_id: string }).staff_user_id),
      ],
      [
        "MODEL_INFERRED",
        (dialog) => expect(dialog).toHaveTextContent(`Suggestion. Nurse confirmed level ${briefing.triage.confirmed.scale} ${briefing.triage.confirmed.level}.`),
      ],
    ];
    for (const [provenance, check] of cases) {
      const { unmount } = render(withIntl(<SourcePopover item={firstOf(provenance)} triage={briefing.triage} />));
      await user.click(screen.getByRole("button", { name: new RegExp(LABELS[provenance as keyof typeof LABELS]) }));
      check(screen.getByRole("dialog"));
      unmount();
    }
  });
});

describe("BriefingView", () => {
  it("shows every line with a labelled chip, and a failed source in red", async () => {
    get.mockResolvedValue(ok(chestPain) as never);
    render(withIntl(<BriefingView sessionId="ses_chest_pain" />));
    const briefing = await screen.findByTestId("briefing");
    for (const provenance of PROVENANCES) {
      expect(within(briefing).getAllByText(LABELS[provenance]).length).toBeGreaterThan(0);
    }
    const chips = briefing.querySelectorAll("[data-provenance]");
    expect(chips.length).toBe(items.length); // safety rule 4: no line without its chip
    expect(within(briefing).getByText("Source missing")).toHaveAttribute("data-missing", "true");
  });

  it("says so when the summary was dropped", async () => {
    get.mockResolvedValue(ok(dropped) as never);
    render(withIntl(<BriefingView sessionId="ses_chest_pain" />));
    expect(await screen.findByText("Summary unavailable. Showing verified facts only.")).toBeInTheDocument();
  });
});

describe("BoardView (public)", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });

  it("shows token numbers and rooms only: no name, level or complaint", async () => {
    get.mockResolvedValue(ok(leakedBoard) as never);
    render(withIntl(<BoardView department="ed" />));
    const board = await screen.findByTestId("board");
    await within(board).findByText("A-014");
    expect(board).toHaveTextContent("Resus 2");
    expect(board).toHaveTextContent("B-007");
    expect(board).toHaveTextContent("2 people waiting");
    for (const leaked of ["Mariam", "Saleh", "Omar", "Haddad", "Layla", "Karim", "chest pain", "level", "Level"]) {
      expect(document.body.textContent).not.toContain(leaked);
    }
  });

  it("marks a board it could not refresh, then shows a fresh one", async () => {
    const fresh = [{ token_code: "C-101", room: "Room 9", status: "called" }];
    get
      .mockResolvedValueOnce(ok(leakedBoard) as never)
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValueOnce(ok(fresh) as never);
    render(withIntl(<BoardView department="ed" />));
    await screen.findByText("A-014");
    expect(screen.queryByTestId("board-stale")).not.toBeInTheDocument();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(BOARD_POLL_MS);
    });
    expect(await screen.findByTestId("board-stale")).toHaveTextContent(/Connection lost\. This board may be out of date \(last updated .+\)\./);
    expect(screen.getByText("A-014")).toBeInTheDocument(); // the last board stays, marked
    await act(async () => {
      await vi.advanceTimersByTimeAsync(BOARD_POLL_MS);
    });
    expect(await screen.findByText("C-101")).toBeInTheDocument();
    expect(screen.queryByText("A-014")).not.toBeInTheDocument();
    expect(screen.queryByTestId("board-stale")).not.toBeInTheDocument();
  });
});

describe("AdminStatusView", () => {
  it("lists every AI task in the config, marks stubs and names a fixture model", async () => {
    get.mockImplementation(((path: string) => Promise.resolve(ok(path === "/v1/admin/status" ? adminStatus : providers))) as never);
    render(withIntl(<AdminStatusView />));
    for (const task of providers.tasks.map((row) => row.task)) {
      expect((await screen.findAllByText(task)).length).toBeGreaterThan(0);
    }
    const urgencyPort = document.querySelector('[data-row="port:urgency"]') as HTMLElement;
    expect(within(urgencyPort).getByText("Stub")).toBeInTheDocument();
    expect(within(urgencyPort).getByText("Degraded")).toBeInTheDocument();
    expect(screen.getByText(/NOT A RESULT/)).toBeInTheDocument();
    const hapi = document.querySelector('[data-row="hapi-fhir"]') as HTMLElement;
    expect(within(hapi).getByText("Down")).toBeInTheDocument();
    // Configured is not running: the write-back's failures and the voice service's absence show.
    const writeback = document.querySelector('[data-row="fhir-writeback"]') as HTMLElement;
    expect(within(writeback).getByText("Degraded")).toBeInTheDocument();
    expect(writeback).toHaveTextContent("2 failed write-backs in this clinic");
    for (const kind of ["stt", "tts"]) {
      const speech = document.querySelector(`[data-row="speech:${kind}"]`) as HTMLElement;
      expect(within(speech).getByText("Down")).toBeInTheDocument();
      expect(speech).toHaveTextContent("voice service not answering");
    }
  });
});
