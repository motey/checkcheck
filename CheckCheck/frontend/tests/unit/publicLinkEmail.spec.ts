import { describe, it, expect } from "vitest";
import {
  AUDIENCE_COMPARISON,
  confirmSentence,
  emailDomain,
  internalDomainHint,
  isInternalAddress,
  looksLikeEmail,
  permissionSentence,
  sendErrorMessage,
  validateSend,
  type PublicLinkEmailOptions,
} from "@/utils/publicLinkEmail";

// The logic behind "send this link to someone without an account" (chunk E6).
//
// Most of these are about one failure mode, and it is a real one rather than a
// theoretical one: somebody means "share with my colleague who has an account"
// and hands out an anonymous capability link instead. The hint below is what
// pushes back on that, so what it fires on, and what it must NOT do, is the
// interesting part of this file.

function options(overrides: Partial<PublicLinkEmailOptions> = {}): PublicLinkEmailOptions {
  return {
    enabled: true,
    internal_email_domains: ["example.com"],
    max_message_length: 500,
    max_per_hour: 10,
    ...overrides,
  };
}

describe("looksLikeEmail", () => {
  it("accepts an ordinary address", () => {
    expect(looksLikeEmail("anna@example.org")).toBe(true);
    expect(looksLikeEmail("  anna.analyst+lists@mail.example.org  ")).toBe(true);
  });

  it("rejects the shapes a typo produces", () => {
    expect(looksLikeEmail("")).toBe(false);
    expect(looksLikeEmail("anna")).toBe(false);
    expect(looksLikeEmail("anna@")).toBe(false);
    expect(looksLikeEmail("anna@example")).toBe(false);
    expect(looksLikeEmail("a@b@c.de")).toBe(false);
    expect(looksLikeEmail("anna @example.org")).toBe(false);
    expect(looksLikeEmail(`${"x".repeat(250)}@example.org`)).toBe(false);
  });
});

describe("isInternalAddress", () => {
  it("matches a declared domain, case-insensitively", () => {
    expect(isInternalAddress("anna@Example.COM", ["example.com"])).toBe(true);
    expect(isInternalAddress("anna@example.com", ["EXAMPLE.com"])).toBe(true);
  });

  it("matches a subdomain of a declared domain", () => {
    // An organisation that declares its domain means its mail, not one host.
    expect(isInternalAddress("anna@mail.example.com", ["example.com"])).toBe(true);
  });

  it("does not match a domain that merely ends in the same letters", () => {
    expect(isInternalAddress("anna@notexample.com", ["example.com"])).toBe(false);
  });

  it("never fires on an empty list, which is the default", () => {
    expect(isInternalAddress("anna@example.com", [])).toBe(false);
    expect(isInternalAddress("anna@example.com", undefined)).toBe(false);
    expect(isInternalAddress("anna@example.com", null)).toBe(false);
  });

  it("tolerates a domain written with a leading @", () => {
    expect(isInternalAddress("anna@example.com", ["@example.com"])).toBe(true);
  });

  it("has no opinion about something that is not an address", () => {
    expect(emailDomain("anna")).toBe("");
    expect(isInternalAddress("anna", ["example.com"])).toBe(false);
  });
});

describe("internalDomainHint", () => {
  it("offers the collaborator box for an address that looks like a colleague's", () => {
    const hint = internalDomainHint("anna.analyst@example.com", ["example.com"]);
    expect(hint).not.toBeNull();
    expect(hint!.body).toContain("collaborator");
  });

  it("carries the local part over, not the whole address", () => {
    // The user search matches names and never email — deliberately, so it cannot
    // be used to test whether an address has an account here. Handing it the
    // full address would put a term in the box that can never match.
    const hint = internalDomainHint("anna.analyst@example.com", ["example.com"]);
    expect(hint!.searchTerm).toBe("anna.analyst");
  });

  it("stays quiet for an outside address", () => {
    expect(internalDomainHint("stranger@elsewhere.org", ["example.com"])).toBeNull();
  });

  it("stays quiet with no declared domains, however the address looks", () => {
    expect(internalDomainHint("anna@example.com", [])).toBeNull();
  });

  it("stays quiet while the address is still being typed", () => {
    expect(internalDomainHint("anna@exam", ["example.com"])).toBeNull();
    expect(internalDomainHint("", ["example.com"])).toBeNull();
  });
});

describe("what the user is told before sending", () => {
  it("states the granted level in words a recipient can act on", () => {
    expect(permissionSentence("edit")).toBe(
      "add, change and tick off items on this list, without signing in"
    );
    expect(permissionSentence("check")).toBe("tick items off this list, without signing in");
    expect(permissionSentence("view")).toBe("read this list, without signing in");
    // A level a later release adds falls back to the safest wording.
    expect(permissionSentence("something_new")).toBe("read this list, without signing in");
  });

  it("names the person and the level in the confirm line", () => {
    expect(confirmSentence("  anna@example.org ", "edit")).toBe(
      "anna@example.org will be able to add, change and tick off items on this list, without signing in."
    );
  });

  it("spells out the difference between the two ways of sharing", () => {
    expect(AUDIENCE_COMPARISON.collaborator.line).toContain("tied to their account");
    expect(AUDIENCE_COMPARISON.collaborator.line).toContain("revoke");
    expect(AUDIENCE_COMPARISON.link.line).toContain("without signing in");
    expect(AUDIENCE_COMPARISON.link.line).toContain("everybody");
  });
});

describe("validateSend", () => {
  it("blocks a send that is not worth making", () => {
    expect(validateSend("nope", "", options()).ok).toBe(false);
    expect(validateSend("anna@example.org", "x".repeat(501), options()).error).toContain(
      "500 characters"
    );
  });

  it("lets an ordinary send through", () => {
    expect(validateSend("anna@example.org", "here you go", options())).toEqual({
      ok: true,
      error: null,
    });
  });

  it("leaves the length to the server when the options never loaded", () => {
    expect(validateSend("anna@example.org", "x".repeat(9000), null).ok).toBe(true);
  });
});

describe("sendErrorMessage", () => {
  it("says something useful for each way a send fails", () => {
    expect(sendErrorMessage(429)).toContain("hour");
    expect(sendErrorMessage(409)).toContain("switched off or expired");
    expect(sendErrorMessage(404)).toContain("no longer exists");
    expect(sendErrorMessage(403)).toContain("owner");
    expect(sendErrorMessage(400)).toContain("email address");
    expect(sendErrorMessage(undefined)).toBe("Could not send the link.");
  });

  it("never has anywhere to put the address", () => {
    // The server refuses to repeat it in an error; a client-side message that
    // did would undo that on the one machine that has it on screen.
    for (const status of [400, 403, 404, 409, 429, 500, undefined]) {
      expect(sendErrorMessage(status)).not.toContain("@");
    }
  });
});
