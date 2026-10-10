if (!process.argv.includes("--dry-run")) {
  console.error("This recovery command currently supports dry-run mode only.");
  process.exitCode = 1;
} else {
  console.log("Chronological recovery dry-run completed.");
}
