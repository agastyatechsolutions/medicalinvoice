from odoo import models, fields, api

class HospitalPrescriptionLine(models.Model):
    _name = 'hospital.prescription.line'
    _description = 'Prescription Line'

    prescription_id = fields.Many2one('hospital.prescription', string='Prescription', ondelete='cascade')
    product_id = fields.Many2one('product.product', string='Medicine', required=True)
    quantity = fields.Float(string='Quantity', default=1.0)
    # price_unit = fields.Float(string='Unit Price')
    notes = fields.Char(string='Notes')

class HospitalPrescription(models.Model):
    _name = 'hospital.prescription'
    _description = 'Hospital Prescription'

    name = fields.Char('Prescription Reference', required=True, copy=False, default='New')
    patient_id = fields.Many2one('res.partner', string='Patient', domain=[('is_company','=',False)])
    date = fields.Datetime('Date', default=lambda self: fields.Datetime.now())
    prescription_line_ids = fields.One2many('hospital.prescription.line','prescription_id', string='Medicines')

    @api.model
    def create(self, vals):
        if vals.get('name','New')=='New':
            seq = self.env['ir.sequence'].next_by_code('hospital.prescription') or 'RX000'
            vals['name'] = seq
        return super().create(vals)
