import { main } from './core.mjs';

process.exitCode = await main(process.argv.slice(2));
