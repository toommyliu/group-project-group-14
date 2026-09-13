import { execFileSync } from 'node:child_process';
import { readFileSync, writeFileSync } from 'node:fs';
import openapiTS, { astToString } from 'openapi-typescript';

const schema = execFileSync(
  'uv',
  ['run', 'python', '-m', 'opengrader.server.schema'],
  {
    cwd: new URL('../../../..', import.meta.url),
    encoding: 'utf8',
  }
);
const output =
  '// Generated from FastAPI. Run pnpm generate:api to update.\n' +
  astToString(await openapiTS(schema));
const target = new URL('../src/api/schema.d.ts', import.meta.url);

if (process.argv.includes('--check')) {
  if (readFileSync(target, 'utf8') !== output) {
    console.error('API types are stale. Run pnpm generate:api.');
    process.exitCode = 1;
  }
} else {
  writeFileSync(target, output);
}
