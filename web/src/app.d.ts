declare global {
	namespace App {
		interface Locals {
			authed: boolean;
		}
		interface Error {
			message: string;
		}
	}
}

export {};
