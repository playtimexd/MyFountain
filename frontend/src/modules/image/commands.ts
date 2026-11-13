// Shared command implementations used by both menus and keyboard shortcuts.
import { hooks } from './doc'
import { saveProject, exportFile, copyToClipboard } from './io'
import { clearSel, copySel, fillSel, layerViaCopy, pasteInternal, selModify, toggleMask } from './ops'
import { closeDoc, curDoc, openDialog, useUI } from './store'
import { fitView, actualSize, zoomCenter } from './view'
import { addGuide, clearGuides } from './guides'

export const cmd = {
  undo() { curDoc()?.undo() },
  redo() { curDoc()?.redo() },
  newDoc() { openDialog({ kind: 'new' }) },
  openFile() { window.dispatchEvent(new Event('ie-open-file')) },
  placeFile() { window.dispatchEvent(new Event('ie-place-file')) },
  save() { const d = curDoc(); if (d) void saveProject(d) },
  exportAs() { if (curDoc()) openDialog({ kind: 'export' }) },
  quickPng() { const d = curDoc(); if (d) void exportFile(d, 'png', 1, 1) },
  copyMerged() { const d = curDoc(); if (d) void copyToClipboard(d) },
  closeDoc() {
    const d = curDoc()
    if (!d) return
    if (d.dirty) openDialog({ kind: 'closeConfirm', docId: d.id }); else closeDoc(d.id)
  },
  toggleRulers() { useUI.setState((s) => ({ rulers: !s.rulers })); hooks.render() },
  toggleGuides() { useUI.setState((s) => ({ showGuides: !s.showGuides })); hooks.render() },
  toggleSnap() { useUI.setState((s) => ({ snap: !s.snap })) },
  clearGuides() { const d = curDoc(); if (d) clearGuides(d) },
  newGuide(axis: 'h' | 'v') {
    const d = curDoc()
    if (!d) return
    openDialog({ kind: 'prompt', title: axis === 'h' ? 'New Horizontal Guide' : 'New Vertical Guide', label: 'Position (px)', value: Math.round((axis === 'h' ? d.h : d.w) / 2), min: 0, max: 20000, onOk: (v) => { const dd = curDoc(); if (dd) addGuide(dd, axis, v) } })
  },
  cut() { const d = curDoc(); if (d) void copySel(d, true) },
  copy() { const d = curDoc(); if (d) void copySel(d) },
  paste() { pasteInternal(curDoc()) },
  clear() { const d = curDoc(); if (d) clearSel(d) },
  fillFg() { const d = curDoc(); if (d) fillSel(d, useUI.getState().fg, 'Fill with foreground') },
  fillBg() { const d = curDoc(); if (d) fillSel(d, useUI.getState().bg, 'Fill with background') },
  selectAll() { curDoc()?.selectAll() },
  deselect() { curDoc()?.deselect() },
  invertSel() { curDoc()?.invertSel() },
  layerViaCopy() { const d = curDoc(); if (d) layerViaCopy(d) },
  layerViaCut() { const d = curDoc(); if (d) layerViaCopy(d, true) },
  newLayer() { curDoc()?.newRaster(`Layer ${(curDoc()?.layers.filter((l) => l.type !== 'group').length ?? 0) + 1}`) },
  duplicateLayer() { const d = curDoc(); if (d?.active) d.duplicate(d.active.id) },
  deleteLayer() { const d = curDoc(); if (d?.active) d.removeLayer(d.active.id) },
  group() { curDoc()?.groupLayers() },
  ungroup() { const d = curDoc(); if (d?.active?.type === 'group') d.ungroup(d.active.id) },
  mergeDown() { const d = curDoc(); if (d?.active) d.mergeDown(d.active.id) },
  flatten() { curDoc()?.flattenImage() },
  mergeVisible() { curDoc()?.mergeVisible() },
  rasterize() { const d = curDoc(); if (d?.active) { d.bake(d.active); d.changed() } },
  moveUp() { const d = curDoc(); if (d?.active) d.moveBy(d.active.id, 1) },
  moveDown() { const d = curDoc(); if (d?.active) d.moveBy(d.active.id, -1) },
  toTop() { const d = curDoc(); if (d?.active) d.moveBy(d.active.id, 9999) },
  toBottom() { const d = curDoc(); if (d?.active) d.moveBy(d.active.id, -9999) },
  toggleMask() { const d = curDoc(); if (d) toggleMask(d) },
  clipToBelow() { const d = curDoc(); const L = d?.active; if (d && L && d.siblingsOf(L).indexOf(L) > 0) d.mutate(L, L.clip ? 'Release clipping mask' : 'Create clipping mask', (l) => { l.clip = !l.clip }) },
  fit() { const d = curDoc(); if (d) { fitView(d); d.view.fitted = true; hooks.render(); hooks.ui() } },
  zoom100() { const d = curDoc(); if (d) { actualSize(d); hooks.render(); hooks.ui() } },
  zoomIn() { const d = curDoc(); if (d) { zoomCenter(d, d.view.zoom * 1.25); hooks.render(); hooks.ui() } },
  zoomOut() { const d = curDoc(); if (d) { zoomCenter(d, d.view.zoom / 1.25); hooks.render(); hooks.ui() } },
  featherSel() { promptSel('Feather Selection', 'Feather radius (px)', 8, 0, 200, 'feather') },
  growSel() { promptSel('Grow Selection', 'Grow by (px)', 4, 1, 200, 'grow') },
  shrinkSel() { promptSel('Shrink Selection', 'Shrink by (px)', 4, 1, 200, 'shrink') },
  borderSel() { promptSel('Border Selection', 'Border width (px)', 6, 1, 100, 'border') },
}
function promptSel(title: string, label: string, value: number, min: number, max: number, kind: 'feather' | 'grow' | 'shrink' | 'border') {
  const d = curDoc()
  if (!d?.sel) { hooks.toast('There is no selection', 'error'); return }
  openDialog({ kind: 'prompt', title, label, value, min, max, onOk: (v) => { const dd = curDoc(); if (dd) selModify(dd, kind, v) } })
}
