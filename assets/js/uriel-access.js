/* Uriel is a separate private research site. No Uriel password or model is
   stored in this public repository. Existing public investor demos are unchanged. */
(() => {
  'use strict';
  const destination = 'https://qsf-uriel-research.pirlon.chatgpt.site';
  const form = document.getElementById('login-form');
  const username = document.getElementById('login-username');
  const password = document.getElementById('login-password');
  const passwordLabel = document.querySelector('label[for="login-password"]');
  const note = document.getElementById('uriel-private-message');
  const buttonLabel = form?.querySelector('button[type="submit"] span');
  if (!form || !username || !password || !note) return;
  const isUriel = () => username.value.trim().toLowerCase() === 'uriel';
  username.addEventListener('input', () => {
    const privateAccess = isUriel();
    note.hidden = !privateAccess;
    password.hidden = privateAccess;
    if (passwordLabel) passwordLabel.hidden = privateAccess;
    password.disabled = privateAccess || username.disabled;
    if (privateAccess) password.value = '';
    if (buttonLabel) buttonLabel.textContent = privateAccess ? 'Continue to private Uriel' : 'Open portfolio';
  });
  form.addEventListener('submit', event => {
    if (!isUriel()) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    password.value = '';
    window.location.assign(destination);
  }, true);
})();
