import { execFileSync } from 'node:child_process';
import { readFileSync, writeFileSync } from 'node:fs';
import openapiTS, { astToString } from 'openapi-typescript';
import ts from 'typescript';

const schema = execFileSync(
  'uv',
  ['run', 'python', '-m', 'opengrader.server.schema'],
  {
    cwd: new URL('../../../..', import.meta.url),
    encoding: 'utf8',
  }
);
const output =
  '// Generated from FastAPI. Run npm run generate:api to update.\n' +
  astToString(
    await openapiTS(schema, {
      transform: (schemaObject) =>
        schemaObject.format === 'binary'
          ? ts.factory.createTypeReferenceNode('Blob')
          : undefined,
    })
  );
const target = new URL('../src/api/schema.d.ts', import.meta.url);

if (process.argv.includes('--check')) {
  if (readFileSync(target, 'utf8') !== output) {
    console.error('API types are stale. Run npm run generate:api.');
    process.exitCode = 1;
  }
} else {
  writeFileSync(target, output);
}
