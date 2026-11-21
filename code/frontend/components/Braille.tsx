/** A word drawn as braille cells. Decorative: always hidden from screen readers,
 * which already read the real text next to it. */
const CELLS: Record<string, number[]> = {
  a: [1], b: [1, 2], c: [1, 4], d: [1, 4, 5], e: [1, 5], h: [1, 2, 5], i: [2, 4], m: [1, 3, 4],
  n: [1, 3, 4, 5], o: [1, 3, 5], p: [1, 2, 3, 4], s: [2, 3, 4], t: [2, 3, 4, 5], w: [2, 4, 5, 6],
};
const POS: Record<number, [number, number]> = { 1: [0, 0], 2: [0, 1], 3: [0, 2], 4: [1, 0], 5: [1, 1], 6: [1, 2] };

export default function Braille({ word, className = "braille" }: { word: string; className?: string }) {
  const letters = word.toLowerCase().split("").filter((c) => CELLS[c]);
  const gap = 14, cellW = 2 * gap, r = 5;
  const w = letters.length * (cellW + gap) - gap + 2 * r;
  const h = 2 * gap + 2 * r;
  return (
    <svg className={className} viewBox={`${-r} ${-r} ${w + r} ${h + r}`} aria-hidden="true" focusable="false">
      {letters.map((c, i) =>
        [1, 2, 3, 4, 5, 6].map((d) => {
          const [cx, cy] = POS[d];
          return (
            <circle key={`${i}-${d}`} cx={i * (cellW + gap) + cx * gap} cy={cy * gap} r={r} className={CELLS[c].includes(d) ? "" : "off"} />
          );
        }),
      )}
    </svg>
  );
}
