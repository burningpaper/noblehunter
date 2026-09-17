// Shows times in the reader's own time zone.
//
// The server cannot know that zone. Vercel runs in UTC, and guessing it from the artist would
// break the moment someone travels or a member is invited from another country. So the server
// renders the instant into a <time datetime="..."> and the browser, which does know, formats it.
//
// Every element is formatted from its `datetime` attribute and never from its own text, so
// running this over the same element twice is harmless. That matters: the hooks below overlap on
// purpose, and idempotence is what makes the overlap free.
(function () {
  var FORMAT = { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hour12: false };

  function localise(element) {
    var instant = new Date(element.getAttribute("datetime"));
    // A value the browser can't parse gives an Invalid Date, whose toLocaleString is the literal
    // string "Invalid Date". Leaving the server's text in place is the honest failure: it is
    // labelled UTC and it is true, which is exactly what the fallback is for.
    if (isNaN(instant.getTime())) {
      return;
    }
    element.textContent = instant.toLocaleString(undefined, FORMAT);
  }

  function localiseEverything() {
    var times = document.querySelectorAll("time[datetime]");
    for (var i = 0; i < times.length; i++) {
      localise(times[i]);
    }
  }

  document.addEventListener("DOMContentLoaded", localiseEverything);

  // The part that would otherwise rot quietly. The pitch panel arrives by hx-get when someone
  // expands a <details>, and sending swaps it again out of band. A script hooked only to
  // DOMContentLoaded would format the first render and leave every swapped-in time raw -- the bug
  // fixed, then back the moment anyone sends a pitch, which is the moment nobody re-checks.
  //
  // Two hooks, because they cover different halves and neither covers both: htmx:load fires for
  // each newly inserted fragment, and htmx:afterSettle fires once per request, after out-of-band
  // swaps have put their content elsewhere on the page. Both run the same idempotent sweep.
  document.addEventListener("htmx:load", localiseEverything);
  document.addEventListener("htmx:afterSettle", localiseEverything);
})();
