// Tiny master-clock holder: the player writes `time` every frame, UI parts subscribe for DOM-level updates
// (so React never re-renders per frame).
type Sub = (t: number) => void
const subs = new Set<Sub>()
export const clock = {
  time: 0,
  playing: false,
  set(t: number) { this.time = t; subs.forEach((f) => f(t)) },
  subscribe(f: Sub) { subs.add(f); return () => { subs.delete(f) } },
}
