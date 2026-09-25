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

  /* Plotly cannot measure a chart that was built inside a display:none page,
     so it comes back 0px tall the first time you navigate home -> play. Nudge
     it with a resize the moment the board becomes visible. */
  function watchBoardVisibility() {
    var board = document.getElementById('page-game');
    if (!board) {
      return false;
    }
    var visible = board.classList.contains('is-active');
    new MutationObserver(function () {
      var nowVisible = board.classList.contains('is-active');
      if (nowVisible && !visible) {
        window.requestAnimationFrame(function () {
          window.dispatchEvent(new Event('resize'));
        });
      }
      visible = nowVisible;
    }).observe(board, { attributes: true, attributeFilter: ['class'] });
    return true;
  }

  if (!watchBoardVisibility()) {
    // Dash renders the layout after this script runs on a cold load.
    var pending = new MutationObserver(function () {
      if (watchBoardVisibility()) {
        pending.disconnect();
      }
    });
    pending.observe(document.body, { childList: true, subtree: true });
  }

  document.addEventListener('keydown', function (event) {
    if (event.defaultPrevented || event.repeat || event.metaKey || event.ctrlKey || event.altKey) {
      return;
    }
    var setup = document.getElementById('setup');
    var result = document.getElementById('result');
    var setupOpen = Boolean(setup && setup.classList.contains('is-open'));
    var resultOpen = Boolean(result && result.classList.contains('is-open'));
    var open = setupOpen || resultOpen;

    if (event.key === 'Escape') {
      if (resultOpen) {
        event.preventDefault();
        press('close-result');
      } else if (setupOpen) {
        event.preventDefault();
        press('close-setup');
      }
      return;
    }
    // While either sheet is up the transport keys must not reach the board.
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
