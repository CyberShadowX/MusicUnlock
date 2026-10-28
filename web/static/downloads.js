/* A single result-ID-only save pipeline for native dialogs and browser downloads. */
(() => {
  'use strict';
  const active = new Set(['choosing', 'preparing', 'saving']);
  let busy = false;
  let timer;
  let polling = false;
  let epoch = 0;
  const status = document.getElementById('saveStatus');
  function paint(snapshot) {
    if (!snapshot || typeof snapshot.phase !== 'string') return;
    const progress = Number.isFinite(snapshot.completed) && Number.isFinite(snapshot.total)
      ? `（${snapshot.completed}/${snapshot.total}）` : '';
    status.textContent = (snapshot.message || '正在保存…') + progress;
    status.dataset.phase = snapshot.phase;
    status.setAttribute('aria-busy', String(busy));
    window.dispatchEvent(new CustomEvent('musicunlock:savestate', {detail: {...snapshot, busy}}));
  }
  function controls() {
    document.querySelectorAll('[data-save-action], #zipAll, #clear, #list .acts .ghost').forEach(node => {
      if (busy) {
        if (!node.hasAttribute('data-save-disabled')) node.dataset.saveDisabled = String(node.disabled);
        node.disabled = true;
      } else if (node.hasAttribute('data-save-disabled')) {
        node.disabled = node.dataset.saveDisabled === 'true';
        delete node.dataset.saveDisabled;
      }
    });
  }
  // Rendering new queue rows during saving must not re-enable destructive actions.
  window.addEventListener('musicunlock:queuechange', controls);
  async function browserSave(ids, archive) {
    paint({phase: 'preparing', message: archive ? '正在准备 ZIP 下载…' : '正在准备下载…'});
    const url = archive ? '/api/zip?ids=' + ids.map(encodeURIComponent).join(',') : '/api/download/' + encodeURIComponent(ids[0]);
    const response = await fetch(url);
    if (!response.ok) {
      let message;
      try { const error = await response.json(); message = error.error || error.message; } catch {}
      throw new Error(message || `下载失败（${response.status}）`);
    }
    // Blob URLs make a browser download explicit without navigating the app.
    // Desktop batches use the native disk-backed path, never this browser fallback.
    const blob = await response.blob();
    const objectUrl = URL.createObjectURL(blob);
    const link = document.createElement('a');
    const disposition = response.headers.get('Content-Disposition') || '';
    const extended = disposition.match(/filename\*=UTF-8''([^;]+)/i);
    const plain = disposition.match(/filename="([^"]+)"|filename=([^;]+)/i);
    let filename = archive ? 'unlocked.zip' : 'audio';
    try { filename = extended ? decodeURIComponent(extended[1]) : plain ? plain[1] || plain[2] : filename; } catch {}
    link.href = objectUrl;
    link.download = filename;
    link.hidden = true;
    document.body.appendChild(link);
    paint({phase: 'saving', message: '已准备下载，正在交给浏览器…'});
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(objectUrl), 60000);
    return {phase: 'completed', message: '已交给浏览器下载，请查看浏览器的下载记录。'};
  }
  async function save(ids, archive = false) {
    if (busy || window.MusicUnlockUpdates?.isInstalling()) return;
    if (!Array.isArray(ids) || !ids.length || ids.some(id => typeof id !== 'string' || !id)) {
      paint({phase: 'failed', message: '没有可保存的转换结果。'}); return;
    }
    busy = true;
    controls();
    paint({phase: 'choosing', message: '请选择保存位置…'});
    const token = ++epoch;
    try {
      const api = window.pywebview?.api;
      let result;
      if (typeof api?.save_results === 'function' && typeof api?.get_save_state === 'function') {
        timer = setInterval(async () => {
          if (polling) return;
          polling = true;
          try {
            const next = await api.get_save_state();
            if (busy && token === epoch && active.has(next?.phase)) paint(next);
          } catch { /* The authoritative save call reports bridge failure. */ }
          finally { polling = false; }
        }, 250);
        result = await api.save_results(ids.slice(), !!archive);
      } else if (window.pywebview) {
        throw new Error('桌面保存连接尚未就绪，请稍后重试。');
      } else result = await browserSave(ids, archive);
      if (!result || !['completed', 'cancelled', 'failed'].includes(result.phase)) throw new Error('保存未完成，请重试。');
      busy = false;
      paint(result);
    } catch (error) {
      busy = false;
      paint({phase: 'failed', message: '保存失败：' + (error.message || String(error))});
    } finally {
      ++epoch;
      clearInterval(timer);
      busy = false;
      controls();
    }
  }
  window.MusicUnlockSaves = Object.freeze({save, isBusy: () => busy});
})();
