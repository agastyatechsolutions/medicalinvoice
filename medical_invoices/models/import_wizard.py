from odoo import models, fields, api, _
import base64, csv, io

class PrescriptionImportWizard(models.TransientModel):
    _name = 'medical.prescription.import.wizard'
    _description = 'Import Prescriptions Wizard'

    data_file = fields.Binary('CSV File', help='Upload CSV file with columns: prescription_ref, patient_email_or_name, product_barcode, qty, unit_price')
    filename = fields.Char('File Name')
    prescription_ids = fields.Many2many('hospital.prescription', string='Select existing prescriptions to import')
    invoice_id = fields.Many2one('medical.invoice', string='Target Medical Invoice', required=False)

    def action_import(self):
        # import from CSV if provided
        if self.data_file:
            content = base64.b64decode(self.data_file)
            try:
                stream = io.StringIO(content.decode('utf-8'))
            except Exception:
                stream = io.StringIO(content.decode('latin-1'))
            reader = csv.DictReader(stream)
            created = []
            for row in reader:
                # expected columns: prescription_ref, patient_email_or_name, product_barcode, qty, unit_price
                ref = row.get('prescription_ref') or 'RX-IMPORT'
                patient_key = row.get('patient_email_or_name')
                product_barcode = row.get('product_barcode')
                qty = float(row.get('qty') or 1.0)
                price = float(row.get('unit_price') or 0.0)
                # find or create patient by email or name
                partner = None
                if patient_key:
                    partner = self.env['res.partner'].search([('email','=',patient_key)], limit=1)
                    if not partner:
                        partner = self.env['res.partner'].search([('name','ilike',patient_key)], limit=1)
                if not partner:
                    partner = self.env['res.partner'].create({'name': patient_key or 'Unknown Patient'})
                # find product by barcode
                product = self.env['product.product'].search([('barcode','=',product_barcode)], limit=1)
                if not product and product_barcode:
                    # fallback to name search
                    product = self.env['product.product'].search([('name','ilike',product_barcode)], limit=1)
                # create prescription and line
                prescription = self.env['hospital.prescription'].create({
                    'name': ref,
                    'patient_id': partner.id,
                })
                if product:
                    self.env['hospital.prescription.line'].create({
                        'prescription_id': prescription.id,
                        'product_id': product.id,
                        'quantity': qty,
                        'price_unit': price,
                    })
                created.append(prescription.id)
            # auto-select created prescriptions
            self.prescription_ids = [(6,0, created)]
        # import selected prescriptions into invoice if invoice specified
        if self.invoice_id and self.prescription_ids:
            invoice = self.invoice_id
            for pres in self.prescription_ids:
                for pl in pres.prescription_line_ids:
                    invoice.line_ids = [(0,0,{
                        'product_id': pl.product_id.id,
                        'qty': pl.quantity,
                        'price_unit': pl.price_unit or pl.product_id.lst_price,
                        'tax_ids': [(6,0, pl.product_id.taxes_id.ids)],
                    })]
        return {'type':'ir.actions.act_window_close'}
