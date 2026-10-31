const { mkdir, writeFile } = require("node:fs/promises");
const { join } = require("node:path");

if (!process.argv.includes("--dry-run")) {
  throw new Error("This recovery command must be run with --dry-run.");
}

if (process.env.ENABLE_TELEGRAM_SENDS !== "false") {
  throw new Error("ENABLE_TELEGRAM_SENDS must be false for a dry run.");
}

async function main() {
  await mkdir("replay-output", { recursive: true });
  await writeFile(
    join("replay-output", "dry-run-summary.json"),
    `${JSON.stringify({
      dryRun: true,
      cutoffReceipt: process.env.LOYVERSE_REPLAY_CUTOFF_RECEIPT || null
    }, null, 2)}\n`
  );
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
