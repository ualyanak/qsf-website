(function () {
  "use strict";

  const header = document.querySelector(".site-header");
  const navToggle = document.querySelector(".nav-toggle");
  const primaryNav = document.getElementById("primary-navigation");
  const year = document.getElementById("currentYear");
  const mobileQuery = window.matchMedia("(max-width: 900px)");

  if (year) {
    year.textContent = String(new Date().getFullYear());
  }

  function updateHeader() {
    if (header) {
      header.classList.toggle("is-scrolled", window.scrollY > 12);
    }
  }

  updateHeader();
  window.addEventListener("scroll", updateHeader, { passive: true });

  if (navToggle && primaryNav) {
    const toggleLabel = navToggle.querySelector(".sr-only");

    function setMenu(open, returnFocus) {
      navToggle.setAttribute("aria-expanded", String(open));
      primaryNav.classList.toggle("is-open", open);
      document.body.classList.toggle("nav-open", open && mobileQuery.matches);

      if (toggleLabel) {
        toggleLabel.textContent = open ? "Close navigation" : "Open navigation";
      }

      if (open) {
        const firstLink = primaryNav.querySelector("a");
        if (firstLink) {
          window.requestAnimationFrame(function () {
            firstLink.focus();
          });
        }
      } else if (returnFocus) {
        navToggle.focus();
      }
    }

    navToggle.addEventListener("click", function () {
      const isOpen = navToggle.getAttribute("aria-expanded") === "true";
      setMenu(!isOpen, false);
    });

    primaryNav.addEventListener("click", function (event) {
      if (event.target.closest("a") && mobileQuery.matches) {
        setMenu(false, false);
      }
    });

    document.addEventListener("keydown", function (event) {
      const menuOpen = navToggle.getAttribute("aria-expanded") === "true";

      if (!menuOpen) {
        return;
      }

      if (event.key === "Escape") {
        setMenu(false, true);
        return;
      }

      if (event.key === "Tab" && mobileQuery.matches) {
        const focusable = [navToggle].concat(Array.from(primaryNav.querySelectorAll("a[href]")));
        const first = focusable[0];
        const last = focusable[focusable.length - 1];

        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    });

    mobileQuery.addEventListener("change", function (event) {
      if (!event.matches) {
        setMenu(false, false);
      }
    });
  }

  const sectionLinks = Array.from(document.querySelectorAll('.primary-nav a[href^="#"]'));
  const linkBySection = new Map();

  sectionLinks.forEach(function (link) {
    const id = link.getAttribute("href").slice(1);
    if (id) {
      linkBySection.set(id, link);
    }
  });

  if ("IntersectionObserver" in window && linkBySection.size) {
    const visibleSections = new Map();

    const observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          visibleSections.set(entry.target.id, entry.boundingClientRect.top);
        } else {
          visibleSections.delete(entry.target.id);
        }
      });

      if (!visibleSections.size) {
        return;
      }

      const activeId = Array.from(visibleSections.entries())
        .sort(function (a, b) { return Math.abs(a[1]) - Math.abs(b[1]); })[0][0];

      sectionLinks.forEach(function (link) {
        const isActive = link === linkBySection.get(activeId);
        link.classList.toggle("is-active", isActive);
        if (isActive) {
          link.setAttribute("aria-current", "location");
        } else {
          link.removeAttribute("aria-current");
        }
      });
    }, {
      rootMargin: "-25% 0px -60% 0px",
      threshold: [0, 0.1]
    });

    linkBySection.forEach(function (_link, id) {
      const section = document.getElementById(id);
      if (section) {
        observer.observe(section);
      }
    });
  }
})();
