(() => {
  const button = document.getElementById("copy-expired");
  const source = document.getElementById("expired-text");
  if (!button || !source) return;
  button.addEventListener("click", async () => {
    const value = source.innerText;
    try {
      if (navigator.clipboard && window.isSecureContext) await navigator.clipboard.writeText(value);
      else { const area = document.createElement("textarea"); area.value = value; area.style.position = "fixed"; area.style.opacity = "0"; document.body.append(area); area.select(); document.execCommand("copy"); area.remove(); }
      button.textContent = "Lista copiada!";
    } catch { button.textContent = "Selecione e copie manualmente"; }
  });
})();
