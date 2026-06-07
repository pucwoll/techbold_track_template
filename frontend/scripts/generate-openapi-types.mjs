import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = dirname(fileURLToPath(import.meta.url));
const frontendRoot = resolve(scriptDir, "..");
const repoRoot = resolve(frontendRoot, "..");
const generatedDir = resolve(frontendRoot, "src", "generated");
const schemaPath = resolve(generatedDir, "openapi.json");
const typesPath = resolve(generatedDir, "openapi.d.ts");

mkdirSync(generatedDir, { recursive: true });

const python = process.env.PYTHON ?? "python";
const pythonPath = [resolve(repoRoot, "backend"), process.env.PYTHONPATH].filter(Boolean).join(":");
const exportSchema = spawnSync(
  python,
  [
    "-c",
    [
      "import json",
      "from app.main import app",
      "print(json.dumps(app.openapi(), indent=2, sort_keys=True))",
    ].join("; "),
  ],
  {
    cwd: repoRoot,
    env: { ...process.env, PYTHONPATH: pythonPath },
    encoding: "utf8",
  },
);

if (exportSchema.status !== 0) {
  process.stderr.write(exportSchema.stderr || "Failed to export FastAPI OpenAPI schema.\n");
  process.exit(exportSchema.status ?? 1);
}

const previousTypes = existsSync(typesPath) ? readFileSync(typesPath, "utf8") : "";

writeFileSync(schemaPath, `${exportSchema.stdout.trim()}\n`);

const openapiTypescript = resolve(frontendRoot, "node_modules", ".bin", "openapi-typescript");
const generateTypes = spawnSync(openapiTypescript, [schemaPath, "-o", typesPath], {
  cwd: frontendRoot,
  stdio: "inherit",
});

if (generateTypes.status !== 0) {
  process.exit(generateTypes.status ?? 1);
}

if (process.argv.includes("--check")) {
  const nextTypes = readFileSync(typesPath, "utf8");
  if (previousTypes !== nextTypes) {
    process.stderr.write("Generated OpenAPI types were stale. Run `pnpm run generate:api` and commit the result.\n");
    process.exit(1);
  }
}
