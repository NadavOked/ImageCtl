// #1012: the kiosk keeps the partition-expansion UI without opening the public
// image manifest route on :8082. The authenticated image list carries only the
// fields the renderer needs; classes.js and room.js must not fetch manifests.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '../server/static/station');

for (const name of ['classes.js', 'room.js']) {
  test(`station/${name}: expansion uses the image list, not a manifest request`, () => {
    const source = fs.readFileSync(path.join(root, name), 'utf8');
    assert.doesNotMatch(source, /\/api\/v1\/images\/.*\/manifest/);
    assert.match(source, /image\.expand_partitions/);
    assert.match(source, /expandBlockHtml\([^\n]+partitions: image\.expand_partitions/);
  });
}
