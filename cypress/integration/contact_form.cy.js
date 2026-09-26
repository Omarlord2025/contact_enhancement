describe("Contact Form UI Tests", () => {
	before(() => {
		cy.login();
	});

	beforeEach(() => {
		cy.login();
	});

	it("JS-16: Normalizes Arabic first_name live on form blur/change", () => {
		cy.visit("/app/contact/new");

		// Type unnormalized Arabic name: initial أ should become ا, multiple spaces collapsed
		cy.get_field("first_name", "Data")
			.clear()
			.type("أحمد   علي", { delay: 50 })
			.blur();

		// Value should be live-normalized to "احمد علي"
		cy.get_field("first_name", "Data")
			.should("have.value", "احمد علي");
	});

	it("JS-17: Enforces channel mutual exclusivity between Landline and Mobile flags in grid", () => {
		cy.visit("/app/contact/new");

		// Add a phone row
		cy.get('.frappe-control[data-fieldname="phone_nos"] .grid-add-row').click();

		// Open first row form or edit inline
		cy.get('.frappe-control[data-fieldname="phone_nos"] .grid-body .grid-row')
			.first()
			.as("firstRow");

		// Click WhatsApp checkbox
		cy.get("@firstRow").find('[data-fieldname="custom_whatsapp"] input').check({ force: true });
		cy.get("@firstRow").find('[data-fieldname="custom_whatsapp"] input').should("be.checked");

		// Click Landline checkbox
		cy.get("@firstRow").find('[data-fieldname="custom_landline"] input').check({ force: true });
		cy.get("@firstRow").find('[data-fieldname="custom_landline"] input').should("be.checked");

		// WhatsApp must now be unchecked automatically
		cy.get("@firstRow").find('[data-fieldname="custom_whatsapp"] input').should("not.be.checked");

		// Now check WhatsApp again
		cy.get("@firstRow").find('[data-fieldname="custom_whatsapp"] input').check({ force: true });
		cy.get("@firstRow").find('[data-fieldname="custom_whatsapp"] input').should("be.checked");

		// Landline must now be unchecked automatically
		cy.get("@firstRow").find('[data-fieldname="custom_landline"] input').should("not.be.checked");
	});
});
