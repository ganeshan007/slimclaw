/**
 * Memory profiling for nanoclaw (Node.js/TypeScript).
 *
 * Measures the same things as profile_python.py for fair comparison:
 * 1. Bare Node.js interpreter baseline
 * 2. Import overhead for each module
 * 3. Database initialization + schema creation
 * 4. Bulk message insertion (1000, 5000, 10000 messages)
 * 5. Bulk message query performance
 * 6. Full module import (all modules loaded)
 * 7. Object creation overhead
 */
import { writeFileSync } from 'fs';
import { dirname, join } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const projectRoot = join(__dirname, '..', '..', 'nanoclaw');  // nanoclaw/ (TS version)
const distDir = join(projectRoot, 'dist');

// Change to project root so config.ts resolves correctly
process.chdir(projectRoot);

function getRssMB() {
  return process.memoryUsage().rss / 1024 / 1024;
}

function getHeapMB() {
  const mem = process.memoryUsage();
  return {
    heapUsed: mem.heapUsed / 1024 / 1024,
    heapTotal: mem.heapTotal / 1024 / 1024,
    external: mem.external / 1024 / 1024,
    arrayBuffers: mem.arrayBuffers / 1024 / 1024,
  };
}

function forceGC() {
  if (global.gc) {
    global.gc();
    global.gc();
    global.gc();
  }
}

const results = {
  runtime: 'nodejs',
  node_version: process.version,
  tests: [],
};

// --- Test 1: Bare interpreter baseline ---
forceGC();
const baselineRss = getRssMB();
const baselineHeap = getHeapMB();
results.tests.push({
  name: 'bare_interpreter',
  rss_mb: Math.round(baselineRss * 1000) / 1000,
  heap_used_mb: Math.round(baselineHeap.heapUsed * 1000) / 1000,
  heap_total_mb: Math.round(baselineHeap.heapTotal * 1000) / 1000,
});
console.log(`[1] Bare interpreter:          RSS=${baselineRss.toFixed(1)} MB  heap_used=${baselineHeap.heapUsed.toFixed(1)} MB`);

// --- Test 2: Import overhead per module ---
console.log('\n[2] Import overhead per module:');
const moduleList = [
  [join(distDir, 'types.js'), 'types'],
  [join(distDir, 'env.js'), 'env'],
  [join(distDir, 'config.js'), 'config'],
  [join(distDir, 'logger.js'), 'logger'],
  [join(distDir, 'router.js'), 'router'],
  [join(distDir, 'container-runtime.js'), 'container_runtime'],
  [join(distDir, 'db.js'), 'db'],
  [join(distDir, 'mount-security.js'), 'mount_security'],
  [join(distDir, 'group-queue.js'), 'group_queue'],
  [join(distDir, 'ipc.js'), 'ipc'],
  [join(distDir, 'task-scheduler.js'), 'task_scheduler'],
  [join(distDir, 'container-runner.js'), 'container_runner'],
];

const importResults = [];
for (const [modPath, name] of moduleList) {
  forceGC();
  const rssBefore = getRssMB();
  const heapBefore = getHeapMB();

  try {
    await import(modPath);
  } catch (e) {
    // Some modules may fail without full environment; that's OK for import measurement
  }

  forceGC();
  const rssAfter = getRssMB();
  const heapAfter = getHeapMB();

  const r = {
    module: name,
    rss_delta_mb: Math.round((rssAfter - rssBefore) * 1000) / 1000,
    heap_delta_mb: Math.round((heapAfter.heapUsed - heapBefore.heapUsed) * 1000) / 1000,
    rss_after_mb: Math.round(rssAfter * 1000) / 1000,
  };
  importResults.push(r);
  console.log(`    ${name.padEnd(25)}  RSS delta=${r.rss_delta_mb >= 0 ? '+' : ''}${r.rss_delta_mb.toFixed(3)} MB  heap delta=${r.heap_delta_mb >= 0 ? '+' : ''}${r.heap_delta_mb.toFixed(3)} MB  RSS total=${r.rss_after_mb.toFixed(1)} MB`);
}

results.tests.push({ name: 'import_overhead', modules: importResults });

forceGC();
const postImportRss = getRssMB();
const postImportHeap = getHeapMB();
results.tests.push({
  name: 'all_modules_imported',
  rss_mb: Math.round(postImportRss * 1000) / 1000,
  heap_used_mb: Math.round(postImportHeap.heapUsed * 1000) / 1000,
  heap_total_mb: Math.round(postImportHeap.heapTotal * 1000) / 1000,
});
console.log(`\n    All modules imported:       RSS=${postImportRss.toFixed(1)} MB  heap_used=${postImportHeap.heapUsed.toFixed(1)} MB`);

// --- Test 3: Database initialization ---
console.log('\n[3] Database initialization:');
const db = await import(join(distDir, 'db.js'));
forceGC();
let rssBefore = getRssMB();
let heapBefore = getHeapMB();

db._initTestDatabase();

forceGC();
let rssAfter = getRssMB();
let heapAfter = getHeapMB();
const dbInitResult = {
  name: 'db_init',
  rss_delta_mb: Math.round((rssAfter - rssBefore) * 1000) / 1000,
  heap_delta_mb: Math.round((heapAfter.heapUsed - heapBefore.heapUsed) * 1000) / 1000,
};
results.tests.push(dbInitResult);
console.log(`    DB init (in-memory):        RSS delta=${dbInitResult.rss_delta_mb >= 0 ? '+' : ''}${dbInitResult.rss_delta_mb.toFixed(3)} MB  heap delta=${dbInitResult.heap_delta_mb >= 0 ? '+' : ''}${dbInitResult.heap_delta_mb.toFixed(3)} MB`);

// --- Test 4: Bulk message insertion ---
console.log('\n[4] Bulk message insertion:');

for (const count of [1000, 5000, 10000]) {
  db._initTestDatabase();
  db.storeChatMetadata('bench@g.us', '2024-01-01T00:00:00.000Z');

  forceGC();
  rssBefore = getRssMB();
  const tStart = performance.now();

  for (let i = 0; i < count; i++) {
    db.storeMessage({
      id: `msg-${i}`,
      chat_jid: 'bench@g.us',
      sender: `user${i % 10}@s.whatsapp.net`,
      sender_name: `User${i % 10}`,
      content: `Message number ${i} with some content to simulate real messages`,
      timestamp: `2024-01-01T${String(Math.floor(i / 3600)).padStart(2, '0')}:${String(Math.floor((i % 3600) / 60)).padStart(2, '0')}:${String(i % 60).padStart(2, '0')}.000Z`,
    });
  }

  const tElapsed = performance.now() - tStart;
  forceGC();
  rssAfter = getRssMB();

  const insertResult = {
    name: `insert_${count}_messages`,
    count,
    time_ms: Math.round(tElapsed * 10) / 10,
    rss_delta_mb: Math.round((rssAfter - rssBefore) * 1000) / 1000,
    msgs_per_sec: Math.round(count / (tElapsed / 1000)),
  };
  results.tests.push(insertResult);
  console.log(`    ${String(count).padStart(5)} messages:  ${String(tElapsed.toFixed(1)).padStart(7)} ms  (${insertResult.msgs_per_sec.toLocaleString()} msg/s)  RSS delta=${insertResult.rss_delta_mb >= 0 ? '+' : ''}${insertResult.rss_delta_mb.toFixed(3)} MB`);
}

// --- Test 5: Query performance ---
console.log('\n[5] Query performance (10000 messages in DB):');

const queries = [
  ['getMessagesSince (all)', () => db.getMessagesSince('bench@g.us', '', 'TARS')],
  ['getMessagesSince (last 100)', () => db.getMessagesSince('bench@g.us', '2024-01-01T02:43:19.000Z', 'TARS')],
  ['getNewMessages (1 group)', () => db.getNewMessages(['bench@g.us'], '', 'TARS')],
];

for (const [label, fn] of queries) {
  forceGC();
  rssBefore = getRssMB();
  const tStart = performance.now();

  const resultData = fn();

  const tElapsed = performance.now() - tStart;
  forceGC();
  rssAfter = getRssMB();

  const rowCount = Array.isArray(resultData) ? resultData.length : resultData.messages.length;
  const queryResult = {
    name: `query_${label}`,
    rows: rowCount,
    time_ms: Math.round(tElapsed * 100) / 100,
    rss_delta_mb: Math.round((rssAfter - rssBefore) * 1000) / 1000,
  };
  results.tests.push(queryResult);
  console.log(`    ${label.padEnd(35)}  ${String(rowCount).padStart(5)} rows  ${String(tElapsed.toFixed(2)).padStart(7)} ms  RSS delta=${queryResult.rss_delta_mb >= 0 ? '+' : ''}${queryResult.rss_delta_mb.toFixed(3)} MB`);
}

// --- Test 6: Object creation overhead ---
console.log('\n[6] Object creation overhead (10000 message objects):');
forceGC();
rssBefore = getRssMB();
heapBefore = getHeapMB();
const tStart6 = performance.now();

const objects = [];
for (let i = 0; i < 10000; i++) {
  objects.push({
    id: `obj-${i}`,
    chat_jid: 'bench@g.us',
    sender: `user${i}@s.whatsapp.net`,
    sender_name: `User${i}`,
    content: `Content ${i}`,
    timestamp: `2024-01-01T00:00:${String(i % 60).padStart(2, '0')}.000Z`,
  });
}

const tElapsed6 = performance.now() - tStart6;
forceGC();
rssAfter = getRssMB();
heapAfter = getHeapMB();

const objResult = {
  name: 'create_10000_objects',
  time_ms: Math.round(tElapsed6 * 100) / 100,
  rss_delta_mb: Math.round((rssAfter - rssBefore) * 1000) / 1000,
  heap_delta_mb: Math.round((heapAfter.heapUsed - heapBefore.heapUsed) * 1000) / 1000,
};
results.tests.push(objResult);
console.log(`    10000 objects:              ${tElapsed6.toFixed(2)} ms  RSS delta=${objResult.rss_delta_mb >= 0 ? '+' : ''}${objResult.rss_delta_mb.toFixed(3)} MB  heap delta=${objResult.heap_delta_mb >= 0 ? '+' : ''}${objResult.heap_delta_mb.toFixed(3)} MB`);

// --- Test 7: Final snapshot ---
forceGC();
const finalRss = getRssMB();
const finalHeap = getHeapMB();
results.tests.push({
  name: 'final_snapshot',
  rss_mb: Math.round(finalRss * 1000) / 1000,
  heap_used_mb: Math.round(finalHeap.heapUsed * 1000) / 1000,
  heap_total_mb: Math.round(finalHeap.heapTotal * 1000) / 1000,
  external_mb: Math.round(finalHeap.external * 1000) / 1000,
});
console.log(`\n[7] Final snapshot:             RSS=${finalRss.toFixed(1)} MB  heap_used=${finalHeap.heapUsed.toFixed(1)} MB  heap_total=${finalHeap.heapTotal.toFixed(1)} MB  external=${finalHeap.external.toFixed(1)} MB`);

// Write results
const outputPath = join(__dirname, 'results_node.json');
writeFileSync(outputPath, JSON.stringify(results, null, 2));
console.log(`\nResults written to ${outputPath}`);
