const { defineConfig } = require("cypress");
const path = require("path");

module.exports = defineConfig({
	projectId: "contact_enhancements",
	adminPassword: "admin",
	testUser: "Administrator",
	defaultCommandTimeout: 20000,
	pageLoadTimeout: 20000,
	video: false,
	viewportHeight: 960,
	viewportWidth: 1400,
	retries: {
		runMode: 1,
		openMode: 1,
	},
	e2e: {
		setupNodeEvents(on, config) {
			try {
				return require("../frappe/cypress/plugins/index.js")(on, config);
			} catch (e) {
				return config;
			}
		},
		testIsolation: false,
		baseUrl: "http://127.0.0.1:8000",
		specPattern: "cypress/integration/**/*.cy.js",
		supportFile: path.resolve(__dirname, "../frappe/cypress/support/e2e.js"),
	},
});
