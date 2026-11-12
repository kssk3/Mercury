import { defineConfig, globalIgnores } from "eslint/config";
import next from "@next/eslint-plugin-next";
import react from "@eslint-react/eslint-plugin";
import hooks from "eslint-plugin-react-hooks";
import a11y from "eslint-plugin-jsx-a11y-x";
import importX from "eslint-plugin-import-x";
import tseslint from "typescript-eslint";
import globals from "globals";

export default defineConfig([
  globalIgnores([".next/**", "out/**", "build/**", "next-env.d.ts", "test-results/**", "playwright-report/**"]),
  {
    files: ["**/*.{js,jsx,mjs,cjs,ts,tsx,mts,cts}"],
    extends: [tseslint.configs.recommended, next.configs["core-web-vitals"], hooks.configs.flat.recommended],
    languageOptions: { globals: { ...globals.browser, ...globals.node }, parserOptions: { ecmaFeatures: { jsx: true } } },
    plugins: { "@eslint-react": react, "jsx-a11y-x": a11y, "import-x": importX },
    rules: {
      "@typescript-eslint/no-unused-vars": "warn",
      "@typescript-eslint/no-unused-expressions": "warn",
      "import-x/no-anonymous-default-export": "warn",
      "@eslint-react/no-missing-component-display-name": "error",
      "@eslint-react/no-missing-context-display-name": "error",
      "@eslint-react/no-missing-key": "error",
      "@eslint-react/no-duplicate-key": "error",
      "@eslint-react/jsx-no-comment-textnodes": "error",
      "@eslint-react/jsx-no-children-prop": "error",
      "@eslint-react/no-direct-mutation-state": "error",
      "@eslint-react/no-component-will-mount": "error",
      "@eslint-react/no-component-will-receive-props": "error",
      "@eslint-react/no-component-will-update": "error",
      "@eslint-react/dom-no-dangerously-set-innerhtml-with-children": "error",
      "@eslint-react/dom-no-find-dom-node": "error",
      "@eslint-react/dom-no-render": "error",
      "@eslint-react/dom-no-render-return-value": "error",
      "@eslint-react/dom-no-hydrate": "error",
      "jsx-a11y-x/alt-text": ["warn", { elements: ["img"], img: ["Image"] }],
      "jsx-a11y-x/aria-props": "warn",
      "jsx-a11y-x/aria-proptypes": "warn",
      "jsx-a11y-x/aria-unsupported-elements": "warn",
      "jsx-a11y-x/role-has-required-aria-props": "warn",
      "jsx-a11y-x/role-supports-aria-props": "warn",
    },
  },
]);
