const fs = require('node:fs');
const path = require('node:path');

const isDryRun = process.argv.includes('--dry-run');

if (!isDryRun) {
  console.error('Refusing to run chronological recovery without --dry-run.');
  process.exit(1);
}

if (process.env.ENABLE_TELEGRAM_SENDS !== 'false') {
  console.error('Refusing dry-run unless ENABLE_TELEGRAM_SENDS=false.');
  process.exit(1);
}

const cutoffReceipt = process.env.LOYVERSE_REPLAY_CUTOFF_RECEIPT || null;
const outputDir = path.join(process.cwd(), 'replay-output');
const outputPath = path.join(outputDir, 'dry-run-summary.json');

fs.mkdirSync(outputDir, { recursive: true });
fs.writeFileSync(
  outputPath,
  `${JSON.stringify(
    {
      dryRun: true,
      telegramSendsEnabled: false,
      cutoffReceipt,
      generatedAt: new Date().toISOString()
    },
    null,
    2
  )}\n`
);

console.log(`Chronological recovery dry-run completed. Wrote ${outputPath}`);
