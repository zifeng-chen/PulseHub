"""M0 数据模型统一导出。"""

from app.db.models.assets import (
    AssetValuation,
    ParkingSpace,
    PreciousMetal,
    RealEstate,
    UtilityAccount,
    Vehicle,
    VehicleInsurance,
)
from app.db.models.business import (
    Account,
    Asset,
    Attachment,
    BusinessEvent,
    EmploymentProfile,
    EventMovement,
    RecurringExpense,
    RecurringIncome,
    TransferGroup,
)
from app.db.models.capture import OcrCallLog
from app.db.models.finance import (
    CreditCard,
    CreditCardBill,
    InvestmentAccount,
    InvestmentTrade,
    InvestmentValuation,
    Liability,
    LiabilityRepayment,
    Mortgage,
    VehicleLoan,
)
from app.db.models.config import (
    AIEvaluationCase,
    Category,
    DeploymentConfig,
    ExchangeRate,
    HouseholdSetting,
    LocalAIConfig,
    MaintenanceWindow,
    NotificationChannel,
    NotificationRecord,
    OcrConfig,
    PrivacyPolicy,
    SchemaVersion,
    SecretKeyVersion,
    ThirdPartyAIProvider,
    UserPreference,
)
from app.db.models.identity import (
    AuditLog,
    AuditLogChange,
    Household,
    HouseholdMember,
    MemberScope,
    Role,
    RolePermission,
    SystemConfig,
    User,
    UserSession,
)
from app.db.models.leases import Lease, LeaseDeposit, LeaseReceivable
from app.db.models.ops import BackupRecord, IdempotencyRecord, RestoreRecord

__all__ = [
    "Account", "Asset", "Attachment", "BusinessEvent", "EventMovement", "TransferGroup",
    "RecurringExpense", "RecurringIncome", "EmploymentProfile",
    "AssetValuation", "ParkingSpace", "PreciousMetal", "RealEstate",
    "UtilityAccount", "Vehicle", "VehicleInsurance",
    "Liability", "LiabilityRepayment", "Mortgage", "VehicleLoan",
    "CreditCard", "CreditCardBill",
    "InvestmentAccount", "InvestmentTrade", "InvestmentValuation",
    "AIEvaluationCase", "Category", "DeploymentConfig", "ExchangeRate",
    "HouseholdSetting", "LocalAIConfig", "MaintenanceWindow", "NotificationChannel", "NotificationRecord",
    "OcrConfig", "PrivacyPolicy", "SchemaVersion", "SecretKeyVersion",
    "ThirdPartyAIProvider", "UserPreference",
    "AuditLog", "AuditLogChange", "Household", "HouseholdMember", "MemberScope",
    "Role", "RolePermission", "SystemConfig", "User", "UserSession",
    "BackupRecord", "IdempotencyRecord", "RestoreRecord",
    "Lease", "LeaseReceivable", "LeaseDeposit",
    "OcrCallLog",
]
