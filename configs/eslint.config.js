// JP_TOOLS shared ESLint flat config (ESLint 9+)
// Covers vanilla JS and browser environments

export default [
  {
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      globals: {
        window: "readonly",
        document: "readonly",
        console: "readonly",
        fetch: "readonly",
        localStorage: "readonly",
        setTimeout: "readonly",
        clearTimeout: "readonly",
        setInterval: "readonly",
        clearInterval: "readonly",
        URL: "readonly",
        Blob: "readonly",
        FileReader: "readonly",
        Image: "readonly",
        requestAnimationFrame: "readonly",
      },
    },
    rules: {
      // Errors
      "no-undef":           "error",
      "no-unused-vars":     ["error", { argsIgnorePattern: "^_" }],
      "no-implicit-globals": "error",
      "eqeqeq":             ["error", "always", { null: "ignore" }],
      "no-eval":            "error",
      "no-implied-eval":    "error",

      // Warnings — style / quality
      "prefer-const":       "warn",
      "no-var":             "warn",
      "no-console":         ["warn", { allow: ["warn", "error"] }],
      "curly":              ["warn", "multi-line"],
      "object-shorthand":   "warn",
      "prefer-template":    "warn",
    },
  },
];
