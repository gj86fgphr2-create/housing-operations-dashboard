#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

const [accountId, target, mediaPath] = process.argv.slice(2);
if (!accountId || !target || !mediaPath) {
  console.error("usage: send-weixin-media-with-context <accountId> <target> <absolute-media-path>");
  process.exit(2);
}
if (!path.isAbsolute(mediaPath) || !fs.existsSync(mediaPath)) {
  console.error("media path must be an existing absolute path");
  process.exit(2);
}

const stateDir = "/home/ubuntu/.openclaw/openclaw-weixin/accounts";
const accountPath = path.join(stateDir, `${accountId}.json`);
const contextsPath = path.join(stateDir, `${accountId}.context-tokens.json`);
const account = JSON.parse(fs.readFileSync(accountPath, "utf8"));
const contexts = JSON.parse(fs.readFileSync(contextsPath, "utf8"));
const contextToken = contexts[target];
if (!contextToken) {
  console.error("no saved conversation context for this account and recipient");
  process.exit(3);
}

const projectsRoot = "/home/ubuntu/.openclaw/npm/projects";
const modulePath = fs.readdirSync(projectsRoot, { withFileTypes: true })
  .filter((entry) => entry.isDirectory())
  .map((entry) => path.join(projectsRoot, entry.name, "node_modules/@tencent-weixin/openclaw-weixin/dist/src/messaging/send-media.js"))
  .find((candidate) => fs.existsSync(candidate));
if (!modulePath) {
  console.error("openclaw-weixin media sender module not found");
  process.exit(4);
}
const { sendWeixinMediaFile } = await import(pathToFileURL(modulePath).href);
try {
  const result = await sendWeixinMediaFile({
    filePath: mediaPath,
    to: target,
    text: "",
    opts: {
      baseUrl: account.baseUrl,
      token: account.token,
      contextToken,
    },
    cdnBaseUrl: account.cdnBaseUrl,
  });
  console.log(JSON.stringify({ sent: true, messageId: result.messageId }));
} catch (error) {
  console.error(String(error));
  process.exit(1);
}
