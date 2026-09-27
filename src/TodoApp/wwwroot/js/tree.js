// 一覧のツリーの開閉（DD-06 §3）。描画済みの子はクライアント側だけで表示を切り替え、
// data-lazy の行は GET /api/tasks/{id}/children で子の行を取得して直後に挿入する（一度だけ）。

const tbody = document.querySelector('#tree tbody');

function level(row) {
  return Number(row.dataset.lv);
}

/** row の子孫（後続の、より深い段の行）を開閉する。開くときは直下の子だけ見せ、閉じた孫は閉じたまま。 */
function setOpen(row, open) {
  const lv = level(row);
  const closedAt = [];
  for (let n = row.nextElementSibling; n && level(n) > lv; n = n.nextElementSibling) {
    const nlv = level(n);
    while (closedAt.length && closedAt[closedAt.length - 1] >= nlv) {
      closedAt.pop();
    }
    const hidden = !open || closedAt.length > 0;
    n.classList.toggle('hidden', hidden);
    const tog = n.querySelector('.tog');
    if (tog && tog.getAttribute('aria-expanded') === 'false') {
      closedAt.push(nlv);
    }
  }
}

async function loadChildren(row, button) {
  const response = await fetch(`/api/tasks/${row.dataset.id}/children${location.search}`, {
    headers: { Accept: 'text/html' },
    credentials: 'same-origin',
  });
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }
  const template = document.createElement('template');
  template.innerHTML = await response.text();
  row.after(template.content);
  delete button.dataset.lazy;
  const kids = row.querySelector('.kids');
  if (kids) {
    kids.remove();
  }
}

tbody?.addEventListener('click', async (event) => {
  const button = event.target instanceof Element ? event.target.closest('.tog') : null;
  if (!button || button.classList.contains('none')) {
    return;
  }
  const row = button.closest('tr');
  const open = button.getAttribute('aria-expanded') !== 'true';
  if (open && button.dataset.lazy) {
    button.disabled = true;
    try {
      await loadChildren(row, button);
    } catch {
      button.disabled = false;
      window.alert('子タスクを取得できませんでした。再読み込みしてください。');
      return;
    }
    button.disabled = false;
  }
  button.setAttribute('aria-expanded', String(open));
  button.textContent = open ? '▾' : '▸';
  setOpen(row, open);
});
