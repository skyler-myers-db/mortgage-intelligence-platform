#!/usr/bin/env node
// Pure-move proof for a TypeScript object literal split across modules.
//
// Usage: node tools/refactor_proof_object_members.mjs <pre.ts> <post.ts> [name]
//
// Lists every property of the exported object literal `name` (default `api`)
// in the PRE file as name -> source text, then resolves the same object in the
// POST file: spread elements (`...leadsApi`) are followed to the module that
// exports that const (relative imports only) and expanded in place, so the
// resolved member list has the same shape as the original literal. A pure
// move prints identical names, identical order and zero text differences.
//
// The TypeScript compiler API is loaded from frontend/node_modules, so run
// `npm --prefix frontend ci` first.

import { createRequire } from "node:module";
import { readFileSync, existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const requireFrontend = createRequire(path.join(here, "..", "frontend", "package.json"));
const ts = requireFrontend("typescript");

function parse(file) {
  return ts.createSourceFile(file, readFileSync(file, "utf8"), ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
}

function exportedObjectLiterals(source) {
  const found = new Map();
  for (const statement of source.statements) {
    if (!ts.isVariableStatement(statement)) continue;
    for (const declaration of statement.declarationList.declarations) {
      if (ts.isIdentifier(declaration.name) && declaration.initializer && ts.isObjectLiteralExpression(declaration.initializer)) {
        found.set(declaration.name.text, declaration.initializer);
      }
    }
  }
  return found;
}

function importSources(source, file) {
  const map = new Map();
  for (const statement of source.statements) {
    if (!ts.isImportDeclaration(statement) || !statement.importClause?.namedBindings) continue;
    const spec = statement.moduleSpecifier.text;
    if (!spec.startsWith(".")) continue;
    const bindings = statement.importClause.namedBindings;
    if (!ts.isNamedImports(bindings)) continue;
    const base = path.resolve(path.dirname(file), spec);
    const target = [base + ".ts", base + ".tsx", path.join(base, "index.ts")].find((candidate) => existsSync(candidate));
    for (const element of bindings.elements) {
      map.set(element.name.text, { file: target, exported: (element.propertyName ?? element.name).text });
    }
  }
  return map;
}

function members(literal, source, file, depth = 0) {
  if (depth > 5) throw new Error("spread resolution too deep");
  const out = [];
  for (const property of literal.properties) {
    if (ts.isSpreadAssignment(property) && ts.isIdentifier(property.expression)) {
      const ref = importSources(source, file).get(property.expression.text);
      if (!ref || !ref.file) throw new Error(`cannot resolve spread ${property.expression.text} in ${file}`);
      const target = parse(ref.file);
      const literalInTarget = exportedObjectLiterals(target).get(ref.exported);
      if (!literalInTarget) throw new Error(`${ref.exported} is not an object literal in ${ref.file}`);
      out.push(...members(literalInTarget, target, ref.file, depth + 1));
      continue;
    }
    const name = property.name && ts.isIdentifier(property.name) ? property.name.text : property.getText(source);
    out.push({ name, text: property.getText(source).replace(/\s+/g, " ").trim() });
  }
  return out;
}

const [preFile, postFile, objectName = "api"] = process.argv.slice(2);
if (!preFile || !postFile) {
  console.error("usage: refactor_proof_object_members.mjs <pre.ts> <post.ts> [name]");
  process.exit(2);
}
const preSource = parse(preFile);
const postSource = parse(postFile);
const preLiteral = exportedObjectLiterals(preSource).get(objectName);
const postLiteral = exportedObjectLiterals(postSource).get(objectName);
if (!preLiteral || !postLiteral) {
  console.error(`object literal ${objectName} not found in both files`);
  process.exit(2);
}
const pre = members(preLiteral, preSource, preFile);
const post = members(postLiteral, postSource, postFile);
const preNames = pre.map((m) => m.name);
const postNames = post.map((m) => m.name);
const sameOrder = JSON.stringify(preNames) === JSON.stringify(postNames);
const postByName = new Map(post.map((m) => [m.name, m.text]));
const differences = pre.filter((m) => postByName.get(m.name) !== m.text).map((m) => m.name);
console.log(`pre members: ${pre.length}`);
console.log(`post members: ${post.length}`);
console.log(`order identical: ${sameOrder}`);
console.log(`missing in post: ${preNames.filter((n) => !postByName.has(n)).join(", ") || "none"}`);
console.log(`text differences: ${differences.length ? differences.join(", ") : "none"}`);
if (!sameOrder || differences.length || pre.length !== post.length) {
  console.error("object-members proof FAILED");
  process.exit(1);
}
console.log("object-members proof OK");
