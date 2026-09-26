describe("Name Sync Dialog UI Tests", () => {
	let testCustomer;
	let companyCustomer;

	before(() => {
		cy.login();
		cy.call("contact_enhancements.api.ui_test_helpers.setup_ui_test_data").then((res) => {
			testCustomer = res.message.individual_customer;
			companyCustomer = res.message.company_customer;
		});
	});

	beforeEach(() => {
		cy.login();
	});

	it("JS-06 & JS-12: Triggers Name Sync dialog when renaming customer, Cancel keeps form without saving", () => {
		cy.visit(`/app/customer/${testCustomer}`);

		// Modify customer name
		cy.get_field("customer_name", "Data")
			.clear()
			.type("Cypress Individual Cust Renamed", { delay: 50 });

		// Click Save
		cy.get(".primary-action").click();

		// Name Sync Dialog should appear
		cy.get(".modal.show", { timeout: 10000 }).should("be.visible");
		cy.get('.modal.show [data-fieldname="new_name"]').should("be.visible");

		// Click "إلغاء" (Cancel) button
		cy.get(".modal.show").contains("إلغاء").click();

		// Modal closes, form remains unsaved / dirty
		cy.get(".modal.show").should("not.exist");
		cy.get(".page-title").should("contain", "Not Saved");
	});

	it("JS-11: 'Save Customer Only' button saves customer without updating linked contact", () => {
		cy.visit(`/app/customer/${testCustomer}`);

		cy.get_field("customer_name", "Data")
			.clear()
			.type("Cypress Individual Cust Only", { delay: 50 });

		// Intercept the save call
		cy.intercept("POST", "/api/method/frappe.desk.form.save.savedocs").as("saveCustomer");

		cy.get(".primary-action").click();

		cy.get(".modal.show", { timeout: 10000 }).should("be.visible");

		// Click secondary button (حفظ العميل فقط)
		cy.get(".modal.show .modal-footer .btn-secondary").first().click();

		cy.wait("@saveCustomer").its("response.statusCode").should("eq", 200);
		cy.get(".page-title").should("not.contain", "Not Saved");
	});

	it("JS-14: Displays company warning note when renaming a Company Customer", () => {
		cy.visit(`/app/customer/${companyCustomer}`);

		cy.get_field("customer_name", "Data")
			.clear()
			.type("Cypress Enterprise Corp Renamed", { delay: 50 });

		cy.get(".primary-action").click();

		cy.get(".modal.show", { timeout: 10000 }).should("be.visible");

		// Company warning note should be rendered
		cy.get('.modal.show [data-fieldname="company_note"]').should("contain", "سجل شركة / شراكة");

		// Dismiss
		cy.get(".modal.show").contains("إلغاء").click();
	});
});
