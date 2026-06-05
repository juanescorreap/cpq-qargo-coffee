/** Tailwind config — replaces the runtime CDN (FRONTEND_AUDIT #2).
 *
 * Mirrors the previous inline cdn.tailwindcss.com config exactly so the build is
 * visually identical. content globs include the Python routers because some HTML
 * (and class strings) is emitted from there (e.g. scraping_ui.py), so those
 * classes must not be purged.
 */
module.exports = {
  content: [
    "./backend/templates/**/*.html",
    "./backend/routers/**/*.py",
  ],
  // Belt-and-suspenders for status colours toggled dynamically. All are also
  // present as literals in scanned files, so this is just defence-in-depth.
  safelist: [
    {
      pattern:
        /(bg|text|border)-(emerald|red|green|stone|sky|rose)-(50|100|200|400|500|600|700)/,
    },
  ],
  theme: {
    extend: {
      colors: {
        espresso: "#3E2723",
        "espresso-light": "#5D4037",
        beige: "#D7CCC8",
        "beige-dark": "#BCAAA4",
        amber: "#FF6F00",
        "amber-light": "#FFA000",
        cream: "#FFF8F0",
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "sans-serif"],
      },
    },
  },
};
