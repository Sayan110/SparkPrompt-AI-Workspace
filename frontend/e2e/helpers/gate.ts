/**
 * Phase 4F — console / pageerror / network gating fixture.
 *
 * Every test automatically collects browser console errors, uncaught page
 * exceptions, failed subresource requests, and HTTP ≥400 responses for the
 * pages it watches. Anything unexpected fails the test at teardown.
 *
 * Deliberately NOT allowlisted by default:
 *   - pageerror (uncaught exception): always a finding, no expectation API.
 *   - 5xx: must be declared per test, even when intentional.
 *
 * Built-in expectations (documented, evidence-based — see e2e/README.md):
 *   - 401 on GET /api/auth/me — the Phase 4A boot probe answers 401 before
 *     login / after logout by design, and Chrome mirrors it to the console.
 *   - 404 on /favicon.ico — the product ships no favicon, so the browser's
 *     automatic probe 404s on every page (pre-existing, cosmetic only;
 *     recorded as a product observation in the 4F report, not a 4F defect).
 *   - net::ERR_ABORTED — the SPA cancels in-flight fetches on client-side
 *     navigation (router.replace); not a server or network failure.
 *
 * Console/status correlation: Chrome mirrors every ≥400 response to the
 * console as "Failed to load resource: the server responded with a status
 * of N …". Such a console line is counted as EXPECTED when a declared
 * status expectation matches the same status + URL — one declaration
 * covers both surfaces (the response itself and its console mirror).
 */
import { test as base, expect, type BrowserContext, type Page } from "@playwright/test";

type TextExpectation = { text: RegExp; url?: RegExp; reason: string };
type StatusExpectation = { status: number; url: RegExp; reason: string };

export class BrowserGate {
  private readonly pageErrors: string[] = [];
  private readonly consoleErrors: Array<{ text: string; url: string }> = [];
  private readonly failedRequests: Array<{ text: string; url: string }> = [];
  private readonly badResponses: Array<{ status: number; url: string }> = [];

  private readonly consoleExpectations: TextExpectation[] = [];
  private readonly failureExpectations: TextExpectation[] = [
    {
      text: /net::ERR_ABORTED/,
      reason: "SPA cancels in-flight requests on client-side navigation",
    },
  ];
  private readonly statusExpectations: StatusExpectation[] = [
    {
      status: 401,
      url: /\/api\/auth\/me$/,
      reason: "Phase 4A auth boot probe answers 401 by design before login / after logout",
    },
    {
      status: 404,
      url: /\/favicon\.ico$/,
      reason:
        "the product ships no favicon — the browser's automatic /favicon.ico probe 404s on every page (pre-existing, cosmetic)",
    },
  ];

  constructor(page: Page) {
    this.watchPage(page);
  }

  /** Attach the gate to an extra page (e.g. a second context in isolation tests). */
  watchPage(page: Page): void {
    page.on("console", (message) => {
      if (message.type() === "error") {
        this.consoleErrors.push({
          text: message.text(),
          url: message.location().url ?? "",
        });
      }
    });
    page.on("pageerror", (error) => {
      this.pageErrors.push(String(error));
    });
    page.on("requestfailed", (request) => {
      this.failedRequests.push({
        text: request.failure()?.errorText ?? "unknown failure",
        url: request.url(),
      });
    });
    page.on("response", (response) => {
      if (response.status() >= 400) {
        this.badResponses.push({ status: response.status(), url: response.url() });
      }
    });
  }

  /** Attach the gate to every page of an extra browser context. */
  watchContext(context: BrowserContext): void {
    context.on("page", (page) => this.watchPage(page));
  }

  /** Declare an intentional HTTP ≥400 response (never 5xx without this). */
  expectStatus(status: number, urlPattern: RegExp, reason: string): void {
    this.statusExpectations.push({ status, url: urlPattern, reason });
  }

  /** Declare an intentional console error (matched on text, optionally URL). */
  expectConsoleError(text: RegExp, urlPattern: RegExp | undefined, reason: string): void {
    this.consoleExpectations.push({ text, url: urlPattern, reason });
  }

  /** Declare an intentional failed request (net::ERR_*). */
  expectRequestFailure(text: RegExp, urlPattern: RegExp | undefined, reason: string): void {
    this.failureExpectations.push({ text, url: urlPattern, reason });
  }

  private matches(
    item: { text?: string; status?: number; url: string },
    expectation: { text?: RegExp; status?: number; url?: RegExp },
  ): boolean {
    if (expectation.status !== undefined && item.status !== expectation.status) return false;
    if (expectation.text && !expectation.text.test(item.text ?? "")) return false;
    if (expectation.url && !expectation.url.test(item.url)) return false;
    return true;
  }

  /**
   * Chrome mirrors every ≥400 response to the console as
   * "Failed to load resource: the server responded with a status of N …".
   * Count such a line as expected when a declared status expectation
   * matches the same status + URL — one declaration covers both surfaces.
   */
  private consoleMirrorsExpectedStatus(item: { text: string; url: string }): boolean {
    const match = /^Failed to load resource: the server responded with a status of (\d{3})/.exec(
      item.text,
    );
    if (!match) return false;
    const status = Number(match[1]);
    // Deliberately NOT routed through matches(): console items carry the status
    // only inside their text, never as an item.status field.
    return this.statusExpectations.some(
      (exp) => exp.status === status && exp.url.test(item.url),
    );
  }

  /** Fail the test when anything unexpected was collected. */
  assertClean(): void {
    const unexpectedConsole = this.consoleErrors.filter(
      (item) =>
        !this.consoleExpectations.some((exp) =>
          this.matches(item, { text: exp.text, url: exp.url }),
        ) && !this.consoleMirrorsExpectedStatus(item),
    );
    const unexpectedFailures = this.failedRequests.filter(
      (item) =>
        !this.failureExpectations.some((exp) =>
          this.matches(item, { text: exp.text, url: exp.url }),
        ),
    );
    const unexpectedStatuses = this.badResponses.filter(
      (item) =>
        !this.statusExpectations.some((exp) =>
          this.matches(item, { status: exp.status, url: exp.url }),
        ),
    );

    const sections: string[] = [];
    if (this.pageErrors.length > 0) {
      sections.push(`Uncaught page exceptions:\n  - ${this.pageErrors.join("\n  - ")}`);
    }
    if (unexpectedConsole.length > 0) {
      sections.push(
        "Unexpected console errors:\n" +
          unexpectedConsole.map((i) => `  - ${i.text} @ ${i.url}`).join("\n"),
      );
    }
    if (unexpectedFailures.length > 0) {
      sections.push(
        "Unexpected failed requests:\n" +
          unexpectedFailures.map((i) => `  - ${i.text} @ ${i.url}`).join("\n"),
      );
    }
    if (unexpectedStatuses.length > 0) {
      sections.push(
        "Unexpected HTTP ≥400 responses:\n" +
          unexpectedStatuses.map((i) => `  - ${i.status} ${i.url}`).join("\n"),
      );
    }

    expect(
      sections,
      sections.length === 0 ? "browser gate clean" : `Browser gate failures:\n${sections.join("\n")}`,
    ).toEqual([]);
  }
}

type GateFixtures = { gate: BrowserGate };

export const test = base.extend<GateFixtures>({
  gate: [
    async ({ page }, use) => {
      const gate = new BrowserGate(page);
      await use(gate);
      gate.assertClean();
    },
    { auto: true },
  ],
});

export { expect };
