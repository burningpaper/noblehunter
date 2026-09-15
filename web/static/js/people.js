// Moves keyboard focus after a People page action.
//
// htmx swaps #people-content with outerHTML, which drops keyboard focus onto <body>. The
// result also refreshes #people-notice out of band, which is the only reliable way a screen
// reader announces what happened (content freshly inserted into a live region it's already
// watching often isn't announced). This listener focuses the first invalid field after a
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
