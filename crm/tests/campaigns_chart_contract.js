const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

class Element {
  constructor(tag = 'div') {
    this.tag = tag;
    this.children = [];
    this.className = '';
    this.textContent = '';
    this.style = {};
    this.listeners = {};
    this.classList = {
      add: (name) => { this.className += ` ${name}`; },
      remove: (name) => { this.className = this.className.split(' ').filter((part) => part !== name).join(' '); },
    };
  }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute() {}
  addEventListener(name, callback) { this.listeners[name] = callback; }
  querySelectorAll(selector) {
    const matches = [];
    const visit = (node) => {
      if (!(node instanceof Element)) return;
      if (node.className.split(' ').includes(selector.slice(1))) matches.push(node);
      node.children.forEach(visit);
    };
    this.children.forEach(visit);
    return matches;
  }
}
const ids = {
  'campaign-chart-data': new Element('script'),
  'campaign-trend-chart': new Element(),
  'campaign-ranking-chart': new Element(),
  'campaign-trend-metric': new Element('select'),
  'campaign-ranking-metric': new Element('select'),
};
ids['campaign-trend-metric'].value = 'chats';
ids['campaign-ranking-metric'].value = 'leads';
ids['campaign-chart-data'].textContent = JSON.stringify({
  from: '2026-08-03', to: '2026-08-16', hasMeta: true,
  trend: [
    { date: '2026-08-03', chats: 4, buyers: 1, spend: 12.5 },
    { date: '2026-08-05', chats: 10, buyers: 2, spend: 25 },
  ],
  rows: [{ source_id: '123', label: 'Anuncio', leads: 14, buyers: 3, spend: 37.5 }],
});
const document = {
  getElementById: (id) => ids[id],
  createElement: (tag) => new Element(tag),
};
const script = fs.readFileSync(path.join(__dirname, '../public/assets/campaigns.js'), 'utf8');
vm.runInNewContext(script, { document, window: { innerWidth: 1000 }, Intl, Date, Map, Math, Number });

const trend = ids['campaign-trend-chart'];
assert.equal(trend.querySelectorAll('.campaigns-plot-scale').length, 1, 'Debe mostrar escala');
assert.equal(trend.querySelectorAll('.campaigns-plot-item').length, 14, 'Debe incluir días sin actividad');
assert.match(trend.querySelectorAll('.campaigns-trend-summary')[0].children[0].children[0].textContent, /14 chats/);
assert.match(trend.querySelectorAll('.campaigns-trend-detail')[0].textContent, /10 chats/);
const firstBar = trend.querySelectorAll('.campaigns-plot-item')[0];
firstBar.listeners.click();
assert.match(trend.querySelectorAll('.campaigns-trend-detail')[0].textContent, /4 chats/);
ids['campaign-trend-metric'].value = 'spend';
ids['campaign-trend-metric'].listeners.change();
assert.match(ids['campaign-trend-chart'].querySelectorAll('.campaigns-trend-summary')[0].children[0].children[0].textContent, /S\/37\.50 de gasto/);
console.log('campaigns chart contract: OK');
