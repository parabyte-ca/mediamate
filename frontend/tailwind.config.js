const SHADES = [50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 950];
const scale = (name) =>
  Object.fromEntries(SHADES.map((s) => [s, `rgb(var(--${name}-${s}) / <alpha-value>)`]));

/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      keyframes: {
        // Moves the diagonal stripes of the upgrade progress bar.
        stripes: { "0%": { backgroundPosition: "0 0" }, "100%": { backgroundPosition: "28px 0" } },
        // Draws the success tick and failure cross (stroke-dasharray is set on the path).
        draw: { "0%": { strokeDashoffset: "48" }, "100%": { strokeDashoffset: "0" } },
        // A soft light sweeping along the slim top bar.
        sweep: { "0%": { transform: "translateX(-100%)" }, "100%": { transform: "translateX(400%)" } },
      },
      animation: {
        stripes: "stripes 0.8s linear infinite",
        draw: "draw 0.6s ease-out 0.15s both",
        sweep: "sweep 1.6s ease-in-out infinite",
      },
      colors: {
        // Colour themes (src/themes.css): gray is the theme's neutral scale and blue its
        // accent, so every existing gray-*/blue-* class follows the chosen theme.
        gray: scale("gray"),
        blue: scale("blue"),
        brand: {
          50:  "#eff6ff",
          100: "#dbeafe",
          500: "#3b82f6",
          600: "#2563eb",
          700: "#1d4ed8",
        },
      },
    },
  },
  plugins: [],
};
