/* Local desktop update controls. Mutation authority remains in the native bridge. */
(() => {
  'use strict';
  const node = id => document.getElementById(id);
  const panel = node('updatePanel');
  const toggle = node('settingsToggle');
  const phases = {
    idle: '尚未检测更新。点击“检测更新”查询 GitHub 源代码。',
    checking: '正在检测 GitHub 源代码更新…',
    up_to_date: '当前构建已包含最新上游提交。',
    available: '发现可用更新。点击“更新软件”开始本地构建。',
    preparing: '正在准备独立源码目录并合并更新…',
    validating: '正在验证源码与转换功能…',
    building: '正在构建桌面软件，请稍候…',
    ready: '更新已构建完成，重启后生效。',
    installing: '正在准备重启并应用更新，请稍候…',
    failed: '更新未完成，请检查提示后重试。',
  };
  const active = new Set(['checking', 'preparing', 'validating', 'building', 'installing']);
  const methods = ['get_update_state', 'check_updates', 'start_update', 'choose_source_workspace', 'restart_after_update'];
  let snapshot = {phase: 'idle'};
  let nativeReady = false;
  let pending = false;
  let restarting = false;
  let timer;
  let epoch = 0;
  let localError = '';

  function bridgeAvailable() {
    const api = window.pywebview?.api;
    return !!api && methods.every(method => typeof api[method] === 'function');
  }
  function installing() { return restarting || snapshot.phase === 'installing'; }
  function queueBusy() {
    return !!window.MusicUnlockSaves?.isBusy() || state.busy > 0 || state.items.some(item => item.status === 'wait' || item.status === 'run');
  }
  function text(id, value, fallback) { node(id).textContent = value ? String(value) : fallback; }
  function revision(id, value, fallback, short = false) {
    const valid = typeof value === 'string' && /^[a-f0-9]{40}$/i.test(value);
    text(id, valid ? (short ? value.slice(0, 12) : value) : '', fallback);
    node(id).title = valid ? value : '';
  }
  function paint() {
    const enabled = nativeReady && bridgeAvailable();
    const busy = pending || active.has(snapshot.phase) || restarting;
    const ready = snapshot.build_ready && snapshot.can_restart;
    panel.dataset.phase = snapshot.phase;
    panel.setAttribute('aria-busy', String(busy));
    text('currentBuild', snapshot.current_build, '尚未获取');
    revision('currentRevision', snapshot.current_revision, '尚未记录上游提交', true);
    revision('latestRevision', snapshot.latest_revision, '尚未检测');
    text('sourceWorkspace', snapshot.source_workspace, '尚未配置');
    text('updateLogPath', snapshot.log_path, '尚无日志');
    const checked = snapshot.checked_at ? new Date(snapshot.checked_at) : null;
    text('updateCheckedAt', checked && !Number.isNaN(checked.getTime()) ? checked.toLocaleString('zh-CN', {hour12: false}) : snapshot.checked_at, '尚未检测');
    text('updateStatus', !enabled ? '软件更新仅在桌面版可用；请等待桌面连接就绪。' : localError || (restarting ? phases.installing : snapshot.message || phases[snapshot.phase]), phases.idle);
    text('latestSummary', snapshot.latest_summary, '');
    node('latestSummary').hidden = !snapshot.latest_summary;
    node('updateProgress').hidden = !busy;
    node('checkUpdates').disabled = !enabled || busy;
    node('updateSoftware').disabled = !enabled || busy || snapshot.phase !== 'available' || !snapshot.can_update;
    node('chooseWorkspace').disabled = !enabled || busy;
    node('restartUpdate').hidden = !ready && !installing();
    node('restartUpdate').disabled = !enabled || busy || !ready || queueBusy();
    node('restartHint').hidden = !ready && !installing();
    const built = typeof snapshot.ready_revision === 'string' && /^[a-f0-9]{40}$/i.test(snapshot.ready_revision)
      ? `已构建上游提交 ${snapshot.ready_revision.slice(0, 12)}。` : '';
    text('restartHint', built + (window.MusicUnlockSaves?.isBusy() ? '正在保存文件，请等待保存完成后再重启。' : queueBusy() ? '请等待转换队列完成，再保存当前结果并重启。' : '重启前请先保存当前转换结果；重启会关闭此窗口。'), '');
    node('drop').setAttribute('aria-disabled', String(installing()));
    node('picker').disabled = installing();
  }
  function schedule() {
    clearTimeout(timer);
    if (nativeReady && (!panel.hidden || active.has(snapshot.phase) || restarting)) {
      timer = setTimeout(refresh, 1500);
    }
  }
  function accept(value) {
    if (!value || typeof value !== 'object' || !Object.hasOwn(phases, value.phase)) {
      throw new Error('桌面更新状态不可用，请重新打开设置重试。');
    }
    snapshot = value;
  }
  async function refresh() {
    if (!nativeReady || !bridgeAvailable() || pending) { schedule(); return; }
    const request = ++epoch;
    try {
      const value = await window.pywebview.api.get_update_state();
      if (request !== epoch) return;
      accept(value);
      localError = '';
    } catch (error) {
      if (request === epoch) localError = '读取更新状态失败：' + String(error?.message || error);
    } finally {
      if (request === epoch) { paint(); schedule(); }
    }
  }
  async function invoke(method) {
    if (!nativeReady || !bridgeAvailable() || pending) return;
    if (method === 'restart_after_update' && queueBusy()) return;
    const request = ++epoch;
    pending = true;
    localError = '';
    if (method === 'restart_after_update') restarting = true;
    paint();
    try {
      const value = await window.pywebview.api[method]();
      if (request !== epoch) return;
      accept(value);
      if (method === 'restart_after_update' && snapshot.phase !== 'installing') restarting = false;
    } catch (error) {
      restarting = false;
      localError = '操作未完成：' + String(error?.message || error);
    } finally {
      if (request === epoch) { pending = false; paint(); schedule(); }
    }
  }
  function setOpen(open) {
    panel.hidden = !open;
    toggle.setAttribute('aria-expanded', String(open));
    if (open) { node('updateHeading').focus(); refresh(); }
    else toggle.focus();
    schedule();
  }
  toggle.addEventListener('click', () => setOpen(panel.hidden));
  node('closeSettings').addEventListener('click', () => setOpen(false));
  panel.addEventListener('keydown', event => {
    if (event.key === 'Escape') { event.preventDefault(); setOpen(false); }
  });
  node('checkUpdates').addEventListener('click', () => invoke('check_updates'));
  node('updateSoftware').addEventListener('click', () => invoke('start_update'));
  node('chooseWorkspace').addEventListener('click', () => invoke('choose_source_workspace'));
  node('restartUpdate').addEventListener('click', () => invoke('restart_after_update'));
  window.addEventListener('musicunlock:queuechange', paint);
  window.addEventListener('musicunlock:savestate', paint);
  window.addEventListener('pywebviewready', () => {
    nativeReady = bridgeAvailable();
    paint();
    if (!panel.hidden) refresh();
    else schedule();
  });
  window.MusicUnlockUpdates = Object.freeze({isInstalling: installing});
  nativeReady = bridgeAvailable();
  paint();
})();
