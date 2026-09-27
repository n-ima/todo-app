// <dialog> の共通処理（DD-11 §3）。Esc で閉じる（ブラウザ標準）・開いたら最初の操作要素へフォーカス・
// [data-close] のボタンで閉じ、その value を returnValue にする。

const FOCUSABLE = 'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** ダイアログを開き、閉じたときの returnValue（Esc なら ""）で解決する。 */
export function openDialog(dialog) {
  return new Promise((resolve) => {
    dialog.returnValue = '';
    dialog.addEventListener('close', () => resolve(dialog.returnValue), { once: true });
    dialog.showModal();
    const first = dialog.querySelector(FOCUSABLE);
    if (first) {
      first.focus();
    }
  });
}

/** [data-close] のボタンで閉じる。value 属性（無ければ ""）を returnValue にする。 */
document.addEventListener('click', (event) => {
  const button = event.target instanceof Element ? event.target.closest('dialog [data-close]') : null;
  if (!button) {
    return;
  }
  event.preventDefault();
  button.closest('dialog').close(button.getAttribute('value') ?? '');
});

/** [data-dialog-open="id"] のボタンで対応するダイアログを開く。 */
document.addEventListener('click', (event) => {
  const opener = event.target instanceof Element ? event.target.closest('[data-dialog-open]') : null;
  if (!opener) {
    return;
  }
  const dialog = document.getElementById(opener.getAttribute('data-dialog-open'));
  if (dialog instanceof HTMLDialogElement) {
    event.preventDefault();
    openDialog(dialog);
  }
});
