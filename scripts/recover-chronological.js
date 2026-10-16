const fs = require("node:fs");
const path = require("node:path");

if (!process.argv.includes("--dry-run")) {
  throw new Error("This recovery command must be run with --dry-run.");
}

if (process.env.ENABLE_TELEGRAM_SENDS !== "false") {
  throw new Error("ENABLE_TELEGRAM_SENDS must be false for a dry run.");
}

const outputDirectory = path.join(process.cwd(), "replay-output");
fs.mkdirSync(outputDirectory, { recursive: true });
fs.writeFileSync(
  path.join(outputDirectory, "dry-run-summary.json"),
  `${JSON.stringify(
    {
      dryRun: true,
      cutoffReceipt: process.env.LOYVERSE_REPLAY_CUTOFF_RECEIPT || null
    },
    null,
    2
  )}\n`
);
