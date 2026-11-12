# Mercury web

An independently managed Next.js introduction site and clickable, synthetic
walkthrough of Mercury. The Python package remains in the repository root.

## Requirements and local development

Use Node.js 24 or newer and npm. Node 24 is the tested local version.
Run these commands from this directory (`web/`):

```sh
npm ci
npm run dev
```

Open <http://127.0.0.1:3000>. Python, AI credentials and a Mercury backend are
not required to run this site. No global Node packages are required.

## Checks and static output

```sh
npm test
npm run lint
npm run typecheck
npm run build
```

The build exports static assets to `out/`; serve that directory with a static
web server. `next start` is not used with static export. The default output
assumes deployment at the domain root. Configure and verify asset paths before
hosting beneath a URL subdirectory. Deployment and backend integration are
separate work.

For browser acceptance, first build, then install Playwright's Chromium once:

```sh
npx playwright install chromium
npm run test:e2e
```

The browser tests start their own loopback-only static server on port 4173,
exercise the exported files and stop it afterward. Port 4173 must be available.
For an existing compatible Chromium installation, set
`PLAYWRIGHT_CHROMIUM_EXECUTABLE` to its absolute path instead of downloading a
browser. `MERCURY_SCREENSHOT_DIR` optionally selects an existing screenshot
directory; the default is ignored `test-results/`.

## Structure and boundaries

- `src/app/`: server-rendered introduction, metadata and shared styles.
- `src/components/demo.tsx`: the interactive browser component.
- `src/lib/demo.ts`: synthetic scenarios and a Zustand store factory scoped to
  each demo instance. There is no shared mutable server store or persistence.
- `tests/demo.test.ts`: navigation, scenario reset and outcome contracts.
- `tests/lint.test.mjs`: bad-source regression checks for the supported lint stack
  and React/JSX checks delegated to TypeScript.
- `tests/e2e/`: exported-page interactions, keyboard and viewport checks.

Next.js, React, Tailwind CSS, Zustand and Lucide provide the UI. React Icons is
used only for the GitHub brand icon. Direct package versions and the npm lockfile
are pinned independently of Python's `pyproject.toml` and `uv.lock`.

The demo uses public synthetic values. It never reads a repository, launches an
agent, executes a command, or contacts a backend. PASS, FAIL and UNKNOWN are
illustrations of technical evidence, separate from review and merge approval.
The displayed local quickstart is a separate, explicitly admitted disposable
Python simulator; read its source and the linked guide before running it.

No screenshots, credentials, runtime reports or private transcripts should be
added to the public site. Node dependencies, build outputs and test artifacts
are ignored within `web/`.

## Supported lint stack

ESLint 10 is composed directly with the official Next.js core-web-vitals plugin,
React Hooks, TypeScript ESLint, ESLint React, the maintained JSX a11y X fork and
Import X. `eslint-config-next` is omitted because its current React and JSX a11y
dependencies declare support only through ESLint 9. Runtime Next.js and React
versions, page content, styles and demo state are unchanged. No forced peers,
legacy-peer-deps or warning suppression are configured.

The migration preserves Next.js and Hooks presets, TypeScript recommended rules
and its existing warning severities, six accessibility checks (including custom
`Image` alternative text), and anonymous-default-export warnings. React keys,
comment text, children props, unsafe HTML, state mutation and legacy lifecycle /
DOM APIs use the maintained React plugin. It is not an identical rule set:

- JSX references are understood natively by ESLint 10; the full typecheck also
  rejects undefined components and duplicate JSX attributes.
- React 19 types reject removed `createClass`, string refs, `isMounted` and a
  class `render` method with no return. These are compile-time protections in
  TypeScript, not equivalent coverage for an untyped JavaScript React app.
- Component/context display-name checks have different detection boundaries.
- The old `react/no-unescaped-entities` text-style rule has no direct equivalent
  and is not enabled. It is not replaced by a claim of complete rule parity.

`npm test` runs synthetic-demo tests plus real ESLint bad-source checks and a
TypeScript diagnostic fixture. These checks exercise the configured tools,
including Hooks/dependencies, keys, alt/ARIA, Next scripts, explicit `any`,
children props, comment text and unsafe HTML. They do not constitute a complete
accessibility audit or make every rule in the two plugins semantically equal.
