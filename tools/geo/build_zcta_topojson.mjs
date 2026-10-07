#!/usr/bin/env node
/**
 * build_zcta_topojson.mjs — the operator-only build of the committed per-state
 * ZCTA geometry (audit dataviz-01 / visual-09, decision D-dataviz-geo-e part A).
 *
 * It turns three U.S. Census Bureau 2020 files (public domain, 17 U.S.C. 105)
 * into one TopoJSON per state plus DC under frontend/src/geo/zcta/, and a
 * manifest.json that tests/unit/test_zcta_geometry_manifest.py gates:
 *
 *   - cb_2020_us_zcta520_500k.zip      ZCTA cartographic boundaries (national);
 *   - tab20_zcta520_county20_natl.txt  the ZCTA-to-county relationship file
 *                                       (2020 ZCTAs carry no state attribute);
 *   - cb_2020_us_state_500k.zip        the 500k state outlines.
 *
 * A ZCTA belongs to every state whose relationship rows give it land
 * (AREALAND_PART > 0, state FIPS = GEOID_COUNTY_20[0:2]), so a cross-line ZCTA
 * appears in each state it touches. Geometry is pre-projected with us-atlas's
 * own projection, geoAlbersUsa().scale(1300).translate([487.5, 305]), so the
 * runtime needs only topojson-client and the map's existing path writer; a
 * feature that projects to null is dropped. Per state: topology({zcta, state}),
 * presimplify, simplify at ONE fixed planar weight (each ring keeps at least
 * three distinct points), quantize 1e5, every `properties` member removed.
 *
 * Fail closed: every source must match its pinned SHA-256, and an unpinned
 * source is refused (its computed hash is printed for the operator to review
 * and pin here). The limits (1.5 MiB and 120,000 arc points per file, 20 MiB
 * in all) are checked before anything is written. Never run by CI or deploy;
 * nothing here is fetched at deploy time or at runtime. The output is
 * shape-only: demographic, ACS or composition overlays need a fair-lending
 * review first (frontend/src/geo/zcta/README.md).
 *
 * Usage (from the repo root, after `npm --prefix tools/geo ci --ignore-scripts`):
 *   node tools/geo/build_zcta_topojson.mjs [--download] [--source-dir DIR]
 *        [--out DIR] [--weight N]
 */
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { geoAlbersUsa } from 'd3-geo';
import { geoProject } from 'd3-geo-projection';
import * as shapefile from 'shapefile';
import { feature, quantize } from 'topojson-client';
import { topology } from 'topojson-server';
import { presimplify, simplify } from 'topojson-simplify';

const toolDir = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(toolDir, '..', '..');

/**
 * The three sources, pinned. A null sha256 is refused (fail closed): the first
 * approved download prints each computed hash, the operator reviews it and
 * pins it here, and only then does a build run.
 * tests/unit/test_zcta_geometry_manifest.py reads these lines.
 */
export const SOURCES = [
  {
    name: 'zcta',
    url: 'https://www2.census.gov/geo/tiger/GENZ2020/shp/cb_2020_us_zcta520_500k.zip',
    sha256: null,
  },
  {
    name: 'relationship',
    url: 'https://www2.census.gov/geo/docs/maps-data/data/rel2020/zcta520/tab20_zcta520_county20_natl.txt',
    sha256: null,
  },
  {
    name: 'state',
    url: 'https://www2.census.gov/geo/tiger/GENZ2020/shp/cb_2020_us_state_500k.zip',
    sha256: null,
  },
];

/** The 50 states and DC: FIPS -> uppercase USPS (the file names). */
export const FIPS_TO_USPS = {
  '01': 'AL', '02': 'AK', '04': 'AZ', '05': 'AR', '06': 'CA', '08': 'CO', '09': 'CT', '10': 'DE',
  '11': 'DC', '12': 'FL', '13': 'GA', '15': 'HI', '16': 'ID', '17': 'IL', '18': 'IN', '19': 'IA',
  '20': 'KS', '21': 'KY', '22': 'LA', '23': 'ME', '24': 'MD', '25': 'MA', '26': 'MI', '27': 'MN',
  '28': 'MS', '29': 'MO', '30': 'MT', '31': 'NE', '32': 'NV', '33': 'NH', '34': 'NJ', '35': 'NM',
  '36': 'NY', '37': 'NC', '38': 'ND', '39': 'OH', '40': 'OK', '41': 'OR', '42': 'PA', '44': 'RI',
  '45': 'SC', '46': 'SD', '47': 'TN', '48': 'TX', '49': 'UT', '50': 'VT', '51': 'VA', '53': 'WA',
  '54': 'WV', '55': 'WI', '56': 'WY',
};

export const PROJECTION = { name: 'geoAlbersUsa', scale: 1300, translate: [487.5, 305] };
/** The one planar weight (triangle area, Albers-1300 units squared); tuned at the first approved build. */
export const DEFAULT_WEIGHT = 0.0001;
export const QUANTIZATION = 1e5;
const MIB = 1024 * 1024;
export const LIMITS = { fileBytes: 1.5 * MIB, arcPoints: 120_000, totalBytes: 20 * MIB };
const DEPENDENCIES = ['d3-geo', 'd3-geo-projection', 'shapefile', 'topojson-client', 'topojson-server', 'topojson-simplify'];

const fileOf = (source) => path.basename(new URL(source.url).pathname);
const sha256 = (buffer) => createHash('sha256').update(buffer).digest('hex');

export function parseFlags(argv) {
  const flags = {
    download: false,
    sourceDir: path.join(os.homedir(), '.cache', 'mip-geo'),
    out: path.join(repoRoot, 'frontend', 'src', 'geo', 'zcta'),
    weight: DEFAULT_WEIGHT,
    fixturePins: null,
  };
  for (let i = 0; i < argv.length; i += 1) {
    const flag = argv[i];
    const value = () => {
      const next = argv[i + 1];
      if (next === undefined || next.startsWith('--')) throw new Error(`${flag} needs a value`);
      i += 1;
      return next;
    };
    if (flag === '--download') flags.download = true;
    else if (flag === '--source-dir') flags.sourceDir = path.resolve(value());
    else if (flag === '--out') flags.out = path.resolve(value());
    else if (flag === '--weight') flags.weight = Number(value());
    // Synthetic-fixture runs only: pins from a JSON file. The manifest then
    // records fixture_pins, which the committed-geometry gate refuses.
    else if (flag === '--fixture-pins') flags.fixturePins = path.resolve(value());
    else throw new Error(`unknown flag ${flag}`);
  }
  if (!(flags.weight > 0)) throw new Error('--weight must be a positive number');
  return flags;
}

/** The source directory must live outside the repository (no Census file is ever committed). */
export function sourceDirProblem(sourceDir, root = repoRoot) {
  const resolved = existsSync(sourceDir) ? realpathSync(sourceDir) : path.resolve(sourceDir);
  const relative = path.relative(realpathSync(root), resolved);
  return relative === '' || (!relative.startsWith('..') && !path.isAbsolute(relative))
    ? `--source-dir ${sourceDir} is inside the repository; keep the Census sources outside it`
    : null;
}

async function download(sources, sourceDir) {
  mkdirSync(sourceDir, { recursive: true });
  for (const source of sources) {
    const response = await fetch(source.url);
    if (!response.ok) throw new Error(`download failed: ${source.url} answered ${response.status}`);
    writeFileSync(path.join(sourceDir, fileOf(source)), Buffer.from(await response.arrayBuffer()));
    console.log(`downloaded ${source.url}`);
  }
}

/** Fail closed: each source file must exist and match its pin; an unpinned source is refused. */
export function verifySources(sources, sourceDir, pins) {
  const problems = [];
  const hashes = {};
  for (const source of sources) {
    const file = path.join(sourceDir, fileOf(source));
    if (!existsSync(file)) {
      problems.push(`${source.name}: ${file} is missing (run with --download under operator approval)`);
      continue;
    }
    const actual = sha256(readFileSync(file));
    hashes[source.name] = actual;
    const pinned = pins[source.name] ?? null;
    if (pinned === null) problems.push(`${source.name}: no pinned SHA-256; computed ${actual}. Review it, pin it in SOURCES, rerun`);
    else if (pinned !== actual) problems.push(`${source.name}: SHA-256 mismatch, pinned ${pinned}, got ${actual}`);
  }
  return { problems, hashes };
}

/** zcta -> Set of state FIPS with land in it, from the pipe-delimited relationship file. */
export function zctaStates(text) {
  const lines = text.replace(/^﻿/, '').split(/\r?\n/).filter((line) => line.length > 0);
  const header = lines[0].split('|');
  const at = (name) => {
    const index = header.indexOf(name);
    if (index < 0) throw new Error(`relationship file has no ${name} column`);
    return index;
  };
  const zctaAt = at('GEOID_ZCTA5_20');
  const countyAt = at('GEOID_COUNTY_20');
  const landAt = at('AREALAND_PART');
  const states = new Map();
  for (const line of lines.slice(1)) {
    const cells = line.split('|');
    const zcta = (cells[zctaAt] ?? '').trim();
    if (!zcta) continue;
    if (!(Number(cells[landAt]) > 0)) continue;
    const fips = (cells[countyAt] ?? '').trim().slice(0, 2);
    if (!(fips in FIPS_TO_USPS)) continue;
    if (!states.has(zcta)) states.set(zcta, new Set());
    states.get(zcta).add(fips);
  }
  return states;
}

async function readShapefile(zipFile, workDir, idOf) {
  const dir = mkdtempSync(path.join(workDir, 'shp-'));
  execFileSync('unzip', ['-o', '-q', zipFile, '-d', dir]);
  const shp = readdirSync(dir).find((file) => file.endsWith('.shp'));
  if (!shp) throw new Error(`${zipFile} holds no .shp`);
  const collection = await shapefile.read(path.join(dir, shp), undefined, { encoding: 'utf-8' });
  return collection.features.map((input) => ({ id: idOf(input.properties ?? {}), geometry: input.geometry }));
}

/** Project with us-atlas's own projection; a feature that projects to null is dropped. */
export function projectFeatures(features) {
  const projection = geoAlbersUsa().scale(PROJECTION.scale).translate(PROJECTION.translate);
  const out = [];
  for (const input of features) {
    const projected = geoProject({ type: 'Feature', properties: {}, geometry: input.geometry }, projection);
    if (projected.geometry) out.push({ type: 'Feature', id: input.id, properties: {}, geometry: projected.geometry });
  }
  return out;
}

function ringsOf(object) {
  if (object.type === 'Polygon') return object.arcs;
  if (object.type === 'MultiPolygon') return object.arcs.flat();
  if (object.type === 'GeometryCollection') return object.geometries.flatMap(ringsOf);
  return [];
}

/**
 * Before simplify: a ring that would keep fewer than three distinct points at
 * `weight` gets its heaviest interior points promoted, so no small ZCTA
 * collapses to a line or a point.
 */
export function protectRings(presimplified, weight) {
  for (const object of Object.values(presimplified.objects)) {
    for (const ring of ringsOf(object)) {
      const arcs = ring.map((index) => presimplified.arcs[index < 0 ? ~index : index]);
      // Each arc's last point is the next arc's first: count it once.
      const kept = arcs.reduce((total, arc) => total + arc.slice(0, -1).filter((point) => point[2] >= weight).length, 0);
      if (kept >= 3) continue;
      const candidates = arcs.flatMap((arc) => arc.slice(1, -1)).filter((point) => point[2] < weight);
      candidates.sort((a, b) => b[2] - a[2]);
      for (const point of candidates.slice(0, 3 - kept)) point[2] = Infinity;
    }
  }
  return presimplified;
}

function stripProperties(value) {
  if (Array.isArray(value)) {
    value.forEach(stripProperties);
  } else if (value && typeof value === 'object') {
    delete value.properties;
    for (const key of Object.keys(value)) if (key !== 'arcs') stripProperties(value[key]);
  }
  return value;
}

const round3 = (value) => Math.round(value * 1000) / 1000;

function bboxOf(geoFeatures) {
  const box = [Infinity, Infinity, -Infinity, -Infinity];
  const visit = (coordinates) => {
    if (typeof coordinates[0] === 'number') {
      box[0] = Math.min(box[0], coordinates[0]);
      box[1] = Math.min(box[1], coordinates[1]);
      box[2] = Math.max(box[2], coordinates[0]);
      box[3] = Math.max(box[3], coordinates[1]);
    } else coordinates.forEach(visit);
  };
  for (const geo of geoFeatures) if (geo.geometry) visit(geo.geometry.coordinates);
  return box.map(round3);
}

/** One state's topology, serialized, with the manifest's statistics. */
export function buildStateTopology(zctaFeatures, stateFeature, weight) {
  // Deep copies: a cross-line ZCTA is built into each state it touches.
  const copy = (input) => JSON.parse(JSON.stringify(input));
  const sorted = zctaFeatures.map(copy).sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
  const raw = topology({
    zcta: { type: 'FeatureCollection', features: sorted },
    state: { type: 'Feature', id: stateFeature.id, properties: {}, geometry: copy(stateFeature.geometry) },
  });
  const built = stripProperties(quantize(simplify(protectRings(presimplify(raw), weight), weight), QUANTIZATION));
  const text = JSON.stringify(built);
  const decoded = JSON.parse(text);
  return {
    text,
    stats: {
      zcta_count: sorted.length,
      bytes: Buffer.byteLength(text),
      sha256: sha256(text),
      arc_point_count: decoded.arcs.reduce((total, arc) => total + arc.length, 0),
      zcta_bbox: bboxOf(feature(decoded, decoded.objects.zcta).features),
      state_bbox: bboxOf([feature(decoded, decoded.objects.state)]),
    },
  };
}

export function limitProblems(files) {
  const problems = [];
  let total = 0;
  for (const [name, stats] of Object.entries(files)) {
    total += stats.bytes;
    if (stats.bytes > LIMITS.fileBytes) problems.push(`${name} is ${stats.bytes} bytes > ${LIMITS.fileBytes}`);
    if (stats.arc_point_count > LIMITS.arcPoints) problems.push(`${name} has ${stats.arc_point_count} arc points > ${LIMITS.arcPoints}`);
  }
  if (total > LIMITS.totalBytes) problems.push(`the geometry totals ${total} bytes > ${LIMITS.totalBytes}`);
  return problems;
}

function dependencyVersions() {
  const lock = JSON.parse(readFileSync(path.join(toolDir, 'package-lock.json'), 'utf8'));
  return Object.fromEntries(DEPENDENCIES.map((name) => [name, lock.packages[`node_modules/${name}`]?.version ?? null]));
}

/** Keys sorted at every level, so a rebuild of the same inputs is byte-identical. */
function stableJson(value) {
  const sortKeys = (input) => {
    if (Array.isArray(input)) return input.map(sortKeys);
    if (input && typeof input === 'object') {
      return Object.fromEntries(Object.keys(input).sort().map((key) => [key, sortKeys(input[key])]));
    }
    return input;
  };
  return `${JSON.stringify(sortKeys(value), null, 2)}\n`;
}

async function main(argv = process.argv.slice(2)) {
  const flags = parseFlags(argv);
  const placement = sourceDirProblem(flags.sourceDir);
  if (placement) throw new Error(placement);
  const pins = flags.fixturePins
    ? JSON.parse(readFileSync(flags.fixturePins, 'utf8'))
    : Object.fromEntries(SOURCES.map((source) => [source.name, source.sha256]));
  if (flags.fixturePins) console.warn('FIXTURE PINS: this output is a synthetic test build and is never committable.');
  if (flags.download) await download(SOURCES, flags.sourceDir);
  const { problems, hashes } = verifySources(SOURCES, flags.sourceDir, pins);
  if (problems.length > 0) {
    for (const problem of problems) console.error(`  - ${problem}`);
    throw new Error('source verification failed (fail closed)');
  }

  const workDir = mkdtempSync(path.join(os.tmpdir(), 'mip-geo-'));
  try {
    const at = (name) => path.join(flags.sourceDir, fileOf(SOURCES.find((source) => source.name === name)));
    const states = projectFeatures(await readShapefile(at('state'), workDir, (p) => String(p.STATEFP ?? '')));
    const zctas = projectFeatures(await readShapefile(at('zcta'), workDir, (p) => String(p.ZCTA5CE20 ?? '')));
    const membership = zctaStates(readFileSync(at('relationship'), 'utf8'));

    const outputs = {};
    for (const [fips, usps] of Object.entries(FIPS_TO_USPS)) {
      const stateFeature = states.find((candidate) => candidate.id === fips);
      if (!stateFeature) throw new Error(`the state file has no outline for FIPS ${fips} (${usps})`);
      const members = zctas.filter((zcta) => /^\d{5}$/.test(zcta.id) && membership.get(zcta.id)?.has(fips));
      outputs[`${usps}.topo.json`] = buildStateTopology(members, stateFeature, flags.weight);
    }
    const files = Object.fromEntries(Object.entries(outputs).map(([name, output]) => [name, output.stats]));
    for (const [name, stats] of Object.entries(files)) {
      console.log(`${name}: ${stats.zcta_count} ZCTAs, ${stats.bytes} bytes, ${stats.arc_point_count} arc points`);
    }
    const overLimit = limitProblems(files);
    if (overLimit.length > 0) {
      for (const problem of overLimit) console.error(`  - ${problem}`);
      throw new Error(`limits exceeded at --weight ${flags.weight}; nothing was written`);
    }

    mkdirSync(flags.out, { recursive: true });
    for (const stale of readdirSync(flags.out).filter((file) => file.endsWith('.topo.json') && !(file in outputs))) {
      rmSync(path.join(flags.out, stale));
    }
    for (const [name, output] of Object.entries(outputs)) writeFileSync(path.join(flags.out, name), output.text);
    const manifest = {
      generated_by: 'tools/geo/build_zcta_topojson.mjs',
      sources: SOURCES.map((source) => ({ name: source.name, url: source.url, sha256: hashes[source.name] })),
      dependencies: dependencyVersions(),
      projection: PROJECTION,
      weight: flags.weight,
      quantization: QUANTIZATION,
      files,
      ...(flags.fixturePins ? { fixture_pins: true } : {}),
    };
    writeFileSync(path.join(flags.out, 'manifest.json'), stableJson(manifest));
    console.log(`wrote ${Object.keys(outputs).length} files and manifest.json to ${flags.out}`);
  } finally {
    rmSync(workDir, { recursive: true, force: true });
  }
}

// Main-module guard, realpath-safe (audit bundle-08): Node runs a symlinked
// script under its real path, so a plain argv[1] comparison skips main().
if (process.argv[1] && import.meta.url === pathToFileURL(realpathSync(process.argv[1])).href) {
  main().catch((error) => {
    console.error(`build_zcta_topojson: ${error instanceof Error ? error.message : String(error)}`);
    process.exit(1);
  });
}
