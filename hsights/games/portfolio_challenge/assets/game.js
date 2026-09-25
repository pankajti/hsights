/* Keyboard transport for Portfolio Lab. Clicks the real buttons, so every
   guard in the Dash callbacks still applies; disabled buttons do nothing. */
(function () {
  'use strict';

  function press(id) {
    var element = document.getElementById(id);
    if (element && !element.disabled) {
      element.click();
    }
  }

  function typing(target) {
    if (!target) {
      return false;
    }
    var tag = (target.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'textarea' || tag === 'select') {
      return true;
    }
    // rc-slider handles are focusable divs that own the arrow keys themselves.
    return Boolean(target.closest && target.closest(
      '.rc-slider, button, a, [role="button"], [role="slider"], [role="radio"], [contenteditable]:not([contenteditable="false"])'
    ));
  }

  document.addEventListener('keydown', function (event) {
    if (event.defaultPrevented || event.repeat || event.metaKey || event.ctrlKey || event.altKey) {
      return;
    }
    var overlay = document.getElementById('setup');
    var open = Boolean(overlay && overlay.classList.contains('is-open'));

    if (event.key === 'Escape') {
      if (open) {
        event.preventDefault();
        press('close-setup');
      }
      return;
    }
    if (open || typing(event.target)) {
      return;
    }
    if (event.code === 'Space') {
      event.preventDefault();
      press('toggle');
    } else if (event.key === 'ArrowRight') {
      event.preventDefault();
      press('step');
    } else if (event.key === 's' || event.key === 'S') {
      event.preventDefault();
      press('open-setup');
    }
  });
})();
