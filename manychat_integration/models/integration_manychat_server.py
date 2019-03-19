# -*- coding: utf-8 -*-

import requests
import json
import base64
from datetime import datetime
import functools
from werkzeug import urls
import logging
_logger = logging.getLogger(__name__)

from odoo import api, fields, models, tools

try:
    # We use a jinja2 sandboxed environment to render mako templates.
    # Note that the rendering does not cover all the mako syntax, in particular
    # arbitrary Python statements are not accepted, and not all expressions are
    # allowed: only "public" attributes (not starting with '_') of objects may
    # be accessed.
    # This is done on purpose: it prevents incidental or malicious execution of
    # Python code that may break the security of the server.
    from jinja2.sandbox import SandboxedEnvironment
    mako_template_env = SandboxedEnvironment(
        block_start_string="<%",
        block_end_string="%>",
        variable_start_string="${",
        variable_end_string="}",
        comment_start_string="<%doc>",
        comment_end_string="</%doc>",
        line_statement_prefix="%",
        line_comment_prefix="##",
        trim_blocks=True,               # do not output newline after blocks
        autoescape=True,                # XML/HTML automatic escaping
    )
    mako_template_env.globals.update({
        'str': str,
        'quote': urls.url_quote,
        'urlencode': urls.url_encode,
        'datetime': datetime,
        'len': len,
        'abs': abs,
        'min': min,
        'max': max,
        'sum': sum,
        'filter': filter,
        'reduce': functools.reduce,
        'map': map,
        'round': round,
        
        # dateutil.relativedelta is an old-style class and cannot be directly
        # instanciated wihtin a jinja2 expression, so a lambda "proxy" is
        # is needed, apparently.
        'relativedelta': lambda *a, **kw : relativedelta.relativedelta(*a, **kw),
    })
except ImportError:
    _logger.warning("jinja2 not available, templating features will not work!")

class IntegrationManyChatServer(models.Model):

    _name = 'integration.manychat.server'

    name = fields.Char(string="Name", help="Human meaningful name to describe the Dynamic Content Server", required=True)
    server_slug = fields.Char(string="Slug", help="Name in url", required=True)
    model_id = fields.Many2one('ir.model', string="Model", help="Reads Data from this model", required=True)
    model_name = fields.Char(related="model_id.model", string="Model Name")
    domain = fields.Char(string="Domain", help="Returns only records that match the domain, Use ${last_input_text} to get the last input text", required=True)
    message_ids = fields.One2many('integration.manychat.server.message', 'server_id', string="Messages")

class IntegrationManyChatServerMessage(models.Model):

    _name = 'integration.manychat.server.message'
    
    server_id = fields.Many2one('integration.manychat.server', string="Server")
    model_id = fields.Many2one(related="server_id.model_id", string="Model")
    model_object_field_id = fields.Many2one('ir.model.fields', string="Field", help="Select target field from the related document model.\nIf it is a relationship field you will be able to select a target field at the destination of the relationship.")
    sub_object_id = fields.Many2one('ir.model', string='Sub-model', readonly=True, help="When a relationship field is selected as first field, this field shows the document model the relationship goes to.")
    sub_model_object_field_id = fields.Many2one('ir.model.fields', string='Sub-field', help="When a relationship field is selected as first field, this field lets you select the target field within the destination document model (sub-model).")
    null_value = fields.Char(string='Default Value', help="Optional value to use if the target field is empty")
    copyvalue = fields.Char(string='Placeholder Expression', help="Final placeholder expression, to be copy-pasted in the desired template field.")
    type = fields.Selection([('text', 'Text')], string="Type", required=True)
    text = fields.Text(string="Text", help="Use Dynamic Placeholders")

    @api.model
    def build_expression(self, field_name, sub_field_name, null_value):
        """Returns a placeholder expression for use in a template field,
           based on the values provided in the placeholder assistant.

          :param field_name: main field name
          :param sub_field_name: sub field name (M2O)
          :param null_value: default value if the target value is empty
          :return: final placeholder expression
        """
        expression = ''
        if field_name:
            expression = "${object." + field_name
            if sub_field_name:
                expression += "." + sub_field_name
            if null_value:
                expression += " or '''%s'''" % null_value
            expression += "}"
        return expression

    @api.onchange('model_object_field_id')
    def _onchange_model_object_field_id(self):
        if self.model_object_field_id.relation:
            self.sub_object_id = self.env['ir.model'].search([('model','=',self.model_object_field_id.relation)])[0].id
        else:
            self.sub_object_id = False    
    
        if self.model_object_field_id:
            self.copyvalue = self.build_expression(self.model_object_field_id.name, self.sub_model_object_field_id.name, self.null_value)

    @api.onchange('sub_model_object_field_id')
    def _onchange_sub_model_object_field_id(self):
        if self.sub_model_object_field_id:
            self.copyvalue = self.build_expression(self.model_object_field_id.name, self.sub_model_object_field_id.name, self.null_value)

    def render_message(self, template, model, res_id):
        """Render the given template text, replace mako expressions ``${expr}``
           with the result of evaluating these expressions with
           an evaluation context containing:

                * ``user``: browse_record of the current user
                * ``object``: browse_record of the document record this mail is
                              related to
                * ``context``: the context passed to the mail composition wizard

           :param str template: the template text to render
           :param str model: model name of the document record this mail is related to.
           :param int res_id: id of document records those mails are related to.
        """

        template = mako_template_env.from_string(tools.ustr(template))

        # prepare template variables
        user = self.env.user
        record = self.env[model].browse(res_id)
        
        variables = {
            'user': user
        }
        
        variables['object'] = record
        try:
            render_result = template.render(variables)
        except Exception:
            _logger.error("Failed to render template %r using values %r" % (template, variables))
            render_result = u""
        if render_result == u"False":
            render_result = u""

        return render_result