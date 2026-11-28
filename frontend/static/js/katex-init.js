// Rendu des formules LaTeX par KaTeX ($…$, $$…$$, \(…\), \[…\]).
// Chargé en `defer` après katex.min.js et auto-render.min.js : les scripts différés s'exécutent dans l'ordre.
// trust: false : aucune commande KaTeX ne peut produire de lien ni de HTML arbitraire (\href{javascript:…}, SEC-05).
(function () {
  'use strict';
  var main = document.querySelector('main');
  if (!main || typeof window.renderMathInElement !== 'function') return;  // CDN indisponible : texte brut
  window.renderMathInElement(main, {
    throwOnError: false,
    trust: false,
    delimiters: [
      {left: '$$', right: '$$', display: true}, {left: '\\[', right: '\\]', display: true},
      {left: '$', right: '$', display: false}, {left: '\\(', right: '\\)', display: false}
    ]
  });
})();
