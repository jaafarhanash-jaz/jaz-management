// Consistent field-level validation UX, usable by any form in the
// platform with a single call at the top of its submit handler:
//
//   const handleSubmit = (e) => {
//     e.preventDefault();
//     if (!validateAndFocus(e.currentTarget)) return;
//     ...
//   };
//
// Built on the native HTML5 constraint-validation API (`required`,
// `checkValidity()`) every form here already uses - no form needs to
// duplicate its own list of required fields, and existing `required`
// attributes/browser behavior (e.g. type="email" format checking)
// keep working exactly as before for forms that don't opt in.
//
// What it does on an invalid submit attempt:
//   - every invalid field gets a red border (matches the app's existing
//     red-500/red-600 error palette - see e.g. login-error-message)
//   - an inline message appears directly below the field (native
//     validationMessage - "required field" / a bad email format / etc.,
//     localized by the browser to the page's lang)
//   - the first invalid field is focused and smooth-scrolled into view
//   - the red state clears itself the moment that field becomes valid
//     (on input/change/blur), no extra wiring needed per-form
//
// Scope/limitation: only affects elements the browser itself treats as
// form controls (input/textarea/select) - a Radix UI Select or Checkbox
// renders as a button, not a native control, so it isn't covered by
// checkValidity() and won't get the red-border treatment from this
// utility alone. Every plain <Input>/<Textarea> required field (the
// large majority of required fields in this app, including every field
// on Login) is fully covered.

const ERROR_CLASSES = ['border-red-500', 'ring-1', 'ring-red-500'];

function messageElementFor(field) {
  if (!field.id) return null;
  return field.parentElement?.querySelector(`[data-validation-message-for="${field.id}"]`) || null;
}

function clearFieldError(field) {
  field.classList.remove(...ERROR_CLASSES);
  const msg = messageElementFor(field);
  if (msg) msg.remove();
}

function showFieldError(field) {
  field.classList.add(...ERROR_CLASSES);
  if (!field.id) return; // no safe anchor to attach an inline message to
  let msg = messageElementFor(field);
  if (!msg) {
    msg = document.createElement('p');
    msg.dataset.validationMessageFor = field.id;
    msg.className = 'text-xs text-red-600 mt-1';
    field.insertAdjacentElement('afterend', msg);
  }
  msg.textContent = field.validationMessage;
}

function bindAutoClear(field) {
  if (field.dataset.validationBound) return;
  field.dataset.validationBound = 'true';
  const recheck = () => {
    if (field.checkValidity()) clearFieldError(field);
  };
  field.addEventListener('input', recheck);
  field.addEventListener('change', recheck);
  field.addEventListener('blur', recheck);
}

/**
 * @param {HTMLFormElement} form
 * @returns {boolean} true if the form is valid (nothing left to do -
 *   caller should proceed with submission); false if it highlighted
 *   invalid fields and focused the first one (caller should return early).
 */
export function validateAndFocus(form) {
  if (!form || typeof form.querySelectorAll !== 'function') return true;

  const fields = Array.from(form.querySelectorAll('input, textarea, select')).filter(
    (el) => !el.disabled && typeof el.checkValidity === 'function'
  );

  const invalid = [];
  fields.forEach((field) => {
    if (field.checkValidity()) {
      clearFieldError(field);
    } else {
      invalid.push(field);
      showFieldError(field);
      bindAutoClear(field);
    }
  });

  if (invalid.length === 0) return true;

  const first = invalid[0];
  first.focus({ preventScroll: true });
  first.scrollIntoView({ behavior: 'smooth', block: 'center' });
  return false;
}
