from odoo import models, fields, api, _
from odoo.exceptions import UserError

class MedicalInvoiceLine(models.Model):
    _name = 'medical.invoice.line'
    _description = 'Medical Invoice Line'

    invoice_id = fields.Many2one('medical.invoice', string='Invoice', ondelete='cascade')
    product_id = fields.Many2one('product.product', string='Medicine', required=True)
    barcode = fields.Char(related='product_id.barcode', readonly=True)
    name = fields.Char('Description', compute='_compute_name', store=True)
    qty = fields.Float('Quantity', default=1.0)
    price_unit = fields.Float('Unit Price', required=True)
    tax_ids = fields.Many2many('account.tax', string='Taxes')
    subtotal = fields.Monetary(string='Subtotal', compute='_compute_subtotal', store=True)
    currency_id = fields.Many2one('res.currency', related='invoice_id.currency_id', store=True, readonly=True)
    # New Fields
    company_name = fields.Char(string='Company')
    batch_no = fields.Char(string='Batch No')
    expiry_date = fields.Char(string='Expiry Date')

    @api.depends('product_id')
    def _compute_name(self):
        for line in self:
            line.name = line.product_id.name or False

    @api.depends('qty','price_unit','tax_ids')
    def _compute_subtotal(self):
        for line in self:
            line.subtotal = line.qty * line.price_unit

    @api.onchange('product_id')
    def _onchange_product_id(self):
        if self.product_id:
            self.price_unit = self.product_id.lst_price
            self.tax_ids = [(6,0,self.product_id.taxes_id.ids)]


class MedicalInvoice(models.Model):
    _name = 'medical.invoice'
    _description = 'Medical Invoice'

    memo_no = fields.Char('Memo No.', required=True,default='New')
    name = fields.Char('Invoice Reference', required=True, copy=False, default=lambda self: _('New'))
    patient_id = fields.Many2one('res.partner', string='Patient', domain=[('is_company','=',False)])
    doctor_id = fields.Many2one('res.partner', string='Doctor', domain=[('is_company','=',False)])
    prescription_id = fields.Many2one('hospital.prescription', string='Prescription')
    date = fields.Datetime('Date', default=lambda self: fields.Datetime.now())
    line_ids = fields.One2many('medical.invoice.line','invoice_id', string='Lines')
    currency_id = fields.Many2one('res.currency', string='Currency', default=lambda self: self.env.company.currency_id.id)
    amount_untaxed = fields.Monetary(string='Untaxed Amount', compute='_compute_amounts', store=True)
    tax_amount = fields.Monetary(string='Tax', compute='_compute_amounts', store=True)
    amount_total = fields.Monetary(string='Total', compute='_compute_amounts', store=True)
    state = fields.Selection([('draft','Draft'),('open','Open'),('delivered','Delivered'),('paid','Paid')], default='draft')
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        required=True,
        default=lambda self: self.env.company,
        readonly=True
    )
    gstin_no = fields.Char('Gstin No.', required=True)
    state_code = fields.Char('State Code', required=True)
    note = fields.Char('Note', required=True)
    signature = fields.Binary("Medical Signature")
    stamp =fields.Binary("Medical Stamp")

    @api.model
    def create(self, vals):
        if vals.get('name','New')=='New':
            seq = self.env['ir.sequence'].next_by_code('medical.invoice') or 'NEW'
            vals['name'] = seq
        return super().create(vals)

    @api.depends('line_ids.qty','line_ids.price_unit','line_ids.tax_ids','currency_id','patient_id')
    def _compute_amounts(self):
        for inv in self:
            untaxed = 0.0
            taxes = 0.0
            currency = inv.currency_id or inv.env.company.currency_id
            partner = inv.patient_id or False
            for line in inv.line_ids:
                price_unit = line.price_unit or 0.0
                qty = line.qty or 0.0
                # Use account.tax compute_all for exact tax computation
                tax_objs = line.tax_ids or self.env['account.tax']
                tax_res = tax_objs._origin.compute_all(price_unit, currency, qty, product=line.product_id, partner=partner) if tax_objs else {'total_excluded': price_unit*qty, 'total_included': price_unit*qty, 'taxes': []}
                untaxed += tax_res.get('total_excluded', 0.0)
                taxes += tax_res.get('total_included', 0.0) - tax_res.get('total_excluded', 0.0)
            inv.amount_untaxed = untaxed
            inv.tax_amount = taxes
            inv.amount_total = untaxed + taxes

    def action_confirm(self):
        # create stock picking to deliver medicines
        StockPicking = self.env['stock.picking']
        StockMove = self.env['stock.move']
        Warehouse = self.env['stock.warehouse']
        Partner = self.env['res.partner']

        for inv in self:
            if not inv.line_ids:
                raise UserError(_('Invoice has no lines to deliver.'))

            # try to find outgoing picking type from first warehouse
            warehouse = Warehouse.search([], limit=1)
            if not warehouse:
                # fallback: no warehouse configured
                raise UserError(_('No stock warehouse configured. Please configure a warehouse.'))

            picking_type = warehouse.out_type_id or warehouse.outgoing_type_id or warehouse._get_default_picking_type()
            if not picking_type:
                raise UserError(_('No outgoing picking type found for warehouse %s') % warehouse.name)

            # partner destination location (customer location)
            dest_location = inv.patient_id.property_stock_customer or self.env.ref('stock.stock_location_customers', raise_if_not_found=False)
            if not dest_location:
                # fallback to partner property or a generic customer location
                dest_location = self.env['stock.location'].search([('usage','=','customer')], limit=1)
            if not dest_location:
                raise UserError(_('No customer location found.'))

            # source location = warehouse stock location
            src_location = warehouse.lot_stock_id

            # create picking
            picking_vals = {
                'partner_id': inv.patient_id.id if inv.patient_id else False,
                'picking_type_id': picking_type.id,
                'location_id': src_location.id,
                'location_dest_id': dest_location.id,
                'origin': inv.name,
            }
            picking = StockPicking.create(picking_vals)

            # create moves
            moves = []
            for line in inv.line_ids:
                if line.product_id.type == 'product' and (line.qty or 0.0) > 0.0:
                    move_vals = {
                        'name': line.name or line.product_id.display_name,
                        'product_id': line.product_id.id,
                        'product_uom_qty': line.qty,
                        'product_uom': line.product_id.uom_id.id,
                        'picking_id': picking.id,
                        'location_id': src_location.id,
                        'location_dest_id': dest_location.id,
                        'origin': inv.name,
                    }
                    moves.append((0,0,move_vals))
            if not moves:
                # nothing to deliver
                pass
            else:
                picking.write({'move_ids': moves})
                # try to confirm & assign (reserve) the picking
                try:
                    picking.action_confirm()
                except Exception:
                    pass
                try:
                    picking.action_assign()
                except Exception:
                    pass

            inv.state = 'delivered'

    def action_create_account_invoice(self):
        AccountMove = self.env['account.move']
        for inv in self:
            company = inv.company_id or self.env.company

            # 🔹 Ensure a Sale Journal exists for the company
            journal = self.env['account.journal'].search([
                ('type', '=', 'sale'),
                ('company_id', '=', company.id)
            ], limit=1)
            if not journal:
                journal = self.env['account.journal'].create({
                    'name': _('Customer Invoices'),
                    'code': 'INV',
                    'type': 'sale',
                    'company_id': company.id,
                })

            lines = []
            for l in inv.line_ids:
                product = l.product_id
                taxes = [(6, 0, l.tax_ids.ids)]
                lines.append((0, 0, {
                    'product_id': product.id,
                    'name': l.name or product.display_name or _('Medicine'),
                    'quantity': l.qty,
                    'price_unit': l.price_unit,
                    'tax_ids': taxes,
                }))

            if not lines:
                raise UserError(_("No medicine lines to invoice."))

            # 🔹 Create the Account Move (Customer Invoice)
            move = AccountMove.create({
                'move_type': 'out_invoice',
                'partner_id': inv.patient_id.id if inv.patient_id else False,
                'invoice_origin': inv.name,
                'invoice_line_ids': lines,
                'journal_id': journal.id,
                'company_id': company.id,
            })

            # Optional: Link to the medical invoice if you have such a field
            if 'account_invoice_id' in inv._fields:
                inv.account_invoice_id = move.id

            inv.message_post(body=_(
                "Customer Invoice <a href='#' data-oe-model='account.move' data-oe-id='%s'>%s</a> created."
            ) % (move.id, move.name))

            return {
                'name': _('Customer Invoice'),
                'type': 'ir.actions.act_window',
                'res_model': 'account.move',
                'view_mode': 'form',
                'res_id': move.id,
            }

    # @api.onchange('prescription_id')
    # def _onchange_prescription_id(self):
    #     if self.prescription_id:
    #         if self.prescription_id.patient_id:
    #             self.patient_id = self.prescription_id.patient_id.id
    #         lines = []
    #         for pl in self.prescription_id.prescription_line_ids:
    #             lines.append((0,0,{
    #                 'product_id': pl.product_id.id,
    #                 'qty': pl.quantity,
    #                 'price_unit': pl.price_unit or pl.product_id.lst_price,
    #                 'tax_ids': [(6,0, pl.product_id.taxes_id.ids)],
    #             }))
    #         # replace existing lines
    #         self.line_ids = [(5,0,0)] + lines

    @api.onchange('prescription_id')
    def _onchange_prescription_id(self):
        if self.prescription_id:
            self.line_ids = [(5, 0, 0)]
            lines = []
            for line in self.prescription_id.prescription_line_ids:
                vals = {
                    'product_id': line.product_id.id,
                    'qty': line.quantity,
                    'price_unit': line.product_id.lst_price,
                    'tax_ids': [(6, 0, line.product_id.taxes_id.ids)],
                    'subtotal': line.quantity * line.product_id.lst_price,
                }
                lines.append((0, 0, vals))
            self.line_ids = lines

    # Public button method
    def action_import_prescription_lines(self):
        """Button to manually import prescription lines."""
        self._onchange_prescription_id()
