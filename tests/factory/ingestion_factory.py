"""Factory helpers for Ingestion domain service and view tests."""

from django.core.files.uploadedfile import SimpleUploadedFile

LOAN_TAPE_HEADER = (
    "loan_id,borrower_name,loan_amount,origination_date,interest_rate,"
    "loan_term_months,loan_status,source_system"
)

LOAN_TAPE_ROW = "LG-0001,Murugan Raman,2500000.00,2023-01-15,8.5,120,ACTIVE,LOAN_TAPE"

SERVICER_UPDATE_HEADER = (
    "loan_id,updated_current_balance,updated_payment_status,"
    "updated_days_past_due,last_payment_date,servicer_as_of_date"
)

SERVICER_UPDATE_ROW = "LG-0001,2400000.50,Current,0,2025-06-01,2025-06-30"

DOCUMENT_MANIFEST_HEADER = (
    "loan_id,promissory_note_present,id_proof_present,"
    "income_verification_present,document_verification_status"
)

DOCUMENT_MANIFEST_ROW = "LG-0001,TRUE,TRUE,TRUE,COMPLETE"


class IngestionFactory:
    """Factory helpers for building CSV UploadedFile objects for tests."""

    @staticmethod
    def make_file(content: str, name: str = "test.csv") -> SimpleUploadedFile:
        return SimpleUploadedFile(
            name=name,
            content=content.encode("utf-8"),
            content_type="text/csv",
        )

    @staticmethod
    def loan_tape_file(rows=None, name="loan_tape.csv", header=None) -> SimpleUploadedFile:
        header = header if header is not None else LOAN_TAPE_HEADER
        rows = rows if rows is not None else [LOAN_TAPE_ROW]
        content = header + "\n" + "\n".join(rows) + "\n"
        return IngestionFactory.make_file(content, name)

    @staticmethod
    def servicer_update_file(
        rows=None, name="servicer_update.csv", header=None
    ) -> SimpleUploadedFile:
        header = header if header is not None else SERVICER_UPDATE_HEADER
        rows = rows if rows is not None else [SERVICER_UPDATE_ROW]
        content = header + "\n" + "\n".join(rows) + "\n"
        return IngestionFactory.make_file(content, name)

    @staticmethod
    def document_manifest_file(
        rows=None, name="document_manifest.csv", header=None
    ) -> SimpleUploadedFile:
        header = header if header is not None else DOCUMENT_MANIFEST_HEADER
        rows = rows if rows is not None else [DOCUMENT_MANIFEST_ROW]
        content = header + "\n" + "\n".join(rows) + "\n"
        return IngestionFactory.make_file(content, name)

    @staticmethod
    def make_csrf_authenticated_client(user):
        """Authenticate a user on a Django test client with CSRF disabled."""
        from django.test import Client

        client = Client(enforce_csrf_checks=False)
        client.force_login(user)
        return client
