// Version mobile : enregistre le service worker et propose l'installation de l'appli.
(function () {
  const swUrl = (document.currentScript && document.currentScript.dataset.sw) || "/sw.js";
  const secure = location.protocol === "https:" || ["localhost", "127.0.0.1"].includes(location.hostname);
  if ("serviceWorker" in navigator && secure) {
    addEventListener("load", () => navigator.serviceWorker.register(swUrl, { scope: "/" }).catch(() => {}));
  }

  const box = document.querySelector("[data-install-box]");
  if (!box) return;
  const installed = matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
  if (installed) return;                                        // déjà ouverte comme une appli : rien à proposer
  const button = box.querySelector("[data-install]");
  const iosHint = box.querySelector("[data-install-ios]");
  let prompt = null;

  addEventListener("beforeinstallprompt", event => {            // Chrome, Edge, Android
    event.preventDefault();
    prompt = event;
    iosHint.hidden = true; button.hidden = false; box.hidden = false;
  });
  button.addEventListener("click", async () => {
    if (!prompt) return;
    prompt.prompt();
    await prompt.userChoice;
    prompt = null;
    box.hidden = true;
  });
  addEventListener("appinstalled", () => { box.hidden = true; });

  // Safari sur iPhone et iPad ne propose pas de bouton : on explique le geste.
  const ios = /iphone|ipad|ipod/i.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  if (ios) { button.hidden = true; iosHint.hidden = false; box.hidden = false; }
})();
