# Open Preview In Codex Browser

Use this after `recorderctl.py start` returns a `status_url`.

Do not run `open <status_url>`, `open -a "Google Chrome" <status_url>`, or any other shell/default-browser command. The live transcript should appear in Codex's in-app browser preview pane.

Before browser work, load the Browser skill and expose the Node REPL `js` execution tool if it is not already available. Then run this cell, replacing `BROWSER_PLUGIN_ROOT` with the absolute root of the installed Browser plugin that contains `scripts/browser-client.mjs`, and replacing `statusUrl` with the URL returned by `recorderctl.py start`.

```js
const BROWSER_PLUGIN_ROOT = "/Users/simonsmith/.codex/plugins/cache/openai-bundled/browser/26.519.81530";
const statusUrl = "http://127.0.0.1:47832";

if (!globalThis.agent) {
  const { setupBrowserRuntime } = await import(`${BROWSER_PLUGIN_ROOT}/scripts/browser-client.mjs`);
  await setupBrowserRuntime({ globals: globalThis });
}
if (!globalThis.browser) {
  globalThis.browser = await agent.browsers.get("iab");
}
await browser.nameSession("Meeting transcript");
await (await browser.capabilities.get("visibility")).set(true);
if (typeof tab === "undefined") {
  globalThis.tab = await browser.tabs.new();
}
await tab.goto(statusUrl);
await tab.playwright.waitForLoadState({ state: "domcontentloaded", timeoutMs: 10000 });
```

After the preview is visible, still include the `status_url` in the assistant response as a manual fallback.
