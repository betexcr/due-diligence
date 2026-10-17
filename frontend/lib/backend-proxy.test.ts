import assert from "node:assert/strict";
import { test } from "node:test";
import { forwardToBackend } from "./backend-proxy.ts";

type Captured = { url: string; init: RequestInit };

function fakeFetch(response: Response, seen: Captured[]): typeof fetch {
  return (async (url: string | URL | Request, init?: RequestInit) => {
    seen.push({ url: String(url), init: init ?? {} });
    return response;
  }) as typeof fetch;
}

test("forwards path, query and body to the backend with the server-side token", async () => {
  const seen: Captured[] = [];
  const req = new Request("http://localhost:3000/api/jobs?status=dead", {
    method: "POST",
    headers: { "content-type": "application/json", cookie: "session=1", authorization: "Bearer from-browser" },
    body: JSON.stringify({ a: 1 }),
  });
  const res = await forwardToBackend(req, {
    backendUrl: "https://api.example.test",
    token: "secret-token",
    fetch: fakeFetch(Response.json({ ok: true }, { status: 201 }), seen),
  });

  assert.equal(res.status, 201);
  assert.deepEqual(await res.json(), { ok: true });
  assert.equal(seen[0].url, "https://api.example.test/api/jobs?status=dead");
  const headers = new Headers(seen[0].init.headers);
  assert.equal(headers.get("authorization"), "Bearer secret-token");
  assert.equal(headers.get("cookie"), null);
  assert.equal(headers.get("content-type"), "application/json");
  assert.equal(new TextDecoder().decode(seen[0].init.body as ArrayBuffer), '{"a":1}');
});

test("never passes the browser's Authorization header through when no token is configured", async () => {
  const seen: Captured[] = [];
  const req = new Request("http://localhost:3000/api/projects", { headers: { authorization: "Bearer guess" } });
  await forwardToBackend(req, { backendUrl: "http://localhost:8000", token: undefined, fetch: fakeFetch(new Response("[]"), seen) });

  assert.equal(new Headers(seen[0].init.headers).get("authorization"), null);
  assert.equal(seen[0].init.body, undefined);
});

test("passes backend errors through and drops backend cookies", async () => {
  const upstream = Response.json({ detail: "Missing or invalid API token" }, {
    status: 401,
    headers: { "set-cookie": "x=1", "www-authenticate": "Bearer" },
  });
  const res = await forwardToBackend(new Request("http://localhost:3000/api/projects"), {
    backendUrl: "http://localhost:8000", token: "t", fetch: fakeFetch(upstream, []),
  });

  assert.equal(res.status, 401);
  assert.equal(res.headers.get("set-cookie"), null);
  assert.equal(res.headers.get("www-authenticate"), "Bearer");
});

test("an unreachable backend becomes a 502 with a readable detail", async () => {
  const failing = (async () => { throw new TypeError("fetch failed"); }) as typeof fetch;
  const res = await forwardToBackend(new Request("http://localhost:3000/api/health"), {
    backendUrl: "http://localhost:8000", token: undefined, fetch: failing,
  });

  assert.equal(res.status, 502);
  assert.deepEqual(await res.json(), { detail: "Cannot reach the backend API." });
});
