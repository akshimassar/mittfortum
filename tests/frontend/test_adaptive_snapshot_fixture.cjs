const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { pathToFileURL } = require("node:url");

const SNAPSHOT_FILE = "tests/frontend/fixtures/test_adaptive_snapshot_missing_total_stats.json";

let bucketMath;

const toMap = (entries) =>
  new Map((Array.isArray(entries) ? entries : []).map(([k, v]) => [Number(k), Number(v)]));

const sumMap = (map) =>
  Array.from(map.values()).reduce((acc, value) => acc + (Number(value) || 0), 0);

test.before(async () => {
  const modulePath = path.resolve(
    __dirname,
    "../../custom_components/fortum/frontend/strategy/shared/adaptive-bucket-math.mjs"
  );
  bucketMath = await import(pathToFileURL(modulePath).href);
});

test("snapshot fixture keeps missing-total buckets zeroed", () => {
  const snapshotPath = path.resolve(__dirname, "../../", SNAPSHOT_FILE);
  const payload = JSON.parse(fs.readFileSync(snapshotPath, "utf8"));

  assert.equal(payload.bucket_ms, 3 * 60 * 60 * 1000);

  const fixtureTotal = toMap(payload.computed.total_consumed_by_bucket);
  const fixtureDevice = toMap(payload.computed.device_totals_by_bucket);
  const fixtureUntracked = toMap(payload.computed.untracked_by_bucket);

  const { totalConsumedByBucket, untrackedByBucket } =
    bucketMath.computeTotalAndUntrackedByBucket({
      usedTotalByMathBucket: fixtureTotal,
      deviceTotalsByMathBucket: fixtureDevice,
      bucketMs: payload.bucket_ms,
      flowBucketMs: 60 * 60 * 1000,
    });

  // All buckets that have no total data are forced to zero total/untracked.
  const missingTotalBuckets = Array.from(fixtureDevice.keys()).filter(
    (ts) => !fixtureTotal.has(ts)
  );
  assert.ok(missingTotalBuckets.length > 0);
  missingTotalBuckets.forEach((ts) => {
    assert.equal(totalConsumedByBucket.get(ts), 0);
    assert.equal(untrackedByBucket.get(ts), 0);
  });

  // For buckets where total exists, helper output matches fixture behavior.
  fixtureTotal.forEach((_total, ts) => {
    assert.equal(totalConsumedByBucket.get(ts), fixtureTotal.get(ts));
    assert.equal(untrackedByBucket.get(ts), fixtureUntracked.get(ts));
  });

  // The visible-range discrepancy equals sum of device-only buckets.
  const discrepancy = sumMap(totalConsumedByBucket) - (sumMap(fixtureDevice) + sumMap(untrackedByBucket));
  const missingDeviceSum = missingTotalBuckets.reduce(
    (acc, ts) => acc + (fixtureDevice.get(ts) || 0),
    0
  );
  assert.ok(Math.abs(discrepancy + missingDeviceSum) < 1e-9);
});
