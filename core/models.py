from enum import Enum
from dataclasses import dataclass

class PromptIntent(Enum):
    FIX_BUG = "fix_bug"
    MODIFY_CODE = "modify_code"
    ADD_TESTS = "add_tests"
    CREATE_FEATURE = "create_feature"
    CREATE_CRUD = "create_crud"     # <--- ESTA ES LA QUE TE FALTABA
    UPGRADE_VERSION = "upgrade_version"
    ANALYZE_CODE = "analyze_code"
    UNKNOWN = "unknown"

@dataclass
class FileChange:
    path: str
    content: str
    mode: str
