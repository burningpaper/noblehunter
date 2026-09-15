// Moves keyboard focus after a People page action.
//
// htmx swaps #people-content with outerHTML, which drops keyboard focus onto <body>. #people-
// notice is announced by a screen reader because it's the thing that receives focus, not
// because it's a live region -- so this listener focuses the first invalid field after a
// validation error, or the notice otherwise, once the settle that swapped #people-content has
// finished. It ignores the separate afterSettle firing for the #people-notice OOB swap itself.
document.body.addEventListener("htmx:afterSettle", function (event) {
  if (!event.target || event.target.id !== "people-content") {
    return;
  }
  var invalid = event.target.querySelector('[aria-invalid="true"]');
  if (invalid) {
    invalid.focus();
    return;
  }
  var notice = document.getElementById("people-notice");
  if (notice) {
    notice.focus();
  }
});
