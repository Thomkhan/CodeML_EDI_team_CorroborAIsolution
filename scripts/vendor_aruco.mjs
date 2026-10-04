/**
 * Re-vendors js-aruco2 (MIT, Damiano Falcioni / Juan Mellado) from node_modules
 * into src/lib/vendor/jsAruco2 as ES modules.
 *
 * Why not just `import 'js-aruco2'`: the upstream files are plain global-scope
 * scripts (`var CV = ...; this.CV = CV;`) with a CommonJS `require` fallback.
 * They can't be imported by a module Web Worker, and loading them with
 * <script>/importScripts would put the detector back on the main thread — the
 * exact thing this rewrite exists to avoid.
 *
 * The patch is deliberately four lines, so the vendored copy stays
 * byte-identical to upstream everywhere else and is trivial to re-audit.
 *
 * Run: node scripts/vendor_aruco.mjs
 */
import { readFileSync, writeFileSync, copyFileSync, mkdirSync } from 'node:fs'

const SRC = 'node_modules/js-aruco2/src'
const OUT = 'src/lib/vendor/jsAruco2'
const BANNER = (name) =>
  `/* eslint-disable */\n` +
  `// Vendored from js-aruco2 ${JSON.parse(readFileSync('node_modules/js-aruco2/package.json', 'utf8')).version} (${name}).\n` +
  `// MIT licence — see LICENSE.txt in this folder. Regenerate: node scripts/vendor_aruco.mjs\n` +
  `// Only the module wrapper differs from upstream.\n`

mkdirSync(OUT, { recursive: true })
copyFileSync('node_modules/js-aruco2/LICENSE.txt', `${OUT}/LICENSE.txt`)

let cv = readFileSync(`${SRC}/cv.js`, 'utf8')
if (!cv.includes('var CV = CV || {};\nthis.CV = CV;')) throw new Error('cv.js preamble changed upstream — re-check the patch')
cv = cv.replace('var CV = CV || {};\nthis.CV = CV;', 'const CV = {};')
writeFileSync(`${OUT}/cv.js`, `${BANNER('cv.js')}${cv}\nexport { CV }\n`)

let ar = readFileSync(`${SRC}/aruco.js`, 'utf8')
if (!ar.includes("var CV = this.CV || require('./cv').CV;")) throw new Error('aruco.js preamble changed upstream — re-check the patch')
ar = ar
  .replace('var AR = {};', "import { CV } from './cv.js'\n\nconst AR = {};")
  .replace("var CV = this.CV || require('./cv').CV;\n", '')
  .replace('this.AR = AR;\n', '')
writeFileSync(`${OUT}/aruco.js`, `${BANNER('aruco.js')}${ar}\nexport { AR }\n`)

console.log('vendored js-aruco2 ->', OUT)
