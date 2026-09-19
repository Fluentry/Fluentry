/* Small enhancements only: the page is readable and complete without this. */
(function () {
  "use strict";

  var calm = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // The waveform is decoration, so it is built here rather than shipped as
  // twenty empty spans in the markup.
  var wave = document.querySelector(".wave");
  if (wave) {
    var heights = [30, 62, 38, 88, 52, 100, 44, 74, 34, 58, 82, 40, 66, 28, 48];
    heights.forEach(function (height, index) {
      var bar = document.createElement("span");
      bar.style.height = height + "%";
      bar.style.animationDelay = (index * 0.07).toFixed(2) + "s";
      bar.style.animationDuration = (0.9 + (index % 4) * 0.12).toFixed(2) + "s";
      if (calm) { bar.style.animation = "none"; }
      wave.appendChild(bar);
    });
  }

  // A hairline appears under the bar once the page has moved.
  var bar = document.getElementById("topbar");
  if (bar) {
    var onScroll = function () {
      bar.classList.toggle("scrolled", window.scrollY > 8);
    };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
  }

  // Reveal on scroll, skipped entirely when the visitor asks for less motion.
  var revealed = document.querySelectorAll(".reveal");
  if (calm || !("IntersectionObserver" in window)) {
    revealed.forEach(function (node) { node.classList.add("in"); });
  } else {
    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) { return; }
        entry.target.classList.add("in");
        observer.unobserve(entry.target);
      });
    }, { rootMargin: "0px 0px -8% 0px", threshold: 0.08 });
    revealed.forEach(function (node, index) {
      node.style.transitionDelay = Math.min(index % 6, 4) * 60 + "ms";
      observer.observe(node);
    });
  }

  // Copy the install commands.
  document.querySelectorAll(".copy").forEach(function (button) {
    button.addEventListener("click", function () {
      var text = button.getAttribute("data-copy") || "";
      var done = function () {
        button.textContent = "Copied";
        button.classList.add("done");
        setTimeout(function () {
          button.textContent = "Copy";
          button.classList.remove("done");
        }, 1800);
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done, function () {
          button.textContent = "Press Ctrl+C";
        });
        return;
      }
      // Older browsers: select a throwaway field so Ctrl+C still works.
      var field = document.createElement("textarea");
      field.value = text;
      field.setAttribute("readonly", "");
      field.style.position = "fixed";
      field.style.opacity = "0";
      document.body.appendChild(field);
      field.select();
      try { document.execCommand("copy"); done(); } catch (error) { /* ignore */ }
      document.body.removeChild(field);
    });
  });
})();
