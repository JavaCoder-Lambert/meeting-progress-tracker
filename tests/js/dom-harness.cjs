// Small offline DOM adapter for app.js. No layout engine or application logic.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

class DOMEvent {
  constructor(type, options = {}) {
    Object.assign(this, {type, bubbles: false, cancelable: true, defaultPrevented: false}, options);
  }
  preventDefault() { if (this.cancelable) this.defaultPrevented = true; }
}

class Element {
  constructor(tag, attributes = {}) {
    this.tagName = tag.toUpperCase();
    this.attributes = {};
    this.dataset = {};
    this.children = [];
    this.listeners = new Map();
    this.value = '';
    this.textContent = '';
    this.disabled = false;
    this.hidden = false;
    this.isContentEditable = false;
    this.classList = {
      add: (...names) => { this.className = [...new Set([...this.className.split(' '), ...names])].join(' '); },
      remove: (...names) => { this.className = this.className.split(' ').filter(name => !names.includes(name)).join(' '); },
    };
    for (const [name, value] of Object.entries(attributes)) this.setAttribute(name, value);
  }
  get className() { return this.attributes.class || ''; }
  set className(value) { this.attributes.class = value; }
  setAttribute(name, value) {
    this.attributes[name] = String(value);
    if (name.startsWith('data-')) {
      this.dataset[name.slice(5).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase())] = String(value);
    } else if (['id', 'name', 'type', 'value', 'href', 'action'].includes(name)) this[name] = String(value);
  }
  getAttribute(name) { return this.attributes[name] ?? null; }
  append(...children) {
    children.forEach(child => { child.parentElement = this; this.children.push(child); });
  }
  matches(selector) {
    const tag = selector.match(/^[a-z]+/i)?.[0];
    if (tag && this.tagName !== tag.toUpperCase()) return false;
    const className = selector.match(/\.([\w-]+)/)?.[1];
    if (className && !this.className.split(' ').includes(className)) return false;
    for (const match of selector.matchAll(/\[([\w-]+)(?:(\$?=)["']([^"']*)["'])?\]/g)) {
      const value = this.getAttribute(match[1]);
      if (value === null) return false;
      if (match[2] === '=' && value !== match[3]) return false;
      if (match[2] === '$=' && !value.endsWith(match[3])) return false;
    }
    return true;
  }
  querySelectorAll(selector) {
    const selectors = selector.split(',').map(item => item.trim().split(/\s+/));
    const descendants = this.children.flatMap(child => [child, ...child.querySelectorAll('*')]);
    return descendants.filter(element => selectors.some(parts => {
      if (!element.matches(parts.at(-1))) return false;
      let ancestor = element.parentElement;
      for (let index = parts.length - 2; index >= 0; index--) {
        while (ancestor && !ancestor.matches(parts[index])) ancestor = ancestor.parentElement;
        if (!ancestor) return false;
        ancestor = ancestor.parentElement;
      }
      return true;
    }));
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  closest(selector) {
    return selector.split(',').some(item => this.matches(item.trim())) ? this : this.parentElement?.closest(selector) || null;
  }
  addEventListener(type, handler) {
    this.listeners.set(type, [...(this.listeners.get(type) || []), handler]);
  }
  dispatchEvent(event) {
    event.target ||= this;
    for (const handler of this.listeners.get(event.type) || []) handler(event);
    if (event.bubbles && this.parentElement) this.parentElement.dispatchEvent(event);
    return !event.defaultPrevented;
  }
  focus() { this.ownerDocument.activeElement = this; }
  get ownerDocument() { return this.parentElement ? this.parentElement.ownerDocument : this; }
  requestSubmit(submitter) { this.dispatchEvent(new DOMEvent('submit', {submitter, bubbles: true})); }
}

class Document extends Element {
  constructor() { super('document'); this.body = new Element('body'); this.append(this.body); }
  createElement(tag) { return new Element(tag); }
  getElementById(id) { return this.querySelectorAll('*').find(element => element.id === id) || null; }
}

// Use native Node FormData for the payload; collect successful form controls using
// HTML rules relevant here. Disabled buttons are excluded, including the submitter.
class BrowserFormData extends FormData {
  constructor(form) {
    super();
    for (const element of form.querySelectorAll('input, select, textarea, button')) {
      if (element.name && !element.disabled && element.tagName !== 'BUTTON') this.append(element.name, element.value);
    }
  }
}

function element(tag, attrs, ...children) {
  const result = new Element(tag, attrs);
  result.append(...children);
  return result;
}

function loadApp(document, overrides = {}) {
  const location = {href: 'http://localhost/meetings/1/?auto_parse=1', assign(value) { this.href = value; }};
  const window = {location, history: {replaceState(_state, _title, url) { location.href = String(url); }},
    setInterval: () => 1, clearInterval: () => {}};
  const context = vm.createContext({document, window, navigator: {clipboard: {writeText: async () => {}}},
    Event: DOMEvent, FormData: BrowserFormData, URL, console, ...overrides});
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../../static/js/app.js'), 'utf8'), context, {filename: 'app.js'});
  document.dispatchEvent(new DOMEvent('DOMContentLoaded'));
  return context;
}

const fire = (target, type, options = {}) => {
  const event = new DOMEvent(type, {bubbles: true, ...options});
  target.dispatchEvent(event);
  return event;
};
module.exports = {Document, element, loadApp, fire, BrowserFormData};
