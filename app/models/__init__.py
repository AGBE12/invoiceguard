"""Package des modèles SQLAlchemy d'InvoiceGuard.

L'import de ces modules garantit que les trois classes (Client, Invoice, User)
sont enregistrées auprès de Base.metadata et que les relations par nom de classe
sont résolues correctement par SQLAlchemy (ex: relationship('Invoice')).
"""

from app.models.client import Client
from app.models.invoice import Invoice
from app.models.user import User

__all__ = ["Client", "Invoice", "User"]
