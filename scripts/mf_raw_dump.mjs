#!/usr/bin/env node
// Dump raw MacroFactor food-log docs for one or more dates to stdout as JSON.
// Usage: node scripts/mf_raw_dump.mjs YYYY-MM-DD [YYYY-MM-DD ...]
// Env:
//   MACROFACTOR_USERNAME, MACROFACTOR_PASSWORD  (required for first login)
//   MACROFACTOR_REFRESH_TOKEN  (optional, preferred if present)
//   MF_REFRESH_TOKEN_OUT_FILE  (optional path; refresh token written on success)
//
// The installed `@sjawhar/macrofactor-mcp` client only exposes a schema-parsed
// `getFoodLog()` which drops per-entry fiber (USDA nutrient id 291) and other
// extra fields. We handle auth via the client, then fetch the raw Firestore
// doc ourselves so we can sum fiber and include every non-deleted entry
// regardless of `k` (see memory/mf_sync_raw.md).

import { MacroFactorClient } from "@sjawhar/macrofactor-mcp/dist/chunk-SMKAZ3NC.js";
import { writeFileSync } from "node:fs";

const PROJECT_ID = "sbs-diet-app";
const BASE_URL = `https://firestore.googleapis.com/v1/projects/${PROJECT_ID}/databases/(default)/documents`;

function parseFirestoreValue(val) {
  if (val == null) return null;
  if ("stringValue" in val) return val.stringValue;
  if ("integerValue" in val) return Number(val.integerValue);
  if ("doubleValue" in val) return val.doubleValue;
  if ("booleanValue" in val) return val.booleanValue;
  if ("nullValue" in val) return null;
  if ("timestampValue" in val) return val.timestampValue;
  if ("referenceValue" in val) return val.referenceValue;
  if ("mapValue" in val) return parseFirestoreFields(val.mapValue?.fields ?? {});
  if ("arrayValue" in val) return (val.arrayValue?.values ?? []).map(parseFirestoreValue);
  return null;
}
function parseFirestoreFields(fields) {
  const out = {};
  if (!fields) return out;
  for (const [k, v] of Object.entries(fields)) out[k] = parseFirestoreValue(v);
  return out;
}
async function getRawFoodDoc(uid, date, token) {
  const resp = await fetch(`${BASE_URL}/users/${uid}/food/${date}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (resp.status === 404) return {};
  if (!resp.ok) throw new Error(`Firestore GET failed (${resp.status}): ${await resp.text()}`);
  const doc = await resp.json();
  if (!doc.fields) return {};
  return parseFirestoreFields(doc.fields);
}

const dates = process.argv.slice(2);
if (dates.length === 0) {
  console.error("usage: mf_raw_dump.mjs YYYY-MM-DD [YYYY-MM-DD ...]");
  process.exit(2);
}

const refresh = process.env.MACROFACTOR_REFRESH_TOKEN;
const email = process.env.MACROFACTOR_USERNAME;
const password = process.env.MACROFACTOR_PASSWORD;

let client;
try {
  if (refresh) {
    client = await MacroFactorClient.fromRefreshToken(refresh);
  } else if (email && password) {
    client = await MacroFactorClient.login(email, password);
  } else {
    console.error("missing credentials: set MACROFACTOR_REFRESH_TOKEN or MACROFACTOR_USERNAME+MACROFACTOR_PASSWORD");
    process.exit(3);
  }
} catch (e) {
  console.error(`auth failed: ${e.message}`);
  process.exit(4);
}

const token = await client.ensureToken();
const uid = client.uid;

const out = {};
for (const d of dates) {
  try {
    out[d] = await getRawFoodDoc(uid, d, token);
  } catch (e) {
    out[d] = { __error: e.message };
  }
}

const tokenFile = process.env.MF_REFRESH_TOKEN_OUT_FILE;
if (tokenFile) {
  try {
    writeFileSync(tokenFile, client.getRefreshToken(), { mode: 0o600 });
  } catch (e) {
    console.error(`warn: failed to persist refresh token: ${e.message}`);
  }
}

process.stdout.write(JSON.stringify(out));
