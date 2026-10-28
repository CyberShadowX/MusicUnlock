/* Progressive enhancement: the native select remains the conversion source. */
(() => {
  'use strict';
  const select = document.getElementById('format');
  if (!select || select.multiple || document.getElementById('formatTrigger')) return;
  const trigger = document.createElement('button');
  trigger.type = 'button';
  trigger.id = 'formatTrigger';
  trigger.setAttribute('role', 'combobox');
  trigger.setAttribute('aria-label', '输出格式');
  trigger.setAttribute('aria-haspopup', 'listbox');
  trigger.setAttribute('aria-controls', 'formatListbox');
  trigger.setAttribute('aria-expanded', 'false');
  const menu = document.createElement('div');
  menu.id = 'formatListbox';
  menu.setAttribute('role', 'listbox');
  menu.setAttribute('aria-label', '输出格式');
  menu.hidden = true;
  // Popover uses the browser top layer; fixed positioning also works on older engines.
  const topLayer = typeof menu.showPopover === 'function';
  if (topLayer) menu.setAttribute('popover', 'manual');
  let opened = false;
  let active = -1;
  let options = [];
  let search = '';
  let searchTimer;
  function enabled() { return options.map((option, index) => option.disabled ? -1 : index).filter(index => index >= 0); }
  function sync() {
    options = [...select.options];
    trigger.textContent = (options[select.selectedIndex]?.text || '选择输出格式') + ' ▾';
    trigger.disabled = select.disabled;
    if (opened) close();
  }
  function highlight(index) {
    if (index < 0 || !options[index] || options[index].disabled) return;
    active = index;
    [...menu.children].forEach((node, item) => node.dataset.active = String(item === active));
    trigger.setAttribute('aria-activedescendant', 'formatOption' + active);
    menu.children[active].scrollIntoView({block: 'nearest'});
  }
  function position() {
    if (!opened) return;
    const box = trigger.getBoundingClientRect();
    const view = window.visualViewport;
    const left = view?.offsetLeft || 0, top = view?.offsetTop || 0;
    const width = view?.width || innerWidth, height = view?.height || innerHeight;
    const anchorTop = Math.max(top + 8, Math.min(box.top, top + height - 8));
    const anchorBottom = Math.max(top + 8, Math.min(box.bottom, top + height - 8));
    const below = Math.max(0, top + height - anchorBottom - 12);
    const above = Math.max(0, anchorTop - top - 12);
    const up = below < Math.min(menu.scrollHeight, 260) && above > below;
    const available = up ? above : below;
    menu.style.width = Math.min(Math.max(box.width, 176), width - 16) + 'px';
    menu.style.maxHeight = Math.min(280, available) + 'px';
    menu.style.left = Math.max(left + 8, Math.min(box.left, left + width - menu.offsetWidth - 8)) + 'px';
    menu.style.top = Math.max(top + 8, Math.min(up ? anchorTop - menu.offsetHeight - 6 : anchorBottom + 6, top + height - menu.offsetHeight - 8)) + 'px';
  }
  function open() {
    if (select.disabled || opened) return;
    menu.replaceChildren();
    options = [...select.options];
    options.forEach((option, index) => {
      const node = document.createElement('div');
      node.id = 'formatOption' + index;
      node.setAttribute('role', 'option');
      node.setAttribute('aria-selected', String(option.selected));
      node.setAttribute('aria-disabled', String(option.disabled));
      node.textContent = option.text;
      node.addEventListener('pointermove', () => highlight(index));
      node.addEventListener('pointerdown', event => event.preventDefault());
      node.addEventListener('click', () => choose(index));
      menu.appendChild(node);
    });
    menu.hidden = false;
    if (topLayer) menu.showPopover();
    opened = true;
    trigger.setAttribute('aria-expanded', 'true');
    position();
    highlight(options[select.selectedIndex]?.disabled ? enabled()[0] : select.selectedIndex);
  }
  function close() {
    if (!opened) return;
    opened = false;
    if (topLayer) menu.hidePopover();
    menu.hidden = true;
    trigger.setAttribute('aria-expanded', 'false');
    trigger.removeAttribute('aria-activedescendant');
  }
  function choose(index) {
    if (select.disabled || !options[index] || options[index].disabled) return;
    if (select.selectedIndex !== index) {
      select.selectedIndex = index;
      select.dispatchEvent(new Event('input', {bubbles: true}));
      select.dispatchEvent(new Event('change', {bubbles: true}));
    }
    close();
    sync();
    trigger.focus({preventScroll: true});
  }
  trigger.addEventListener('click', () => opened ? close() : open());
  trigger.addEventListener('keydown', event => {
    const choices = enabled();
    const index = choices.indexOf(active);
    if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
      event.preventDefault();
      const wasOpen = opened;
      open();
      if (event.key === 'Home') highlight(choices[0]);
      else if (event.key === 'End') highlight(choices.at(-1));
      else if (wasOpen) highlight(choices[Math.max(0, Math.min(choices.length - 1, index + (event.key === 'ArrowDown' ? 1 : -1)))]);
    } else if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      opened ? choose(active) : open();
    } else if (event.key === 'Escape' && opened) {
      event.preventDefault(); close();
    } else if (event.key === 'Tab') close();
    else if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
      event.preventDefault(); open();
      clearTimeout(searchTimer);
      search += event.key.toLocaleLowerCase();
      const match = choices.find(index => options[index].text.toLocaleLowerCase().startsWith(search));
      if (match !== undefined) highlight(match);
      searchTimer = setTimeout(() => search = '', 700);
    }
  });
  trigger.addEventListener('blur', close);
  document.addEventListener('pointerdown', event => {
    if (!trigger.contains(event.target) && !menu.contains(event.target)) close();
  }, true);
  document.addEventListener('keydown', event => { if (event.key === 'Escape') close(); });
  window.addEventListener('resize', position);
  window.addEventListener('scroll', position, true);
  window.visualViewport?.addEventListener('resize', position);
  window.visualViewport?.addEventListener('scroll', position);
  select.addEventListener('change', sync);
  new MutationObserver(sync).observe(select, {subtree: true, childList: true, attributes: true, characterData: true});
  // An explicit label target preserves the existing inline field layout.
  const label = select.closest('label');
  if (label) { label.htmlFor = trigger.id; label.appendChild(trigger); }
  else select.insertAdjacentElement('afterend', trigger);
  document.body.appendChild(menu);
  sync();
  select.hidden = true;
  select.setAttribute('aria-hidden', 'true');
})();
