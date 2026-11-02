// Where things are, relative to this repo (tests run from mobile/).
import * as path from "node:path";

export const ROOT = path.resolve(__dirname, "..", "..");
export const PYTHON = process.env.WINLINK_PYTHON ?? path.join(ROOT, ".venv", "bin", "python");
export const HARNESS = path.join(__dirname, "harness");
