describe("Contact Picker Dialog UI Tests", () => {
	before(() => {
		cy.login();
		// Seed test fixtures
		cy.call("contact_enhancements.api.ui_test_helpers.setup_ui_test_data");
	});

	beforeEach(() => {
		cy.login();
	});

	it("JS-01 & JS-02: Dialog opens on new Customer and searches by phone with channel badges", () => {
		cy.visit("/app/customer/new");

		// Dialog should open automatically
		cy.get(".modal.show", { timeout: 10000 }).should("be.visible");
		cy.get('.modal.show [data-fieldname="country"]').should("be.visible");
		cy.get('.modal.show [data-fieldname="phone"]').should("be.visible");

		// Type test phone into phone search input
		cy.get('.modal.show [data-fieldname="phone"] input')
			.clear()
			.type("01099887766", { delay: 50 });

		// Wait for search debounced API call and results render
		cy.get('.modal.show [data-fieldname="results"] .select-contact-btn', { timeout: 10000 })
			.should("exist")
			.and("be.visible");

		// Verify channel badge appears for WhatsApp
		cy.get('.modal.show [data-fieldname="results"]').contains("WhatsApp").should("exist");
	});

	it("JS-03: Shows live name-matching suggestions when typing in Full Name field", () => {
		cy.visit("/app/customer/new");
		cy.get(".modal.show", { timeout: 10000 }).should("be.visible");

		// Type in new_first_name field
		cy.get('.modal.show [data-fieldname="new_first_name"] input')
			.clear()
			.type("Cypress Test", { delay: 50 });

		// Verify name matching container displays matching contact
		cy.get('.modal.show [data-fieldname="name_matches"]', { timeout: 10000 })
			.should("contain", "Cypress Test User");
	});

	it("JS-04: Clicking Select button fills Customer primary contact and dismisses dialog", () => {
		cy.visit("/app/customer/new");
		cy.get(".modal.show", { timeout: 10000 }).should("be.visible");

		cy.get('.modal.show [data-fieldname="phone"] input')
			.clear()
			.type("01099887766", { delay: 50 });

		cy.get('.modal.show .select-contact-btn', { timeout: 10000 }).first().click();

		// Modal should close
		cy.get(".modal.show").should("not.exist");

		// Field customer_primary_contact should be populated
		cy.get_field("customer_primary_contact", "Link")
			.should("not.have.value", "");
	});

	it("JS-19: Lead dialog includes Organization Name field", () => {
		cy.visit("/app/lead/new");
		cy.get(".modal.show", { timeout: 10000 }).should("be.visible");

		// Organization Name field was added specifically for Lead dialog
		cy.get('.modal.show [data-fieldname="company_name"]').should("exist");
	});
});
