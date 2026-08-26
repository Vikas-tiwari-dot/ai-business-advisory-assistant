/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        ink: {
          950: "#0A0E13",
          900: "#0F141B",
          800: "#141B24",
          700: "#1B2430",
          600: "#26303D",
          500: "#3A4655",
        },
        mist: {
          400: "#5B6B7F",
          300: "#8B98A9",
          200: "#B4C0CD",
          100: "#DCE3EA",
          50: "#EEF2F6",
        },
        recovered: {
          DEFAULT: "#2DD4BF",
          dim: "#0F3D38",
        },
        risk: {
          DEFAULT: "#F5A623",
          dim: "#3D2E0F",
        },
        blocked: {
          DEFAULT: "#F87171",
          dim: "#3D1717",
        },
        signal: {
          DEFAULT: "#6366F1",
          dim: "#1E1F3D",
        },
      },
      fontFamily: {
        display: ["Inter", "system-ui", "sans-serif"],
        sans: ["Inter", "system-ui", "sans-serif"],
        mono: ["'JetBrains Mono'", "ui-monospace", "monospace"],
      },
      fontSize: {
        "2xs": ["0.6875rem", { lineHeight: "1rem", letterSpacing: "0.03em" }],
      },
      boxShadow: {
        panel: "0 1px 0 0 rgba(255,255,255,0.03) inset, 0 8px 24px -12px rgba(0,0,0,0.5)",
      },
    },
  },
  plugins: [],
};
