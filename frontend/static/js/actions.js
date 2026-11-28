// Comportements déclaratifs des gabarits (remplacent les attributs onsubmit=, onchange=, onclick=, interdits par la
// Content-Security-Policy, SEC-09). Écouteurs délégués au document : valables aussi pour le contenu ajouté plus tard.
//   <form data-confirm="Question ?">            demande confirmation avant l'envoi
//   <select data-autosubmit>                     envoie le formulaire au changement
//   <input data-sync-form="goal">                recopie la valeur dans form.dataset.goal (affichage conditionnel)
//   <select data-sync-body="goal">               recopie la valeur dans document.body.dataset.goal
//   <button data-confirm-unanswered="Question ?"> confirmation si une question (fieldset) est restée sans réponse
//   <button data-confirm-click="Question ?">     confirmation avant l'envoi par ce bouton-là (un formulaire, plusieurs choix)
//   <button data-print-cards>                    déplie toutes les fiches puis imprime
(function () {
  'use strict';

  document.addEventListener('submit', function (event) {
    var form = event.target.closest ? event.target.closest('form[data-confirm]') : null;
    if (form && !window.confirm(form.dataset.confirm)) {
      event.preventDefault();
    }
  }, true);

  document.addEventListener('change', function (event) {
    var el = event.target;
    if (!el || !el.dataset) return;
    if (el.dataset.syncForm && el.form) el.form.dataset[el.dataset.syncForm] = el.value;
    if (el.dataset.syncBody) document.body.dataset[el.dataset.syncBody] = el.value;
    if ('autosubmit' in el.dataset && el.form) {
      if (el.form.requestSubmit) el.form.requestSubmit(); else el.form.submit();
    }
  });

  document.addEventListener('click', function (event) {
    var risky = event.target.closest ? event.target.closest('[data-confirm-click]') : null;
    if (risky && !window.confirm(risky.dataset.confirmClick)) {
      event.preventDefault();
      return;
    }
    var button = event.target.closest ? event.target.closest('[data-confirm-unanswered], [data-print-cards]') : null;
    if (!button) return;
    if (button.dataset.confirmUnanswered !== undefined) {
      var form = button.form || button.closest('form');
      var fieldsets = form ? Array.prototype.slice.call(form.querySelectorAll('fieldset')) : [];
      var complete = fieldsets.every(function (f) { return f.querySelector('input:checked'); });
      if (!complete && !window.confirm(button.dataset.confirmUnanswered)) event.preventDefault();
    } else {
      document.querySelectorAll('.rv-card-flip').forEach(function (d) { d.open = true; });
      window.print();
    }
  });
})();
