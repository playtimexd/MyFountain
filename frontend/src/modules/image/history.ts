// Undo / redo with a bounded memory budget. Entries are closures (pixel patches or structural swaps).
export interface Entry {
  name: string
  undo(): void
  redo(): void
  bytes: number
  key?: string
  target?: unknown
  time: number
  merge?(next: Entry): boolean
}

export class History {
  entries: Entry[] = []
  pos = 0
  bytes = 0
  budget = 600e6
  maxSteps = 300
  private depth = 0
  private pending: Entry[] = []
  private pname = ''
  onChange: () => void = () => {}

  push(e: Omit<Entry, 'time'> & { time?: number }) {
    const en = e as Entry
    en.time = Date.now()
    if (this.depth > 0) { this.pending.push(en); return }
    this.commit(en)
  }
  private commit(e: Entry) {
    while (this.entries.length > this.pos) this.bytes -= this.entries.pop()!.bytes
    const last = this.entries[this.entries.length - 1]
    if (last && e.key && last.key === e.key && last.target === e.target && e.time - last.time < 900 && last.merge?.(e)) {
      last.time = e.time
      this.onChange()
      return
    }
    this.entries.push(e)
    this.pos = this.entries.length
    this.bytes += e.bytes
    while ((this.bytes > this.budget || this.entries.length > this.maxSteps) && this.entries.length > 1) {
      const x = this.entries.shift()!
      this.bytes -= x.bytes
      this.pos--
    }
    this.onChange()
  }
  begin(name: string) {
    if (this.depth++ === 0) { this.pending = []; this.pname = name }
  }
  end() {
    if (--this.depth > 0) return
    const list = this.pending
    this.pending = []
    if (!list.length) return
    if (list.length === 1) { list[0].name = this.pname || list[0].name; this.commit(list[0]); return }
    this.commit({
      name: this.pname, bytes: list.reduce((a, b) => a + b.bytes, 0), time: Date.now(),
      undo: () => { for (let i = list.length - 1; i >= 0; i--) list[i].undo() },
      redo: () => { for (const x of list) x.redo() },
    })
  }
  group<T>(name: string, fn: () => T): T {
    this.begin(name)
    try { return fn() } finally { this.end() }
  }
  get canUndo() { return this.pos > 0 }
  get canRedo() { return this.pos < this.entries.length }
  undo() { if (!this.canUndo) return false; this.entries[--this.pos].undo(); this.onChange(); return true }
  redo() { if (!this.canRedo) return false; this.entries[this.pos++].redo(); this.onChange(); return true }
  jump(to: number) {
    while (this.pos > to && this.undo());
    while (this.pos < to && this.redo());
  }
  clear() { this.entries = []; this.pos = 0; this.bytes = 0; this.onChange() }
}
