const fs = require("node:fs");
const path = require("node:path");

if (!process.argv.includes("--dry-run")) {
  throw new Error("Chronological recovery requires the --dry-run flag.");
}

if (process.env.ENABLE_TELEGRAM_SENDS !== "false") {
  throw new Error("Chronological recovery requires ENABLE_TELEGRAM_SENDS=false.");
}

const outputDirectory = path.join(process.cwd(), "replay-output");
fs.mkdirSync(outputDirectory, { recursive: true });
fs.writeFileSync(
  path.join(outputDirectory, "dry-run-summary.json"),
  `${JSON.stringify({ dryRun: true }, null, 2)}\n`,
);

console.log("Chronological recovery dry-run completed.");
