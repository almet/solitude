from datetime import datetime, timedelta

import commonware.log

from django import forms
from django.conf import settings

from lib.bango.constants import (COUNTRIES, CURRENCIES, INVALID_PERSON,
                                 RATINGS, RATINGS_SCHEME,
                                 VAT_NUMBER_DOES_NOT_EXIST)
from lib.bango.utils import verify_sig
from lib.sellers.models import SellerProductBango
from lib.transactions.constants import (SOURCE_BANGO, STATUS_COMPLETED,
                                        TYPE_PAYMENT, TYPE_REFUND)
from lib.transactions.models import Transaction
from solitude.fields import ListField, URLField

log = commonware.log.getLogger('s.bango')


class ProductForm(forms.ModelForm):
    seller_bango = URLField(to='lib.bango.resources.package.PackageResource')
    seller_product = URLField(to='lib.sellers.resources.SellerProductResource')
    name = forms.CharField()
    packageId = forms.IntegerField()

    class Meta:
        model = SellerProductBango
        fields = ('seller_bango', 'seller_product', 'name', 'packageId')


class PackageForm(forms.Form):
    adminEmailAddress = forms.CharField()
    supportEmailAddress = forms.CharField()
    financeEmailAddress = forms.CharField()
    paypalEmailAddress = forms.CharField()
    vendorName = forms.CharField()
    companyName = forms.CharField()
    address1 = forms.CharField()
    address2 = forms.CharField(required=False)
    addressCity = forms.CharField()
    addressState = forms.CharField()
    addressZipCode = forms.CharField()
    addressPhone = forms.CharField()
    addressFax = forms.CharField(required=False)
    vatNumber = forms.CharField(required=False)
    countryIso = forms.CharField()
    currencyIso = forms.CharField()
    homePageURL = forms.CharField(required=False)
    eventNotificationURL = forms.CharField(required=False)
    seller = URLField(to='lib.sellers.resources.SellerResource')

    @property
    def bango_data(self):
        result = self.cleaned_data.copy()
        del result['seller']
        return result


class SupportEmailForm(forms.Form):
    supportEmailAddress = forms.CharField()

    @property
    def bango_meta(self):
        return {'raise_on': (INVALID_PERSON,),
                'to_field': 'support_person_id',
                'from_field': 'personId',
                'method': 'UpdateSupportEmailAddress'}

    @property
    def bango_data(self):
        return {'emailAddress': self.cleaned_data.get('supportEmailAddress')}


class FinanceEmailForm(forms.Form):
    financeEmailAddress = forms.CharField()

    @property
    def bango_data(self):
        return {'emailAddress': self.cleaned_data.get('financeEmailAddress')}

    @property
    def bango_meta(self):
        return {'raise_on': (INVALID_PERSON,),
                'to_field': 'finance_person_id',
                'from_field': 'personId',
                'method': 'UpdateFinanceEmailAddress'}


class VatNumberForm(forms.Form):
    vatNumber = forms.CharField(required=False)
    _is_delete = False

    def clean_vatNumber(self):
        data = self.cleaned_data.get('vatNumber', '')
        if not data:
            self._is_delete = True
        return data

    @property
    def bango_data(self):
        return {} if self._is_delete else self.cleaned_data.copy()

    @property
    def bango_meta(self):
        if self._is_delete:
            return {'raise_on': (VAT_NUMBER_DOES_NOT_EXIST,),
                    'method': 'DeleteVATNumber'}
        return {'method': 'SetVATNumber'}


class UpdateAddressForm(forms.Form):
    vendorName = forms.CharField()
    address1 = forms.CharField()
    address2 = forms.CharField(required=False)
    addressCity = forms.CharField()
    addressState = forms.CharField()
    addressZipCode = forms.CharField()
    addressPhone = forms.CharField()
    addressFax = forms.CharField(required=False)
    # Note the docs are wrong, its not AddressCountryIso.
    countryIso = forms.CharField()
    homePageURL = forms.CharField(required=False)

    @property
    def bango_data(self):
        return self.cleaned_data.copy()

    @property
    def bango_meta(self):
        return {'method': 'UpdateAddressDetails'}


class CreateBangoNumberForm(forms.Form):
    seller_bango = URLField(to='lib.bango.resources.package.PackageResource')
    seller_product = URLField(to='lib.sellers.resources.SellerProductResource')
    name = forms.CharField(max_length=100)
    # TODO: Expand this bug 814492.
    categoryId = forms.IntegerField()

    @property
    def bango_data(self):
        result = self.cleaned_data.copy()
        result['applicationSize'] = 1
        result['packageId'] = result['seller_bango'].package_id
        del result['seller_bango']
        del result['seller_product']
        return result


class SellerProductForm(forms.Form):
    # Base class for a form that interacts using the
    # seller_product_bango resource.
    seller_product_bango = URLField(
        to='lib.bango.resources.package.BangoProductResource')

    @property
    def bango_data(self):
        result = self.cleaned_data.copy()
        result['bango'] = result['seller_product_bango'].bango_id
        del result['seller_product_bango']
        return result

    def clean_seller_product_bango(self):
        res = self.cleaned_data['seller_product_bango']
        if not res.bango_id:
            raise forms.ValidationError('Empty bango_id for: %s' % res.pk)
        return res


class MakePremiumForm(SellerProductForm):
    currencyIso = forms.ChoiceField(choices=([r, r] for r
                                             in CURRENCIES.keys()))
    price = forms.DecimalField()


class UpdateRatingForm(SellerProductForm):
    ratingScheme = forms.ChoiceField(choices=([r, r] for r in RATINGS_SCHEME))
    rating = forms.ChoiceField(choices=([r, r] for r in RATINGS))


class CreateBillingConfigurationForm(SellerProductForm):
    pageTitle = forms.CharField()
    prices = ListField()
    redirect_url_onerror = forms.URLField()
    redirect_url_onsuccess = forms.URLField()
    transaction_uuid = forms.CharField()

    @property
    def bango_data(self):
        data = super(CreateBillingConfigurationForm, self).bango_data
        data['externalTransactionId'] = data.pop('transaction_uuid')
        del data['prices']
        return data

    def clean_prices(self):
        # Remarkably like a formset, but without the drama.
        prices = self.cleaned_data.get('prices', [])
        results = []
        for price in prices:
            result = PriceForm(price)
            try:
                if not result.is_valid():
                    raise forms.ValidationError(result.errors)
            except AttributeError:
                raise forms.ValidationError('Invalid JSON.')
            results.append(result)
        if not results:
            raise forms.ValidationError(self.fields['prices']
                                            .error_messages['required'])
        return results


class PriceForm(forms.Form):
    amount = forms.DecimalField()
    currency = forms.ChoiceField(choices=([r, r] for r in CURRENCIES.keys()))


class CreateBankDetailsForm(forms.Form):
    seller_bango = URLField(to='lib.bango.resources.package.PackageResource')
    bankAccountPayeeName = forms.CharField(max_length=50)
    bankAccountNumber = forms.CharField(max_length=20, required=False)
    bankAccountCode = forms.CharField(max_length=20)
    bankAccountIban = forms.CharField(max_length=34, required=False)
    bankName = forms.CharField(max_length=50)
    bankAddress1 = forms.CharField(max_length=50)
    bankAddress2 = forms.CharField(max_length=50, required=False)
    bankAddressCity = forms.CharField(max_length=50, required=False)
    bankAddressState = forms.CharField(max_length=50, required=False)
    bankAddressZipCode = forms.CharField(max_length=50)
    bankAddressIso = forms.ChoiceField(choices=([r, r] for r in COUNTRIES))

    def clean(self):
        if not (self.cleaned_data.get('bankAccountNumber')
                or self.cleaned_data.get('bankAccountIban')):
            raise forms.ValidationError('Need either bankAccountNumber '
                                        'or bankIban')
        return self.cleaned_data

    @property
    def bango_data(self):
        result = self.cleaned_data.copy()
        result['packageId'] = result['seller_bango'].package_id
        del result['seller_bango']
        return result


class NotificationForm(forms.Form):
    # This is our own signature of the moz_transaction that we sent to
    # the Billing Config API
    moz_signature = forms.CharField()
    # When passed into the form, this must be a valid transaction_uuid.
    moz_transaction = forms.CharField()
    # This is the Bango billing config ID we created with the API.
    billing_config_id = forms.CharField()
    # These parameters arrive in the query string.
    bango_response_code = forms.CharField()
    bango_response_message = forms.CharField()
    bango_trans_id = forms.CharField()
    # Store the actual price paid.
    amount = forms.DecimalField(required=False)
    currency = forms.CharField(required=False)

    def clean(self):
        cleaned_data = super(NotificationForm, self).clean()
        trans = cleaned_data.get('moz_transaction')
        sig = cleaned_data.get('moz_signature')
        if trans and sig:
            # Both fields were non-empty so check the signature.
            if not verify_sig(sig, trans.uuid):
                log.info('Signature failed: %s'
                         % cleaned_data.get('billing_config_id'))
                raise forms.ValidationError(
                        'Signature did not match: %s for %s'
                        % (sig, trans.uuid))
        return cleaned_data

    def clean_moz_transaction(self):
        uuid = self.cleaned_data['moz_transaction']
        billing_id = self.cleaned_data.get('billing_config_id')

        try:
            trans = Transaction.objects.get(uuid=uuid)
        except Transaction.DoesNotExist:
            log.info('Transaction not found: %s' % billing_id)
            raise forms.ValidationError('Transaction not found: %s' % uuid)

        if trans.status == STATUS_COMPLETED:
            raise forms.ValidationError('Transaction completed: %s' % uuid)

        if trans.created < (datetime.now() -
                            timedelta(seconds=settings.TRANSACTION_EXPIRY)):
            log.info('Transaction: %s' % billing_id)
            raise forms.ValidationError('Transaction expired: %s' % uuid)

        return trans


class SBIForm(forms.Form):
    seller_bango = URLField(to='lib.bango.resources.package.PackageResource')

    @property
    def bango_data(self):
        result = self.cleaned_data.copy()
        result['packageId'] = result['seller_bango'].package_id
        del result['seller_bango']
        return result


class RefundForm(forms.Form):
    uuid = forms.CharField()

    def clean_uuid(self):
        # Rather than just returning a 404, let's help the caller of this API
        # tell them why their transaction is denied.
        transaction = Transaction.objects.get(uuid=self.cleaned_data['uuid'])
        if transaction.provider != SOURCE_BANGO:
            raise forms.ValidationError('Not a Bango transaction')

        elif transaction.status != STATUS_COMPLETED:
            raise forms.ValidationError('Not completed')

        elif transaction.type != TYPE_PAYMENT:
            raise forms.ValidationError('Not a payment')

        elif transaction.is_refunded():
            raise forms.ValidationError('Already refunded')

        return transaction


class RefundStatusForm(forms.Form):
    uuid = forms.CharField()

    def clean_uuid(self):
        # Rather than just returning a 404, let's help the caller of this API
        # tell them why their transaction is denied.
        transaction = Transaction.objects.get(uuid=self.cleaned_data['uuid'])
        if transaction.type != TYPE_REFUND:
            raise forms.ValidationError('Not a refund')

        return transaction
