import adapter from '@sveltejs/adapter-node';
import { sveltekit } from '@sveltejs/kit/vite';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';
import tailwindcss from '@tailwindcss/vite';
import { defineConfig } from 'vitest/config';

export default defineConfig({
	plugins: [
		tailwindcss(),
		sveltekit({
			preprocess: vitePreprocess(),
			adapter: adapter({ out: 'build' }),
			// Kit 3 replaced $lib with #lib; keep the $lib alias so existing imports work
			alias: { $lib: 'src/lib' }
		})
	],
	test: { include: ['src/**/*.test.ts'] }
});
