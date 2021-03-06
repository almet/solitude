import logging
import uuid

from tastypie import fields
from tastypie.exceptions import ImmediateHttpResponse
from tastypie.http import HttpNotFound

from lib.bango.constants import CANT_REFUND, NOT_SUPPORTED, OK, PENDING
from lib.bango.errors import BangoFormError
from lib.bango.forms import RefundForm, RefundStatusForm
from lib.transactions.constants import (STATUS_COMPLETED, STATUS_FAILED,
                                        STATUS_PENDING, TYPE_REFUND)
from lib.transactions.models import Transaction
from lib.transactions.resources import TransactionResource
from cached import SimpleResource

log = logging.getLogger('s.bango.refund')


class RefundResponse(object):

    def __init__(self, bango, transaction):
        self.pk = transaction.pk
        self.status = bango
        self.transaction = transaction


class BangoResponse(object):

    def __init__(self, code, message, id):
        self.responseCode = code
        self.responseMessage = message
        self.refundTransactionId = id


class RefundResource(SimpleResource):
    """
    A specific resource for creating refunds and then checking the state of
    that refund against Bango. Since a transaction is created, you can examine
    the state of the transaction in solitude without having to check against
    Bango.
    """
    status = fields.CharField(attribute='status')
    transaction = fields.ToOneField(TransactionResource, 'transaction')

    class Meta(SimpleResource.Meta):
        allowed_methods = ['get']
        list_allowed_methods = ['post']
        resource_name = 'refund'

    def obj_get(self, request, **kw):
        # Work around tastypie by not getting a list, but just
        # an object. With some data in the body.
        if kw['pk'] != 'status':
            raise ImmediateHttpResponse(response=HttpNotFound())

        data = self.deserialize(request, request.raw_post_data,
                                format='application/json')
        form = RefundStatusForm(data)
        if not form.is_valid():
            raise self.form_errors(form)

        obj = form.cleaned_data['uuid']

        try:
            res = self.client('GetRefundStatus', {
                'refundTransactionId': obj.uid_pay
            }, raise_on=(PENDING, CANT_REFUND, NOT_SUPPORTED))
            code = res.responseCode
        except BangoFormError, exc:
            res = BangoResponse(exc.id, exc.message, '')

        code = res.responseCode
        response = RefundResponse(code, obj)

        log.info('Transaction %s: %s' % (code, obj.pk))
        # Alter our transaction if we need to.
        if code == OK and obj.status != STATUS_COMPLETED:
            log.info('Status updated to %s: %s' % (code, obj.pk))
            obj.status = STATUS_COMPLETED
            obj.save()

        elif code == PENDING and obj.status != STATUS_PENDING:
            log.info('Status updated to %s: %s' % (code, obj.pk))
            obj.status = STATUS_PENDING
            obj.save()

        elif (code in (CANT_REFUND, NOT_SUPPORTED) and
              obj.status != STATUS_FAILED):
            log.info('Status updated to %s: %s' % (code, obj.pk))
            obj.status = STATUS_FAILED
            obj.save()

        return response

    def obj_create(self, bundle, request, **kw):
        form = RefundForm(bundle.data)
        if not form.is_valid():
            raise self.form_errors(form)

        obj = form.cleaned_data['uuid']

        external_uuid = str(uuid.uuid4())

        try:
            res = self.client('DoRefund', {
                'transactionId': obj.uid_support,
                'refundType': 'OPERATOR',
                'bango': obj.seller_product.product.bango_id,
                'externalTransactionId': external_uuid
            }, raise_on=(PENDING,))
        except BangoFormError, exc:
            # We haven't been able to get a response back that is pending
            # so I'm not sure if the refundTransactionId is there. Check this.
            res = BangoResponse(exc.id, exc.message, 'todo')

        status = {OK: STATUS_COMPLETED,
                  PENDING: STATUS_PENDING}

        # If that succeeded, create a new transaction for the refund.
        obj = Transaction.objects.create(
            amount=obj.amount,
            buyer=obj.buyer,
            currency=obj.currency,
            provider=obj.provider,
            related=obj,
            seller_product=obj.seller_product,
            # Note: check on this when we can actually do refunds, but for
            # the moment we'll assume they go straight through.
            status=status.get(res.responseCode),
            source='',
            type=TYPE_REFUND,
            uuid=external_uuid,
            uid_pay=res.refundTransactionId)

        # Turn the object back into a bundle so that we get the new transaction
        # in the response.
        bundle.obj = RefundResponse(res.responseCode, obj)
        return bundle
