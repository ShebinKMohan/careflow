import type { CardPayload } from "./card-reader";

/**
 * The seeded demo personas' synthetic mock national ids (infra/seed/personas.yaml, written onto the
 * HAPI Patients by infra/seed/add_mock_ids.py). For the simulated reader panel only. Synthetic;
 * never a real ID.
 */
export const MOCK_CARDS: ReadonlyArray<{ persona: string; card: CardPayload }> = [
  { persona: "chest-pain", card: { card_type: "mock_national_id", id_number: "784-1968-5433012-1" } },
  { persona: "stroke", card: { card_type: "mock_national_id", id_number: "784-1955-2530829-2" } },
  { persona: "child-drowsy", card: { card_type: "mock_national_id", id_number: "784-2024-6624039-1" } },
  { persona: "sepsis", card: { card_type: "mock_national_id", id_number: "784-1960-0810111-3" } },
  { persona: "routine", card: { card_type: "mock_national_id", id_number: "784-1992-1215279-9" } },
  { persona: "qr-phone", card: { card_type: "mock_national_id", id_number: "784-1997-8990608-0" } },
  { persona: "no-show", card: { card_type: "mock_national_id", id_number: "784-1981-1579240-4" } },
];

/** A card number as the panel shows it: masked, like the API's logs. */
export function maskedCard(idNumber: string): string {
  return `784-****-*******-${idNumber.slice(-1)}`;
}
