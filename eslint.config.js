/** @type {import("eslint").Linter.Config[]} */
module.exports = [
  {
    ignores: [
      "panel.html",
      "penta5_backend_runner.js",
      "data/**",
      "logs/**",
      "api/node_modules/**",
      "node_modules/**",
      "**/*.min.js",
      "claude_ensemble_sidecar.js"
    ]
  },
  {
    files: ["algorithms/**/*.js", "api/*.js"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "commonjs",
      globals: {
        console: "readonly",
        process: "readonly",
        Buffer: "readonly",
        __dirname: "readonly",
        __filename: "readonly",
        module: "readonly",
        require: "readonly",
        exports: "writable",
        setTimeout: "readonly",
        clearTimeout: "readonly",
        setInterval: "readonly",
        clearInterval: "readonly"
      }
    },
    rules: {
      "no-undef": "warn",
      "no-unused-vars": ["warn", { argsIgnorePattern: "^_", varsIgnorePattern: "^_", caughtErrorsIgnorePattern: "^_" }],
      "no-unreachable": "warn"
    }
  }
];
