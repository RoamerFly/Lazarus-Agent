/* Preserve each tool's state while moving infrequent controls out of the workspace. */
(() => {
  function open(id) {
    const dialog=document.getElementById(id);
    if(dialog && !dialog.open)dialog.showModal();
  }
  document.addEventListener("click",event=>{
    const trigger=event.target.closest("[data-open-dialog]");
    if(trigger)open(trigger.dataset.openDialog);
    const close=event.target.closest("[data-close-dialog]");
    if(close)close.closest("dialog").close();
  });
  window.LazarusDialogs={open};
})();
