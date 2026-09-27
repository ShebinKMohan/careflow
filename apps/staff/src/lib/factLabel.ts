/** Top-level groups that need no repeating in a label ("vitals.heart_rate" → "heart rate"). */
const SELF_EVIDENT = new Set(["vitals", "patient"]);
const WORDS: Record<string, string> = { spo2: "SpO2", bp: "BP", acvpu: "ACVPU", ecg: "ECG", fast: "FAST", ehr: "EHR" };
const FIELDS: Record<string, string> = { temperature_c: "temperature", age_years: "age", age_months: "age (months)" };

/** A readable label for a fact's path in StructuredFacts, e.g. "chest_pain.onset" → "chest pain onset". */
export function factLabel(path: string): string {
  const parts = path.split(".").filter((part) => !/^\d+$/.test(part) && part !== "value");
  if (parts.length > 1 && SELF_EVIDENT.has(parts[0] ?? "")) parts.shift();
  const words = parts
    .slice(-2)
    .map((part) => FIELDS[part] ?? part)
    .join(" ")
    .split(/[_ ]/)
    .map((word) => WORDS[word] ?? word);
  return words.filter((word, i) => i === 0 || word !== words[i - 1]).join(" ");
}
