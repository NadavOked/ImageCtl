const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const js = fs.readFileSync(path.resolve(__dirname, '../server/static/console.js'), 'utf8');

test('scheduled reboot settings stay in the existing general card and use one sheet', () => {
  for (const key of ['auto_reboot', 'auto_reboot_time', 'auto_reboot_weekday'])
    assert.match(js, new RegExp(`key: "${key}", card: "general"`));
  assert.match(js, /title: "הפעלה מחדש אוטומטית"/);
  assert.match(js, /label: "שעה"/);
  assert.match(js, /label: "יום"/);
  assert.doesNotMatch(js, /UI\.card\(\{ title: "הפעלה מחדש אוטומטית"/);
});
