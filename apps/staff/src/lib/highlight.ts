/**
 * Split text around a span given in Unicode code points ([start, end), as the extractor and the
 * briefing count them), so a span after an emoji or an Arabic letter lands on the right
 * characters. JavaScript strings index UTF-16 units, so the text is split into code points first.
 */
export interface Highlighted {
  before: string;
  marked: string;
  after: string;
}

export function highlight(text: string, start: number, end: number): Highlighted {
  const points = Array.from(text);
  const from = Math.max(0, Math.min(start, points.length));
  const to = Math.max(from, Math.min(end, points.length));
  return {
    before: points.slice(0, from).join(""),
    marked: points.slice(from, to).join(""),
    after: points.slice(to).join(""),
  };
}
